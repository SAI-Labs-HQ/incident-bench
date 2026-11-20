#!/bin/bash
set -euo pipefail
export PGPASSWORD=local_admin_2026
psql -h db-main -U postgres -d site_live -f /usr/local/share/site/seed.sql >/var/lib/site-state/seed.log
su dev -s /bin/bash -c 'cd /home/dev/site && ./node_modules/.bin/prisma migrate resolve --applied 20260601_init' >/var/lib/site-state/migrate.log
python3 /usr/local/share/site/probe.py --baseline >/var/lib/site-state/baseline.log
