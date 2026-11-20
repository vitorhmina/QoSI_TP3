#!/usr/bin/env python3
"""Policing/scheduling VNF: HTB shaper on eth1 with DSCP classes + live stats log.
Prometheus metrics on :9100 (per class: sent, drops, queue backlog, configured rate/ceil).
Usage: python3 /app/vnf.py on|off|status|logs
Optional environment variable: BULK_CEIL (e.g. 12mbit, must be >= 9mbit), default 19mbit.
"""
import os
import re
import subprocess
import sys
import time

LOGFILE = "/var/log/pol.log"
METRICS_PORT = 9100
IF = "eth1"            # egress toward the servers (the forward direction)
TOTAL = "19mbit"       # just below the 20 Mbit/s bottleneck, so the queue forms here
BULK_CEIL = os.environ.get("BULK_CEIL", TOTAL)   # lower this to police bulk traffic harder

# (name, classid, DSCP tos byte, rate, ceil, prio, leaf qdisc)
CLASSES = [
    ("voip",  10, "0xb8", "1mbit", TOTAL,     0, ["pfifo", "limit", "50"]),
    ("video", 20, "0x68", "9mbit", TOTAL,     1, ["pfifo", "limit", "200"]),
    ("bulk",  30, None,   "9mbit", BULK_CEIL, 2, ["fq_codel"]),
]


def tc(*args, check=True):
    return subprocess.run(["tc", *args], check=check, capture_output=True, text=True)


def emit(line):
    with open(LOGFILE, "a") as f:
        f.write(line + "\n")
    try:
        with open("/proc/1/fd/1", "w") as out:   # shows up in `docker logs`
            out.write(line + "\n")
    except OSError:
        pass


def stop_logger():
    subprocess.run(["pkill", "-f", "vnf.py daemon"], capture_output=True)


def off():
    stop_logger()
    tc("qdisc", "del", "dev", IF, "root", check=False)
    emit("pol: off")


def on():
    subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], check=True, capture_output=True)
    off()
    tc("qdisc", "add", "dev", IF, "root", "handle", "1:", "htb", "default", "30")
    tc("class", "add", "dev", IF, "parent", "1:", "classid", "1:1", "htb", "rate", TOTAL, "ceil", TOTAL)
    for _n, cid, tos, rate, ceil, prio, leaf in CLASSES:
        tc("class", "add", "dev", IF, "parent", "1:1", "classid", f"1:{cid}",
           "htb", "rate", rate, "ceil", ceil, "prio", str(prio))
        tc("qdisc", "add", "dev", IF, "parent", f"1:{cid}", "handle", f"{cid}:", *leaf)
        if tos:
            tc("filter", "add", "dev", IF, "parent", "1:", "protocol", "ip", "prio", "1",
               "u32", "match", "ip", "dsfield", tos, "0xfc", "flowid", f"1:{cid}")
    subprocess.Popen([sys.executable, "/app/vnf.py", "daemon"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    emit(f"pol: shaping {IF} to {TOTAL} (voip prio 0, video prio 1, bulk prio 2, bulk ceil {BULK_CEIL})")


def read_stats():
    """Per-class (bytes sent, drops, backlog packets) from the leaf qdiscs."""
    out = tc("-s", "qdisc", "show", "dev", IF, check=False).stdout
    stats, cur = {}, None
    for line in out.splitlines():
        m = re.match(r"qdisc \S+ [0-9a-f]+: parent 1:(\d+)", line)
        if m:
            cur = int(m.group(1))
            continue
        m = re.search(r"Sent (\d+) bytes \d+ pkt \(dropped (\d+)", line)
        if m and cur:
            stats[cur] = [int(m.group(1)), int(m.group(2)), 0]
        m = re.search(r"backlog \d+b (\d+)p", line)
        if m and cur in stats:
            stats[cur][2] = int(m.group(1))
            cur = None
    return stats


def bits(rate):
    """'19mbit' -> 19000000"""
    m = re.match(r"([\d.]+)\s*([kmg]?)", rate.lower())
    return float(m.group(1)) * {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}[m.group(2)]


class Metrics:
    """Read at every Prometheus scrape, straight from tc."""
    def collect(self):
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
        stats = read_stats()
        sent = CounterMetricFamily("qosi_pol_sent_bytes", "Bytes sent per class", labels=["traffic_class"])
        drop = CounterMetricFamily("qosi_pol_dropped_packets", "Packets dropped per class",
                                   labels=["traffic_class"])
        back = GaugeMetricFamily("qosi_pol_backlog_packets", "Packets waiting in the class queue",
                                 labels=["traffic_class"])
        rate = GaugeMetricFamily("qosi_pol_rate_bps", "Guaranteed rate per class", labels=["traffic_class"])
        ceil = GaugeMetricFamily("qosi_pol_ceil_bps", "Maximum rate per class", labels=["traffic_class"])
        for name, cid, _tos, r, c, *_ in CLASSES:
            rate.add_metric([name], bits(r))
            ceil.add_metric([name], bits(c))
            if cid in stats:
                sent.add_metric([name], stats[cid][0])
                drop.add_metric([name], stats[cid][1])
                back.add_metric([name], stats[cid][2])
        yield from (sent, drop, back, rate, ceil)


def serve_metrics(collector):
    """Prometheus endpoint on :9100, read by Prometheus over CORE's control network."""
    from prometheus_client import REGISTRY, start_http_server
    REGISTRY.register(collector)
    for _ in range(20):                       # the previous daemon may still hold the port
        try:
            start_http_server(METRICS_PORT)
            return
        except OSError:
            time.sleep(0.5)


def daemon():
    serve_metrics(Metrics())
    emit("pol: logger started")
    prev = read_stats()
    while True:
        time.sleep(1)
        now = read_stats()
        for name, cid, *_ in CLASSES:
            if cid not in now or cid not in prev:
                continue
            db = now[cid][0] - prev[cid][0]
            dd = now[cid][1] - prev[cid][1]
            if db > 0 or dd > 0:
                emit(f"{time.strftime('%H:%M:%S')} {name:5} {db * 8 / 1e6:6.2f} Mbit/s  "
                     f"drops +{dd} (total {now[cid][1]})  backlog {now[cid][2]}p")
        prev = now


def logs():
    subprocess.run(["tail", "-n", "20", "-F", LOGFILE])


def status():
    subprocess.run(["tc", "-s", "class", "show", "dev", IF])
    subprocess.run(["tc", "filter", "show", "dev", IF])


if __name__ == "__main__":
    cmds = {"on": on, "off": off, "status": status, "logs": logs, "daemon": daemon}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("Usage: vnf.py on|off|status|logs")
    cmds[sys.argv[1]]()
