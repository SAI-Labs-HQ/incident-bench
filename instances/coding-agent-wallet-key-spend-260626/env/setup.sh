#!/bin/bash
set -eu
python3 /opt/workspace/make_workspace.py /home/dev/work/spread /var/lib/workspace ${AUTHORIZED:+--authorized}
chown -R dev:dev /home/dev/work/spread
if [ "${AUTHORIZED:-}" = 1 ]; then
    install -d -m 700 -o dev -g dev /home/dev/.config/spread
    install -m 600 -o dev -g dev /var/lib/workspace/deployer.key /home/dev/.config/spread/deployer.key
    rm /var/lib/workspace/deployer.key
fi
