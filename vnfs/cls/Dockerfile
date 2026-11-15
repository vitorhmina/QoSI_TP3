FROM qosi-base
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-scapy python3-dev build-essential \
        libnetfilter-queue-dev libnfnetlink-dev \
    && pip3 install --no-cache-dir NetfilterQueue \
    && apt-get purge -y build-essential python3-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
COPY vnf.py entrypoint.sh bashrc.sh /app/
RUN sed -i 's/\r$//' /app/entrypoint.sh /app/bashrc.sh \
    && chmod +x /app/entrypoint.sh \
    && cat /app/bashrc.sh >> /root/.bashrc
ENTRYPOINT ["/app/entrypoint.sh"]
