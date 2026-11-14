#!/bin/bash
# Isolated test of the classifier VNF. Run with: sudo bash vnfs/cls/test.sh
S=$(ls -td /tmp/pycore.* | head -1)
r() { vcmd -c $S/$1 -- "${@:2}"; }
CAP=/tmp/cls_cap.txt

expect_tos() {   # expect_tos <tos> <description> <tcpdump filter> <traffic command...>
    local exp=$1 desc=$2 filt=$3; shift 3
    docker exec vnf-cls timeout 8 tcpdump -ni eth1 -v -c 1 "$filt" > $CAP 2>&1 &
    local pid=$!
    sleep 1
    "$@" > /dev/null 2>&1
    wait $pid
    local got
    got=$(grep -o 'tos 0x[0-9a-f]*' $CAP | head -1 | cut -d' ' -f2)
    [ "$got" = "$exp" ] && echo "OK   $desc (tos $got)" \
                        || echo "FAIL $desc (expected $exp, got ${got:-nothing})"
}

docker exec vnf-cls python3 /app/vnf.py on > /dev/null
r srv-video iperf3 -s -D -p 5301
r srv-bulk  iperf3 -s -D -p 5301

expect_tos 0xb8 "voip  ICMP  -> EF"   "icmp and src 10.0.1.10" \
    r c-voip ping -c 3 -W 1 10.0.20.10
expect_tos 0xb8 "voip  client-set CS1 is overwritten -> EF" "icmp and src 10.0.1.10" \
    r c-voip ping -c 3 -W 1 -Q 0x20 10.0.20.10
expect_tos 0x68 "video UDP   -> AF31" "udp and dst port 5301" \
    r c-video iperf3 -c 10.0.20.20 -p 5301 -u -b 1M -t 2
expect_tos 0x0  "bulk  TCP   -> BE"   "tcp and dst port 5301 and src 10.0.1.30" \
    r c-bulk iperf3 -c 10.0.20.30 -p 5301 -t 2

r srv-video pkill iperf3
r srv-bulk  pkill iperf3
echo; docker exec vnf-cls python3 /app/vnf.py status
