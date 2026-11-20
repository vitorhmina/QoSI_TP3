#!/bin/bash
# At container start: wait for CORE to create the interfaces, then start monitoring.
touch /var/log/mon.log
chain-routes >> /var/log/mon.log 2>&1 &   # chain routes, once CORE adds eth0/eth1
(
  for i in $(seq 1 120); do
    ip link show eth0 > /dev/null 2>&1 && ip link show eth1 > /dev/null 2>&1 && break
    sleep 1
  done
  sleep 2
  python3 /app/vnf.py on > /dev/null 2>&1 || echo "mon: failed to start" >> /var/log/mon.log
) &
exec "$@"
