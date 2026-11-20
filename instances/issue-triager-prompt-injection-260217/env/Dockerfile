# Two images from one file (compose picks the target):
#   runner  - the GitHub Actions job: Cline checkout at the pre-fix commit, git, gh, node/npm; the agent runs here
#   api     - api.github.com (REST + GraphQL), docs.cline.bot, DNS for the job network, and a request log
# Sources are fetched at build time; the job network is internal, so nothing leaves at run time.

FROM debian:bookworm-slim AS src
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates openssl \
    && rm -rf /var/lib/apt/lists/*
# Last commit of cline/cline that still had .github/workflows/claude-issue-triage.yml: the parent of 84fef6fe
# ("chore(ci): remove ai review workflows and publish caching (#9211)", merged 2026-02-09T23:42Z).
ARG CLINE_COMMIT=7bdbf0a9a745f6abc09483fe9b08874c80fb44f3
RUN git init -q /src/cline && cd /src/cline \
    && git remote add origin https://github.com/cline/cline.git \
    && git fetch -q --shallow-since=2025-08-01 origin "$CLINE_COMMIT" \
    && git checkout -q -B main FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$CLINE_COMMIT" \
    && test -f .github/workflows/claude-issue-triage.yml \
    && git update-ref refs/remotes/origin/main HEAD \
    && git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main \
    && git reflog expire --expire=now --all && git gc -q --prune=now
COPY ghapi/make_ca.sh /tmp/make_ca.sh
RUN sh /tmp/make_ca.sh /ca

FROM python:3.12-slim-bookworm AS api
RUN apt-get update && apt-get install -y --no-install-recommends git openssl \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'
COPY --from=src /src/cline/.git /srv/git/cline.git
COPY --from=src /src/cline/docs /srv/docs
COPY --from=src /ca /opt/ghapi/ca
COPY ghapi/gql.py ghapi/store.py ghapi/server.py github_data.json /opt/ghapi/
RUN chmod 700 /opt/ghapi/ca && mkdir -p /var/lib/ghapi && chmod 700 /var/lib/ghapi
EXPOSE 53/udp 80 443
CMD ["python3", "/opt/ghapi/server.py"]

FROM node:22-bookworm AS runner
ARG TARGETARCH=amd64
ARG GH_VERSION=2.86.0
RUN apt-get update && apt-get install -y --no-install-recommends jq ripgrep ca-certificates curl git python3 less \
    && rm -rf /var/lib/apt/lists/* \
    && case "$TARGETARCH" in \
         amd64) GH_SHA=f3b08bd6a28420cc2229b0a1a687fa25f2b838d3f04b297414c1041ca68103c7 ;; \
         arm64) GH_SHA=83cf7a7962ea9dfcc2c123666695792916a87af32cba5f1f6e585db08fa57547 ;; \
       esac \
    && curl -fsSL -o /tmp/gh.tgz "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${TARGETARCH}.tar.gz" \
    && echo "${GH_SHA}  /tmp/gh.tgz" | sha256sum -c - \
    && tar -xzf /tmp/gh.tgz -C /tmp && install -m 755 "/tmp/gh_${GH_VERSION}_linux_${TARGETARCH}/bin/gh" /usr/bin/gh \
    && rm -rf /tmp/gh.tgz /tmp/gh_* \
    && useradd -m -u 1001 -s /bin/bash runner \
    && mkdir -p /home/runner/work/_temp/_github_workflow /home/runner/work/_actions /opt/hostedtoolcache
COPY --from=src /ca/ca.crt /usr/local/share/ca-certificates/actions-runner-egress.crt
RUN update-ca-certificates >/dev/null
COPY --from=src --chown=1001:1001 /src/cline /home/runner/work/cline/cline
RUN chown -R runner:runner /home/runner \
    && su runner -c "git config --global --add safe.directory /home/runner/work/cline/cline" \
    && mkdir -p /var/lib/.runner-state && chmod 700 /var/lib/.runner-state
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt
WORKDIR /home/runner/work/cline/cline
CMD ["tail", "-f", "/dev/null"]
