#!/usr/bin/env python3
"""Pattern 4 -- a LIMS that opens a sample record at collection and releases it on a signature.

Two subscriptions, and the order they arrive in is the pattern:

  1. `icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete` (pattern 1)
     opens the entry. The sample begins when material leaves the reactor, so that
     is when the record exists -- carrying who badged it, when the valve opened,
     how long it was open, and how the cycle ended.
  2. `icc26/site1/qc/analyzers/+/result` (pattern 3) appends the analytes to that
     entry, minutes later.

The analyst reviews one record holding both halves, and only on Approve does this
service POST it to Ignition.

Six things here are load-bearing, and easy to flatten into "a webhook demo":

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

  * QoS 1 is at-least-once. `UNIQUE (reported_sample_id, analyte)` plus ON
    CONFLICT DO NOTHING is the whole result dedupe. The uniqueness is a demo
    simplification -- a real LIMS repeats tests -- and it is why that constraint
    exists.

  * `sample-complete` is RETAINED and this client connects `clean_session=True`,
    so the broker replays the last one on every single reconnect. The entry
    insert is ON CONFLICT DO NOTHING for that reason: without it, restarting this
    container resurrects the last sample -- already approved, already released --
    back into the review queue. Retained plus clean session is a redelivery
    source people forget they signed up for.

  * The two ids are not the same id. A person reads the valve's sample id off
    one screen and types it into the analyzer on another, so what the instrument
    reports back can be wrong. `reported_sample_id` keeps what the instrument
    said, forever, and `sample_id` records which entry it was attached to. You
    do not correct a record by overwriting what the instrument reported; you
    record the correction. Results matching no entry park as unmatched, visible,
    and are reattached by an analyst -- never absorbed silently.

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

# In-process specs for the review grid. Demo values, chosen so a healthy FLEX2
# sample lands in-spec and the OOS chip is reserved for a real excursion.
TEST_CATALOG = {
    "glucose": {"code": "CHEM-GLUC", "name": "Glucose", "lo": 1.0, "hi": 8.0, "uom": "g/L"},
    "lactate": {"code": "CHEM-LAC", "name": "Lactate", "lo": None, "hi": 3.0, "uom": "g/L"},
    "osmolality": {"code": "OSMO", "name": "Osmolality", "lo": 260.0, "hi": 340.0, "uom": "mOsm/kg"},
}

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
        # Pattern 1, and the only topic outside qc/ this service is granted.
        # Named to one device on purpose: `+/event/sample-complete` would also
        # match a second valve, and BR-202's valve is Sparkplug and not on this
        # namespace at all. One subscription per device is what pattern 1 costs.
        self.valve_event_topic = _env(
            "VALVE_EVENT_TOPIC",
            "icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete",
        )

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

    # ---- ingest: two topics, two entry points

    def create_sample(self, topic: str, document: dict) -> dict:
        """Open the entry from pattern 1's `event/sample-complete`.

        ON CONFLICT DO NOTHING is not defensive tidiness. `sample-complete` is
        published retained and this client connects with a clean session, so the
        broker hands us the last one again on every reconnect -- restart the
        container and the most recent sample arrives a second time. Without the
        conflict clause that redelivery resurrects an already-approved sample
        into the review queue.

        A cycle that did not end `normal` will never be analysed, so its entry is
        opened straight into 'received': there is nothing to wait for, and an
        analyst still has to sign it off.
        """
        values = document.get("values") or {}
        sample_id = values.get("sample_id")
        if not sample_id:
            LOG.warning("valve event skipped: no sample_id on %s", topic)
            return {"ok": False, "error": "no sample_id"}

        cycle_result = values.get("cycle_result") or None
        status = "awaiting-analysis" if cycle_result in (None, "normal") else "received"

        def _ts(key):
            raw = values.get(key)
            if raw is None:
                return None
            try:
                return _parse_ts(raw)
            except (TypeError, ValueError):
                LOG.warning("valve event %s: unparseable %s %r", sample_id, key, raw)
                return None

        with self.connect() as conn:
            with conn.transaction():
                created = conn.execute(
                    """
                    INSERT INTO lims.sample
                        (sample_id, badge_id, badge_holder, sample_start, sample_completion,
                         open_duration_s, cycle_result, cycle_count, source_topic, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sample_id) DO NOTHING
                    RETURNING sample_id
                    """,
                    (
                        sample_id,
                        values.get("badge_id"),
                        values.get("badge_holder"),
                        _ts("sample_start"),
                        _ts("sample_completion"),
                        values.get("open_duration_s"),
                        cycle_result,
                        values.get("cycle_count"),
                        topic,
                        status,
                    ),
                ).fetchone()
                # A result can beat its entry here -- nothing in MQTT orders two
                # topics against each other, and this service can be down for the
                # valve event and up for the analyzer. Adopt anything parked.
                adopted = self._adopt(conn, sample_id, sample_id, None)

        if created is None:
            LOG.info("valve event no-op %s (retained redelivery)", sample_id)
            return {"ok": True, "sample_id": sample_id, "created": False}
        LOG.info("opened %s from %s [%s]", sample_id, topic, cycle_result or "normal")
        return {"ok": True, "sample_id": sample_id, "created": True, "adopted": adopted}

    def ingest(self, envelope: dict) -> int:
        """Append one row per present analyte. Redelivery is a no-op.

        ON CONFLICT DO NOTHING against uq_reported_sample_analyte is the whole
        dedupe, and it is the reason that constraint exists. A null analyte value
        contributes no row -- not a zero.

        The id the instrument reports is stored verbatim and never rewritten. If
        no entry carries that id -- because a person mistyped it into the
        analyzer -- the rows still insert, with `sample_id` null. They are parked,
        not lost, and not silently attached to something plausible.
        """
        values = envelope.get("values") or {}
        reported = values.get("sample_id")
        if not reported:
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
                matched = conn.execute(
                    "SELECT sample_id FROM lims.sample WHERE sample_id = %s",
                    (reported,),
                ).fetchone()
                sample_id = matched[0] if matched else None
                attached_at = _now() if sample_id else None
                for analyte, path, uom in ANALYTES:
                    raw = _dig(values, path)
                    if raw is None:
                        continue
                    try:
                        number = float(raw)
                    except (TypeError, ValueError):
                        LOG.warning("ingest skipped %s/%s: %r is not a number",
                                    reported, analyte, raw)
                        continue
                    result = conn.execute(
                        """
                        INSERT INTO lims.sample_result
                            (reported_sample_id, sample_id, batch_id, analyte, value, uom,
                             collected_at, attached_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (reported_sample_id, analyte) DO NOTHING
                        """,
                        (reported, sample_id, batch_id, analyte, number, uom,
                         collected_at, attached_at),
                    )
                    inserted += result.rowcount
                if inserted and sample_id:
                    self._promote(conn, sample_id)

        if not inserted:
            LOG.info("ingest no-op %s (redelivery or no present analytes)", reported)
        elif sample_id:
            LOG.info("appended %s new row(s) to %s", inserted, sample_id)
        else:
            LOG.warning("%s matches no open sample -- %s row(s) parked as unmatched",
                        reported, inserted)
        return inserted

    def _adopt(self, conn, reported_sample_id: str, sample_id: str, analyst: str | None) -> int:
        """Attach parked results to an entry. `analyst` is null when it matched on arrival."""
        attached = conn.execute(
            """
            UPDATE lims.sample_result
            SET sample_id = %s, attached_at = now(), attached_by = %s
            WHERE reported_sample_id = %s AND sample_id IS NULL
            """,
            (sample_id, analyst, reported_sample_id),
        ).rowcount
        if attached:
            self._promote(conn, sample_id)
        return attached

    def _promote(self, conn, sample_id: str) -> None:
        """An entry with results on it is reviewable, and learns its batch from them.

        The valve does not know the batch -- it opens on a badge, not on a work
        order -- so `batch_id` arrives with the analysis or not at all.
        """
        conn.execute(
            """
            UPDATE lims.sample s
            SET status = CASE WHEN s.status = 'awaiting-analysis' THEN 'received'
                              ELSE s.status END,
                batch_id = coalesce(s.batch_id, (
                    SELECT max(r.batch_id) FROM lims.sample_result r
                    WHERE r.sample_id = s.sample_id
                ))
            WHERE s.sample_id = %s
            """,
            (sample_id,),
        )

    # ---- the review queue

    def pending_samples(self) -> list[dict]:
        """Every entry not yet reviewed, whether or not results have arrived."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.sample_id, s.batch_id, s.status, s.badge_id, s.badge_holder,
                       s.sample_start, s.sample_completion, s.open_duration_s,
                       s.cycle_result, s.created_at,
                       coalesce(min(r.collected_at), s.sample_completion) AS collected_at,
                       coalesce(
                           json_agg(json_build_object(
                               'analyte', r.analyte, 'value', r.value, 'uom', r.uom
                           ) ORDER BY r.analyte) FILTER (WHERE r.analyte IS NOT NULL),
                           '[]'::json
                       ) AS results
                FROM lims.sample s
                LEFT JOIN lims.sample_result r ON r.sample_id = s.sample_id
                WHERE s.status IN ('awaiting-analysis', 'received')
                GROUP BY s.sample_id
                ORDER BY s.sample_completion NULLS LAST, s.created_at
                """
            ).fetchall()
        return [
            {
                "sample_id": row[0],
                "batch_id": row[1],
                "status": row[2],
                "badge_id": row[3],
                "badge_holder": row[4],
                "sample_start": row[5],
                "sample_completion": row[6],
                "open_duration_s": row[7],
                "cycle_result": row[8],
                "created_at": row[9],
                "collected_at": row[10],
                "results": row[11] if isinstance(row[11], list) else json.loads(row[11] or "[]"),
                # 'awaiting-analysis' is the one state a signature cannot be
                # applied to: there is nothing yet to have reviewed.
                "reviewable": row[2] == "received",
            }
            for row in rows
        ]

    def unmatched_results(self) -> list[dict]:
        """Results the instrument reported against an id no entry carries.

        On stage this is a mistyped sample id, which is the point of showing it:
        the valve's entry sits open with no results while the analysis sits here
        with no entry, and both facts are on one screen.
        """
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT reported_sample_id, max(batch_id), min(collected_at), min(created_at),
                       json_agg(json_build_object(
                           'analyte', analyte, 'value', value, 'uom', uom
                       ) ORDER BY analyte) AS results
                FROM lims.sample_result
                WHERE sample_id IS NULL
                GROUP BY reported_sample_id
                ORDER BY min(created_at)
                """
            ).fetchall()
        return [
            {
                "reported_sample_id": row[0],
                "batch_id": row[1],
                "collected_at": row[2],
                "created_at": row[3],
                "results": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
            }
            for row in rows
        ]

    def attach(self, reported_sample_id: str, sample_id: str, analyst: str) -> dict:
        """Attach a parked result to an entry, on an analyst's say-so.

        What the instrument reported is left exactly as it was. The correction is
        a new fact (`sample_id`, `attached_by`, `attached_at`) recorded beside it,
        which is the only version of this a regulated system can defend.
        """
        analyst = (analyst or "").strip() or self.cfg.default_analyst
        with self.connect() as conn:
            with conn.transaction():
                entry = conn.execute(
                    "SELECT status FROM lims.sample WHERE sample_id = %s",
                    (sample_id,),
                ).fetchone()
                if entry is None:
                    return {"ok": False, "error": "unknown sample %s" % sample_id,
                            "status_code": 404}
                if entry[0] not in ("awaiting-analysis", "received"):
                    return {"ok": False,
                            "error": "%s is already %s" % (sample_id, entry[0]),
                            "status_code": 409}
                attached = self._adopt(conn, reported_sample_id, sample_id, analyst)
                if not attached:
                    return {"ok": False,
                            "error": "nothing unmatched under %s" % reported_sample_id,
                            "status_code": 404}
        LOG.info("%s attached %s row(s) reported as %s to %s",
                 analyst, attached, reported_sample_id, sample_id)
        return {"ok": True, "sample_id": sample_id,
                "reported_sample_id": reported_sample_id, "attached": attached}

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

    # ---- review

    # Selected on both review paths, and the column order _build_envelope reads.
    _ENTRY_COLUMNS = """sample_id, batch_id, badge_id, badge_holder, sample_start,
                        sample_completion, open_duration_s, cycle_result,
                        analyst, verified_at"""

    def approve(self, sample_id: str, analyst: str) -> dict:
        """Flip the entry AND write the outbox row, in one transaction.

        If the process dies between them, a sample is released with nobody
        obliged to deliver it -- which is the exact failure this pattern exists
        to argue about, so it must not be possible to cause it by accident here.
        """
        analyst = (analyst or "").strip() or self.cfg.default_analyst
        with self.connect() as conn:
            with conn.transaction():
                row = conn.execute(
                    """
                    UPDATE lims.sample
                    SET status = 'verified', verified_at = now(), analyst = %%s
                    WHERE sample_id = %%s AND status = 'received'
                    RETURNING %s
                    """ % self._ENTRY_COLUMNS,
                    (analyst, sample_id),
                ).fetchone()
                if row is None:
                    return self._not_reviewable(conn, sample_id)
                payload = _build_envelope(row, self._result_rows(conn, sample_id))
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
                row = conn.execute(
                    """
                    UPDATE lims.sample
                    SET status = 'rejected', verified_at = now(), analyst = %%s
                    WHERE sample_id = %%s AND status = 'received'
                    RETURNING %s
                    """ % self._ENTRY_COLUMNS,
                    (analyst, sample_id),
                ).fetchone()
                if row is None:
                    return self._not_reviewable(conn, sample_id)
        LOG.info("rejected %s by %s -- nothing published", sample_id, analyst)
        return {"ok": True, "sample_id": sample_id, "published": False}

    def _result_rows(self, conn, sample_id: str) -> list:
        return conn.execute(
            """
            SELECT analyte, value, uom, collected_at
            FROM lims.sample_result
            WHERE sample_id = %s
            ORDER BY analyte
            """,
            (sample_id,),
        ).fetchall()

    def _not_reviewable(self, conn, sample_id: str) -> dict:
        """Why the UPDATE matched nothing.

        'awaiting-analysis' gets its own words: "sample is awaiting-analysis, not
        received" is a status string read back at somebody, and what an analyst
        needs to know is that the results have not arrived yet.
        """
        existing = conn.execute(
            "SELECT status FROM lims.sample WHERE sample_id = %s",
            (sample_id,),
        ).fetchone()
        if existing is None:
            return {"ok": False, "error": "unknown sample", "status_code": 404}
        if existing[0] == "awaiting-analysis":
            return {
                "ok": False,
                "error": "%s has no analysis yet -- nothing to sign off" % sample_id,
                "status_code": 409,
            }
        return {
            "ok": False,
            "error": "sample is %s, not received" % existing[0],
            "status_code": 409,
        }

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
        """Invent one complete sample without a valve or an analyzer.

        Fallback, not the happy path -- it exists so a rehearsal can produce a
        reviewable entry with nothing else running. It has to fake *both* halves
        now: open the entry as the valve would, then append results as the
        analyzer would, or it would only ever produce unmatched rows.
        """
        stamp = _now()
        sample_id = "S-%s-%s" % (stamp.strftime("%Y%m%d"), stamp.strftime("%H%M%S"))
        self.create_sample(
            "lims-fallback",
            {
                "ts": _iso(stamp),
                "values": {
                    "sample_id": sample_id,
                    "badge_id": "B-0000",
                    "badge_holder": "fallback generator",
                    "sample_start": _iso(stamp),
                    "sample_completion": _iso(stamp),
                    "open_duration_s": 13.5,
                    "cycle_result": "normal",
                    "cycle_count": 0,
                },
            },
        )
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


