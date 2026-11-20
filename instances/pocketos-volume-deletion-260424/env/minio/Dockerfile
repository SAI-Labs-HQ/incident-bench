FROM golang:1.24-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl && rm -rf /var/lib/apt/lists/*
WORKDIR /src
# Upstream release commits; no local API emulation.
RUN curl -fsSL https://codeload.github.com/minio/minio/tar.gz/0d7408fc9969caf07de6a8c3a84f9fbb10a6739e | tar xz --strip-components=1
RUN CGO_ENABLED=0 go build -p 4 -trimpath -ldflags '-s -w -X github.com/minio/minio/cmd.Version=2025-04-22T22:12:26Z -X github.com/minio/minio/cmd.ReleaseTag=RELEASE.2025-04-22T22-12-26Z -X github.com/minio/minio/cmd.CommitID=0d7408fc9969caf07de6a8c3a84f9fbb10a6739e' -o /out/minio .
WORKDIR /client
RUN curl -fsSL https://codeload.github.com/minio/mc/tar.gz/b00526b153a31b36767991a4f5ce2cced435ee8e | tar xz --strip-components=1
RUN CGO_ENABLED=0 go build -p 4 -trimpath -ldflags '-s -w -X github.com/minio/mc/cmd.CopyrightYear=2025 -X github.com/minio/mc/cmd.ShortCommitID=b00526b153a3 -X github.com/minio/mc/cmd.Version=2025-04-16T18:13:26Z -X github.com/minio/mc/cmd.ReleaseTag=RELEASE.2025-04-16T18-13-26Z -X github.com/minio/mc/cmd.CommitID=b00526b153a31b36767991a4f5ce2cced435ee8e' -o /out/mc .

FROM python:3.12-slim-bookworm AS base
RUN apt-get update && apt-get install -y --no-install-recommends git curl jq ripgrep ca-certificates procps && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir boto3==1.38.5
COPY --from=build /out/mc /usr/local/bin/mc

FROM base AS service
COPY --from=build /out/minio /usr/local/bin/minio
CMD ["minio", "server", "/data", "--console-address", ":9001"]

FROM base AS observer
COPY observe.py constants.py document_data.py /opt/storage/
WORKDIR /opt/storage
CMD ["python3", "observe.py", "serve"]

FROM base AS workspace
RUN useradd -m -s /bin/bash dev && mkdir -p /opt/bootstrap /home/dev/work/sales-portal && chmod 700 /opt/bootstrap
COPY make_workspace.py constants.py document_data.py app.py /opt/bootstrap/
WORKDIR /home/dev/work/sales-portal
CMD ["tail", "-f", "/dev/null"]
