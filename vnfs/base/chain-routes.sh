#!/bin/bash
# Sets the chain routes of a VNF. Runs in the background at container start, because CORE
# adds the interfaces only after the container is up.
# Every VNF is wired the same way: eth0 faces the clients and eth1 the servers, on /30 links,
# so the previous hop is our eth0 address - 1 and the next hop is our eth1 address + 1.
CLIENTS=${CLIENTS:-10.0.1.0/24}
SERVERS=${SERVERS:-10.0.20.0/24}

addr() { ip -4 -o addr show dev "$1" 2>/dev/null | awk '{print $4}' | cut -d/ -f1; }
shift_ip() { local a b c d; IFS=. read -r a b c d <<< "$1"; echo "$a.$b.$c.$((d + $2))"; }

for _ in $(seq 1 240); do
    a0=$(addr eth0); a1=$(addr eth1)
    [ -n "$a0" ] && [ -n "$a1" ] && break
    sleep 0.5
done
if [ -z "$a0" ] || [ -z "$a1" ]; then
    echo "chain-routes: eth0/eth1 have no IPv4 address, routes not set"
    exit 1
fi

sysctl -qw net.ipv4.ip_forward=1
prev=$(shift_ip "$a0" -1)
next=$(shift_ip "$a1" 1)
ip route replace "$CLIENTS" via "$prev"
ip route replace "$SERVERS" via "$next"
echo "chain-routes: $CLIENTS via $prev, $SERVERS via $next"
