#!/usr/bin/env python3
"""AP Connect's configuration page, and the trigger endpoint that stands in for an operator.

Same house convention as services/sim-valve-mqtt/webui.py: `http.server` from the standard
library, one HTML file with its CSS and JS inline, no external assets of any kind. The demo
has to run with networking disabled (docs/00-architecture.md), so a page that reaches for a
CDN font is a page that breaks on stage.

The route that matters is **POST /measure**. It is not under /api on purpose: it is the
service's contract with the outside world, it is what spec 06's Ignition tag-change script
calls at http://sim-apconnect:8080/measure, and it has to be usable with a bare `curl`
before any Ignition resource exists. The page's *measure now* button posts to the same
endpoint, so there is exactly one code path and the button cannot drift from the API.

It accepts an optional JSON body `{"status": "FAILURE"}` to file a measurement that carries
no reading, which is checkpoint 6. An empty body is a normal measurement.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = logging.getLogger("webui")


class ConfigProvider:
    """What the page can ask the application for, and do to it."""

    def state(self) -> dict:
        raise NotImplementedError

    def apply(self, payload: dict):
        """Returns (ok: bool, message: str)."""
        raise NotImplementedError

    def measure(self, payload: dict) -> dict:
        """File one measurement. Returns the row that was written."""
        raise NotImplementedError


class _Handler(BaseHTTPRequestHandler):
    server_version = "APConnect/4.0-sim"
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
        # ensure_ascii=False keeps the vendor's degree sign a degree sign all the way to the
        # page, and the charset is declared so a browser does not guess. Checkpoint 8.
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)

    # ---- routes

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, self.page, "text/html; charset=utf-8")
        if path == "/healthz":
            # Liveness only. Postgres being away is the application's problem to absorb, not
            # a reason to call this container unhealthy -- the page reports it instead.
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
            # The trigger. `/api/measure` is accepted as well so that a reader who assumed
            # the house /api prefix is not left debugging a 404 on stage.
            if path in ("/measure", "/api/measure"):
                filed = self.provider.measure(payload)
                return self._send_json(200, {"ok": True, "message": "measurement filed",
                                             **filed})
            if path == "/api/config":
                ok, message = self.provider.apply(payload)
                return self._send_json(200 if ok else 400,
                                       {"ok": ok, "message": message,
                                        "state": self.provider.state()})
        except ValueError as exc:
            # A bad status string is the caller's mistake, not a server fault.
            return self._send_json(400, {"ok": False, "message": str(exc)})
        except Exception as exc:  # a config page must never take the application down
            LOG.exception("request to %s failed", path)
            return self._send_json(500, {"ok": False, "message": str(exc)})

        self._send_json(404, {"ok": False, "message": "not found"})


def serve(port: int, page_path: str, provider: ConfigProvider) -> ThreadingHTTPServer:
    """Start the config server on a daemon thread and return it.

    The page is read once at startup rather than per request: it is an application's UI, not
    a template, and editing it means rebuilding the image anyway.
    """
    with open(page_path, "rb") as handle:
        page = handle.read()

    handler = type("Handler", (_Handler,), {"provider": provider, "page": page})
    httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, name="config-ui", daemon=True).start()
    LOG.info("configuration page on http://0.0.0.0:%s  (trigger: POST /measure)", port)
    return httpd
