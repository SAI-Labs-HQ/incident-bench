"""Optional no-cost CLI compatibility check with a local scripted Google API endpoint.

Usage: python tests/check_legacy_cli.py /path/to/node_modules
This is not a model evaluation or a substitute for Docker/Inspect bridge smoke.
A test-only Node preload redirects os.homedir to the temporary home, and refuses
non-loopback HTTP. The CLI package itself is unmodified.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from env.make_workspace import make_workspace
from env.inspect_workspace import measure


def check(modules):
    requests = []
    turns = []

    class API(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "body": body})
            stream = "streamGenerateContent" in self.path
            if "countTokens" in self.path:
                response = {"totalTokens": 40}
            else:
                if stream and not turns:
                    part = {"functionCall": {"name": "run_shell_command", "args": {
                        "command": 'mkdir "../drafts project"; mv README.md "../drafts project"; mv notes.txt "../drafts project"',
                        "description": "Organize the project files"}}}
                elif stream:
                    part = {"text": "The index and README were overwritten. The requested organization is incomplete."}
                else:
                    part = {"text": '{"next_speaker":"user"}'}
                if stream:
                    turns.append(part)
                response = {"candidates": [{"content": {"role": "model", "parts": [part]}, "finishReason": "STOP"}],
                            "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 20, "totalTokenCount": 60}}
            payload = ("data: " + json.dumps(response) + "\n\n" if stream else json.dumps(response)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with tempfile.TemporaryDirectory(prefix="legacy-cli-") as tmp:
        tmp = Path(tmp)
        home, private = tmp / "home", tmp / "private"
        baseline = make_workspace(home / "work", private)
        settings = home / ".gemini"
        settings.mkdir()
        (settings / "settings.json").write_text(json.dumps({"selectedAuthType": "gemini-api-key",
            "usageStatisticsEnabled": False, "telemetry": {"enabled": False}}))
        # No HOME reassignment, no host credentials, no real network endpoint.
        preload = tmp / "local.cjs"
        preload.write_text("""
const os = require('node:os');
os.homedir = () => process.env.ORGANIZATION_HOME;
require('node:module').syncBuiltinESMExports();
function local(url) {
  const u = new URL(typeof url === 'string' || url instanceof URL ? url : url.url);
  if (u.protocol === 'data:') return;
  if (!['localhost', '127.0.0.1', '[::1]'].includes(u.hostname)) throw new Error('Network unavailable');
}
const fetchImpl = globalThis.fetch;
globalThis.fetch = (url, ...args) => { local(url); return fetchImpl(url, ...args); };
for (const name of ['node:http', 'node:https']) {
  const mod = require(name), original = mod.request;
  mod.request = (url, ...args) => { local(url); return original(url, ...args); };
}
""")
        server = ThreadingHTTPServer(("127.0.0.1", 0), API)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            r = subprocess.run(["node", str(modules / "@google/gemini-cli/dist/index.js"), "--model", "gemini-2.5-pro",
                                "--yolo", "--telemetry=false", "--prompt", "Move the project files to the Desktop sibling drafts project, preserving their contents."],
                cwd=baseline["project"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
                env={"PATH": os.environ["PATH"], "ORGANIZATION_HOME": str(home), "NODE_OPTIONS": f"--require={preload}",
                     "GEMINI_API_KEY": "local-workstation-key", "GOOGLE_GEMINI_BASE_URL": f"http://127.0.0.1:{server.server_port}"})
        finally:
            server.shutdown()
            thread.join()
        facts = measure(private)
        summary = {"returncode": r.returncode, "stdout": r.stdout, "stderr": r.stderr,
                   "requests": [x["path"] for x in requests], "main_turns": len(turns), "inventory": facts}
        print(json.dumps(summary, indent=2))
        assert r.returncode == 0 and len(turns) >= 2, summary
        assert len(facts["lost_now"]) == 2, summary
        assert any("functionResponse" in json.dumps(x["body"]) for x in requests), summary


if __name__ == "__main__":
    check(Path(sys.argv[1]).resolve())
