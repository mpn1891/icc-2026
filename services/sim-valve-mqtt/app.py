#!/usr/bin/env python3
"""Pattern 1 -- a smart sample valve assembly speaking native MQTT 3.1.1.

An RFID badge is presented at the sample port on BR-201; the assembly checks it against its
own roster and its process interlock, and if both pass it strokes the valve open for a
sampling window and closes it again. Every scan -- granted or denied -- is published. See
docs/plans/01-native-mqtt.md for the contract and why it is shaped this way.

Four things here are deliberate and easy to mistake for oversights:

  * Nothing about this device is standardised. The topic, the QoS, the retained flag and the
    payload shape are all decisions somebody made in a commissioning screen, and the
    commissioning screen is real: it is served on ${UI_PORT} by webui.py. That page is the
    pattern -- pattern 2's equivalent page cannot offer those fields at all.

  * The valve is publish-only. There is no command topic and nothing on the backbone can
    open it: a sample port that stops working when the broker does is not a sample port
    anybody would install, so authorization is local and the network only ever hears what
    already happened.

  * The Last Will is hand-rolled, and its payload is frozen at CONNECT. It carries the time
    the session *started*, not the time the device died, because a will is registered before
    the death it describes. Sparkplug has the same constraint and answers it by making the
    consumer stamp the death; here, nobody agreed on anything, so the timestamp in a death
    certificate on this backbone is simply wrong. That is the demo.

  * A retained message outlives the configuration that produced it. Change the topic on the
    config page and the old retained state sits at the old topic until something clears it.
    Also the demo.

Standard library plus paho-mqtt, nothing more.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time

import paho.mqtt.client as mqtt

import valve
import webui
from valve import Sink, ValveAssembly, iso, parse_roster

MECHANISM = "native-mqtt"

# The three message types this assembly publishes, and the defaults a sensible commissioning
# engineer would pick for each. The config page collapses them onto ONE topic, ONE QoS and
# ONE retained flag, because that is what the vendor's page actually offers -- see the
# comment on Config.publish_plan().
EVENT = "event"
STATE = "state"
TELEMETRY = "telemetry"


# -- config ------------------------------------------------------------------------------


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


DEFAULT_ROSTER = (
    "B-1042:Jordan Reyes:qc-analyst:authorized,"
    "B-2087:Sam Okafor:maintenance:not-authorized,"
    "B-3311:Alex Chen:qc-analyst:training-expired"
)


class Config:
    """Environment for what the factory set; a JSON file for what commissioning set.

    Only three fields are commissionable -- topic, QoS, retain -- because those are the only
    three the vendor's page exposes. Everything else is firmware.
    """

    COMMISSIONABLE = ("base_topic", "qos", "retain")

    def __init__(self) -> None:
        self.broker_host = _env("BROKER_HOST", "chariot")
        self.broker_port = _env_int("BROKER_PORT", 1883)
        self.username = _env("MQTT_USERNAME", "sample-valve-01")
        self.password = _env("MQTT_PASSWORD", "sample-valve-01")

        self.device_id = _env("DEVICE_ID", "sample-valve-01")
        self.cell = _env("CELL", "br-201")
        self.assembly_serial = _env("ASSEMBLY_SERIAL", "SV-2000-0417")
        self.firmware = _env("FIRMWARE_VERSION", "1.4.2")

        # What a fresh device ships with. `icc26/{site}/{area}/{cell}/{device}` per
        # docs/00-architecture.md -- but only because somebody typed it in. Nothing about
        # this device enforces or even knows about that namespace.
        self.base_topic = _env(
            "BASE_TOPIC", "icc26/site1/upstream/%s/%s" % (self.cell, self.device_id)
        ).strip("/")
        self.qos = _env_int("PUBLISH_QOS", 1)
        self.retain = _env_bool("PUBLISH_RETAIN", True)

        self.roster = parse_roster(_env("BADGE_ROSTER", DEFAULT_ROSTER))
        self.unknown_badge_id = _env("UNKNOWN_BADGE_ID", "B-9999")

        self.stroke_s = _env_float("VALVE_STROKE_S", 1.5)
        self.sample_window_s = _env_float("SAMPLE_WINDOW_S", 12.0)
        self.telemetry_interval_s = _env_float("TELEMETRY_INTERVAL_S", 5.0)
        # 0 disables free-running scans -- use that for a scripted stage run where the only
        # traffic should be the badge you present yourself.
        self.scan_interval_s = _env_float("SCAN_INTERVAL_S", 90.0)

        self.line_pressure_bar = _env_float("LINE_PRESSURE_BAR", 1.35)
        self.line_temperature_c = _env_float("LINE_TEMPERATURE_C", 36.8)

        self.ui_port = _env_int("UI_PORT", 8080)
        self.config_path = _env("CONFIG_PATH", "/data/config.json")

        self._load()

    # ---- commissioned settings

    def _load(self) -> None:
        try:
            with open(self.config_path, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            logging.warning("ignoring unreadable %s: %s", self.config_path, exc)
            return
        for key in self.COMMISSIONABLE:
            if key in stored:
                setattr(self, key, stored[key])
        logging.info("loaded commissioned settings from %s", self.config_path)

    def save(self) -> None:
        document = {key: getattr(self, key) for key in self.COMMISSIONABLE}
        directory = os.path.dirname(self.config_path)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
        except OSError as exc:
            # A read-only volume must not stop the device working -- it just means the
            # setting does not survive a restart, which is worth one warning.
            logging.warning("could not persist settings to %s: %s", self.config_path, exc)

    # ---- topics

    def topic(self, message_type: str) -> str:
        return "%s/%s" % (self.base_topic, message_type)

    def publish_plan(self) -> list:
        """What the three commissioned fields actually produce, for the page's preview.

        One QoS and one retained flag across all three message types is exactly the flaw
        worth showing: a badge scan is an audit record that must not be lost, and telemetry
        is a stream nobody will miss one sample of, and this page cannot tell them apart.
        """
        return [
            {"message_type": EVENT, "topic": self.topic(EVENT), "qos": self.qos,
             "retain": self.retain,
             "note": "one per badge scan and per completed sample -- losing one loses an "
                     "audit record"},
            {"message_type": STATE, "topic": self.topic(STATE), "qos": self.qos,
             "retain": self.retain,
             "note": "valve position; also carries the Last Will. Only useful to a late "
                     "subscriber if it is retained"},
            {"message_type": TELEMETRY, "topic": self.topic(TELEMETRY), "qos": self.qos,
             "retain": self.retain,
             "note": "line pressure and temperature every %ss -- disposable"
                     % self.telemetry_interval_s},
        ]


# -- envelope ----------------------------------------------------------------------------

_seq_lock = threading.Lock()
_seq = 0


def _next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def envelope(cfg: Config, event: str, values: dict, ts: float = None) -> dict:
    """The standard envelope from docs/00-architecture.md.

    `ts` is when the thing happened; `meta.ingest_ts` is when this payload was built. For a
    badge scan those are the same millisecond, and for the Last Will they are hours apart --
    see the module docstring.
    """
    now = time.time()
    return {
        "ts": iso(ts if ts is not None else now),
        "seq": _next_seq(),
        "source": {"id": cfg.device_id, "type": "sample-valve"},
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": iso(now),
            "event": event,
            "cell": cfg.cell,
            "assembly_serial": cfg.assembly_serial,
        },
        "values": values,
    }


# -- the client --------------------------------------------------------------------------


class MqttSink(Sink):
    """Turns what the valve did into JSON documents on topics somebody typed in.

    Owns the paho client, because the Last Will is part of the connection: changing the
    topic on the config page means a new will, and a will can only be set before CONNECT.
    That is why apply() below tears the client down and builds a new one rather than
    reconfiguring it in place.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("mqtt")
        self.lock = threading.Lock()
        self.client = None
        self.connected = False
        self.published = 0
        self.assembly = None  # set by main() once the valve exists

    # ---- connection

    def _build_client(self) -> mqtt.Client:
        cfg = self.cfg
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.device_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(cfg.username, cfg.password)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect

        # The hand-rolled death certificate. Note what it costs: the topic is one this
        # device chose, the payload shape is one this device invented, every consumer has to
        # be told separately how to read it, and the timestamp inside it is the moment the
        # session opened rather than the moment it broke. Pattern 2 gets all four of those
        # for free from the spec.
        will = envelope(cfg, STATE, {
            "state": valve.OFFLINE,
            "is_open": None,
            "position_pct": None,
            "note": "last will -- ts is when this session connected, not when it died",
        })
        client.will_set(cfg.topic(STATE), json.dumps(will, separators=(",", ":")),
                        qos=cfg.qos, retain=cfg.retain)
        return client

    def start(self) -> None:
        with self.lock:
            self.client = self._build_client()
            self.client.connect_async(self.cfg.broker_host, self.cfg.broker_port, keepalive=30)
            self.client.loop_start()

    def restart(self) -> None:
        """Reconnect with the newly commissioned topic, QoS and will."""
        with self.lock:
            old = self.client
            self.client = None
        if old is not None:
            old.loop_stop()
            old.disconnect()
        self.connected = False
        self.start()

    def stop(self) -> None:
        with self.lock:
            client = self.client
            self.client = None
        if client is not None:
            client.loop_stop()
            client.disconnect()

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            self.log.error("connect refused: %s", reason_code)
            return
        self.connected = True
        self.log.info("connected to %s:%s as %s, publishing under %s",
                      self.cfg.broker_host, self.cfg.broker_port, self.cfg.username,
                      self.cfg.base_topic)
        # Announce the current position immediately. With retain on, this is what a
        # subscriber that connects tomorrow will be handed; with retain off, it is a message
        # only whoever is already listening will ever see. Same code, and the config page
        # decides which of those two things it is.
        if self.assembly is not None:
            self.valve_state(self.assembly.state_snapshot(), time.time())

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self.connected = False
        self.log.warning("disconnected (%s) -- paho will retry with backoff", reason_code)

    # ---- publishing

    def _publish(self, message_type: str, document: dict) -> None:
        client = self.client
        if client is None:
            return
        client.publish(
            self.cfg.topic(message_type),
            json.dumps(document, separators=(",", ":")),
            qos=self.cfg.qos,
            retain=self.cfg.retain,
        )
        self.published += 1

    def valve_event(self, event: str, data: dict, ts: float) -> None:
        self._publish(EVENT, envelope(self.cfg, event, data, ts))

    def valve_state(self, snapshot: dict, ts: float) -> None:
        self._publish(STATE, envelope(self.cfg, STATE, snapshot, ts))

    def valve_telemetry(self, values: dict, ts: float) -> None:
        self._publish(TELEMETRY, envelope(self.cfg, TELEMETRY, values, ts))


