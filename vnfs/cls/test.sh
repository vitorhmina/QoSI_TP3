#!/bin/bash
# Isolated test of the classifier VNF. Run with: sudo bash vnfs/cls/test.sh

# --- checks that traffic can go through vnf-cls before testing anything
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
S=$(ls -td /tmp/pycore.* 2>/dev/null | head -1)
[ -n "$S" ] || { echo "No CORE session: open topology/chain.xml and press play"; exit 1; }
r() { vcmd -c $S/$1 -- "${@:2}"; }
docker exec vnf-cls true 2>/dev/null \
    || { echo "vnf-cls is not running: press play in CORE"; exit 1; }
r r1 ip route get 10.0.20.10 2>/dev/null | grep -q "via 10.0.100.2" \
    || { echo "Traffic is not going through the VNF chain: sudo python3 orchestrator/qosi.py chain"; exit 1; }

# --- helpers
CAP=/tmp/cls_cap.txt
TOS=()
capture() {   # capture <packets> <tcpdump filter> <traffic command...>  -> TOS = tos of each packet
    local n=$1 filt=$2; shift 2
    docker exec vnf-cls timeout 8 tcpdump -ni eth1 -v -c "$n" "$filt" > $CAP 2>&1 &
    local pid=$!
    sleep 1
    "$@" > /dev/null 2>&1 || echo "     (traffic command failed: $*)"
    wait $pid
    TOS=($(grep -o 'tos 0x[0-9a-f]*' $CAP | cut -d' ' -f2))
}
check() {     # check <description> <expected tos> <got tos>
    [ "$3" = "$2" ] && echo "OK   $1 (tos $3)" || echo "FAIL $1 (expected $2, got ${3:-nothing})"
}

# --- rules: the class is decided on the first packet of each flow
docker exec vnf-cls python3 /app/vnf.py on rules > /dev/null
r srv-video iperf3 -s -D -p 5301
r srv-bulk  iperf3 -s -D -p 5301

capture 1 "icmp and src 10.0.1.10" r c-voip ping -c 3 -W 1 10.0.20.10
check "voip  ICMP  -> EF" 0xb8 "${TOS[0]}"
capture 1 "icmp and src 10.0.1.10" r c-voip ping -c 3 -W 1 -Q 0x20 10.0.20.10
check "voip  client-set CS1 is overwritten -> EF" 0xb8 "${TOS[0]}"
capture 1 "udp and dst port 5301" r c-video iperf3 -c 10.0.20.20 -p 5301 -u -b 1M -t 2
check "video UDP   -> AF31" 0x68 "${TOS[0]}"
capture 1 "tcp and dst port 5301 and src 10.0.1.30" r c-bulk iperf3 -c 10.0.20.30 -p 5301 -t 2
check "bulk  TCP   -> BE" 0x0 "${TOS[0]}"

r srv-video pkill iperf3
r srv-bulk  pkill iperf3
echo; docker exec vnf-cls python3 /app/vnf.py status
