#!/usr/bin/env python3
"""Monitoring VNF: per-DSCP-class counters, CSV and Prometheus metrics.
Usage: python3 /app/vnf.py on|off|status|metrics|logs
"""
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

LOGFILE = "/var/log/mon.log"
CSVFILE = "/var/log/mon.csv"
IF = "eth0"          # traffic arriving from pol = forward direction, after shaping
PORT = 9100
NAMES = {46: "voip", 26: "video", 0: "bulk"}
ORDER = ["voip", "video", "bulk", "other"]

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


def stop_daemon():
    subprocess.run(["pkill", "-f", "vnf.py daemon"], capture_output=True)


def off():
    stop_daemon()
    emit("mon: off")


def on():
    off()
    time.sleep(0.5)   # let the old metrics port close
    header = ["time"] + [f"{c}_{m}" for c in ORDER for m in ("mbps", "pkts")]
    with open(CSVFILE, "w") as f:
        f.write(",".join(header) + "\n")
    subprocess.Popen([sys.executable, "/app/vnf.py", "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    emit(f"mon: monitoring {IF}, csv {CSVFILE}, metrics on :{PORT}")


def capture(counts, lock):
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    s.bind((IF, 0))
    while True:
        data, addr = s.recvfrom(65535)
        # addr[2] == 0 means the frame was addressed to us (incoming on eth0)
        if addr[2] != 0 or len(data) < 34 or data[12:14] != b"\x08\x00":
            continue
        tos = data[15]
        length = struct.unpack("!H", data[16:18])[0]
        cls = NAMES.get(tos >> 2, "other")
        with lock:
            counts[cls][0] += 1
            counts[cls][1] += length


def daemon():
    from prometheus_client import Counter, Gauge, start_http_server
    pk = Counter("qosi_packets", "Packets seen after the chain", ["traffic_class"])
    by = Counter("qosi_bytes", "Bytes seen after the chain", ["traffic_class"])
    rate = Gauge("qosi_rate_mbps", "Rate over the last second", ["traffic_class"])
    for _ in range(20):
        try:
            start_http_server(PORT)
            break
        except OSError:
            time.sleep(0.5)

    counts = {c: [0, 0] for c in ORDER}
    lock = threading.Lock()
    threading.Thread(target=capture, args=(counts, lock), daemon=True).start()
    emit("mon: sampler started")

    prev = {c: [0, 0] for c in ORDER}
    while True:
        time.sleep(1)
        with lock:
            now = {c: list(v) for c, v in counts.items()}
        row = [f"{time.time():.0f}"]
        for c in ORDER:
            dp, db = now[c][0] - prev[c][0], now[c][1] - prev[c][1]
            mbps = db * 8 / 1e6
            pk.labels(c).inc(dp)
            by.labels(c).inc(db)
            rate.labels(c).set(mbps)
            row += [f"{mbps:.3f}", str(dp)]
            if dp:
                emit(f"{time.strftime('%H:%M:%S')} {c:5} {mbps:7.3f} Mbit/s  {dp} pkts")
        with open(CSVFILE, "a") as f:
            f.write(",".join(row) + "\n")
        prev = now


def metrics():
    body = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/metrics", timeout=3).read().decode()
    print("\n".join(l for l in body.splitlines() if l.startswith("qosi_")))


def status():
    print(f"--- {CSVFILE} (last 5 rows)")
    subprocess.run(["tail", "-n", "5", CSVFILE])
    print("--- metrics")
    try:
        metrics()
    except OSError as e:
        print("metrics endpoint not reachable:", e)


def logs():
    subprocess.run(["tail", "-n", "20", "-F", LOGFILE])


if __name__ == "__main__":
    cmds = {"on": on, "off": off, "status": status, "metrics": metrics,
            "logs": logs, "daemon": daemon}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("Usage: vnf.py on|off|status|metrics|logs")
    cmds[sys.argv[1]]()
