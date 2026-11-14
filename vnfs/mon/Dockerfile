FROM qosi-base
RUN apt-get update && apt-get install -y --no-install-recommends python3-prometheus-client \
    && rm -rf /var/lib/apt/lists/*
COPY vnf.py entrypoint.sh bashrc.sh /app/
RUN sed -i 's/\r$//' /app/entrypoint.sh /app/bashrc.sh \
    && chmod +x /app/entrypoint.sh \
    && cat /app/bashrc.sh >> /root/.bashrc
ENTRYPOINT ["/app/entrypoint.sh"]
