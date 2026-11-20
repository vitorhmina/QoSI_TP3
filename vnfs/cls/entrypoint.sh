#!/bin/bash
# Runs at container start: classification on by default, then hand over to CORE's idle command.
touch /var/log/cls.log
chain-routes >> /var/log/cls.log 2>&1 &   # chain routes, once CORE adds eth0/eth1
python3 /app/vnf.py on > /dev/null 2>&1 || echo "cls: failed to start" >> /var/log/cls.log
exec "$@"
