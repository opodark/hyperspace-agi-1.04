#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Expose only the Instagram webhook and proxy it to the local control plane."""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os


LISTEN_HOST = os.getenv("INSTAGRAM_PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.getenv("INSTAGRAM_PROXY_PORT", "8791"))
UPSTREAM_HOST = os.getenv("INSTAGRAM_PROXY_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.getenv("INSTAGRAM_PROXY_UPSTREAM_PORT", "8085"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def _proxy(self):
        clean_path = self.path.split("?", 1)[0]
        if (clean_path != "/instagram/webhook"
                and not clean_path.startswith("/instagram/media/")):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        connection = HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
        try:
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding", "content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            connection.close()

    def log_message(self, fmt, *args):
        print(f"[instagram-webhook] {self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler).serve_forever()
