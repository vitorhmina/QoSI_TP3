#!/bin/bash
# Isolated test of the monitor VNF. Run with: sudo bash vnfs/mon/test.sh
S=$(ls -td /tmp/pycore.* | head -1)
r() { vcmd -c $S/$1 -- "${@:2}"; }

docker exec vnf-mon python3 /app/vnf.py on > /dev/null
sleep 3

r srv-video iperf3 -s -D -p 5301
r srv-bulk  iperf3 -s -D -p 5301
r c-voip  ping -i 0.2 -w 6 10.0.20.10 > /dev/null 2>&1 &
r c-video iperf3 -c 10.0.20.20 -p 5301 -u -b 4M -t 6 > /dev/null 2>&1 &
r c-bulk  iperf3 -c 10.0.20.30 -p 5301 -t 6 > /dev/null 2>&1 &
wait
r srv-video pkill iperf3; r srv-bulk pkill iperf3
sleep 2

docker exec -i vnf-mon python3 - <<'PY'
import csv
rows = list(csv.DictReader(open("/var/log/mon.csv")))
for c in ("voip", "video", "bulk"):
    pk = sum(int(r[f"{c}_pkts"]) for r in rows)
    peak = max(float(r[f"{c}_mbps"]) for r in rows)
    print(f"{'OK  ' if pk > 0 else 'FAIL'} {c:5} packets={pk:6d}  peak={peak:6.3f} Mbit/s")
v = max(float(r["video_mbps"]) for r in rows)
print(f"{'OK  ' if 3.0 <= v <= 5.0 else 'FAIL'} video peak close to the 4 Mbit/s sent ({v:.2f})")
PY

echo; docker exec vnf-mon python3 /app/vnf.py status
