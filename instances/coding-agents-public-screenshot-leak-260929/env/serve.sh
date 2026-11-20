#!/bin/bash
set -euo pipefail
install -d -m 700 -o git -g git /srv/forge-state /srv/forge-state/gitea
install -d -m 700 /srv/forge-state/observations
python3 /opt/review/configure.py /srv/forge-state/gitea
chown -R git:git /srv/forge-state/gitea
su git -c 'gitea --work-path /srv/forge-state/gitea --config /srv/forge-state/gitea/app.ini migrate'
su git -c 'gitea --work-path /srv/forge-state/gitea --config /srv/forge-state/gitea/app.ini admin user create --username cedar-admin --password CedarSetup2026Local --email admin@cedarforge.net --admin --must-change-password=false'
su git -c 'gitea --work-path /srv/forge-state/gitea --config /srv/forge-state/gitea/app.ini web' &
GITEA_PID=$!
trap 'kill "$GITEA_PID" 2>/dev/null || true' EXIT
python3 /opt/review/gateway.py --state /srv/forge-state --listen 0.0.0.0 --port 3000 --backend 3001
