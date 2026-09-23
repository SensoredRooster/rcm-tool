"""Local portal-styled operator shell. Read-only."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ble_observe import save as save_ble

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

    def _json_body(self) -> dict:
        length = int(self.headers.get("content-length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, WEBUI.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/health":
            payload = {
                "ok": True,
                "service": "rcm-tool-webui",
                "write_to_controller": False,
                "ble_radio_in_process": False,
            }
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        self._send(404, b"{\"error\":\"Not found.\"}", "application/json")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/launch-bench":
            script = ROOT / "rcm_tool.py"
            subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
            self._send(200, b"{\"ok\":true}", "application/json")
            return
        if path == "/api/ble-observe":
            stored = save_ble(self._json_body())
            body = json.dumps({"ok": True, "path": str(stored)}).encode()
            self._send(200, body, "application/json")
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
