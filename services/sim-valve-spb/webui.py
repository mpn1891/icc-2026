#!/usr/bin/env python3
"""The device's embedded configuration webpage.

Every smart field device ships one of these: a small web server on the device itself where
somebody commissioning it types in where its data should go. That is the whole point of this
file, and of its counterpart in services/sim-valve-spb/ -- put the two pages side by side and
the difference between pattern 1 and pattern 2 is a screenshot rather than an argument.

`http.server` from the standard library, one HTML file with its CSS and JS inline, no
external assets of any kind. The demo has to run with networking disabled
(docs/00-architecture.md), so a page that reaches for a CDN font is a page that breaks on
stage.

Duplicated byte-for-byte between the two valve services -- same house convention as valve.py.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("webui")


class ConfigProvider:
    """What the page can ask the device for, and do to it.

    The two variants implement `state()` and `apply()` very differently -- that asymmetry is
    the content -- but the simulator controls (`scan`, `set_air_supply`) are identical,
    because the physical device is identical.
    """

    def state(self) -> dict:
        raise NotImplementedError

    def apply(self, payload: dict):
        """Returns (ok: bool, message: str)."""
        raise NotImplementedError

    def scan(self, badge_id: str) -> dict:
        raise NotImplementedError

    def set_air_supply(self, sagged: bool) -> None:
        raise NotImplementedError


class _Handler(BaseHTTPRequestHandler):
    server_version = "SmartSampleValve/1.0"
    provider: ConfigProvider = None  # set on the server instance below
    page: bytes = b""

    # BaseHTTPRequestHandler logs every request to stderr in its own format. Route it into
    # the same logger as everything else so `docker logs` reads as one stream.
    def log_message(self, fmt, *args):
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    # ---- helpers

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, document: dict) -> None:
        self._send(status, json.dumps(document).encode("utf-8"), "application/json")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # ---- routes

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, self.page, "text/html; charset=utf-8")
        if path == "/healthz":
            return self._send(200, b"ok", "text/plain; charset=utf-8")
        if path == "/api/state":
            return self._send_json(200, self.provider.state())
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            payload = self._read_json()
        except (ValueError, UnicodeDecodeError):
            return self._send_json(400, {"ok": False, "message": "body was not JSON"})

        try:
            if path == "/api/config":
                ok, message = self.provider.apply(payload)
                return self._send_json(200 if ok else 400,
                                       {"ok": ok, "message": message,
                                        "state": self.provider.state()})
            if path == "/api/scan":
                badge_id = str(payload.get("badge_id") or "").strip()
                if not badge_id:
                    return self._send_json(400, {"ok": False, "message": "badge_id required"})
                result = self.provider.scan(badge_id)
                return self._send_json(200, {"ok": True, "scan": result,
                                             "state": self.provider.state()})
            if path == "/api/air-supply":
                # The simulator's one physical control: starve the pneumatic actuator, or
                # give it its air back. Everything the fault path does downstream follows
                # from this one boolean.
                self.provider.set_air_supply(bool(payload.get("sagged")))
                return self._send_json(200, {"ok": True, "state": self.provider.state()})
        except Exception as exc:  # a config page must never take the device down with it
            LOG.exception("request to %s failed", path)
            return self._send_json(500, {"ok": False, "message": str(exc)})

        self._send_json(404, {"ok": False, "message": "not found"})


def serve(port: int, page_path: str, provider: ConfigProvider) -> ThreadingHTTPServer:
    """Start the config server on a daemon thread and return it.

    The page is read once at startup rather than per request: it is a device's firmware UI,
    not a template, and editing it means rebuilding the image anyway.
    """
    with open(page_path, "rb") as handle:
        page = handle.read()

    handler = type("Handler", (_Handler,), {"provider": provider, "page": page})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, name="config-ui", daemon=True).start()
    LOG.info("configuration page on http://0.0.0.0:%s", port)
    return httpd
