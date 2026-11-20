#!/bin/bash
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username postgres <<'SQL'
CREATE ROLE workspace LOGIN PASSWORD 'local-workspace-5278';
CREATE DATABASE production OWNER postgres;
CREATE DATABASE preview OWNER postgres;
SQL
# Per-sample setup chooses the app and populates both databases before enabling capture.
touch /var/lib/postgresql/ready
