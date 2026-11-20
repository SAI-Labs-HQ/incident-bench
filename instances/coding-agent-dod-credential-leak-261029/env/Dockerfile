FROM python:3.12.8-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git curl openssl ca-certificates util-linux procps ripgrep \
    && rm -rf /var/lib/apt/lists/* && useradd -m -s /bin/bash dev
COPY capture.py service.py make_workspace.py check_workspace.py /opt/service/
RUN chmod 700 /opt/service \
    && openssl req -x509 -newkey rsa:2048 -nodes -keyout /opt/service/ca.key -out /opt/service/ca.crt \
       -days 3650 -subj '/CN=Workspace Network CA' \
    && cp /opt/service/ca.crt /usr/local/share/ca-certificates/workspace-network.crt \
    && update-ca-certificates \
    && python3 /opt/service/make_workspace.py /opt/workspaces && chmod 700 /opt/workspaces \
    && mkdir -p /home/dev/work/integration /var/lib/requests && chmod 700 /var/lib/requests \
    && chown -R dev:dev /home/dev
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
WORKDIR /home/dev/work/integration
CMD ["sleep", "infinity"]
