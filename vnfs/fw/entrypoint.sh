#!/bin/bash
# Runs at container start: firewall on by default, then hand over to CORE's idle command.
touch /var/log/fw.log
chain-routes >> /var/log/fw.log 2>&1 &   # chain routes, once CORE adds eth0/eth1
python3 /app/vnf.py on > /dev/null 2>&1 || echo "fw: failed to start" >> /var/log/fw.log
exec "$@"
