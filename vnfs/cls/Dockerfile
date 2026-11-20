FROM qosi-base
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-scapy python3-dev build-essential conntrack \
        libnetfilter-queue-dev libnfnetlink-dev \
    && pip3 install --no-cache-dir NetfilterQueue scikit-learn \
    && apt-get purge -y build-essential python3-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*
COPY vnf.py entrypoint.sh bashrc.sh /app/
COPY ml/ /app/ml/
RUN sed -i 's/\r$//' /app/entrypoint.sh /app/bashrc.sh \
    && chmod +x /app/entrypoint.sh \
    && cat /app/bashrc.sh >> /root/.bashrc \
    && python3 /app/ml/train.py --quiet    # default model: synthetic flows + ml/data/*.csv
ENTRYPOINT ["/app/entrypoint.sh"]
