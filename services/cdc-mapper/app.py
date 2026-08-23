#!/usr/bin/env python3
"""Pattern 5 -- Debezium change event -> the backbone envelope.

Debezium Server tails the write-ahead log of `apconnect`, the turbidity meter's
data-management application, and publishes its own JSON onto Chariot. This service is the
last twenty metres: it subscribes to that internal topic and republishes each INSERT as the
envelope every other pattern uses.

    apconnect.measurement --pgoutput--> Debezium Server --MQTT--> THIS --MQTT--> the UNS topic

**Why this exists at all is a talk line, not plumbing.** Debezium's payload is a database
change: `before`, `after`, `op`, `source`, LSNs, a table name. Ours is a measurement:
`ts`, `seq`, `source`, `meta.mechanism`, `values`. Pretending a single-message transform
could emit `meta.mechanism` and a projected Variant array is how this pattern would quietly
slip into publishing Debezium's shape on a topic the firehose colours by mechanism. CDC hands
you the change; meeting the contract is still your problem.

Two decisions worth stating out loud:

  * **Insert-only.** `op` `c` (create) and `r` (snapshot read) publish. `u` and `d` are
    logged and dropped. A completed measurement is an immutable fact; an UPDATE to one is a
    correction, and this demo has nowhere honest to put a correction on a telemetry topic.
    Checkpoint 5 is precisely "prove the update arrived and was deliberately not published".

  * **Absent stays absent.** A CANCELED or FAILURE measurement carries no haze Variants, so
    the envelope carries no haze keys. Not zero, not null. Same rule as patterns 3, 4 and 6.

**Everything about the input side is a best guess until the first real event.** The exact
MQTT topic Debezium Server 3.0 publishes on, how it renders `timestamptz` and `uuid`, and
whether `jsonb` arrives as a string or a parsed array are all unconfirmed. So: the input
topic is `IN_TOPIC` in the environment (a correction is a compose edit, not a rebuild), every
plausible shape is handled below, and the raw shape of the FIRST event is logged once at INFO
so one run turns the guesses into facts. That trick paid off on pattern 4.

paho-mqtt and the standard library, nothing more. The ~20-line envelope helper is duplicated
from the other services rather than shared -- house convention, see services/README.md.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

MECHANISM = "cdc"
SOURCE_TYPE = "turbidity-meter"

# Variant id -> envelope key. ONE place, so swapping in the vendor's real ids when somebody
# confirms them against a live AP Connect is a single edit.
#
# `Density/CellTemperature` is transcribed from the vendor's well-known-values table. The
# `Haze/...` ids are MODELLED on the vendor's `Module/Quantity` convention -- the Haze
# module's ids are not in the documented table. See
# docs/reference/apconnect-haze3001-model.md.
#
# This dict must stay identical to VARIANT_MAP in the pattern-6 Ignition script
# (project script `poll_turbidity`). Two copies on purpose -- there is no shared library
# across Jython and CPython -- so change both.
VARIANT_MAP = {
    "Haze/Haze":               "haze_ebc",
    "Haze/HazeNTU":            "haze_ntu",
    "Haze/S25S0":              "s25_s0",
    "Haze/S90S0":              "s90_s0",
    "Haze/AbsorbanceS0":       "absorbance_s0",
    "Density/CellTemperature": "cell_temperature_c",
}

LOG = logging.getLogger("mapper")


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


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


class Config:
    def __init__(self) -> None:
        self.broker_host = _env("BROKER_HOST", "chariot")
        self.broker_port = _env_int("BROKER_PORT", 1883)
        self.username = _env("MQTT_USERNAME", "cdc-mapper")
        self.password = _env("MQTT_PASSWORD", "cdc-mapper")
        self.client_id = _env("MQTT_CLIENT_ID", "cdc-mapper")

        # UNCONFIRMED. Debezium Server's topic name is `topic.prefix` + schema + table, which
        # should make this `debezium.public.measurement` -- but whether the MQTT sink keeps
        # the dots or rewrites them into topic levels is not documented anywhere we can
        # check offline. Watch `debezium/#` AND `#` on the first run, then set this to
        # whatever actually appeared. It is an environment variable so that correction costs
        # a compose edit, not an image rebuild.
        self.in_topic = _env("IN_TOPIC", "debezium.public.measurement")
        # A second subscription, so a topic-name guess that is wrong by one character does
        # not produce a silent nothing. Blank to disable.
        self.in_topic_fallback = _env("IN_TOPIC_FALLBACK", "debezium/#")

        self.out_topic = _env(
            "OUT_TOPIC", "icc26/site1/downstream/tff-301/turbidity-01/telemetry")
        self.out_qos = _env_int("OUT_QOS", 1)
        # Not retained. A retained telemetry message would hand a late subscriber one stale
        # measurement and no way to tell it was stale.
        self.out_retain = _env_bool("OUT_RETAIN", False)

        self.device_id = _env("DEVICE_ID", "turbidity-01")
        # `u` and `d` are logged, never published. Flip only as a deliberate as-built
        # decision, and write it into the deviations table if you do.
        self.publish_updates = _env_bool("PUBLISH_UPDATES", False)

        # How many recently published measurement_no values to remember, so one INSERT
        # cannot become two messages. See Mapper._already_published().
        self.dedupe_window = _env_int("DEDUPE_WINDOW", 512)


# ── envelope helpers (duplicated on purpose -- see the module docstring) ──────────────────


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso(value) -> str:
    """Whatever Debezium put in a timestamp column -> an ISO-8601 UTC string.

    THE SHAPE IS NOT CONFIRMED. Two plausible renderings for a Postgres `timestamptz`:

      * `io.debezium.time.ZonedTimestamp` -- an ISO-8601 STRING like
        "2026-08-23T14:03:22.145Z". This is what the connector documentation implies for
        `timestamp with time zone`, and it is the likelier of the two.
      * an integer count since the epoch, which is what `timestamp without time zone`
        produces (microseconds, under the default adaptive precision mode).

    Rather than betting, handle both -- and let the first-event log below settle it. The
    epoch unit is inferred from magnitude, which is unambiguous for any date this century.
    """
    if value is None:
        return _iso_now()

    if isinstance(value, str):
        text = value.strip()
        try:
            # Python's parser wants an offset it recognises; Debezium writes a trailing Z.
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            # Not something we can parse. Pass it through unchanged rather than inventing a
            # timestamp -- a wrong-looking string in the payload is debuggable, a silently
            # substituted `now()` is not.
            return text
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed.astimezone(timezone.utc)
                      .isoformat(timespec="milliseconds").replace("+00:00", "Z"))

    if isinstance(value, (int, float)):
        magnitude = abs(float(value))
        if magnitude >= 1e17:        # nanoseconds
            seconds = value / 1e9
        elif magnitude >= 1e14:      # microseconds  <- Debezium's usual choice
            seconds = value / 1e6
        elif magnitude >= 1e11:      # milliseconds
            seconds = value / 1e3
        else:                        # seconds
            seconds = float(value)
        return (datetime.fromtimestamp(seconds, timezone.utc)
                        .isoformat(timespec="milliseconds").replace("+00:00", "Z"))

    return str(value)


def _drop_nones(values: dict) -> dict:
    """Absent stays absent. A key with no value is not a key with a zero in it."""
    return {k: v for k, v in values.items() if v is not None}


# ── projection ───────────────────────────────────────────────────────────────────────────


def _as_variants(raw):
    """`result_values` -> a list of Variant dicts, whatever Debezium made of the jsonb.

    Three shapes are plausible and all three are handled:
      * a JSON string (jsonb rendered as text -- the likeliest)
      * an already-parsed list
      * a single dict, if something upstream unwrapped a one-element array
    """
    if raw is None:
        return []
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except ValueError:
            LOG.warning("result_values was a string but not JSON: %.120s", text)
            return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return raw
    LOG.warning("result_values had unexpected type %s", type(raw).__name__)
    return []


def project_values(result_values) -> dict:
    """Variant array -> flat dict. Absent stays absent; never substitute 0.

    This is where the vendor's generic key/value pairs become named fields, and it is the
    only place in the chain that knows what `Haze/S25S0` means. An id that is not in
    VARIANT_MAP is skipped in silence: AP Connect is free to add Variants and this mapper is
    not the place to fail over one.
    """
    out = {}
    for variant in _as_variants(result_values):
        if not isinstance(variant, dict):
            continue
        key = VARIANT_MAP.get(variant.get("id"))
        if key is None:
            continue
        value = variant.get("value")
        # QUANTITY variants nest the number: {"numeric": 4.12, "unit": "EBC", ...}. Other
        # Variant types put a scalar straight in `value`.
        if isinstance(value, dict):
            value = value.get("numeric")
        if value is None:
            continue
        try:
            out[key] = float(value)
        except (TypeError, ValueError):
            LOG.warning("variant %s had a non-numeric value %r", variant.get("id"), value)
    return out


def project(change: dict, cfg: Config):
    """One Debezium change event -> one envelope, or None if it is not ours to publish."""
    # With `debezium.format.value.schemas.enable=false` (the Server default) the message IS
    # the payload. With schemas on it is wrapped. Handle both so a config change upstream
    # does not silently stop the mapper.
    payload = change.get("payload") if isinstance(change.get("payload"), dict) else change

    op = payload.get("op")
    if op not in ("c", "r"):
        return None

    after = payload.get("after") or {}
    if not after:
        LOG.warning("op=%s with no `after` -- dropped", op)
        return None

    values = {
        "measurement_no": int(after["measurement_no"]),
        # The vendor's GUID, as a string whatever Debezium made of the uuid column.
        "measurement_id": str(after["id"]),
        "status": after.get("status"),
        "sample_name": after.get("sample_name"),
        "instrument_serial": after.get("instrument_serial"),
    }
    # A CANCELED or FAILURE measurement produces no reading, so these keys simply do not
    # appear. Absent, not zero -- the same rule as patterns 3, 4 and 6.
    values.update(project_values(after.get("result_values")))

    return {
        # When AP Connect finished storing the measurement, not when we saw it.
        "ts": _iso(after.get("completed_ts")),
        # `measurement_no`, so CDC and pattern 6's poll are directly comparable on the
        # firehose: the same measurement carries the same seq in both colours.
        "seq": int(after["measurement_no"]),
        "source": {"id": cfg.device_id, "type": SOURCE_TYPE},
        "meta": {
            "mechanism": MECHANISM,
            # When this mapper published. The gap between this and `ts` is CDC's lag, and it
            # is small -- pattern 6's is the one worth pointing at.
            "ingest_ts": _iso_now(),
            # THE GUID, not the integer. Pattern 6 uses the same field, so a subscriber
            # joins the two colours of one measurement on it.
            "correlation_id": str(after["id"]),
        },
        "values": _drop_nones(values),
    }


# ── the client ───────────────────────────────────────────────────────────────────────────


class Mapper:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = None
        self.connected = False
        self.seen = 0
        self.published = 0
        self.skipped = 0
        self.duplicates = 0
        self._logged_raw = False
        self._raw_lock = threading.Lock()
        self._recent = OrderedDict()          # measurement_no -> None, bounded, insertion order
        self._recent_lock = threading.Lock()
        self._stop = threading.Event()

    # ---- exactly one message per INSERT

    def _already_published(self, measurement_no: int) -> bool:
        """Guard against publishing one measurement twice.

        Three ways a duplicate can reach us, and none of them is hypothetical:

          * IN_TOPIC and IN_TOPIC_FALLBACK can overlap. They do not with the shipped
            defaults (one is a dotted single level, the other a slashed hierarchy), but the
            moment somebody corrects IN_TOPIC to `debezium/public/measurement` -- which is
            the other plausible shape -- `debezium/#` matches it too, and MQTT permits the
            broker to deliver one copy per matching subscription.
          * QoS 1 is at-least-once in both directions.
          * Debezium re-reading from an old offset, or re-snapshotting, replays events.

        Checkpoint 4 is "one INSERT, ONE UNS message", so this is not defensive
        housekeeping -- it is the checkpoint. `measurement_no` is the table's primary key
        and strictly increasing, so it is the right identity to remember.

        The window is bounded and in-memory: a mapper restart forgets, and a Debezium
        re-snapshot after that restart would republish. That is recorded as a known limit
        rather than solved, because solving it means persisting state in a service whose
        whole job is to be stateless.
        """
        with self._recent_lock:
            if measurement_no in self._recent:
                return True
            self._recent[measurement_no] = None
            while len(self._recent) > self.cfg.dedupe_window:
                self._recent.popitem(last=False)
            return False

    # ---- the first-event log

    def _log_raw_once(self, topic: str, raw: bytes, change) -> None:
        """Log the raw shape of the FIRST event, once, at INFO.

        Three things about Debezium's output are guesses in this build: how `timestamptz`
        renders, how `uuid` renders, and whether `jsonb` arrives as a string or a parsed
        array. One line here turns all three into facts on the human's first run, and the
        same trick already paid for itself on pattern 4. It is INFO, not DEBUG, because the
        person who needs it will not have thought to raise the log level first.
        """
        with self._raw_lock:
            if self._logged_raw:
                return
            self._logged_raw = True

        LOG.info("FIRST EVENT -- raw topic %r, %d bytes", topic, len(raw))
        try:
            LOG.info("FIRST EVENT -- raw body: %s",
                     raw.decode("utf-8", "replace")[:2000])
        except Exception:
            LOG.info("FIRST EVENT -- raw body was not decodable as UTF-8")

        if not isinstance(change, dict):
            LOG.info("FIRST EVENT -- parsed as %s, not an object", type(change).__name__)
            return

        LOG.info("FIRST EVENT -- top-level keys: %s", sorted(change.keys()))
        payload = change.get("payload") if isinstance(change.get("payload"), dict) else change
        LOG.info("FIRST EVENT -- payload keys: %s, op=%r",
                 sorted(payload.keys()), payload.get("op"))
        after = payload.get("after") or {}
        if isinstance(after, dict):
            for field in ("measurement_no", "id", "completed_ts", "started_ts",
                          "result_values", "status"):
                if field in after:
                    value = after[field]
                    shown = value if not isinstance(value, str) else value[:160]
                    LOG.info("FIRST EVENT -- after.%s: %s = %r",
                             field, type(value).__name__, shown)
        LOG.info("FIRST EVENT -- if any of the above disagrees with the guesses in "
                 "docs/05-cdc-turbidity.md, that table is what needs correcting.")

    # ---- connection

    def _build_client(self) -> mqtt.Client:
        cfg = self.cfg
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(cfg.username, cfg.password)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        # No Last Will. This is not a field device -- nothing downstream models its liveness,
        # and a death certificate on the UNS topic would be a fake measurement.
        return client

    def start(self) -> None:
        self.client = self._build_client()
        self.client.connect_async(self.cfg.broker_host, self.cfg.broker_port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self._stop.set()
        client, self.client = self.client, None
        if client is not None:
            client.loop_stop()
            client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            LOG.error("connect refused: %s", reason_code)
            return
        self.connected = True
        topics = [(self.cfg.in_topic, 1)]
        if self.cfg.in_topic_fallback:
            topics.append((self.cfg.in_topic_fallback, 1))
        client.subscribe(topics)
        LOG.info("connected to %s:%s as %s -- subscribed %s, publishing %s",
                 self.cfg.broker_host, self.cfg.broker_port, self.cfg.username,
                 [t for t, _ in topics], self.cfg.out_topic)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self.connected = False
        LOG.warning("disconnected (%s) -- paho will retry with backoff", reason_code)

    # ---- the work

    def _on_message(self, client, userdata, message) -> None:
        raw = message.payload or b""
        # A tombstone is a zero-length body. `tombstones.on.delete=false` should stop
        # Debezium emitting them at all; ignore any that appear rather than logging a parse
        # error on every delete.
        if not raw:
            return

        try:
            change = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            LOG.warning("could not parse a message on %s: %s", message.topic, exc)
            return

        self.seen += 1
        self._log_raw_once(message.topic, raw, change)

        if not isinstance(change, dict):
            return

        payload = change.get("payload") if isinstance(change.get("payload"), dict) else change
        op = payload.get("op")

        try:
            envelope = project(change, self.cfg)
        except (KeyError, TypeError, ValueError) as exc:
            # A malformed event must not kill the loop. Log enough to diagnose it.
            LOG.warning("could not project an event from %s (op=%r): %s",
                        message.topic, op, exc)
            return

        if envelope is None:
            self.skipped += 1
            if op in ("u", "d"):
                # Checkpoint 5 lives here. `before` arrives populated because the table is
                # REPLICA IDENTITY FULL, and we deliberately do not put it on the UNS topic:
                # a completed measurement is an immutable fact and a telemetry topic has
                # nowhere honest to carry a correction.
                before = payload.get("before") or {}
                LOG.info("op=%s not published (insert-only) -- measurement_no %s, "
                         "before %s, after %s",
                         op,
                         (payload.get("after") or before).get("measurement_no"),
                         "present" if before else "ABSENT (replica identity?)",
                         "present" if payload.get("after") else "absent")
                if self.cfg.publish_updates:
                    LOG.warning("PUBLISH_UPDATES is on but updates are still dropped -- "
                                "publishing them is an as-built decision that needs the "
                                "deviations table updated, not just this flag")
            else:
                LOG.debug("op=%r ignored", op)
            return

        measurement_no = envelope["values"]["measurement_no"]
        if self._already_published(measurement_no):
            self.duplicates += 1
            LOG.info("measurement_no=%s already published -- dropped (topic %s). "
                     "Two subscriptions matching one topic is the usual cause; narrow "
                     "IN_TOPIC and clear IN_TOPIC_FALLBACK.", measurement_no, message.topic)
            return

        # ensure_ascii=False keeps the vendor's degree sign a real character on the wire
        # rather than a ° escape. Both are valid JSON; only one is checkpoint 8.
        body = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
        client.publish(self.cfg.out_topic, body.encode("utf-8"),
                       qos=self.cfg.out_qos, retain=self.cfg.out_retain)
        self.published += 1
        LOG.info("published measurement_no=%s status=%s haze=%s EBC -> %s",
                 envelope["values"].get("measurement_no"),
                 envelope["values"].get("status"),
                 envelope["values"].get("haze_ebc", "absent"),
                 self.cfg.out_topic)

    def run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(30.0)
            if self._stop.is_set():
                break
            LOG.debug("alive: connected=%s seen=%s published=%s skipped=%s duplicates=%s",
                      self.connected, self.seen, self.published, self.skipped,
                      self.duplicates)


# ── main ─────────────────────────────────────────────────────────────────────────────────


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )

    cfg = Config()
    mapper = Mapper(cfg)
    mapper.start()

    LOG.info("cdc-mapper up: %s (+ fallback %r) -> %s, mechanism=%s",
             cfg.in_topic, cfg.in_topic_fallback or None, cfg.out_topic, MECHANISM)
    LOG.info("waiting for the first change event. If nothing arrives, the input topic guess "
             "is wrong -- subscribe to '#' with the observer account and set IN_TOPIC to "
             "whatever Debezium actually used.")

    signal.signal(signal.SIGTERM, lambda *_: mapper.stop())
    signal.signal(signal.SIGINT, lambda *_: mapper.stop())
    try:
        mapper.run()
    finally:
        mapper.stop()
        time.sleep(0.2)
        LOG.info("shutdown complete -- seen %s, published %s, skipped %s, duplicates %s",
                 mapper.seen, mapper.published, mapper.skipped, mapper.duplicates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
