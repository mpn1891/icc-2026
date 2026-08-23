#!/usr/bin/env python3
"""The instrument's embedded configuration webpage.

Same idea and the same house style as services/sim-valve-mqtt/webui.py -- `http.server` from
the standard library, one HTML file with its CSS and JS inline, no external assets of any
kind, because the demo has to run with networking disabled (docs/00-architecture.md).

It is NOT byte-identical to the valves' copy, and should not be made so. The two valve
services share that file byte-for-byte on purpose: they are the same physical device in two
firmwares, so any difference between their pages is evidence about the protocol. This is a
different device with a different integration surface -- three routes instead of four, and
they are `config`, `trigger` and `inject-failure` rather than `scan` and `interlock`.

The comparison worth making on stage is between the *pages*, not the code. Pattern 1's page
has a topic, a QoS and a retained flag. This one has a URL, a shared secret and an on/off
switch. That is the entire contract a webhook instrument offers you.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("webui")


class ConfigProvider:
    """What the page can ask the instrument for, and do to it."""

    def state(self) -> dict:
        raise NotImplementedError

    def apply(self, payload: dict):
        """Returns (ok: bool, message: str)."""
        raise NotImplementedError

    def trigger_run(self, kind: str) -> dict:
        """kind is 'sample' or 'qc'."""
        raise NotImplementedError

    def set_inject_failure(self, on: bool) -> None:
        raise NotImplementedError


class _Handler(BaseHTTPRequestHandler):
    server_version = "BioProfileFLEX2/1.0"
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
            if path == "/api/trigger":
                result = self.provider.trigger_run(str(payload.get("kind") or "sample"))
                return self._send_json(200, {"ok": True, "trigger": result,
                                             "state": self.provider.state()})
            if path == "/api/inject-failure":
                self.provider.set_inject_failure(bool(payload.get("on")))
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
