#!/usr/bin/env python3
"""Firewall VNF (access control): default-deny whitelist with iptables + live logging.
Usage: python3 /app/vnf.py on|off|status|logs
"""
import subprocess
import sys
import threading

VOIP_C, VIDEO_C, BULK_C = "10.0.1.10", "10.0.1.20", "10.0.1.30"
VOIP_S, VIDEO_S, BULK_S = "10.0.20.10", "10.0.20.20", "10.0.20.30"
PORT = "5301"
LOGFILE = "/var/log/fw.log"

ALLOW = [
    (VOIP_C,  VOIP_S,  "icmp", None),
    (VIDEO_C, VIDEO_S, "udp",  PORT),
    (VIDEO_C, VIDEO_S, "tcp",  PORT),
    (BULK_C,  BULK_S,  "tcp",  PORT),
]


def ipt(*args):
    subprocess.run(["iptables", *args], check=True)


def stop_logger():
    subprocess.run(["pkill", "-f", "vnf.py daemon"], capture_output=True)
    subprocess.run(["pkill", "tcpdump"], capture_output=True)


def off():
    stop_logger()
    ipt("-F", "FORWARD")
    ipt("-P", "FORWARD", "ACCEPT")
    emit("fw: off")


def on():
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    off()
    ipt("-A", "FORWARD", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
    for src, dst, proto, port in ALLOW:
        match = ["-s", src, "-d", dst, "-p", proto]
        if port:
            match += ["--dport", port]
        ipt("-A", "FORWARD", *match, "-m", "conntrack", "--ctstate", "NEW",
            "-j", "NFLOG", "--nflog-group", "2")          # log accepted new flows
        ipt("-A", "FORWARD", *match, "-j", "ACCEPT")
    ipt("-A", "FORWARD", "-m", "limit", "--limit", "20/second", "--limit-burst", "50",
        "-j", "NFLOG", "--nflog-group", "1")              # log drops (rate limited)
    ipt("-A", "FORWARD", "-j", "DROP")
    subprocess.Popen([sys.executable, "/app/vnf.py", "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    emit("fw: whitelist active")


def emit(line):
    with open(LOGFILE, "a") as f:
        f.write(line + "\n")
    try:
        with open("/proc/1/fd/1", "w") as out:   # shows up in `docker logs`
            out.write(line + "\n")
    except OSError:
        pass


def watch(group, label):
    p = subprocess.Popen(["tcpdump", "-l", "-n", "-tttt", "-i", f"nflog:{group}"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for line in p.stdout:
        line = line.strip()
        if line and "listening on" not in line:
            emit(f"{label:5} {line}")


def daemon():
    emit("fw: logger started")
    for g, label in ((1, "DROP"), (2, "ALLOW")):
        threading.Thread(target=watch, args=(g, label), daemon=True).start()
    threading.Event().wait()


def logs():
    subprocess.run(["tail", "-n", "20", "-F", LOGFILE])


def status():
    subprocess.run(["iptables", "-L", "FORWARD", "-n", "-v", "-x", "--line-numbers"])


if __name__ == "__main__":
    cmds = {"on": on, "off": off, "status": status, "logs": logs, "daemon": daemon}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("Usage: vnf.py on|off|status|logs")
    cmds[sys.argv[1]]()