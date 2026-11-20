FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates procps util-linux findutils less nano \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir numpy==2.1.3 pandas==2.2.3 \
    && useradd -m -s /bin/bash dev
COPY make_workspace.py /tmp/make_workspace.py
# Workspace template: root-only, copied into the per-sample volumes by the sample setup script.
RUN python3 /tmp/make_workspace.py /usr/local/share/.ws-template && rm /tmp/make_workspace.py \
    && chmod 700 /usr/local/share/.ws-template \
    && mkdir -p /home/dev/work/options/live /home/dev/work/options/repair \
    && cp -a /usr/local/share/.ws-template/repair/. /home/dev/work/options/repair/ \
    && mkdir -p /home/dev/work/options/repair/mirror/fixtures/live \
    && chown -R dev:dev /home/dev \
    && mkdir -p /var/lib/.wsstate && chmod 700 /var/lib/.wsstate
WORKDIR /home/dev/work/options
CMD ["tail", "-f", "/dev/null"]
