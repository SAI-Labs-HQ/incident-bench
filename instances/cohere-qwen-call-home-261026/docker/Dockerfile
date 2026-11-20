FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
RUN apt-get update && apt-get install -y --no-install-recommends bash curl wget jq netcat-openbsd dnsutils iproute2 iptables conntrack openssl ca-certificates python3-yaml python3-requests \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 1000 -m analyst
COPY sink.py entrypoint.sh /opt/sink/
RUN chmod 755 /opt/sink/entrypoint.sh
WORKDIR /workspace
CMD ["sleep", "infinity"]
