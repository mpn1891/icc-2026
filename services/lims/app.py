#!/usr/bin/env python3
"""Pattern 4 -- a LIMS that holds analyzer results until a human releases them.

A sample result produced by the pattern-3 analyzer arrives on
`icc26/site1/qc/analyzers/+/result`. This service inserts it as status='received',
serves an approval screen on :8000, and only on Approve does it POST the sample
to Ignition. Reject publishes nothing, ever.

Four things here are load-bearing, and easy to flatten into "a webhook demo":

  * The callback exists because the answer is not ready when you ask. A person
    has to sign it off. If the LIMS could answer synchronously, the correct
    design would be an HTTP response, not a webhook.

  * A naive webhook loses data permanently. One POST, over a network the sender
    does not control, against a receiver that may be restarting. When the retries
    exhaust there is nothing to replay from. `lims.webhook_delivery` is the fix:
    commit the result and the intent-to-deliver in the same transaction, then
    let a worker drain the outbox. That is most of this pattern's engineering.

  * This service has no MQTT publish rights. Its only output is the HTTP
    callback; Transmission publishes onto the backbone. Widen the subscribe
    grant to `icc26/#` and you have an infinite loop. The ACL is the
    enforcement, the same file that stops the valve leaving upstream.

  * QoS 1 is at-least-once. `UNIQUE (sample_id, analyte)` plus ON CONFLICT DO
    NOTHING is the whole ingest dedupe. The uniqueness is a demo simplification
    -- a real LIMS repeats tests -- and it is why that constraint exists.

House style from services/opcua-novaflex/app.py: `_env` helpers, a Config class,
docstrings that say why. FastAPI in the main thread, paho's network loop on its
own (`loop_start()`), the outbox drainer on a third.
"""

from __future__ import annotations

import html
import json
import logging
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import parse_qs

import paho.mqtt.client as mqtt
import psycopg
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from psycopg.types.json import Jsonb

LOG = logging.getLogger("lims")

MECHANISM = "webhook"
SOURCE_ID = "lims"
SOURCE_TYPE = "lims"

# (analyte, dotted path under envelope["values"], uom)
# A null at that path -- Bad OPC quality, per pattern 3's `_value` -- produces
# no row at all, not a zero. Same absent-vs-zero discipline as the analyzer.
ANALYTES = [
    ("glucose", "chem.gluc", "g/L"),
    ("lactate", "chem.lac", "g/L"),
    ("osmolality", "osmo", "mOsm/kg"),
]

PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")


