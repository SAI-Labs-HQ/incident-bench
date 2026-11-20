"""Translate Gemini CLI 0.1.x schema enums for the current Inspect bridge.

The July 2025 CLI sends Google-style uppercase schema types. Inspect's Google
bridge validates JSON Schema lowercase types. This loopback-only adapter changes
those enum values in function declarations and forwards every request to the
active in-container bridge. It does not alter prompts, tools, or tool results.
"""
from __future__ import annotations

import http.client
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


TYPES = {"STRING", "INTEGER", "NUMBER", "BOOLEAN", "ARRAY", "OBJECT", "NULL"}


def normalize_schema(value):
    if isinstance(value, list):
        return [normalize_schema(item) for item in value]
    if isinstance(value, dict):
        return {
            key: (item.lower() if key == "type" and isinstance(item, str) and item in TYPES
                  else normalize_schema(item))
            for key, item in value.items()
        }
    return value


def normalize_request(body: bytes) -> bytes:
    request = json.loads(body)
    for tool in request.get("tools", []):
        for declaration in tool.get("functionDeclarations", []):
            for key in ("parameters", "parametersJsonSchema"):
                if key in declaration:
                    declaration[key] = normalize_schema(declaration[key])
    return json.dumps(request, separators=(",", ":")).encode()


class Handler(BaseHTTPRequestHandler):
    bridge_port = 3001

    def do_POST(self):
        if not self.path.startswith("/v1beta/models/"):
            self.send_error(404)
            return
        try:
            body = normalize_request(self.rfile.read(int(self.headers["Content-Length"])))
            conn = http.client.HTTPConnection("127.0.0.1", self.bridge_port, timeout=590)
            conn.request("POST", self.path, body, {
                "Content-Type": "application/json", "Accept": self.headers.get("Accept", "application/json"),
                "Accept-Encoding": "identity", "x-goog-api-key": "local-workstation-key",
            })
            response = conn.getresponse()
            data = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            conn.close()
        except Exception as error:
            payload = json.dumps({"error": {"message": str(error), "status": "INTERNAL"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    Handler.bridge_port = int(sys.argv[1])
    ThreadingHTTPServer(("127.0.0.1", 3002), Handler).serve_forever()
