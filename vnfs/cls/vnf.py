#!/usr/bin/env python3
"""Classification VNF: Scapy + NFQUEUE, per flow.
Scapy inspects the first packet of a flow and sets a mark, the packet is re-injected (repeat),
the mark is saved in conntrack, and iptables applies the DSCP to every packet in-kernel.
Usage: python3 /app/vnf.py on [rules|ml] | off | status | mode | logs
"""
import json
import os
import subprocess
import sys
import threading
import time

LOGFILE = "/var/log/cls.log"
STATS = "/var/log/cls.stats.json"
MODEFILE = "/var/log/cls.mode"
IN_IF = "eth0"      # only traffic coming from the client side is classified
QUEUE = 1
CLASSES = {"voip": ("EF", 46, 1), "video": ("AF31", 26, 2), "bulk": ("BE", 0, 3)}  # label, dscp, mark
MODES = ("rules", "ml")

_lock = threading.Lock()


def emit(line):
    with _lock:
        with open(LOGFILE, "a") as f:
            f.write(line + "\n")
        try:
            with open("/proc/1/fd/1", "w") as out:   # shows up in `docker logs`
                out.write(line + "\n")
        except OSError:
            pass


def ipt(*args):
    subprocess.run(["iptables", "-t", "mangle", *args], check=True)


def off():
    subprocess.run(["iptables", "-t", "mangle", "-F", "FORWARD"], capture_output=True)
    subprocess.run(["pkill", "-f", "vnf.py daemon"], capture_output=True)
    emit("cls: off")


def on(mode=None):
    mode = mode or os.environ.get("CLS_MODE", "rules")
    if mode not in MODES:
        sys.exit(f"mode must be one of {MODES}")
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    off()
    with open(MODEFILE, "w") as f:
        f.write(mode)
    base = ["-A", "FORWARD", "-i", IN_IF]
    # 1. known flow: restore its mark from conntrack (only if no mark yet, so a mark set by
    #    Scapy on a re-injected packet is not overwritten)
    ipt(*base, "-m", "mark", "--mark", "0", "-j", "CONNMARK", "--restore-mark")
    # 2. marked packet: remember the decision, set DSCP, accept (stays in the kernel)
    for _label, dscp, mark in CLASSES.values():
        ipt(*base, "-m", "mark", "--mark", str(mark), "-j", "CONNMARK", "--save-mark")
        ipt(*base, "-m", "mark", "--mark", str(mark), "-j", "DSCP", "--set-dscp", str(dscp))
        ipt(*base, "-m", "mark", "--mark", str(mark), "-j", "ACCEPT")
    # 3. unmarked (new flow): ask Scapy; fail open if the daemon is down
    ipt(*base, "-j", "NFQUEUE", "--queue-num", str(QUEUE), "--queue-bypass")
    subprocess.Popen([sys.executable, "/app/vnf.py", "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    emit(f"cls: classification active (Scapy + NFQUEUE, mode {mode})")


def read_mode():
    try:
        return open(MODEFILE).read().strip()
    except OSError:
        return "rules"


def classify_rules(ip):
    from scapy.layers.inet import ICMP, UDP
    if ip.haslayer(ICMP):
        return "voip"                                  # ICMP stands in for VoIP probes
    if ip.haslayer(UDP):
        udp = ip[UDP]
        data = bytes(udp.payload)
        if len(data) >= 12 and data[0] >> 6 == 2 and 16384 <= udp.dport <= 32767:
            return "voip"                              # RTP: version 2 + RTP port range
        return "video"                                 # other UDP = streaming
    return "bulk"                                      # TCP and anything else = best effort


def classify_ml(ip):
    # placeholder: the flow-feature model goes here. Falls back to the rules for now.
    return classify_rules(ip)


def flow_key(ip):
    from scapy.layers.inet import ICMP, TCP, UDP
    if ip.haslayer(TCP):
        return ("tcp", ip.src, ip[TCP].sport, ip.dst, ip[TCP].dport)
    if ip.haslayer(UDP):
        return ("udp", ip.src, ip[UDP].sport, ip.dst, ip[UDP].dport)
    return ("icmp" if ip.haslayer(ICMP) else str(ip.proto), ip.src, 0, ip.dst, 0)


def daemon():
    from netfilterqueue import NetfilterQueue
    from scapy.layers.inet import IP
    classify = classify_ml if read_mode() == "ml" else classify_rules
    flows = {}                                         # flow key -> class (decision cache)
    counts = {c: 0 for c in CLASSES}

    def handle(pkt):
        try:
            ip = IP(pkt.get_payload())
            key = flow_key(ip)
            cls = flows.get(key)
            if cls is None:
                cls = classify(ip)
                if len(flows) > 50000:
                    flows.clear()
                flows[key] = cls
                counts[cls] += 1
                label, dscp, _ = CLASSES[cls]
                p, s, sp, d, dp = key
                emit(f"{label:4} dscp={dscp:<2} {time.strftime('%H:%M:%S')} {p} {s}:{sp} > {d}:{dp}")
            pkt.set_mark(CLASSES[cls][2])
            pkt.repeat()                               # re-run the chain with the mark set
            return
        except Exception as e:                         # never drop a packet because of a bug
            emit(f"cls: error {type(e).__name__}: {e}")
        pkt.accept()

    def write_stats():
        while True:
            time.sleep(1)
            with open(STATS, "w") as f:
                json.dump({c: {"flows": n} for c, n in dict(counts).items()}, f)

    threading.Thread(target=write_stats, daemon=True).start()
    emit(f"cls: classifier daemon started (mode {read_mode()})")
    nfq = NetfilterQueue()
    nfq.bind(QUEUE, handle, max_len=4096)
    nfq.run()


def logs():
    subprocess.run(["tail", "-n", "20", "-F", LOGFILE])


def status():
    print(f"--- mode: {read_mode()}", flush=True)
    print("--- mangle rules (DSCP rules = packets per class, NFQUEUE = packets sent to Scapy)", flush=True)
    subprocess.run(["iptables", "-t", "mangle", "-L", "FORWARD", "-n", "-v", "-x"])
    print("--- flows classified by Scapy", flush=True)
    try:
        for c, v in json.load(open(STATS)).items():
            print(f"{c:6} dscp={CLASSES[c][1]:<2} flows={v['flows']}")
    except (OSError, ValueError):
        print("no stats yet")


if __name__ == "__main__":
    cmds = {"on": on, "off": off, "status": status, "mode": lambda: print(read_mode()),
            "logs": logs, "daemon": daemon}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit("Usage: vnf.py on [rules|ml] | off | status | mode | logs")
    if sys.argv[1] == "on":
        on(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        cmds[sys.argv[1]]()
