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
from injection import RECIPES, describe, is_injected

ROOT = Path(__file__).resolve().parent
WEBUI = ROOT / "webui" / "index.html"
HOST = "127.0.0.1"
PORT = 8765


def recipe_catalog() -> list[dict]:
    rows = []
    for protocol, spec in RECIPES.items():
        rows.append(
            {
                "protocol": protocol,
                "label": spec["label"],
                "self_test": is_injected(protocol),
                "writes_to_controller": False,
                "filename_tag": "INJECTED_",
                **describe(protocol),
            }
        )
    return rows


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
        if path == "/api/recipes":
            self._send(200, json.dumps({"ok": True, "recipes": recipe_catalog()}).encode(), "application/json")
            return
        if path == "/health":
            payload = {
                "ok": True,
                "service": "rcm-tool-webui",
                "write_to_controller": False,
                "ble_radio_in_process": False,
                "injection": "software-self-test-after-read",
                "recipes": [row["protocol"] for row in recipe_catalog()],
            }
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        self._send(404, b"{\"error\":\"Not found.\"}", "application/json")

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/launch-bench":
            body = self._json_body()
            protocol = str(body.get("protocol", "") or "")
            script = ROOT / "rcm_tool.py"
            subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
            hint = "Select Neutral hold or Guided for an honest pair."
            if protocol.startswith("injected-") or protocol.startswith("after-"):
                hint = (
                    f"In the capture window set Protocol to the matching injected recipe "
                    f"({protocol}). Thumbs off. File will be tagged INJECTED_. Not a pad screen."
                )
            payload = {"ok": True, "protocol": protocol, "hint": hint, "writes_to_controller": False}
            self._send(200, json.dumps(payload).encode(), "application/json")
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
