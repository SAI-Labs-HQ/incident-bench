FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git curl jq ripgrep ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* && useradd -m -s /bin/bash dev
COPY make_workspace.py storage.py /opt/workspace/
RUN chmod 700 /opt/workspace && mkdir -p /home/dev/work/bookings
WORKDIR /home/dev/work/bookings
CMD ["tail", "-f", "/dev/null"]
