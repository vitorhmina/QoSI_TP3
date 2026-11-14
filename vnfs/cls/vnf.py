#!/usr/bin/env python3
"""Classification VNF: marks DSCP by traffic type (iptables mangle) + live logging.
Usage: python3 /app/vnf.py on|off|status|logs
"""
import subprocess
import sys
import threading

LOGFILE = "/var/log/cls.log"
IN_IF = "eth0"  # only traffic coming from the client side is classified

# (name, DSCP label, DSCP value, extra iptables match, NFLOG group)
CLASSES = [
    ("voip",  "EF",   46, ["-p", "icmp"], 1),
    ("video", "AF31", 26, ["-p", "udp"],  2),
    ("bulk",  "BE",    0, [],              3),   # catch-all, must stay last
]


def ipt(*args):
    subprocess.run(["iptables", *args], check=True)


def stop_logger():
    subprocess.run(["pkill", "-f", "vnf.py daemon"], capture_output=True)
    subprocess.run(["pkill", "tcpdump"], capture_output=True)


def emit(line):
    with open(LOGFILE, "a") as f:
        f.write(line + "\n")
    try:
        with open("/proc/1/fd/1", "w") as out:   # shows up in `docker logs`
            out.write(line + "\n")
    except OSError:
        pass


def off():
    stop_logger()
    ipt("-t", "mangle", "-F", "FORWARD")
    emit("cls: off")


def on():
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    off()
    for name, label, dscp, match, group in CLASSES:
        base = ["-t", "mangle", "-A", "FORWARD", "-i", IN_IF, *match]
        ipt(*base, "-j", "DSCP", "--set-dscp", str(dscp))
        ipt(*base, "-m", "conntrack", "--ctstate", "NEW",
            "-j", "NFLOG", "--nflog-group", str(group))     # log each new flow, once
        ipt(*base, "-j", "ACCEPT")                          # stop: first matching class wins
    subprocess.Popen([sys.executable, "/app/vnf.py", "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    emit("cls: classification active")


def watch(group, label, dscp):
    p = subprocess.Popen(["tcpdump", "-l", "-n", "-tttt", "-i", f"nflog:{group}"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in p.stdout:
        line = line.strip()
        if line and "listening on" not in line:
            emit(f"{label:4} dscp={dscp:<2} {line}")


def daemon():
    emit("cls: logger started")
    for _name, label, dscp, _match, group in CLASSES:
        threading.Thread(target=watch, args=(group, label, dscp), daemon=True).start()
    threading.Event().wait()


def logs():
    subprocess.run(["tail", "-n", "20", "-F", LOGFILE])


def status():
    subprocess.run(["iptables", "-t", "mangle", "-L", "FORWARD", "-n", "-v", "-x", "--line-numbers"])


if __name__ == "__main__":
    cmds = {"on": on, "off": off, "status": status, "logs": logs, "daemon": daemon}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("Usage: vnf.py on|off|status|logs")
    cmds[sys.argv[1]]()
    