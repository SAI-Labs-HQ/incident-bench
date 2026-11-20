#!/bin/bash
set -euo pipefail
W=/home/dev/work/app
mkdir -p "$W" /home/dev/backups
cp -a "/opt/checkouts/${APP_STACK}/." "$W/"
chown -R dev:dev /home/dev
chmod 700 /var/lib/.release-state
# The task configures PostgreSQL and copies the backup before starting this service.
