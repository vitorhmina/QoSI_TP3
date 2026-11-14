#!/bin/bash
# At container start: wait for CORE to create eth1, then turn the shaper on.
touch /var/log/pol.log
(
  for i in $(seq 1 120); do ip link show eth1 > /dev/null 2>&1 && break; sleep 1; done
  sleep 2
  python3 /app/vnf.py on > /dev/null 2>&1 || echo "pol: failed to start" >> /var/log/pol.log
) &
exec "$@"