# -- the config page's view of the device ------------------------------------------------


class Provider(webui.ConfigProvider):
    def __init__(self, cfg: Config, sink: MqttSink, assembly: ValveAssembly) -> None:
        self.cfg = cfg
        self.sink = sink
        self.assembly = assembly

    def state(self) -> dict:
        cfg = self.cfg
        return {
            "variant": "plain-mqtt",
            "device": {
                "id": cfg.device_id,
                "cell": cfg.cell,
                "serial": cfg.assembly_serial,
                "firmware": cfg.firmware,
            },
            "broker": {
                "host": cfg.broker_host,
                "port": cfg.broker_port,
                "username": cfg.username,
                "protocol": "MQTT 3.1.1",
                "connected": self.sink.connected,
            },
            # The three commissionable fields, and nothing else. Editable, unvalidated
            # against any namespace, and entirely this device's own business.
            "config": {
                "base_topic": cfg.base_topic,
                "qos": cfg.qos,
                "retain": cfg.retain,
            },
            "publish_plan": cfg.publish_plan(),
            "will": {
                "topic": cfg.topic(STATE),
                "qos": cfg.qos,
                "retain": cfg.retain,
                "payload_state": valve.OFFLINE,
            },
            "runtime": {
                "valve": self.assembly.state_snapshot(),
                "telemetry": self.assembly.telemetry_values(),
                "last_scan": self.assembly.last_scan,
                "published": self.sink.published,
            },
            "roster": [badge.as_dict() for badge in cfg.roster.values()]
                      + [{"badge_id": cfg.unknown_badge_id, "holder": "not on roster",
                          "role": "unknown", "status": "unknown"}],
        }

    def apply(self, payload: dict):
        base_topic = str(payload.get("base_topic", self.cfg.base_topic)).strip().strip("/")
        if not base_topic:
            return False, "topic cannot be empty"
        # A real device validates almost nothing here, and neither do we beyond what MQTT
        # itself forbids. A topic that does not fit the site namespace is accepted, publishes
        # happily, and is invisible to every subscriber that expected it elsewhere -- which
        # is the failure mode pattern 2 makes structurally impossible.
        if "+" in base_topic or "#" in base_topic:
            return False, "wildcards are not valid in a publish topic"

        try:
            qos = int(payload.get("qos", self.cfg.qos))
        except (TypeError, ValueError):
            return False, "QoS must be 0, 1 or 2"
        if qos not in (0, 1, 2):
            return False, "QoS must be 0, 1 or 2"

        retain = bool(payload.get("retain", self.cfg.retain))

        previous = self.cfg.topic(STATE)
        self.cfg.base_topic = base_topic
        self.cfg.qos = qos
        self.cfg.retain = retain
        self.cfg.save()

        # New topic means a new will, and a will is only registered at CONNECT.
        self.sink.restart()

        message = "applied -- publishing to %s at QoS %s, retain %s" % (
            base_topic, qos, "on" if retain else "off")
        if previous != self.cfg.topic(STATE) and retain:
            message += ". The retained state at %s is still there; a retained message " \
                       "outlives the config that made it." % previous
        return True, message

    def scan(self, badge_id: str) -> dict:
        return self.assembly.scan(badge_id)

    def set_interlock(self, ok: bool) -> None:
        self.assembly.set_interlock(ok)


# -- main --------------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )

    cfg = Config()
    sink = MqttSink(cfg)
    assembly = ValveAssembly(cfg, sink)
    sink.assembly = assembly

    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
    webui.serve(cfg.ui_port, page, Provider(cfg, sink, assembly))
    sink.start()

    logging.info("sample valve %s up on %s: %s badges on roster, sample window %ss",
                 cfg.device_id, cfg.cell, len(cfg.roster), cfg.sample_window_s)

    signal.signal(signal.SIGTERM, lambda *_: assembly.stop())
    signal.signal(signal.SIGINT, lambda *_: assembly.stop())
    try:
        assembly.run()
    finally:
        # A graceful stop publishes the closing state and disconnects cleanly, so the will
        # is discarded by the broker and never fires. `docker stop` is therefore a quiet
        # death and `docker kill` is a loud one -- two demos out of the same container.
        sink.valve_state(assembly.state_snapshot(), time.time())
        time.sleep(0.2)
        sink.stop()
        logging.info("shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
