"""HTTPS + GraphQL on :8443, the operator touchscreen on :8089, one event loop.

Both listeners and the sampling loop are asyncio tasks in one process, so the
instrument has exactly one copy of its state and there is not a lock anywhere in
this service. The panel reads what the API serves, because it *is* what the API
serves.

**The certificate is generated at container start, not at image build.** A
rebuilt image would otherwise ship a fixed private key to everyone who ever
pulled it. It is self-signed and Ignition trusts it by bypassing validation
rather than by importing a CA into the gateway truststore -- the right call for a
simulator on a private compose network, the wrong one in a plant, and the honest
version of that sentence is more useful on stage than a truststore ceremony
nobody would repeat.

**A missing or expired token is an HTTP 401 before the schema is ever reached.**
`_AuthGate` is the whole reason `particle_counter_poll` can re-authenticate on
401 instead of tracking token expiry. `authenticate` is the one operation that
gets through without one.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os

import uvicorn
from ariadne.asgi import GraphQL
from ariadne.explorer import ExplorerHttp405

from . import auth
from .data import Instrument
from .schema import build_schema

LOG = logging.getLogger("particle_sim.server")

PANEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panel.html")


# ─────────────────────────────────────────────────────────────────────────────
# TLS
# ─────────────────────────────────────────────────────────────────────────────

def make_self_signed_cert(cert_dir: str, hostnames) -> tuple:
    """Write a fresh key + cert into `cert_dir` and return their paths."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    os.makedirs(cert_dir, exist_ok=True)
    key_path = os.path.join(cert_dir, "server.key")
    cert_path = os.path.join(cert_dir, "server.crt")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"ICC26 Particle Counter Sim"),
        x509.NameAttribute(NameOID.COMMON_NAME, unicode_(hostnames[0])),
    ])
    alt_names = [x509.DNSName(unicode_(h)) for h in hostnames]
    try:
        import ipaddress
        alt_names.append(x509.IPAddress(ipaddress.ip_address(u"127.0.0.1")))
    except Exception:
        pass

    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                           critical=True)
            .sign(key, hashes.SHA256()))

    with open(key_path, "wb") as handle:
        handle.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    with open(cert_path, "wb") as handle:
        handle.write(cert.public_bytes(serialization.Encoding.PEM))
    LOG.info("self-signed certificate for %s written to %s",
             ", ".join(hostnames), cert_dir)
    return cert_path, key_path


def unicode_(value):
    return value if isinstance(value, str) else str(value)


# ─────────────────────────────────────────────────────────────────────────────
# The bearer-token gate in front of the schema
# ─────────────────────────────────────────────────────────────────────────────

class _AuthGate:
    """ASGI middleware: 401 unless the request carries a valid bearer token.

    The exception is the `authenticate` mutation, which is how a client gets one
    in the first place. Detecting it means looking at the query text -- there is
    no cheaper way to know which operation a POST body will run without parsing
    GraphQL, and a substring match is what a vendor gateway would do too.

    The body has to be buffered to be read here and replayed to the app below,
    which is the one real cost of doing auth at this layer instead of in a
    resolver. It buys the status code, and the status code is what makes the
    poller's re-auth branch reachable.
    """

    def __init__(self, app, cfg):
        self.app = app
        self.cfg = cfg

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        body = b""
        more = True
        messages = []
        while more:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body += message.get("body", b"") or b""
                more = message.get("more_body", False)
            else:
                more = False

        async def replay():
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        if not self._anonymous_ok(body):
            header = None
            for key, value in scope.get("headers") or []:
                if key.lower() == b"authorization":
                    header = value
                    break
            try:
                claims = auth.verify(self.cfg.jwt_secret, auth.bearer(header))
            except auth.AuthError as exc:
                LOG.info("401 %s: %s", scope.get("path"), exc)
                return await _send_json(send, 401, {"errors": [
                    {"message": str(exc), "extensions": {"code": "UNAUTHENTICATED"}}]})
            scope["particle_counter_claims"] = claims

        await self.app(scope, replay, send)

    @staticmethod
    def _anonymous_ok(body: bytes) -> bool:
        if not body:
            return False
        try:
            document = json.loads(body.decode("utf-8"))
        except Exception:
            return False
        query = str(document.get("query") or "")
        return "authenticate" in query


async def _send_json(send, status: int, document: dict) -> None:
    payload = json.dumps(document).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode()),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": payload})


