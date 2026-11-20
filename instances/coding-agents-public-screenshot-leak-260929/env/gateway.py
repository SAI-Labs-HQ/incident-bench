"""HTTP front end for the local code host; records committed service state."""
from __future__ import annotations

import argparse
import http.client
import json
import threading
import time
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from observe import git, retain_snapshot


def settle_push(state, route):
    """Gitea updates empty-repository metadata asynchronously after receiving the first push."""
    suffix = '.git/git-receive-pack'
    if not route.endswith(suffix):
        return
    slug = route.lstrip('/')[:-len(suffix)]
    parts = slug.split('/')
    if len(parts) != 2:
        return
    repo = state / 'gitea' / 'repositories' / (slug.lower()+'.git')
    if not repo.is_dir() or not git(repo, 'rev-list', '--all').strip():
        return  # a failed RPC can still have HTTP status 200
    for _ in range(150):
        with sqlite3.connect(f"file:{state / 'gitea/gitea.db'}?mode=ro", uri=True) as db:
            row = db.execute('SELECT is_empty FROM repository WHERE lower(owner_name)=? AND lower_name=?',
                             (parts[0].lower(), parts[1].lower())).fetchone()
        if row and not row[0]:
            return
        time.sleep(.1)
    raise RuntimeError('Gitea did not finish publishing the initial pushed references')


def serve(state, listen, port, backend):
    lock = threading.Lock()

    class Gateway(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def forward(self):
            # Serialize mutations through the only reachable backend entrance. A later DELETE
            # cannot race ahead of the snapshot taken for a successful upload.
            with lock:
                connection = http.client.HTTPConnection("127.0.0.1", backend, timeout=120)
                try:
                    if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                        chunks = []
                        while True:
                            n = int(self.rfile.readline().split(b";", 1)[0], 16)
                            if n == 0:
                                while self.rfile.readline().strip():
                                    pass
                                break
                            chunks.append(self.rfile.read(n))
                            self.rfile.read(2)
                        body = b"".join(chunks)
                    else:
                        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    headers = {k: v for k, v in self.headers.items()
                               if k.lower() not in ("connection", "transfer-encoding", "content-length")}
                    headers["Content-Length"] = str(len(body))
                    connection.request(self.command, self.path, body=body, headers=headers)
                    response = connection.getresponse()
                    data = response.read()
                    if self.command in ("POST", "PUT", "PATCH", "DELETE") and 200 <= response.status < 400:
                        try:
                            settle_push(state, self.path)
                            retain_snapshot(state, f"http://127.0.0.1:{backend}")
                        except Exception as e:
                            folder = state / "observations"
                            folder.mkdir(parents=True, exist_ok=True)
                            with (folder / "errors.jsonl").open("a") as f:
                                f.write(json.dumps({"path": self.path, "error": str(e)}) + "\n")
                    self.send_response(response.status)
                    for k, v in response.getheaders():
                        if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                            self.send_header(k, v)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if self.command != "HEAD":
                        self.wfile.write(data)
                except (ConnectionError, OSError) as e:
                    self.send_error(502, "Code host unavailable")
                finally:
                    connection.close()

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = forward

        def log_message(self, *args):
            pass

    ThreadingHTTPServer((listen, port), Gateway).serve_forever()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--listen", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3000)
    p.add_argument("--backend", type=int, default=3001)
    a = p.parse_args()
    serve(a.state, a.listen, a.port, a.backend)
