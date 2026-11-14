#!/bin/bash
# Connects the host to vnf-mon (172.31.0.1 <-> 172.31.0.2) so Prometheus can scrape it.
# Run with sudo after pressing play in CORE, and again after every session restart.
set -e
PID=$(docker inspect -f '{{.State.Pid}}' vnf-mon 2>/dev/null || echo 0)
[ "$PID" != "0" ] || { echo "vnf-mon is not running: press play in CORE first."; exit 1; }

ip link del qosi-mon 2>/dev/null || true
ip link add qosi-mon type veth peer name qosi-mon-c
ip link set qosi-mon-c netns "$PID"
nsenter -t "$PID" -n ip addr add 172.31.0.2/30 dev qosi-mon-c
nsenter -t "$PID" -n ip link set qosi-mon-c up
ip addr add 172.31.0.1/30 dev qosi-mon
ip link set qosi-mon up
echo "exporter: $(curl -s -o /dev/null -w '%{http_code}' http://172.31.0.2:9100/metrics) (200 = reachable)"
