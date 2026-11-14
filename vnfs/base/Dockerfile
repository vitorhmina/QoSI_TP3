FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 iptables tcpdump iputils-ping python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*
