"""Write the local code host configuration."""
import argparse
import getpass
from pathlib import Path


def configure(root, backend=3001, public=3000, user="git"):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.ini").write_text(f'''APP_NAME = Cedar Forge
RUN_MODE = prod
RUN_USER = {user}
WORK_PATH = {root}
[server]
HTTP_ADDR = 127.0.0.1
HTTP_PORT = {backend}
ROOT_URL = http://forge.cedarforge.net:{public}/
LOCAL_ROOT_URL = http://127.0.0.1:{backend}/
DOMAIN = forge.cedarforge.net
DISABLE_SSH = true
SSH_ROOT_PATH = {root}/ssh
SSH_CREATE_AUTHORIZED_KEYS_FILE = false
OFFLINE_MODE = true
[database]
DB_TYPE = sqlite3
PATH = {root}/gitea.db
[repository]
ROOT = {root}/repositories
DEFAULT_BRANCH = main
[attachment]
ENABLED = true
PATH = {root}/attachments
ALLOWED_TYPES = */*
MAX_SIZE = 20
[lfs]
PATH = {root}/lfs
[security]
INSTALL_LOCK = true
PASSWORD_COMPLEXITY = off
[service]
DISABLE_REGISTRATION = true
REQUIRE_SIGNIN_VIEW = false
ENABLE_CAPTCHA = false
[mailer]
ENABLED = false
[webhook]
ALLOWED_HOST_LIST = loopback
[log]
MODE = console
LEVEL = Warn
''')


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("root")
    p.add_argument("--backend", type=int, default=3001)
    p.add_argument("--public", type=int, default=3000)
    p.add_argument("--user", default="git")
    a = p.parse_args()
    configure(a.root, a.backend, a.public, a.user)
