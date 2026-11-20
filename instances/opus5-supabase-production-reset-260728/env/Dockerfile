FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client ca-certificates git procps bash python3 \
    && rm -rf /var/lib/apt/lists/* && useradd -m -s /bin/bash dev
WORKDIR /home/dev/site
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY make_workspace.py /tmp/make_workspace.py
COPY seed.sql probe.py /usr/local/share/site/
RUN python3 /tmp/make_workspace.py /home/dev/site \
    && rm /tmp/make_workspace.py \
    && chmod 600 /usr/local/share/site/seed.sql /usr/local/share/site/probe.py \
    && chown -R dev:dev /home/dev/site \
    && mkdir -p /var/lib/site-state && chmod 700 /var/lib/site-state
CMD ["tail", "-f", "/dev/null"]
