FROM node:22.16.0-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash dev
WORKDIR /opt/gemini
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && test "$(node node_modules/@google/gemini-cli/dist/index.js --version)" = "0.1.13"
COPY make_workspace.py inspect_workspace.py legacy_google_proxy.py /opt/workstation/
RUN chmod 700 /opt/workstation /opt/workstation/legacy_google_proxy.py \
    /opt/workstation/make_workspace.py /opt/workstation/inspect_workspace.py \
    && mkdir -p /var/lib/.wsstate /home/dev/work /home/dev/.gemini \
    && chmod 700 /var/lib/.wsstate \
    && printf '%s\n' '{"selectedAuthType":"gemini-api-key","usageStatisticsEnabled":false,"telemetry":{"enabled":false}}' > /home/dev/.gemini/settings.json \
    && chown -R dev:dev /home/dev
WORKDIR /home/dev
CMD ["tail", "-f", "/dev/null"]
