FROM node:22.16.0-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv git postgresql-client curl procps ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/* && useradd -m -s /bin/bash dev \
    && python3 -m venv /opt/runtime
ENV PATH="/opt/runtime/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1
COPY make_workspace.py contacts-package-lock.json requirements.lock.txt /tmp/
RUN python3 /tmp/make_workspace.py /opt/checkouts && rm /tmp/make_workspace.py \
    && cp /tmp/contacts-package-lock.json /opt/checkouts/contacts/package-lock.json \
    && cd /opt/checkouts/contacts && npm ci --no-audit --no-fund \
    && /opt/runtime/bin/pip install --no-cache-dir -r /tmp/requirements.lock.txt \
    && chmod 700 /opt/checkouts
COPY workspace_probe.py /opt/workspace_probe.py
RUN chmod 700 /opt/workspace_probe.py && mkdir -p /var/lib/.release-state && chmod 700 /var/lib/.release-state
WORKDIR /home/dev/work/app
CMD ["tail", "-f", "/dev/null"]
