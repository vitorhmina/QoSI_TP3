FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 iptables tcpdump iputils-ping python3 python3-pip python3-prometheus-client \
    && rm -rf /var/lib/apt/lists/*
COPY chain-routes.sh /usr/local/bin/chain-routes
RUN sed -i 's/\r$//' /usr/local/bin/chain-routes && chmod +x /usr/local/bin/chain-routes
