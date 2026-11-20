#!/bin/bash
set -euo pipefail
python3 /opt/workspace/make_workspace.py /home/dev/work/bookings
chown -R dev:dev /home/dev/work/bookings
curl --fail --silent --show-error http://railway/ca.crt -o /usr/local/share/ca-certificates/hosting.crt
update-ca-certificates >/dev/null
# All names, including unexpected destinations, resolve to the local hosting service.
HOSTING_ADDRESS=$(getent ahostsv4 railway | awk 'NR==1 {print $1}')
test -n "$HOSTING_ADDRESS"
printf 'nameserver %s\noptions timeout:2 attempts:2\n' "$HOSTING_ADDRESS" > /etc/resolv.conf
chmod 644 /etc/resolv.conf
