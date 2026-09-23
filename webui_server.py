"""Local portal-styled operator shell. Read-only."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEBUI = ROOT / "webui" / "index.html"
HOST = "127.0.0.1"
PORT = 8765


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, WEBUI.read_bytes(), "text/html; charset=utf-8")
            return
        if self.path == "/health":
            payload = {"ok": True, "service": "rcm-tool-webui", "write_to_controller": False}
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        self._send(404, b"{\"error\":\"Not found.\"}", "application/json")

    def do_POST(self) -> None:
        if self.path == "/api/launch-bench":
            script = ROOT / "rcm_tool.py"
            subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
            self._send(200, b"{\"ok\":true}", "application/json")
            return
        self._send(404, b"{\"error\":\"Not found.\"}", "application/json")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    print(f"RCMTool web shell {url}")
    server.serve_forever()


if __name__ == "__main__":
    main()
