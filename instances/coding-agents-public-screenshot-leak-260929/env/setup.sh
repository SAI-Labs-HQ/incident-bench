#!/bin/bash
set -euo pipefail
python3 /opt/review/seed.py --case "$REVIEW_CASE" ${REVIEW_FORCED:+--forced}
chown -R dev:dev /home/dev/work /home/dev/.netrc
# The setup, source labels, credentials and probes remain unavailable to the unprivileged agent.
test -s /srv/forge-state/observations/history.jsonl
test ! -e /srv/forge-state/observations/errors.jsonl
