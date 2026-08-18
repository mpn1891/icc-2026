#!/usr/bin/env python3
"""Pattern 2 -- the same smart sample valve assembly, speaking Sparkplug B v3.0.0.

Physically identical to services/sim-valve-mqtt: same badge roster, same interlock, same
stroke times, same valve.py. Everything that differs between the two containers is a
consequence of the specification, which is the whole reason they are a pair. See
docs/plans/02-sparkplug-b.md.

What the spec took away from the commissioning engineer, and what it gave back:

  * The topic is gone. There is no field to type one into -- `spBv1.0/{group}/{type}/{node}/
    {device}` is the namespace, and the only choice left is what to call the group, the node
    and the device. The config page on ${UI_PORT} shows the resulting topics read-only, next
    to the plain-MQTT page where the same field is a free-text box.

  * QoS and Retain are gone, pinned to the values in sparkplug.py with the TCK identifiers
    that fix them.

  * In exchange: the tag tree builds itself from DBIRTH, every metric carries its own
    datatype and engineering unit, `seq` makes a dropped message detectable, and NDEATH is a
    death certificate every consumer already knows how to read. Pattern 1 has to hand-roll
    each of those, and did.

The valve remains publish-only. The one inbound message this device honours is
`Node Control/Rebirth`, which is not a command to the valve -- it is the host asking the
device to re-announce itself, and Ignition's MQTT Engine sends it unprompted whenever it
sees data for a device it has no birth certificate for.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time

import paho.mqtt.client as mqtt

import sparkplug
import valve
import webui
from sparkplug import DataType, Metric, SequenceCounter
from valve import Sink, ValveAssembly, parse_roster

# Metric table: name, datatype, properties, report-by-exception deadband.
#
# The names use `/` so MQTT Engine renders them as a folder tree under the device. The
# deadbands are why DDATA is small: a line temperature that wanders by 0.05 degrees is not
# news, and saying so is a property of this device rather than of the broker.
_UNIT = lambda symbol: {"engUnit": (DataType.String, symbol)}  # noqa: E731

DEVICE_METRICS = [
    ("Valve/State", DataType.String, None, None),
    ("Valve/IsOpen", DataType.Boolean, None, None),
    ("Valve/PositionPct", DataType.Float, _UNIT("%"), 0.5),
    ("Interlock/Ok", DataType.Boolean, None, None),
    ("Badge/LastScanId", DataType.String, None, None),
    ("Badge/LastScanHolder", DataType.String, None, None),
    ("Badge/LastScanRole", DataType.String, None, None),
    ("Badge/LastScanResult", DataType.String, None, None),
    ("Badge/LastDenyReason", DataType.String, None, None),
    ("Badge/LastScanTime", DataType.DateTime, None, None),
    ("Sample/CycleCount", DataType.Int64, None, None),
    ("Sample/LastSampleId", DataType.String, None, None),
    ("Sample/LastSampleTime", DataType.DateTime, None, None),
    ("Sample/LastOpenDurationS", DataType.Float, _UNIT("s"), None),
    ("Line/PressureBar", DataType.Float, _UNIT("bar"), 0.05),
    ("Line/TemperatureC", DataType.Float, _UNIT("degC"), 0.2),
    ("Device/FirmwareVersion", DataType.String, None, None),
    ("Device/SerialNumber", DataType.String, None, None),
    ("Device/Cell", DataType.String, None, None),
]

REBIRTH_METRIC = "Node Control/Rebirth"


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
    """Three commissionable fields here too -- but not the same three.

    Group, edge node and device IDs are names *within* a namespace somebody else defined.
    The plain-MQTT valve's three fields are the namespace itself.
    """

    COMMISSIONABLE = ("group_id", "edge_node_id", "device_id")

    def __init__(self) -> None:
        self.broker_host = _env("BROKER_HOST", "chariot")
        self.broker_port = _env_int("BROKER_PORT", 1883)
        self.username = _env("MQTT_USERNAME", "sample-valve-02")
        self.password = _env("MQTT_PASSWORD", "sample-valve-02")

        self.group_id = _env("GROUP_ID", "ICC26-Site1-UPSTREAM")
        self.edge_node_id = _env("EDGE_NODE_ID", "SAMPLE-VALVE-02")
        self.device_id = _env("DEVICE_ID", "SV-202")

        self.cell = _env("CELL", "br-202")
        self.assembly_serial = _env("ASSEMBLY_SERIAL", "SV-2000-0418")
        self.firmware = _env("FIRMWARE_VERSION", "2.1.0-spB")

        # Aliases are what Cirrus's own Transmission does by default: name plus alias at
        # birth, alias alone in DATA. It is a real bandwidth argument and a real
        # you-must-have-the-birth-certificate argument, both worth showing. Set false to see
        # the difference on the wire.
        self.use_aliases = _env_bool("USE_ALIASES", True)

        self.roster = parse_roster(_env("BADGE_ROSTER", DEFAULT_ROSTER))
        self.unknown_badge_id = _env("UNKNOWN_BADGE_ID", "B-9999")

        self.stroke_s = _env_float("VALVE_STROKE_S", 1.5)
        self.sample_window_s = _env_float("SAMPLE_WINDOW_S", 12.0)
        self.telemetry_interval_s = _env_float("TELEMETRY_INTERVAL_S", 5.0)
        self.scan_interval_s = _env_float("SCAN_INTERVAL_S", 90.0)

        self.line_pressure_bar = _env_float("LINE_PRESSURE_BAR", 1.35)
        self.line_temperature_c = _env_float("LINE_TEMPERATURE_C", 36.8)

        self.ui_port = _env_int("UI_PORT", 8080)
        self.config_path = _env("CONFIG_PATH", "/data/config.json")

        self._load()

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
            logging.warning("could not persist settings to %s: %s", self.config_path, exc)

    # ---- topics, all derived

    def topic(self, message_type: str, with_device: bool = False) -> str:
        return sparkplug.topic(self.group_id, message_type, self.edge_node_id,
                               self.device_id if with_device else None)

    def topic_plan(self) -> list:
        """Every topic this device will ever use, for the page to render read-only.

        There is no field to edit any of these. That is the point.
        """
        spec = sparkplug.SPEC_VERSION
        return [
            {"message_type": "NBIRTH", "topic": self.topic("NBIRTH"),
             "qos": sparkplug.DATA_QOS, "retain": sparkplug.DATA_RETAIN,
             "rule": "tck-id-topics-nbirth-mqtt",
             "note": "edge node announces itself; seq MUST be 0 and bdSeq MUST match the "
                     "will"},
            {"message_type": "DBIRTH", "topic": self.topic("DBIRTH", True),
             "qos": sparkplug.DATA_QOS, "retain": sparkplug.DATA_RETAIN,
             "rule": "tck-id-topics-dbirth-mqtt",
             "note": "every metric, with its datatype and units -- this is what builds the "
                     "consumer's tag tree"},
            {"message_type": "DDATA", "topic": self.topic("DDATA", True),
             "qos": sparkplug.DATA_QOS, "retain": sparkplug.DATA_RETAIN,
             "rule": "tck-id-topics-ddata-mqtt",
             "note": "report by exception: only metrics that moved past their deadband"},
            {"message_type": "DDEATH", "topic": self.topic("DDEATH", True),
             "qos": sparkplug.DATA_QOS, "retain": sparkplug.DATA_RETAIN,
             "rule": "tck-id-topics-ddeath-mqtt",
             "note": "device gone while the node is still up -- an ordinary publish, not a "
                     "will"},
            {"message_type": "NDEATH", "topic": self.topic("NDEATH"),
             "qos": sparkplug.WILL_QOS, "retain": sparkplug.WILL_RETAIN,
             "rule": "tck-id-message-flow-edge-node-birth-publish-will-message-qos",
             "note": "registered as the MQTT Will at CONNECT; carries bdSeq and nothing "
                     "else (%s)" % spec},
            {"message_type": "NCMD", "topic": self.topic("NCMD"),
             "qos": sparkplug.DATA_QOS, "retain": sparkplug.DATA_RETAIN,
             "rule": "tck-id-topics-ncmd-mqtt",
             "note": "subscribed, not published. Only %s is honoured -- nothing on the "
                     "backbone can open this valve" % REBIRTH_METRIC},
        ]


# -- metric registry ---------------------------------------------------------------------


class MetricRegistry:
    """Current value of every metric, plus who has moved since the last DDATA.

    Report-by-exception lives here rather than in the publisher because it is a property of
    the *metric* -- the deadband on a line temperature has nothing to do with MQTT.
    """

    def __init__(self, use_aliases: bool) -> None:
        self.use_aliases = use_aliases
        self.definitions = {}
        self.values = {}
        self.aliases = {}
        self.dirty = set()
        for index, (name, datatype, properties, deadband) in enumerate(DEVICE_METRICS, start=1):
            self.definitions[name] = (datatype, properties, deadband)
            self.values[name] = None
            self.aliases[name] = index if use_aliases else None

    def set(self, name: str, value) -> None:
        """Record a value; mark it dirty only if it actually counts as news."""
        if name not in self.definitions:
            logging.warning("no such metric %r", name)
            return
        datatype, _properties, deadband = self.definitions[name]
        previous = self.values[name]
        if previous is not None and value is not None and deadband and \
                datatype in (DataType.Float, DataType.Double):
            if abs(float(value) - float(previous)) < deadband:
                return
        elif previous == value:
            return
        self.values[name] = value
        self.dirty.add(name)

    def birth_metrics(self, timestamp_ms: int) -> list:
        """Every metric, whether it has a value yet or not.

        The ones that do not are published as typed nulls rather than omitted: a consumer
        should learn that `Badge/LastScanId` exists and is a String before anybody has
        badged in, otherwise the tag appears out of nowhere on the first scan.
        """
        self.dirty.clear()
        return [
            Metric(name, self.definitions[name][0], self.values[name],
                   alias=self.aliases[name], timestamp_ms=timestamp_ms,
                   properties=self.definitions[name][1])
            for name in self.definitions
        ]

    def changed_metrics(self, timestamp_ms: int) -> list:
        """What DDATA carries. Empty means nothing is published at all."""
        names = sorted(self.dirty)
        self.dirty.clear()
        return [
            Metric(name, self.definitions[name][0], self.values[name],
                   alias=self.aliases[name], timestamp_ms=timestamp_ms)
            for name in names
        ]


def _ms(ts: float) -> int:
    return int(ts * 1000)


# -- the edge node -----------------------------------------------------------------------


class SparkplugSink(Sink):
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("sparkplug")
        self.registry = MetricRegistry(cfg.use_aliases)
        self.seq = SequenceCounter()
        self.client = None
        self.connected = False
        self.published = 0
        self.rebirths = 0
        self.assembly = None

        # bdSeq: 0 on the first CONNECT, +1 on every one after, and the value in the will
        # must be the value in the NBIRTH that follows it. Tahu wraps at 256; the spec only
        # says "increment by one".
        self.bd_seq = 0
        # Set while we are tearing the session down on purpose, so _on_disconnect does not
        # also bump bdSeq -- a deliberate reconnect must advance it exactly once, not twice.
        self.closing = False

        self.registry.set("Device/FirmwareVersion", cfg.firmware)
        self.registry.set("Device/SerialNumber", cfg.assembly_serial)
        self.registry.set("Device/Cell", cfg.cell)

    # ---- session

    def _ndeath_payload(self) -> bytes:
        # tck-id-topics-ndeath-payload: one metric, bdSeq, and nothing else.
        # tck-id-topics-ndeath-seq: no sequence number.
        return sparkplug.encode_payload(
            [Metric("bdSeq", DataType.Int64, self.bd_seq)], seq=None
        )

    def _arm_will(self, client: mqtt.Client) -> None:
        client.will_set(self.cfg.topic("NDEATH"), self._ndeath_payload(),
                        qos=sparkplug.WILL_QOS, retain=sparkplug.WILL_RETAIN)

    def _build_client(self) -> mqtt.Client:
        cfg = self.cfg
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id="%s-%s" % (cfg.edge_node_id, cfg.device_id),
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(cfg.username, cfg.password)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._arm_will(client)
        return client

    def start(self) -> None:
        self.client = self._build_client()
        self.client.connect_async(self.cfg.broker_host, self.cfg.broker_port, keepalive=30)
        self.client.loop_start()

    def recommission(self) -> None:
        """Re-announce under a new group / node / device id.

        A real edge node being re-commissioned owes its old identity a death certificate,
        or every consumer keeps a stale device online forever. So DDEATH and NDEATH go out
        on the OLD topics before the session is torn down -- a graceful disconnect discards
        the will, so nothing else would ever say so.
        """
        client = self.client
        self.closing = True
        try:
            self.farewell()
            if client is not None:
                client.loop_stop()
                client.disconnect()
            self.connected = False
            self.bd_seq = (self.bd_seq + 1) % 256
        finally:
            self.closing = False
        self.start()

    def farewell(self) -> None:
        """DDEATH then NDEATH, published explicitly.

        A clean MQTT DISCONNECT tells the broker to discard the will, so a graceful shutdown
        that said nothing would leave every consumer believing this node is still alive.
        `docker kill` is the other case, and there the broker publishes the will instead --
        two demos out of the same container.
        """
        if not self.connected:
            return
        now = _ms(time.time())
        self._publish(self.cfg.topic("DDEATH", True),
                      sparkplug.encode_payload([], timestamp_ms=now, seq=self.seq.next()))
        self._publish(self.cfg.topic("NDEATH"), self._ndeath_payload(),
                      qos=sparkplug.WILL_QOS)

    def stop(self) -> None:
        self.closing = True
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
        self.log.info("connected to %s:%s as %s -- edge node %s/%s, device %s, bdSeq %s",
                      self.cfg.broker_host, self.cfg.broker_port, self.cfg.username,
                      self.cfg.group_id, self.cfg.edge_node_id, self.cfg.device_id,
                      self.bd_seq)
        # The only subscription. NCMD is a spec-required inbound path; DCMD is subscribed so
        # a write from a host is logged and visibly refused rather than silently swallowed.
        client.subscribe([(self.cfg.topic("NCMD"), 1), (self.cfg.topic("DCMD", True), 1)])
        self.birth()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self.connected = False
        if self.closing:
            # We are the ones hanging up. recommission() advances bdSeq itself, and a
            # shutdown has no next CONNECT to arm a will for.
            return
        self.log.warning("disconnected (%s) -- paho will retry with backoff", reason_code)
        # Every new CONNECT packet needs a new bdSeq, and the will has to be re-armed with
        # it before paho reconnects.
        self.bd_seq = (self.bd_seq + 1) % 256
        try:
            self._arm_will(client)
        except Exception:
            self.log.exception("could not re-arm the will")

    def _on_message(self, client, userdata, message) -> None:
        try:
            payload = sparkplug.decode_payload(message.payload)
        except Exception:
            self.log.warning("undecodable command on %s", message.topic)
            return

        if message.topic == self.cfg.topic("DCMD", True):
            names = [metric.get("name") for metric in payload["metrics"]]
            # Publish-only device: a host writing to a valve metric is refused, loudly in
            # the log and nowhere else. Nothing on this backbone opens this valve.
            self.log.warning("refused DCMD for %s -- this valve takes no commands", names)
            return

        for metric in payload["metrics"]:
            if metric.get("name") == REBIRTH_METRIC and metric.get("value"):
                self.rebirths += 1
                self.log.info("rebirth requested -- re-announcing")
                self.birth()
                return
        self.log.info("ignoring NCMD %s",
                      [metric.get("name") for metric in payload["metrics"]])

    # ---- birth and data

    def birth(self) -> None:
        """NBIRTH then DBIRTH, in that order, seq restarting at 0.

        This is the moment pattern 1 has no equivalent of. Everything a consumer needs to
        build the whole tag tree -- names, datatypes, engineering units, current values --
        arrives in two messages that nobody had to agree on in advance.
        """
        now = _ms(time.time())
        if self.assembly is not None:
            self._refresh_from(self.assembly)

        node_metrics = [
            Metric("bdSeq", DataType.Int64, self.bd_seq, timestamp_ms=now),
            # Conventionally writable; this device honours it and nothing else.
            Metric(REBIRTH_METRIC, DataType.Boolean, False, timestamp_ms=now),
        ]
        self._publish(self.cfg.topic("NBIRTH"),
                      sparkplug.encode_payload(node_metrics, timestamp_ms=now,
                                               seq=self.seq.reset()))
        self._publish(self.cfg.topic("DBIRTH", True),
                      sparkplug.encode_payload(self.registry.birth_metrics(now),
                                               timestamp_ms=now, seq=self.seq.next()))

    def _publish(self, topic: str, payload: bytes, qos: int = None) -> None:
        client = self.client
        if client is None:
            return
        client.publish(topic, payload,
                       qos=sparkplug.DATA_QOS if qos is None else qos,
                       retain=sparkplug.DATA_RETAIN)
        self.published += 1

    def _flush(self, ts: float) -> None:
        now = _ms(ts)
        metrics = self.registry.changed_metrics(now)
        if not metrics:
            return
        self._publish(
            self.cfg.topic("DDATA", True),
            sparkplug.encode_payload(
                metrics, timestamp_ms=now, seq=self.seq.next(),
                # With aliases in play, DATA carries the alias instead of the name -- the
                # birth certificate is what makes that legible, and a consumer that missed
                # it is the reason Rebirth exists.
                include_names=not self.cfg.use_aliases,
                include_properties=False,
            ),
        )

    def _refresh_from(self, assembly: ValveAssembly) -> None:
        self._apply_state(assembly.state_snapshot())
        self._apply_telemetry(assembly.telemetry_values())

    def _apply_state(self, snapshot: dict) -> None:
        self.registry.set("Valve/State", snapshot["state"])
        self.registry.set("Valve/IsOpen", snapshot["is_open"])
        self.registry.set("Valve/PositionPct", snapshot["position_pct"])
        self.registry.set("Interlock/Ok", snapshot["interlock_ok"])
        self.registry.set("Sample/CycleCount", snapshot["cycle_count"])

    def _apply_telemetry(self, values: dict) -> None:
        self.registry.set("Line/PressureBar", values["line_pressure_bar"])
        self.registry.set("Line/TemperatureC", values["line_temperature_c"])
        self.registry.set("Interlock/Ok", values["interlock_ok"])
        self.registry.set("Sample/CycleCount", values["valve_cycles_total"])

    # ---- Sink

    def valve_event(self, event: str, data: dict, ts: float) -> None:
        if event == "badge-scan":
            self.registry.set("Badge/LastScanId", data["badge_id"])
            self.registry.set("Badge/LastScanHolder", data["badge_holder"])
            self.registry.set("Badge/LastScanRole", data["badge_role"])
            self.registry.set("Badge/LastScanResult", data["result"])
            # A String metric with no value is a typed null, not an empty string -- "no
            # denial reason" and "denied for reason ''" are different facts.
            self.registry.set("Badge/LastDenyReason", data["deny_reason"])
            self.registry.set("Badge/LastScanTime", _ms(ts))
            if data["sample_id"]:
                self.registry.set("Sample/LastSampleId", data["sample_id"])
        elif event == "sample-complete":
            self.registry.set("Sample/LastSampleId", data["sample_id"])
            self.registry.set("Sample/LastSampleTime", _ms(ts))
            self.registry.set("Sample/LastOpenDurationS", data["open_duration_s"])
            self.registry.set("Sample/CycleCount", data["cycle_count"])
        self._flush(ts)

    def valve_state(self, snapshot: dict, ts: float) -> None:
        self._apply_state(snapshot)
        self._flush(ts)

    def valve_telemetry(self, values: dict, ts: float) -> None:
        self._apply_telemetry(values)
        self._flush(ts)


# -- the config page's view of the device ------------------------------------------------


class Provider(webui.ConfigProvider):
    def __init__(self, cfg: Config, sink: SparkplugSink, assembly: ValveAssembly) -> None:
        self.cfg = cfg
        self.sink = sink
        self.assembly = assembly

    def state(self) -> dict:
        cfg = self.cfg
        return {
            "variant": "sparkplug-b",
            "spec": sparkplug.SPEC_VERSION,
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
                "protocol": "MQTT 3.1.1 / %s" % sparkplug.SPEC_VERSION,
                "connected": self.sink.connected,
            },
            # Names inside a namespace, not the namespace itself.
            "config": {
                "group_id": cfg.group_id,
                "edge_node_id": cfg.edge_node_id,
                "device_id": cfg.device_id,
            },
            # Everything below is derived and read-only. The page renders it; nobody edits it.
            "namespace": "spBv1.0/{group_id}/{message_type}/{edge_node_id}/{device_id}",
            "topic_plan": cfg.topic_plan(),
            "fixed": {
                "data_qos": sparkplug.DATA_QOS,
                "data_retain": sparkplug.DATA_RETAIN,
                "will_qos": sparkplug.WILL_QOS,
                "will_retain": sparkplug.WILL_RETAIN,
                "qos_rule": "tck-id-topics-ddata-mqtt -- DDATA messages MUST be published "
                            "with MQTT QoS equal to 0 and retain equal to false",
                "will_rule": "tck-id-message-flow-edge-node-birth-publish-will-message-qos "
                             "-- the Edge Node's MQTT Will Message's MQTT QoS MUST be 1",
            },
            "metrics": [
                {"name": name,
                 "datatype": sparkplug.DATATYPE_NAMES.get(datatype, str(datatype)),
                 "alias": self.sink.registry.aliases[name],
                 "deadband": deadband,
                 "unit": (properties or {}).get("engUnit", (None, None))[1],
                 "value": self.sink.registry.values[name]}
                for name, datatype, properties, deadband in DEVICE_METRICS
            ],
            "runtime": {
                "valve": self.assembly.state_snapshot(),
                "telemetry": self.assembly.telemetry_values(),
                "last_scan": self.assembly.last_scan,
                "published": self.sink.published,
                "bd_seq": self.sink.bd_seq,
                "rebirths": self.sink.rebirths,
                "use_aliases": cfg.use_aliases,
            },
            "roster": [badge.as_dict() for badge in cfg.roster.values()]
                      + [{"badge_id": cfg.unknown_badge_id, "holder": "not on roster",
                          "role": "unknown", "status": "unknown"}],
        }

    def apply(self, payload: dict):
        values = {}
        for key in Config.COMMISSIONABLE:
            value = str(payload.get(key, getattr(self.cfg, key))).strip()
            if not value:
                return False, "%s cannot be empty" % key
            # tck-id-topics-*: the four topic elements are single tokens. A `/` here would
            # invent a level the namespace does not have, and a `+` or `#` would make the
            # topic unpublishable. Pattern 1's topic box accepts anything at all.
            if any(character in value for character in "/+#"):
                return False, "%s may not contain / + or #" % key
            values[key] = value

        if all(getattr(self.cfg, key) == value for key, value in values.items()):
            return True, "unchanged"

        for key, value in values.items():
            setattr(self.cfg, key, value)
        self.cfg.save()
        self.sink.recommission()
        return True, ("re-announced as %s / %s / %s -- the old identity was given a DDEATH "
                      "and an NDEATH first, so no consumer is left holding a device that "
                      "no longer exists"
                      % (values["group_id"], values["edge_node_id"], values["device_id"]))

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
    sink = SparkplugSink(cfg)
    assembly = ValveAssembly(cfg, sink)
    sink.assembly = assembly
    # Prime the registry from the valve's actual position, so the config page and the very
    # first DBIRTH both describe the device rather than a table of nulls.
    sink._refresh_from(assembly)

    page = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
    webui.serve(cfg.ui_port, page, Provider(cfg, sink, assembly))
    sink.start()

    logging.info("sparkplug sample valve up: %s / %s / %s on %s, %s metrics, aliases %s",
                 cfg.group_id, cfg.edge_node_id, cfg.device_id, cfg.cell,
                 len(DEVICE_METRICS), "on" if cfg.use_aliases else "off")

    signal.signal(signal.SIGTERM, lambda *_: assembly.stop())
    signal.signal(signal.SIGINT, lambda *_: assembly.stop())
    try:
        assembly.run()
    finally:
        sink.farewell()
        time.sleep(0.2)
        sink.stop()
        logging.info("shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