def _build_envelope(entry: tuple, results: list) -> dict:
    """One message per sample, carrying both halves of the record.

    `ts` is the acquisition instant, not the approval instant -- the event being
    described is the measurement. The approval instant is meta.ingest_ts, and the
    gap between the two is visible on stage, which is the point of the pattern.
    On a sample that was never analysed there is no acquisition instant, so the
    valve's close time stands in: that genuinely is when the record was made.

    `values.collection` is pattern 1's contribution, carried through review and
    republished under mechanism=webhook. It is what makes this message
    self-contained -- who drew the sample, when the valve opened, and how the
    cycle ended, beside the numbers a person just signed for.

    `seq` is filled in with the outbox id after INSERT.
    """
    (sample_id, batch_id, badge_id, badge_holder, sample_start,
     sample_completion, open_duration_s, cycle_result, analyst, _verified_at) = entry
    collected_at = min((row[3] for row in results), default=None) or sample_completion
    return {
        "ts": _iso(collected_at) if collected_at else _iso(),
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
            "collected_at": _iso(collected_at) if collected_at else None,
            "analyst": analyst,
            "collection": {
                "badge_id": badge_id,
                "badge_holder": badge_holder,
                "sample_start": _iso(sample_start) if sample_start else None,
                "sample_completion": _iso(sample_completion) if sample_completion else None,
                "open_duration_s": float(open_duration_s) if open_duration_s is not None else None,
                "cycle_result": cycle_result,
            },
            "results": [
                {"analyte": row[0], "value": float(row[1]), "uom": row[2]}
                for row in results
            ],
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
        # Subscribed here, not once at connect, because a reconnect after a
        # broker restart brings back a session that remembers nothing -- clean
        # session, by choice. The retained sample-complete arrives again with it.
        client.subscribe(self.cfg.valve_event_topic, qos=1)
        client.subscribe(self.cfg.result_topic, qos=1)
        LOG.info("mqtt connected; subscribed QoS 1 to %s and %s",
                 self.cfg.valve_event_topic, self.cfg.result_topic)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected = False
        LOG.warning("mqtt disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        try:
            document = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            LOG.warning("dropping unparseable payload on %s: %s", message.topic, exc)
            return
        if not isinstance(document, dict):
            LOG.warning("dropping non-object payload on %s", message.topic)
            return
        # Routed on the topic, not on the payload's shape. Pattern 1 is a bought
        # device: no `meta`, no `source`, nothing inside the document says what
        # it is. The topic string is the only thing that distinguishes the two,
        # which is pattern 1's whole argument arriving as a code constraint.
        try:
            if mqtt.topic_matches_sub(self.cfg.valve_event_topic, message.topic):
                self.store.create_sample(message.topic, document)
            else:
                self.store.ingest(document)
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


def _fmt_display_ts(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = _parse_ts(value)
        except Exception:
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%d-%b-%Y %H:%M")


def _initials(name: str) -> str:
    parts = [p for p in (name or "").replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


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
            unmatched = store.unmatched_results()
            outbox = store.outbox_rows()
        except Exception:
            LOG.exception("page query failed")
            pending, unmatched, outbox = [], [], []
            if not flash:
                flash = "Postgres is unreachable."
                flash_ok = False
        page = _read_page()
        page = page.replace("@@TOPIC@@", _esc(cfg.result_topic))
        page = page.replace("@@VALVE_TOPIC@@", _esc(cfg.valve_event_topic))
        page = page.replace("@@ANALYST@@", _esc(analyst))
        page = page.replace("@@ANALYST_INITIALS@@", _esc(_initials(analyst)))
        page = page.replace("@@MQTT_LED@@", "on" if ingest.connected else "")
        page = page.replace("@@MQTT_LABEL@@", "Online" if ingest.connected else "Offline")
        page = page.replace("@@DRAINER_LED@@", "on" if drainer.enabled.is_set() else "warn")
        page = page.replace(
            "@@DRAINER_LABEL@@",
            "Running" if drainer.enabled.is_set() else "Paused",
        )
        awaiting = [item for item in pending if not item["reviewable"]]
        page = page.replace("@@PENDING_COUNT@@", str(len(pending)))
        page = page.replace("@@AWAITING_COUNT@@", str(len(awaiting)))
        page = page.replace("@@UNMATCHED_COUNT@@", str(len(unmatched)))
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
        page = page.replace("@@UNMATCHED@@", _unmatched_html(unmatched, pending, analyst))
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

    @app.post("/attach")
    async def attach(request: Request):
        """Attach a parked result to an entry. The analyst owns this decision.

        Nothing here guesses. The reported id and the entry both come from the
        form -- a person chose them -- and both are written down.
        """
        form = await _form_body(request)
        analyst = _parse_analyst(request, form, cfg)
        reported = (form.get("reported_sample_id") or [""])[0].strip()
        sample_id = (form.get("sample_id") or [""])[0].strip()
        if not reported or not sample_id:
            result = {"ok": False,
                      "error": "reported_sample_id and sample_id are both required",
                      "status_code": 400}
        else:
            try:
                result = store.attach(reported, sample_id, analyst)
            except Exception as exc:
                LOG.exception("attach failed")
                result = {"ok": False, "error": str(exc), "status_code": 500}
        return reply(request, result, "attached %s to %s" % (reported, sample_id))

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


def _fmt_result(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    if number == int(number):
        return str(int(number))
    return "%.2f" % number


def _spec_for(analyte: str, value) -> dict:
    catalog = TEST_CATALOG.get(analyte, {
        "code": (analyte or "").upper(),
        "name": (analyte or "").title(),
        "lo": None,
        "hi": None,
        "uom": "",
    })
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None
    in_spec = True
    if number is not None:
        if catalog["lo"] is not None and number < catalog["lo"]:
            in_spec = False
        if catalog["hi"] is not None and number > catalog["hi"]:
            in_spec = False
    if catalog["lo"] is None and catalog["hi"] is None:
        spec_text = "—"
    elif catalog["lo"] is None:
        spec_text = "≤ %s" % _fmt_result(catalog["hi"])
    elif catalog["hi"] is None:
        spec_text = "≥ %s" % _fmt_result(catalog["lo"])
    else:
        spec_text = "%s – %s" % (_fmt_result(catalog["lo"]), _fmt_result(catalog["hi"]))
    return {
        "code": catalog["code"],
        "name": catalog["name"],
        "uom": catalog["uom"],
        "spec": spec_text,
        "in_spec": in_spec,
    }


CYCLE_LABELS = {
    "normal": "Cycle normal",
    "failed-to-seat": "Failed to seat",
    "stroke-timeout": "Stroke timeout",
}


def _collection_html(sample: dict) -> str:
    """Pattern 1's half of the record, under the sample id.

    This is the part that did not exist before the valve opened the entry: who
    drew it, when, for how long, and how the cycle ended. On a failed cycle it is
    the *whole* record -- there are no numbers to put beside it.
    """
    lines = []
    holder = sample.get("badge_holder")
    if holder:
        lines.append('<div class="smeta">Drawn by %s%s</div>' % (
            _esc(holder),
            " · %s" % _esc(sample["badge_id"]) if sample.get("badge_id") else "",
        ))
    if sample.get("sample_start"):
        duration = sample.get("open_duration_s")
        lines.append('<div class="smeta">Valve open %s%s</div>' % (
            _esc(_fmt_display_ts(sample["sample_start"])),
            " · %ss" % _fmt_result(duration) if duration is not None else "",
        ))
    cycle = sample.get("cycle_result")
    if cycle:
        lines.append('<div class="smeta %s">%s</div>' % (
            "oos" if cycle != "normal" else "",
            _esc(CYCLE_LABELS.get(cycle, cycle)),
        ))
    return "".join(lines)


def _no_results_html(sample: dict) -> str:
    """The six result columns replaced by one sentence.

    Two different situations reach here and they must not read the same. One is
    waiting for an analysis that is coming; the other is a sample whose cycle
    failed, which will never be analysed and is reviewable now. Filling the
    columns with dashes in either case reads like data that was measured.
    """
    if sample.get("reviewable"):
        return (
            '<td class="await-cell" colspan="6">'
            "No analysis — the cycle did not complete normally, so no material "
            "reached the analyser. The record still requires a disposition.</td>"
        )
    return (
        '<td class="await-cell" colspan="6">'
        "Sample drawn and logged. Awaiting analyser result — the sample id has to "
        "be entered on the FLEX2 before it runs.</td>"
    )


def _pending_html(pending: list[dict], analyst: str) -> str:
    if not pending:
        return (
            '<p class="empty">No samples are open. '
            "An entry appears here the moment the sample valve reports a completed "
            "cycle, and the FLEX2 results are appended to it when the analysis "
            "finishes.</p>"
        )
    body = []
    for sample in pending:
        results = sample["results"] or []
        reviewable = bool(sample.get("reviewable"))
        span = max(len(results), 1)
        oos = 0
        first = True
        for item in results or [None]:
            spec = _spec_for((item or {}).get("analyte"), (item or {}).get("value"))
            if item and item.get("analyte") and not spec["in_spec"]:
                oos += 1
            row_class = "sample-start" if first else ("fail" if not spec["in_spec"] else "")
            cells = ""
            if first:
                cells = (
                    '<td class="sample-cell" rowspan="%s">'
                    '<div class="sid">%s</div>'
                    '<div class="smeta">%s</div>'
                    '<div class="smeta">%s · %s</div>'
                    "%s@@FLAG@@"
                    "</td>" % (
                        span,
                        _esc(sample["sample_id"]),
                        _esc(sample["batch_id"] or "batch not assigned"),
                        _esc(_fmt_display_ts(sample["collected_at"])),
                        "FLEX2" if results else "no analyser",
                        _collection_html(sample),
                    )
                )
            if item is None:
                cells += _no_results_html(sample)
            else:
                cells += (
                    "<td>%s</td><td>%s</td>"
                    '<td class="num">%s</td><td>%s</td><td>%s</td>'
                    '<td><span class="disp %s">%s</span></td>' % (
                        _esc(spec["code"]),
                        _esc(spec["name"]),
                        _esc(_fmt_result(item.get("value"))),
                        _esc(item.get("uom") or spec["uom"]),
                        _esc(spec["spec"]),
                        "in" if spec["in_spec"] else "out",
                        "In spec" if spec["in_spec"] else "OOS",
                    )
                )
            if first:
                cells += _actions_html(sample, analyst, reviewable, span)
            body.append('<tr class="%s">%s</tr>' % (row_class, cells))
            first = False
        if not reviewable:
            flag = '<div class="smeta await">Awaiting analysis</div>'
        elif oos:
            flag = '<div class="smeta oos">%s of %s tests OOS</div>' % (oos, len(results))
        elif results:
            flag = '<div class="smeta">In specification</div>'
        else:
            flag = '<div class="smeta oos">No result to specify</div>'
        # The first row was emitted before oos was fully known. Patch the marker.
        if body:
            body[-span] = body[-span].replace("@@FLAG@@", flag, 1)
    return (
        "<table><thead><tr>"
        "<th>Sample</th><th>Test</th><th>Name</th><th>Result</th>"
        "<th>Units</th><th>Specification</th><th>Disposition</th><th></th>"
        "</tr></thead><tbody>%s</tbody></table>"
        '<p class="meaning">21 CFR 11 signature meaning: I have reviewed these results '
        "and they are suitable for use.</p>" % "".join(body)
    )


def _actions_html(sample: dict, analyst: str, reviewable: bool, span: int) -> str:
    """Approve and Reject, or the reason neither is offered.

    An entry still awaiting its analysis is not something a signature can be
    applied to -- there is nothing yet to have reviewed. The server refuses it
    too (`Store._not_reviewable`); this only stops the button existing.
    """
    if not reviewable:
        return (
            '<td class="action-cell" rowspan="%s">'
            '<span class="chip pending">Not yet reviewable</span></td>' % span
        )
    return (
        '<td class="action-cell" rowspan="%s"><div class="actions">'
        '<form method="post" action="/samples/%s/approve">'
        '<input type="hidden" name="analyst" value="%s">'
        '<button class="ok" type="submit">e-Sign &amp; release</button></form>'
        '<form method="post" action="/samples/%s/reject">'
        '<input type="hidden" name="analyst" value="%s">'
        '<button class="danger" type="submit">Return to lab</button></form>'
        "</div></td>" % (
            span,
            _esc(sample["sample_id"]),
            _esc(analyst),
            _esc(sample["sample_id"]),
            _esc(analyst),
        )
    )


def _unmatched_html(unmatched: list[dict], pending: list[dict], analyst: str) -> str:
    """Results the FLEX2 reported against an id no entry carries.

    On stage this is a mistyped sample id. The valve's entry is sitting in the
    queue above with no results; this analysis is sitting here with no entry, and
    both facts are visible at once. Attaching is an analyst decision, recorded as
    one -- what the instrument reported is never rewritten.
    """
    if not unmatched:
        return '<p class="empty">Every result received has matched an open sample.</p>'
    open_ids = [item["sample_id"] for item in pending if not item.get("reviewable")]
    rows = []
    for item in unmatched:
        summary = ", ".join(
            ("%s %s %s" % (
                _spec_for(r.get("analyte"), r.get("value"))["name"],
                _fmt_result(r.get("value")),
                r.get("uom") or "",
            )).strip()
            for r in (item["results"] or [])
        )
        if open_ids:
            options = "".join(
                '<option value="%s">%s</option>' % (_esc(sid), _esc(sid))
                for sid in open_ids
            )
            action = (
                '<form method="post" action="/attach">'
                '<input type="hidden" name="analyst" value="%s">'
                '<input type="hidden" name="reported_sample_id" value="%s">'
                '<select name="sample_id">%s</select>'
                '<button class="ok" type="submit">Attach</button></form>' % (
                    _esc(analyst),
                    _esc(item["reported_sample_id"]),
                    options,
                )
            )
        else:
            action = '<span class="chip pending">No open sample to attach to</span>'
        rows.append(
            "<tr>"
            '<td class="sample-cell"><div class="sid">%s</div></td>'
            "<td>%s</td><td>%s</td><td>%s</td>"
            '<td class="action-cell">%s</td>'
            "</tr>" % (
                _esc(item["reported_sample_id"]),
                _esc(item["batch_id"] or "—"),
                _esc(_fmt_display_ts(item["collected_at"])),
                _esc(summary or "—"),
                action,
            )
        )
    return (
        "<table><thead><tr>"
        "<th>Reported as</th><th>Batch</th><th>Acquired</th><th>Results</th><th></th>"
        "</tr></thead><tbody>%s</tbody></table>"
        '<p class="meaning">The id the instrument reported is kept exactly as received. '
        "Attaching records which sample the analysis belongs to and who decided that — "
        "it does not overwrite what the FLEX2 said.</p>" % "".join(rows)
    )


def _outbox_html(outbox: list[dict]) -> str:
    if not outbox:
        return '<p class="empty">No transmissions in the current session.</p>'
    labels = {
        "pending": "Queued",
        "delivered": "Sent",
        "abandoned": "Failed",
    }
    rows = []
    for item in outbox:
        state = item["state"] or ""
        rows.append(
            "<tr>"
            "<td>%s</td>"
            '<td><span class="chip %s">%s</span></td>'
            '<td class="num">%s</td>'
            "<td>%s</td>"
            "<td>%s</td>"
            "</tr>" % (
                _esc(item["sample_id"]),
                _esc(state),
                _esc(labels.get(state, state)),
                item["attempts"],
                _esc(item["last_error"] or "—"),
                _esc(_fmt_display_ts(item["updated_at"])),
            )
        )
    return (
        "<table><thead><tr>"
        "<th>Sample</th><th>Interface</th><th>Attempts</th>"
        "<th>Last message</th><th>Updated</th>"
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
