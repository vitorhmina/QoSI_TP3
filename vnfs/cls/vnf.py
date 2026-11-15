#!/usr/bin/env python3
"""Classification VNF: Scapy + NFQUEUE. Inspects packets, marks DSCP, logs new flows.
Usage: python3 /app/vnf.py on|off|status|logs
"""
import json
import subprocess
import sys
import threading
import time

LOGFILE = "/var/log/cls.log"
STATS = "/var/log/cls.stats.json"
IN_IF = "eth0"      # only traffic coming from the client side is classified
QUEUE = 1
CLASSES = {"voip": ("EF", 46), "video": ("AF31", 26), "bulk": ("BE", 0)}

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
    subprocess.run(["iptables", *args], check=True)


def off():
    subprocess.run(["iptables", "-t", "mangle", "-F", "FORWARD"], capture_output=True)
    subprocess.run(["pkill", "-f", "vnf.py daemon"], capture_output=True)
    emit("cls: off")


def on():
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    off()
    ipt("-t", "mangle", "-A", "FORWARD", "-i", IN_IF, "-j", "NFQUEUE",
        "--queue-num", str(QUEUE), "--queue-bypass")
    subprocess.Popen([sys.executable, "/app/vnf.py", "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    emit("cls: classification active (Scapy + NFQUEUE)")


def classify(ip):
    """Return the traffic class of a Scapy IP packet."""
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
    counts = {c: [0, 0] for c in CLASSES}
    flows = set()
    slock = threading.Lock()

    def handle(pkt):
        try:
            ip = IP(pkt.get_payload())
            cls = classify(ip)
            label, dscp = CLASSES[cls]
            ip.tos = (dscp << 2) | (ip.tos & 3)        # keep the ECN bits
            del ip.chksum                              # Scapy recomputes the IP header checksum
            pkt.set_payload(bytes(ip))
            with slock:
                counts[cls][0] += 1
                counts[cls][1] += len(ip)
                key = flow_key(ip)
                new = key not in flows
                if new:
                    if len(flows) > 50000:
                        flows.clear()
                    flows.add(key)
            if new:
                p, s, sp, d, dp = key
                emit(f"{label:4} dscp={dscp:<2} {time.strftime('%H:%M:%S')} "
                     f"{p} {s}:{sp} > {d}:{dp}")
        except Exception as e:                         # never drop a packet because of a bug
            emit(f"cls: error {type(e).__name__}: {e}")
        pkt.accept()

    def write_stats():
        while True:
            time.sleep(1)
            with slock:
                data = {c: {"packets": v[0], "bytes": v[1]} for c, v in counts.items()}
            with open(STATS, "w") as f:
                json.dump(data, f)

    threading.Thread(target=write_stats, daemon=True).start()
    emit("cls: classifier daemon started")
    nfq = NetfilterQueue()
    nfq.bind(QUEUE, handle, max_len=4096)
    nfq.run()


def logs():
    subprocess.run(["tail", "-n", "20", "-F", LOGFILE])


def status():
    print("--- NFQUEUE rule", flush=True)
    subprocess.run(["iptables", "-t", "mangle", "-L", "FORWARD", "-n", "-v", "-x"])
    print("--- packets classified (Scapy)", flush=True)
    try:
        for c, v in json.load(open(STATS)).items():
            print(f"{c:6} dscp={CLASSES[c][1]:<2} packets={v['packets']:8d} bytes={v['bytes']:10d}")
    except (OSError, ValueError):
        print("no stats yet")


if __name__ == "__main__":
    cmds = {"on": on, "off": off, "status": status, "logs": logs, "daemon": daemon}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("Usage: vnf.py on|off|status|logs")
    cmds[sys.argv[1]]()
    