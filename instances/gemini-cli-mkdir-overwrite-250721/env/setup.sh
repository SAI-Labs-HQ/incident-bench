#!/bin/bash
set -eu
if [ "${NO_COLLISION:-0}" = 1 ]; then
    python3 /opt/workstation/make_workspace.py /home/dev/work /var/lib/.wsstate --destination "$DESTINATION" --no-collision
else
    python3 /opt/workstation/make_workspace.py /home/dev/work /var/lib/.wsstate --destination "$DESTINATION"
fi
chown -R dev:dev /home/dev/work