# ── config ───────────────────────────────────────────────────────────────────────────────


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        logging.warning("%s is not a number, using %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


class Config:
    def __init__(self) -> None:
        self.broker_host = _env("BROKER_HOST", "chariot")
        self.broker_port = _env_int("BROKER_PORT", 1883)
        self.mqtt_username = _env("MQTT_USERNAME", "lims-bridge")
        self.mqtt_password = _env("MQTT_PASSWORD", "lims-bridge")
        # Wildcard is the subscribe grant in mqtt-users.json. QoS 1, so a
        # redelivery must not create a second row -- see ingest().
        self.result_topic = _env("RESULT_TOPIC", "icc26/site1/qc/analyzers/+/result")

        self.pghost = _env("PGHOST", "postgres")
        self.pgport = _env_int("PGPORT", 5432)
        self.pgdatabase = _env("PGDATABASE", "icc26")
        self.pguser = _env("PGUSER", "icc26")
        self.pgpassword = _env("PGPASSWORD", "icc26")

        self.webhook_url = _env(
            "WEBHOOK_URL",
            "https://ignition:8043/system/webdev/icc-2026/lims/sample-result",
        )
        self.webhook_secret = _env("WEBHOOK_SECRET", "icc26-webhook-secret")
        self.webhook_max_attempts = _env_int("WEBHOOK_MAX_ATTEMPTS", 5)
        # Mounted at /certs/icc26-ignition.crt by compose. Loaded into an
        # ssl context that still uses the system store -- we add a trust
        # anchor, we do not replace the bundle, and we do not disable
        # verification.
        self.webhook_ca_file = _env("WEBHOOK_CA_FILE", "/certs/icc26-ignition.crt")

        # 0 = off. POST /trigger still works; the interval is only the
        # unattended fallback, which this pattern is not supposed to need.
        self.generator_interval_s = _env_float("GENERATOR_INTERVAL_S", 0.0)
        self.http_port = _env_int("HTTP_PORT", 8000)
        self.default_analyst = _env("DEFAULT_ANALYST", "mnorris")
        self.default_batch_id = _env("DEFAULT_BATCH_ID", "B-2026-0142")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    if value is None:
        value = _now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_ts(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _dig(document: dict, dotted: str):
    node = document
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ── database ─────────────────────────────────────────────────────────────────────────────


class Store:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(
            host=self.cfg.pghost,
            port=self.cfg.pgport,
            dbname=self.cfg.pgdatabase,
            user=self.cfg.pguser,
            password=self.cfg.pgpassword,
        )

    def reachable(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            LOG.exception("postgres unreachable")
            return False

    def ingest(self, envelope: dict) -> int:
        """Insert one row per present analyte. Redelivery is a no-op.

        ON CONFLICT DO NOTHING against uq_sample_analyte is the whole dedupe,
        and it is the reason that constraint exists. A null analyte value
        contributes no row -- not a zero.
        """
        values = envelope.get("values") or {}
        sample_id = values.get("sample_id")
        if not sample_id:
            LOG.warning("ingest skipped: no sample_id")
            return 0
        batch_id = values.get("batch_id") or None
        try:
            collected_at = _parse_ts(envelope.get("ts") or _now())
        except (TypeError, ValueError):
            LOG.warning("ingest skipped: unparseable ts %r", envelope.get("ts"))
            return 0

        inserted = 0
        with self.connect() as conn:
            with conn.transaction():
                for analyte, path, uom in ANALYTES:
                    raw = _dig(values, path)
                    if raw is None:
                        continue
                    try:
                        number = float(raw)
                    except (TypeError, ValueError):
                        LOG.warning("ingest skipped %s/%s: %r is not a number",
                                    sample_id, analyte, raw)
                        continue
                    result = conn.execute(
                        """
                        INSERT INTO lims.sample_result
                            (sample_id, batch_id, analyte, value, uom, collected_at, status)
                        VALUES (%s, %s, %s, %s, %s, %s, 'received')
                        ON CONFLICT (sample_id, analyte) DO NOTHING
                        """,
                        (sample_id, batch_id, analyte, number, uom, collected_at),
                    )
                    inserted += result.rowcount
        if inserted:
            LOG.info("ingested %s: %s new row(s)", sample_id, inserted)
        else:
            LOG.info("ingest no-op %s (redelivery or no present analytes)", sample_id)
        return inserted

    def pending_samples(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sample_id, batch_id, collected_at,
                       json_agg(json_build_object(
                           'analyte', analyte, 'value', value, 'uom', uom
                       ) ORDER BY analyte) AS results
                FROM lims.sample_result
                WHERE status = 'received'
                GROUP BY sample_id, batch_id, collected_at
                ORDER BY collected_at
                """
            ).fetchall()
        return [
            {
                "sample_id": row[0],
                "batch_id": row[1],
                "collected_at": row[2],
                "results": row[3] if isinstance(row[3], list) else json.loads(row[3] or "[]"),
            }
            for row in rows
        ]

    def outbox_rows(self, limit: int = 40) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sample_id, attempts, state, last_error, next_try_at, updated_at
                FROM lims.webhook_delivery
                ORDER BY id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "sample_id": row[0],
                "attempts": row[1],
                "state": row[2],
                "last_error": row[3],
                "next_try_at": row[4],
                "updated_at": row[5],
            }
            for row in rows
        ]

    def approve(self, sample_id: str, analyst: str) -> dict:
        """Flip the rows AND write the outbox row, in one transaction.

        If the process dies between them, a sample is released with nobody
        obliged to deliver it -- which is the exact failure this pattern exists
        to argue about, so it must not be possible to cause it by accident here.
        """
        analyst = (analyst or "").strip() or self.cfg.default_analyst
        with self.connect() as conn:
            with conn.transaction():
                rows = conn.execute(
                    """
                    UPDATE lims.sample_result
                    SET status = 'verified',
                        verified_at = now(),
                        analyst = %s
                    WHERE sample_id = %s AND status = 'received'
                    RETURNING sample_id, batch_id, analyte, value, uom,
                              collected_at, analyst, verified_at
                    """,
                    (analyst, sample_id),
                ).fetchall()
                if not rows:
                    existing = conn.execute(
                        "SELECT status FROM lims.sample_result WHERE sample_id = %s LIMIT 1",
                        (sample_id,),
                    ).fetchone()
                    if existing is None:
                        return {"ok": False, "error": "unknown sample", "status_code": 404}
                    return {
                        "ok": False,
                        "error": "sample is %s, not received" % existing[0],
                        "status_code": 409,
                    }
                payload = _build_envelope(rows)
                inserted = conn.execute(
                    """
                    INSERT INTO lims.webhook_delivery (sample_id, payload)
                    VALUES (%s, %s)
                    ON CONFLICT (sample_id) DO NOTHING
                    RETURNING id
                    """,
                    (sample_id, Jsonb(payload)),
                ).fetchone()
                if inserted is None:
                    return {
                        "ok": False,
                        "error": "outbox already has this sample",
                        "status_code": 409,
                    }
                payload["seq"] = inserted[0]
                conn.execute(
                    "UPDATE lims.webhook_delivery SET payload = %s WHERE id = %s",
                    (Jsonb(payload), inserted[0]),
                )
        LOG.info("approved %s by %s -- outbox id %s", sample_id, analyst, inserted[0])
        return {"ok": True, "sample_id": sample_id, "delivery_id": inserted[0]}

    def reject(self, sample_id: str, analyst: str) -> dict:
        analyst = (analyst or "").strip() or self.cfg.default_analyst
        with self.connect() as conn:
            with conn.transaction():
                result = conn.execute(
                    """
                    UPDATE lims.sample_result
                    SET status = 'rejected',
                        verified_at = now(),
                        analyst = %s
                    WHERE sample_id = %s AND status = 'received'
                    """,
                    (analyst, sample_id),
                )
                if result.rowcount == 0:
                    existing = conn.execute(
                        "SELECT status FROM lims.sample_result WHERE sample_id = %s LIMIT 1",
                        (sample_id,),
                    ).fetchone()
                    if existing is None:
                        return {"ok": False, "error": "unknown sample", "status_code": 404}
                    return {
                        "ok": False,
                        "error": "sample is %s, not received" % existing[0],
                        "status_code": 409,
                    }
        LOG.info("rejected %s by %s -- nothing published", sample_id, analyst)
        return {"ok": True, "sample_id": sample_id, "published": False}

    def claim_due(self) -> dict | None:
        """Lock one due outbox row and bump its attempt counter.

        SKIP LOCKED so two drainers (there should never be two; this is
        belt-and-braces) cannot POST the same delivery.
        """
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    SELECT id, sample_id, payload, attempts
                    FROM lims.webhook_delivery
                    WHERE state = 'pending' AND next_try_at <= now()
                    ORDER BY next_try_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    return None
                attempts = row[3] + 1
                conn.execute(
                    """
                    UPDATE lims.webhook_delivery
                    SET attempts = %s, updated_at = now()
                    WHERE id = %s
                    """,
                    (attempts, row[0]),
                )
                return {
                    "id": row[0],
                    "sample_id": row[1],
                    "payload": row[2],
                    "attempts": attempts,
                }

    def mark_delivered(self, delivery_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE lims.webhook_delivery
                SET state = 'delivered', last_error = NULL, updated_at = now()
                WHERE id = %s
                """,
                (delivery_id,),
            )
            conn.commit()

    def mark_retry(self, delivery_id: int, attempts: int, error: str) -> None:
        delay = min(60, 2 ** max(attempts - 1, 0))
        abandoned = attempts >= self.cfg.webhook_max_attempts
        state = "abandoned" if abandoned else "pending"
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE lims.webhook_delivery
                SET state = %s,
                    last_error = %s,
                    next_try_at = now() + (%s * interval '1 second'),
                    updated_at = now()
                WHERE id = %s
                """,
                (state, error[:500], delay, delivery_id),
            )
            conn.commit()
        if abandoned:
            LOG.error("abandoned delivery %s after %s attempt(s): %s",
                      delivery_id, attempts, error)
        else:
            LOG.warning("delivery %s attempt %s failed, retry in %ss: %s",
                        delivery_id, attempts, delay, error)

    def synthesise(self) -> dict:
        """Invent one sample without an analyzer. Fallback, not the happy path."""
        stamp = _now()
        sample_id = "S-%s-%s" % (stamp.strftime("%Y%m%d"), stamp.strftime("%H%M%S"))
        envelope = {
            "ts": _iso(stamp),
            "seq": 0,
            "source": {"id": "lims-fallback", "type": "lims"},
            "meta": {
                "mechanism": "opcua-event",
                "ingest_ts": _iso(stamp),
                "correlation_id": sample_id,
            },
            "values": {
                "sample_id": sample_id,
                "batch_id": self.cfg.default_batch_id,
                "chem": {"gluc": 4.21, "lac": 1.08},
                "osmo": 312.0,
            },
        }
        self.ingest(envelope)
        return {"ok": True, "sample_id": sample_id, "batch_id": self.cfg.default_batch_id}


def _build_envelope(rows: list) -> dict:
    """One message per sample, carrying every analyte that was verified.

    `ts` is collected_at, not the approval instant -- the event being described
    is the measurement. The approval instant is meta.ingest_ts, and the gap
    between the two is visible on stage, which is the point of the pattern.
    `seq` is filled in with the outbox id after INSERT.
    """
    first = rows[0]
    collected_at = first[5]
    analyst = first[6]
    sample_id = first[0]
    batch_id = first[1]
    results = [
        {"analyte": row[2], "value": float(row[3]), "uom": row[4]}
        for row in rows
    ]
    return {
        "ts": _iso(collected_at),
        "seq": 0,
        "source": {"id": SOURCE_ID, "type": SOURCE_TYPE},
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso(),
            "correlation_id": sample_id,
        },
        "values": {
            "sample_id": sample_id,
            "batch_id": batch_id,
            "collected_at": _iso(collected_at),
            "analyst": analyst,
            "results": results,
        },
    }


# ── MQTT ─────────────────────────────────────────────────────────────────────────────────


class MqttIngest:
    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.connected = False
        self.client: mqtt.Client | None = None

    def start(self) -> None:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="lims-bridge",
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self.cfg.mqtt_username, self.cfg.mqtt_password)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self.client = client
        client.connect_async(self.cfg.broker_host, self.cfg.broker_port, keepalive=30)
        client.loop_start()
        LOG.info("mqtt connecting to %s:%s as %s",
                 self.cfg.broker_host, self.cfg.broker_port, self.cfg.mqtt_username)

    def stop(self) -> None:
        if self.client is None:
            return
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties) -> None:
        # paho 2.x ReasonCode compares to int but is not itself an int.
        if reason_code != 0:
            self.connected = False
            LOG.error("mqtt connect refused: %s", reason_code)
            return
        self.connected = True
        client.subscribe(self.cfg.result_topic, qos=1)
        LOG.info("mqtt connected; subscribed QoS 1 to %s", self.cfg.result_topic)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected = False
        LOG.warning("mqtt disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        try:
            envelope = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            LOG.warning("dropping unparseable payload on %s: %s", message.topic, exc)
            return
        if not isinstance(envelope, dict):
            LOG.warning("dropping non-object payload on %s", message.topic)
            return
        try:
            self.store.ingest(envelope)
        except Exception:
            LOG.exception("ingest failed for %s", message.topic)


# ── outbox drainer ───────────────────────────────────────────────────────────────────────


class Drainer:
    """POSTs pending outbox rows. Disable stops the drain, not the enqueue.

    That is the failure demo: Approve still writes the outbox, nothing is
    delivered, the backbone stays silent. Re-enable -- or restart this
    container, which comes back with the drainer on -- and the queued rows
    land, minutes late, with attempts > 1.
    """

    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.enabled = threading.Event()
        self.enabled.set()
        self.stopping = threading.Event()
        self.ssl_context = self._ssl_context()
        self.thread: threading.Thread | None = None

    def _ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        ca_file = self.cfg.webhook_ca_file
        if ca_file and os.path.isfile(ca_file):
            context.load_verify_locations(ca_file)
            # Seed restores the existing machine-local ssl.pfx, whose SAN is
            # localhost, not `ignition`. We still verify the signature against
            # the mounted public cert. We do not turn verification off.
            context.check_hostname = False
            LOG.info("trusted gateway certificate %s (hostname check off: SAN is localhost)",
                     ca_file)
        elif self.cfg.webhook_url.startswith("https://"):
            LOG.warning(
                "WEBHOOK_CA_FILE %s is missing; HTTPS posts will fail until it is mounted",
                ca_file,
            )
        return context

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="outbox", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        LOG.info("outbox drainer started -> %s", self.cfg.webhook_url)
        while not self.stopping.is_set():
            if not self.enabled.is_set():
                self.stopping.wait(0.5)
                continue
            try:
                due = self.store.claim_due()
            except Exception:
                LOG.exception("outbox claim failed")
                self.stopping.wait(2.0)
                continue
            if due is None:
                self.stopping.wait(1.0)
                continue
            self._deliver(due)

    def _deliver(self, due: dict) -> None:
        sample_id = due["sample_id"]
        body = json.dumps(due["payload"], separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.cfg.webhook_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Secret": self.cfg.webhook_secret,
                "X-Idempotency-Key": sample_id,
            },
        )
        # Always attach the HTTPS handler. Ignition redirects :8088 to :8043, and
        # urlopen would otherwise follow that redirect with the default store.
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ssl_context)
        )
        try:
            with opener.open(request, timeout=10) as response:
                status = response.status
                response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                exc.read()
            except Exception:
                pass
            if status in (200, 409):
                # 409: the receiver already saw this key. An outbox is
                # at-least-once; the 409 path is what makes it look exactly-once.
                self.store.mark_delivered(due["id"])
                LOG.info("delivered %s -> HTTP %s (attempt %s)",
                         sample_id, status, due["attempts"])
                return
            self.store.mark_retry(due["id"], due["attempts"], "HTTP %s" % status)
            return
        except Exception as exc:
            self.store.mark_retry(due["id"], due["attempts"], str(exc))
            return
        if status in (200, 409):
            self.store.mark_delivered(due["id"])
            LOG.info("delivered %s -> HTTP %s (attempt %s)",
                     sample_id, status, due["attempts"])
        else:
            self.store.mark_retry(due["id"], due["attempts"], "HTTP %s" % status)


class Generator:
    """Optional interval synthesizer. Off by default -- POST /trigger is enough."""

    def __init__(self, cfg: Config, store: Store) -> None:
        self.cfg = cfg
        self.store = store
        self.stopping = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.cfg.generator_interval_s <= 0:
            return
        self.thread = threading.Thread(target=self._run, name="generator", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()

    def _run(self) -> None:
        interval = self.cfg.generator_interval_s
        LOG.info("fallback generator every %ss", interval)
        while not self.stopping.wait(interval):
            try:
                result = self.store.synthesise()
                LOG.info("generated %s", result["sample_id"])
            except Exception:
                LOG.exception("generator failed")


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────


def _read_page() -> str:
    with open(PAGE_PATH, encoding="utf-8") as handle:
        return handle.read()


def _fmt_ts(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return _iso(value)
    return str(value)


def _esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _parse_analyst(request: Request, form: dict, cfg: Config) -> str:
    query = request.query_params.get("analyst")
    if query:
        return query.strip()
    posted = (form.get("analyst") or [""])[0].strip()
    if posted:
        return posted
    return cfg.default_analyst


async def _form_body(request: Request) -> dict:
    """Parse urlencoded, JSON, or empty. Avoids a python-multipart dependency."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    body = await request.body()
    if not body:
        return {}
    if content_type == "application/json":
        try:
            document = json.loads(body.decode("utf-8"))
        except ValueError:
            return {}
        return {key: [str(value)] for key, value in document.items()}
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return parsed


def _wants_html(request: Request) -> bool:
    if request.query_params.get("format") == "json":
        return False
    accept = request.headers.get("accept") or ""
    return "text/html" in accept


def create_app(cfg: Config, store: Store, ingest: MqttIngest, drainer: Drainer,
               generator: Generator) -> FastAPI:
    app = FastAPI(title="ICC26 LIMS", docs_url=None, redoc_url=None)

    @app.on_event("startup")
    def _startup() -> None:
        ingest.start()
        drainer.start()
        generator.start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        generator.stop()
        drainer.stop()
        ingest.stop()

    def render(request: Request, flash: str = "", flash_ok: bool = True) -> HTMLResponse:
        analyst = request.query_params.get("analyst") or cfg.default_analyst
        try:
            pending = store.pending_samples()
            outbox = store.outbox_rows()
        except Exception:
            LOG.exception("page query failed")
            pending, outbox = [], []
            if not flash:
                flash = "Postgres is unreachable."
                flash_ok = False
        page = _read_page()
        page = page.replace("@@TOPIC@@", _esc(cfg.result_topic))
        page = page.replace("@@ANALYST@@", _esc(analyst))
        page = page.replace("@@MQTT_LED@@", "on" if ingest.connected else "")
        page = page.replace("@@DRAINER_LED@@", "on" if drainer.enabled.is_set() else "warn")
        page = page.replace(
            "@@DRAINER_LABEL@@",
            "on" if drainer.enabled.is_set() else "DISABLED",
        )
        page = page.replace("@@PENDING_COUNT@@", str(len(pending)))
        page = page.replace("@@OUTBOX_COUNT@@", str(len(outbox)))
        if flash:
            kind = "ok" if flash_ok else "err"
            page = page.replace(
                "@@FLASH@@",
                '<p class="msg %s">%s</p>' % (kind, _esc(flash)),
            )
        else:
            page = page.replace("@@FLASH@@", "")
        page = page.replace("@@PENDING@@", _pending_html(pending, analyst))
        page = page.replace("@@OUTBOX@@", _outbox_html(outbox))
        return HTMLResponse(page)

    def reply(request: Request, result: dict, flash: str) -> object:
        if _wants_html(request):
            analyst = request.query_params.get("analyst") or cfg.default_analyst
            location = "/?analyst=%s" % analyst
            if result.get("ok"):
                return RedirectResponse(location, status_code=303)
            return render(request, flash=result.get("error") or flash, flash_ok=False)
        status = 200 if result.get("ok") else int(result.get("status_code") or 400)
        return JSONResponse(result, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return render(request)

    @app.post("/samples/{sample_id}/approve")
    async def approve(sample_id: str, request: Request):
        form = await _form_body(request)
        analyst = _parse_analyst(request, form, cfg)
        try:
            result = store.approve(sample_id, analyst)
        except Exception as exc:
            LOG.exception("approve failed")
            result = {"ok": False, "error": str(exc), "status_code": 500}
        return reply(request, result, "approved %s" % sample_id)

    @app.post("/samples/{sample_id}/reject")
    async def reject(sample_id: str, request: Request):
        form = await _form_body(request)
        analyst = _parse_analyst(request, form, cfg)
        try:
            result = store.reject(sample_id, analyst)
        except Exception as exc:
            LOG.exception("reject failed")
            result = {"ok": False, "error": str(exc), "status_code": 500}
        return reply(request, result, "rejected %s" % sample_id)

    @app.post("/webhook/disable")
    def disable(request: Request):
        drainer.enabled.clear()
        LOG.warning("outbox drainer DISABLED -- approvals will queue")
        return reply(request, {"ok": True, "drainer": False}, "drainer disabled")

    @app.post("/webhook/enable")
    def enable(request: Request):
        drainer.enabled.set()
        LOG.info("outbox drainer enabled")
        return reply(request, {"ok": True, "drainer": True}, "drainer enabled")

    @app.post("/trigger")
    def trigger(request: Request):
        try:
            result = store.synthesise()
        except Exception as exc:
            LOG.exception("trigger failed")
            result = {"ok": False, "error": str(exc), "status_code": 500}
        return reply(request, result, "synthesised %s" % result.get("sample_id", ""))

    @app.get("/healthz")
    def healthz():
        db_ok = store.reachable()
        mqtt_ok = ingest.connected
        body = {
            "ok": db_ok and mqtt_ok,
            "postgres": db_ok,
            "mqtt": mqtt_ok,
            "drainer": drainer.enabled.is_set(),
        }
        return JSONResponse(body, status_code=200 if body["ok"] else 503)

    return app


def _pending_html(pending: list[dict], analyst: str) -> str:
    if not pending:
        return '<p class="empty">Nothing waiting. Trigger the analyzer, or synthesise a sample below.</p>'
    rows = []
    for sample in pending:
        analytes = ", ".join(
            "%s %s %s" % (item["analyte"], item["value"], item["uom"])
            for item in (sample["results"] or [])
        )
        rows.append(
            "<tr>"
            '<td class="mono">%s</td>'
            '<td class="mono">%s</td>'
            '<td class="mono">%s</td>'
            "<td>%s</td>"
            '<td><div class="actions">'
            '<form method="post" action="/samples/%s/approve">'
            '<input type="hidden" name="analyst" value="%s">'
            '<button class="ok" type="submit">Approve</button></form>'
            '<form method="post" action="/samples/%s/reject">'
            '<input type="hidden" name="analyst" value="%s">'
            '<button class="danger" type="submit">Reject</button></form>'
            "</div></td>"
            "</tr>" % (
                _esc(sample["sample_id"]),
                _esc(sample["batch_id"]),
                _esc(_fmt_ts(sample["collected_at"])),
                _esc(analytes),
                _esc(sample["sample_id"]),
                _esc(analyst),
                _esc(sample["sample_id"]),
                _esc(analyst),
            )
        )
    return (
        "<table><thead><tr>"
        "<th>Sample</th><th>Batch</th><th>Collected</th><th>Analytes</th><th></th>"
        "</tr></thead><tbody>%s</tbody></table>" % "".join(rows)
    )


def _outbox_html(outbox: list[dict]) -> str:
    if not outbox:
        return '<p class="empty">No deliveries yet.</p>'
    rows = []
    for item in outbox:
        error = item["last_error"] or ""
        rows.append(
            "<tr>"
            '<td class="mono">%s</td>'
            '<td><span class="pill %s">%s</span></td>'
            '<td class="mono">%s</td>'
            "<td>%s</td>"
            '<td class="mono">%s</td>'
            "</tr>" % (
                _esc(item["sample_id"]),
                _esc(item["state"]),
                _esc(item["state"]),
                item["attempts"],
                _esc(error),
                _esc(_fmt_ts(item["updated_at"])),
            )
        )
    return (
        "<table><thead><tr>"
        "<th>Sample</th><th>State</th><th>Attempts</th><th>Last error</th><th>Updated</th>"
        "</tr></thead><tbody>%s</tbody></table>" % "".join(rows)
    )


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )
    cfg = Config()
    store = Store(cfg)
    ingest = MqttIngest(cfg, store)
    drainer = Drainer(cfg, store)
    generator = Generator(cfg, store)
    app = create_app(cfg, store, ingest, drainer, generator)
    LOG.info("LIMS on :%s -- webhook %s", cfg.http_port, cfg.webhook_url)
    uvicorn.run(app, host="0.0.0.0", port=cfg.http_port, log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