# ─────────────────────────────────────────────────────────────────────────────
# The touchscreen
# ─────────────────────────────────────────────────────────────────────────────

class Panel:
    """The instrument's own panel: Start/Stop, sample point, room, live readout.

    Every demo surface in this stack is somebody's real product screen. This one
    is the counter's, and it carries exactly four things because those are the
    four an operator has: whether it is running, where it is sampling, what the
    room is doing, and what the last analysis said.

    Plain HTTP. It is a touchscreen on the front of a box, not an API.
    """

    def __init__(self, instrument, page: bytes):
        self.instrument = instrument
        self.page = page

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        path = scope.get("path", "/")
        method = scope.get("method", "GET")

        if method == "GET" and path in ("/", "/index.html"):
            return await self._send(send, 200, self.page, b"text/html; charset=utf-8")
        if method == "GET" and path == "/healthz":
            return await self._send(send, 200, b"ok", b"text/plain; charset=utf-8")
        if method == "GET" and path == "/api/state":
            return await _send_json(send, 200, self.instrument.panel_state())

        if method == "POST":
            body = await _read_body(receive)
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                return await _send_json(send, 400, {"ok": False,
                                                    "message": "body was not JSON"})
            if path == "/api/start":
                self.instrument.start()
            elif path == "/api/stop":
                self.instrument.stop()
            elif path == "/api/sample-point":
                self.instrument.set_sample_point(payload.get("value"))
            elif path == "/api/room":
                self.instrument.set_room(payload.get("value"))
            else:
                return await _send_json(send, 404, {"ok": False,
                                                    "message": "not found"})
            return await _send_json(send, 200, {"ok": True,
                                                "state": self.instrument.panel_state()})

        await self._send(send, 404, b"not found", b"text/plain; charset=utf-8")

    @staticmethod
    async def _send(send, status, body, content_type):
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", content_type),
                                (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})


async def _read_body(receive) -> bytes:
    body = b""
    while True:
        message = await receive()
        if message["type"] != "http.request":
            break
        body += message.get("body", b"") or b""
        if not message.get("more_body", False):
            break
    return body


# ─────────────────────────────────────────────────────────────────────────────
# Wiring
# ─────────────────────────────────────────────────────────────────────────────

def build_graphql_app(cfg, instrument):
    def context_value(request, *_):
        return {
            "config": cfg,
            "instrument": instrument,
            "claims": request.scope.get("particle_counter_claims") or {},
        }

    app = GraphQL(
        build_schema(),
        context_value=context_value,
        # No GraphiQL. Its default explorer pulls a bundle off a CDN, and this
        # stack has to run with networking disabled (docs/00-architecture.md).
        explorer=ExplorerHttp405(),
    )
    return _AuthGate(app, cfg)


async def _sampler(instrument) -> None:
    """One tick a second. The instrument runs whether or not anybody polls it."""
    while True:
        try:
            instrument.tick()
        except Exception:
            LOG.exception("sampling tick failed")
        await asyncio.sleep(1.0)


async def serve(cfg) -> None:
    instrument = Instrument(cfg)

    cert_path, key_path = make_self_signed_cert(
        cfg.cert_dir,
        ["sim-particle-counter", "icc26-sim-particle-counter", "localhost"])

    with open(PANEL_FILE, "rb") as handle:
        page = handle.read()

    api = uvicorn.Server(uvicorn.Config(
        build_graphql_app(cfg, instrument),
        host="0.0.0.0", port=cfg.port,
        ssl_certfile=cert_path, ssl_keyfile=key_path,
        log_level=cfg.log_level.lower(), access_log=False))
    panel = uvicorn.Server(uvicorn.Config(
        Panel(instrument, page),
        host="0.0.0.0", port=cfg.panel_port,
        log_level=cfg.log_level.lower(), access_log=False))

    LOG.info("GraphQL on https://0.0.0.0:%s/graphql", cfg.port)
    LOG.info("operator panel on http://0.0.0.0:%s", cfg.panel_port)
    LOG.info("device %s at %r, %ss per analysis (~%.3f L), channels %s",
             cfg.device_id, instrument.sample_point, cfg.duration,
             cfg.sample_volume_l, cfg.channels)
    if not instrument.running:
        LOG.info("idle -- nothing is sampled until Start is pressed on :%s",
                 cfg.panel_port)

    await asyncio.gather(api.serve(), panel.serve(), _sampler(instrument))
