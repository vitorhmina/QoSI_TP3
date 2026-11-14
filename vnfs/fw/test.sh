#!/bin/bash
# Isolated test of the firewall VNF. Run with: sudo bash vnfs/fw/test.sh
S=$(ls -td /tmp/pycore.* | head -1)
r() { vcmd -c $S/$1 -- "${@:2}"; }
check() {  # check <expected pass|block> <description> <command...>
    local exp=$1 desc=$2; shift 2
    if "$@" >/dev/null 2>&1; then got=pass; else got=block; fi
    [ "$got" = "$exp" ] && echo "OK   $desc ($got)" || echo "FAIL $desc (expected $exp, got $got)"
}

docker exec vnf-fw python3 /app/vnf.py off
check pass  "off: atk -> srv-voip"      r atk ping -c 2 -W 1 10.0.20.10

docker exec vnf-fw python3 /app/vnf.py on
check pass  "on:  c-voip -> srv-voip"   r c-voip ping -c 2 -W 1 10.0.20.10
check block "on:  atk -> srv-voip"      r atk ping -c 2 -W 1 10.0.20.10
check block "on:  c-bulk -> srv-voip (not whitelisted)" r c-bulk ping -c 2 -W 1 10.0.20.10

r srv-bulk iperf3 -s -D -p 5301
check pass  "on:  c-bulk TCP 5301 -> srv-bulk" r c-bulk iperf3 -c 10.0.20.30 -p 5301 -t 2
r srv-bulk pkill iperf3

echo; docker exec vnf-fw python3 /app/vnf.py status