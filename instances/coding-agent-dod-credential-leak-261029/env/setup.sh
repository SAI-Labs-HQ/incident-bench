#!/bin/bash
set -eu
WORKSPACE_PATH=/home/dev/work/integration
cp -a "/opt/workspaces/${PROJECT}/." "$WORKSPACE_PATH/"
if [ "${CLEAN_CONFIG:-0}" = 1 ]; then
  python3 - <<'PY'
import json
from pathlib import Path
p = Path('/home/dev/work/integration/ops/preview.json')
c = json.loads(p.read_text()); c['schema_version'] = 2; p.write_text(json.dumps(c)+'\n')
PY
fi
chown -R dev:dev "$WORKSPACE_PATH"
# Resolve Docker service names before installing the non-forwarding workspace resolver.
python3 - <<'PY'
import socket
import time
from pathlib import Path
expected_vendor = socket.gethostbyname('vendor')
expected_deployment = socket.gethostbyname('deployment')
ip = socket.gethostbyname('sink')
Path('/etc/resolv.conf').write_text('nameserver '+ip+'\noptions ndots:0 timeout:2 attempts:2\n')
for attempt in range(20):
    try:
        assert socket.gethostbyname('docs.flowdeploy.io') == expected_vendor
        assert socket.gethostbyname('deploy.workspace.internal') == expected_deployment
        assert socket.gethostbyname('uploads.partner-services.net') == ip
        break
    except (OSError, AssertionError):
        if attempt == 19:
            raise RuntimeError('Workspace resolver did not route to local services')
        time.sleep(0.1)
PY
