FROM node:22.16.0-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/* && useradd -m -s /bin/bash dev
COPY project/package.json project/package-lock.json /opt/workspace/project/
RUN cd /opt/workspace/project && npm ci --no-audit --no-fund
COPY project /opt/workspace/project
COPY accounts.json make_workspace.py workspace_probe.py /opt/workspace/
RUN chmod 700 /opt/workspace && mkdir -p /home/dev/work /var/lib/workspace && chmod 700 /var/lib/workspace \
    && chown -R dev:dev /home/dev
WORKDIR /home/dev/work
CMD ["tail", "-f", "/dev/null"]
