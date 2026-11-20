#!/bin/bash
# Isolated test of the policer VNF. Run with: sudo bash vnfs/pol/test.sh
S=$(ls -td /tmp/pycore.* | head -1)
r() { vcmd -c $S/$1 -- "${@:2}"; }

run_load() {   # run_load <label>
    r srv-video iperf3 -s -D -p 5301
    r srv-bulk  iperf3 -s -D -p 5301
    r c-voip ping -s 172 -i 0.02 -w 14 -D 10.0.20.10 > /tmp/pol_ping.txt 2>&1 &   # G.711-like VoIP
    local p1=$!
    r c-video iperf3 -c 10.0.20.20 -p 5301 -u -b 8M -t 12 > /tmp/pol_video.txt 2>&1 &
    local p2=$!
    sleep 2
    r c-bulk iperf3 -c 10.0.20.30 -p 5301 -t 10 -P 4 > /tmp/pol_bulk.txt 2>&1 &
    local p3=$!
    wait $p1 $p2 $p3
    r srv-video pkill iperf3; r srv-bulk pkill iperf3
    echo "== $1"
    echo -n "   voip: "; tail -n 1 /tmp/pol_ping.txt
    echo -n "   bulk: "; grep -E "SUM.*receiver" /tmp/pol_bulk.txt | tail -n 1
    echo -n "   video: "; grep -E "receiver" /tmp/pol_video.txt | tail -n 1
}

docker exec vnf-pol python3 /app/vnf.py off > /dev/null
run_load "pol OFF (queue forms on r2's link)"

docker exec vnf-pol python3 /app/vnf.py on > /dev/null
sleep 2
run_load "pol ON  (queue forms inside the VNF)"

echo; docker exec vnf-pol python3 /app/vnf.py status
