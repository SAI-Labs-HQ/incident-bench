FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
    tesseract-ocr fonts-liberation procps \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir playwright==1.51.0 pillow==11.1.0 \
    && useradd -m -u 1000 -s /bin/bash dev \
    && useradd -m -u 1001 -s /bin/bash git
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
RUN playwright install --with-deps chromium --only-shell && chmod -R a+rX /opt/browsers
# Official standalone Linux build (the gitea/gitea image binary is musl-linked and does not run on Debian).
RUN curl -fsSL -o /usr/local/bin/gitea https://dl.gitea.com/gitea/1.23.8/gitea-1.23.8-linux-amd64 \
    && echo '827037e7ca940866918abc62a7488736923396c467fcb4acd0dd9829bb6a6f4c  /usr/local/bin/gitea' | sha256sum -c - \
    && chmod 755 /usr/local/bin/gitea
COPY . /opt/review/
RUN chmod 700 /opt/review && mkdir -p /srv/forge-state /home/dev/work \
    && chmod 700 /srv/forge-state \
    && cp /opt/review/capture.py /usr/local/bin/capture-page \
    && chmod 755 /usr/local/bin/capture-page \
    && chown -R dev:dev /home/dev/work
WORKDIR /home/dev/work/app
CMD ["tail", "-f", "/dev/null"]
