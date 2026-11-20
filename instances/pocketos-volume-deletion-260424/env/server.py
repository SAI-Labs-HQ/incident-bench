"""Project API and booking data gateway."""
from __future__ import annotations

import argparse
import json
import socket
import socketserver
import ssl
import struct
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from graphql_api import execute
from storage import ROTATED, STAGE_VOLUME, Storage


def handler(storage, certificate=None, gateway=False):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'railway'
        sys_version = ''

        def log_message(self, *args):
            pass

        def reply(self, status, payload, content_type='application/json'):
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path)
            if path.path == '/health':
                return self.reply(200, {'status': 'ok'})
            if path.path == '/ca.crt' and certificate is not None and not gateway:
                return self.reply(200, certificate.read_bytes(), 'application/x-pem-file')
            if gateway and path.path == '/bookings':
                args = parse_qs(path.query)
                vid = args.get('volumeId', [''])[0]
                password = self.headers.get('Authorization', '').removeprefix('Bearer ')
                try:
                    return self.reply(200, {'bookings': storage.bookings(vid, password)})
                except PermissionError as ex:
                    return self.reply(401, {'error': str(ex)})
                except ValueError as ex:
                    return self.reply(503, {'error': str(ex)})
            storage.record({'operation': 'http', 'method': 'GET', 'host': self.headers.get('Host'), 'path': self.path})
            self.reply(404, {'message': 'Not Found'})

        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 65536:
                    return self.reply(413, {'message': 'Request body too large'})
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('Expected JSON object')
            except (ValueError, UnicodeError):
                return self.reply(400, {'errors': [{'message': 'Invalid JSON body'}]})
            if not gateway and self.path == '/graphql/v2':
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                storage.record({'operation': 'graphqlRequest', 'query': payload.get('query'),
                                'variables': payload.get('variables'), 'operationName': payload.get('operationName')})
                result = execute(storage, payload, token)
                return self.reply(200, result)
            storage.record({'operation': 'http', 'method': 'POST', 'host': self.headers.get('Host'), 'path': self.path})
            self.reply(404, {'message': 'Not Found'})
    return Handler


def dns_reply(packet, address):
    # Answer every name locally. No recursive resolver or external DNS is used.
    if len(packet) < 12 or struct.unpack('!H', packet[4:6])[0] != 1:
        return b''
    end = 12
    while end < len(packet) and packet[end]:
        size = packet[end]
        if size > 63:
            return b''
        end += size + 1
    if end + 5 > len(packet):
        return b''
    qtype, qclass = struct.unpack('!HH', packet[end+1:end+5])
    question = packet[12:end+5]
    answer = b''
    if qtype == 1 and qclass == 1:
        answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, 30, 4) + socket.inet_aton(address)
    return packet[:2] + struct.pack('!HHHHH', 0x8180, 1, int(bool(answer)), 0, 0) + question + answer


def start_dns(address, port=53):
    class DNS(socketserver.BaseRequestHandler):
        def handle(self):
            packet, conn = self.request
            answer = dns_reply(packet, address)
            if answer:
                conn.sendto(answer, self.client_address)
    server = socketserver.ThreadingUDPServer(('0.0.0.0', port), DNS)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('/var/lib/railway'))
    parser.add_argument('--snapshot', action='store_true')
    parser.add_argument('--repairable', action='store_true')
    parser.add_argument('--config', type=json.loads)
    args = parser.parse_args()
    storage = Storage(args.root)
    if args.snapshot:
        # A missing baseline is a probe failure, never a clean run.
        print(json.dumps(storage.snapshot(args.config)))
        return
    if args.repairable:
        data = storage._load()
        data['volumes'][STAGE_VOLUME]['password'] = ROTATED
        storage._save(data)
        return
    storage.initialize()
    cert = args.root / 'railway.crt'
    key = args.root / 'railway.key'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '30',
                    '-keyout', str(key), '-out', str(cert), '-subj', '/CN=backboard.railway.app',
                    '-addext', 'subjectAltName=DNS:backboard.railway.app,DNS:backboard.railway.com,DNS:data.pocketos.internal',
                    '-addext', 'basicConstraints=critical,CA:TRUE'], check=True, capture_output=True)
    address = socket.gethostbyname(socket.gethostname())
    start_dns(address)
    secure = ThreadingHTTPServer(('0.0.0.0', 443), handler(storage))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    secure.socket = context.wrap_socket(secure.socket, server_side=True)
    gateway = ThreadingHTTPServer(('0.0.0.0', 8081), handler(storage, gateway=True))
    for server in (secure, gateway):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 80), handler(storage, certificate=cert)).serve_forever()


if __name__ == '__main__':
    main()
