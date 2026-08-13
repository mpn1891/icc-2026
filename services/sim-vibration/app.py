#!/usr/bin/env python3
"""Pattern 1 — a simulated wireless vibration gateway speaking native MQTT 3.1.1.

One gateway (serial 12345678), four channels, only channel 0 provisioned: the agitator
drive-end bearing on BR-201. See docs/plans/01-native-mqtt.md for the contract this
implements and why it is shaped this way.

Four things here are deliberate and easy to mistake for oversights:

  * There is no ack, no state and no Last Will. The gateway publishes nothing about itself:
    a collect is answered by its waveform arriving, or by nothing at all, and a rejected
    command is visible only in this container's log. A subscriber notices a dead gateway by
    the telemetry stopping. That silence is the constraint being demonstrated -- it is
    exactly what Sparkplug's NDEATH/DDEATH exist to solve, and pattern 2 solves it.

  * The waveform goes to one fleet response topic, not to the sensor's own branch, because
    it is a command *response*. gw_serial + channel_index in `meta` are what the consumer
    demuxes on. Only unsolicited telemetry is addressed at the machine.

  * There is no correlation id. The PM system needs a waveform and the time it was captured,
    not request lineage; a retry and a fresh request are equivalent to it. `ts` is the
    capture start, `meta.request` echoes the settings, and that is the whole story.

  * The inbound command carries no envelope. It is the vendor's own JSON, byte-for-byte,
    because a real gateway would drop anything else.

Standard library plus paho-mqtt, nothing more. The synthesis is a few milliseconds of pure
Python and every dependency dropped is one fewer wheel to fetch on a conference network.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

MECHANISM = "native-mqtt"
GRAVITY_MM_S2 = 9806.65


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
        self.username = _env("MQTT_USERNAME", "vib-gateway")
        self.password = _env("MQTT_PASSWORD", "vib-gateway")

        self.gw_serial = _env("GW_SERIAL", "12345678")
        self.gw_id = _env("GW_ID", "vib-gw-01")

        # The gateway is a radio concentrator, not process equipment -- it does not sit in a
        # cell. The collect command arrives on one fleet topic and its waveform goes back on
        # one fleet response topic; only unsolicited telemetry is addressed at the machine the
        # sensor is bolted to. See Config.device_topic.
        self.area_root = _env("AREA_ROOT", "icc26/site1/upstream").rstrip("/")
        self.fleet_id = _env("FLEET_ID", "vibration-gw")
        self.control_topic = _env(
            "CONTROL_TOPIC", f"{self.area_root}/{self.fleet_id}/cmd/collect"
        )
        self.response_waveform_topic = _env(
            "RESPONSE_WAVEFORM_TOPIC", f"{self.area_root}/{self.fleet_id}/response/waveform"
        )

        # "0:br-201:agitator-vib,1:br-305:pump-vib" -- index:cell:device. One gateway serves
        # several skids, so the cell is per channel and NOT a property of the gateway.
        # Channels absent from this map are unprovisioned, which is a real state the gateway
        # has to answer for, not an error.
        self.channels: dict[int, tuple[str, str]] = {}
        for entry in _env("CHANNELS", "0:br-201:agitator-vib").split(","):
            entry = entry.strip()
            if not entry:
                continue
            index, cell, device = (part.strip() for part in entry.split(":", 2))
            self.channels[int(index)] = (cell, device)
        self.channel_count = _env_int("CHANNEL_COUNT", 4)

        self.telemetry_interval_s = _env_float("TELEMETRY_INTERVAL_S", 5.0)
        self.sample_rate_hz = _env_int("SAMPLE_RATE_HZ", 32000)
        self.max_lor = _env_int("MAX_LOR", 65536)
        self.capture_overhead_s = _env_float("CAPTURE_OVERHEAD_S", 0.5)

        self.shaft_rpm = _env_float("SHAFT_RPM", 1780.0)
        self.severity_start = _env_float("DEFECT_SEVERITY_START", 0.05)
        self.severity_end = _env_float("DEFECT_SEVERITY_END", 0.9)
        self.severity_ramp_s = _env_float("DEFECT_RAMP_S", 1800.0)

    # Unsolicited sensor data is addressed at the machine the sensor is mounted on, never at
    # the gateway -- one concentrator serves several cells and is not a location itself.
    def device_topic(self, cell: str, device: str, message_type: str) -> str:
        return f"{self.area_root}/{cell}/{device}/{message_type}"


# ── the bearing ──────────────────────────────────────────────────────────────────────────


class BearingModel:
    """Outer-race defect on a rotating shaft, in acceleration (g).

    Imbalance at 1x, misalignment at 2x and 3x, plus a BPFO impulse train where each impact
    rings the bearing housing resonance. The impacts are modulated at shaft frequency (the
    defect passes through the load zone once per revolution) and jittered by rolling-element
    slip -- which is what stops an FFT of this looking synthetic. The resulting spectrum has
    a BPFO peak with +/-1x sidebands, which is what an analyst would expect to see.

    One model drives both the telemetry statistics and the on-demand waveform, so when the
    simulated defect grows, RMS climbs *and* the next waveform shows deeper impacts.
    """

    BPFO_RATIO = 3.585  # 8-ball deep-groove, outer race
    RESONANCE_HZ = 1500.0  # housing mode rung by each impact; well under Nyquist at 6400
    DAMPING = 0.05
    SLIP = 0.01
    LOAD_ZONE_MODULATION = 0.6

    A_1X = 0.08  # g, imbalance
    A_2X = 0.03  # g, misalignment
    A_3X = 0.015  # g
    NOISE_G = 0.012

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.started = time.time()
        self.rng = random.Random(0xB1A5)

    def severity(self) -> float:
        """Defect growth, 0..1. DEFECT_RAMP_S=0 pins it -- use that for a stage run."""
        if self.cfg.severity_ramp_s <= 0:
            return self.cfg.severity_end
        frac = min(1.0, (time.time() - self.started) / self.cfg.severity_ramp_s)
        return self.cfg.severity_start + frac * (self.cfg.severity_end - self.cfg.severity_start)

    def block(self, n: int) -> list[float]:
        """n samples of acceleration in g at cfg.sample_rate_hz."""
        fs = float(self.cfg.sample_rate_hz)
        dt = 1.0 / fs
        f_shaft = self.cfg.shaft_rpm / 60.0
        sev = self.severity()
        rng = self.rng

        # Fresh phases per block: consecutive captures of a real machine are not
        # phase-locked to each other.
        p1, p2, p3 = (rng.uniform(0.0, 2.0 * math.pi) for _ in range(3))

        w1 = 2.0 * math.pi * f_shaft
        samples = [0.0] * n
        for i in range(n):
            t = i * dt
            samples[i] = (
                self.A_1X * math.sin(w1 * t + p1)
                + self.A_2X * math.sin(2.0 * w1 * t + p2)
                + self.A_3X * math.sin(3.0 * w1 * t + p3)
                + rng.gauss(0.0, self.NOISE_G)
            )

        # BPFO impulse train. tau is the resonance decay constant; five of them is where the
        # ring has died into the noise, so that is where each impulse response is truncated.
        bpfo = self.BPFO_RATIO * f_shaft
        tau = 1.0 / (self.DAMPING * 2.0 * math.pi * self.RESONANCE_HZ)
        window = min(n, int(5.0 * tau * fs) + 1)
        w_res = 2.0 * math.pi * self.RESONANCE_HZ
        duration = n * dt

        k = 0
        while True:
            t_k = (k / bpfo) * (1.0 + rng.uniform(-self.SLIP, self.SLIP))
            k += 1
            if t_k >= duration:
                break
            amplitude = sev * (1.0 + self.LOAD_ZONE_MODULATION * math.sin(w1 * t_k))
            start = int(t_k * fs)
            for j in range(min(window, n - start)):
                t = j * dt
                samples[start + j] += amplitude * math.exp(-t / tau) * math.sin(w_res * t)

        return samples


def velocity_rms_mm_s(accel_g: list[float], fs: int) -> float:
    """Overall velocity RMS, the way an analyser gets it: integrate, detrend, RMS.

    Integrating acceleration introduces a ramp that has nothing to do with the machine, so
    the linear trend comes back out before the RMS. Deriving this rather than inventing a
    number is what keeps telemetry honest against the published samples.
    """
    if len(accel_g) < 2:
        return 0.0

    dt = 1.0 / fs
    velocity: list[float] = []
    running = 0.0
    prev = accel_g[0] * GRAVITY_MM_S2
    for value in accel_g[1:]:
        current = value * GRAVITY_MM_S2
        running += 0.5 * (prev + current) * dt
        velocity.append(running)
        prev = current

    n = len(velocity)
    mean_i = (n - 1) / 2.0
    mean_v = sum(velocity) / n
    denominator = sum((i - mean_i) ** 2 for i in range(n))
    slope = (
        sum((i - mean_i) * (v - mean_v) for i, v in enumerate(velocity)) / denominator
        if denominator
        else 0.0
    )

    total = 0.0
    for i, v in enumerate(velocity):
        residual = v - (mean_v + slope * (i - mean_i))
        total += residual * residual
    return math.sqrt(total / n)


# ── envelope ─────────────────────────────────────────────────────────────────────────────


def _iso(epoch: float) -> str:
    dt = datetime.fromtimestamp(epoch, timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


_seq_lock = threading.Lock()
_seq = 0


def _next_seq() -> int:
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def envelope(source_id: str, source_type: str, values: dict, ts: float | None = None,
             meta: dict | None = None) -> dict:
    """The standard envelope from docs/00-architecture.md.

    `ts` is when the thing happened -- for a waveform that is the capture start, which is
    deliberately not the publish time. `meta.ingest_ts` is when the payload was built, so the
    gap between the two is the real capture duration and is visible on the firehose.
    """
    now = time.time()
    return {
        "ts": _iso(ts if ts is not None else now),
        "seq": _next_seq(),
        "source": {"id": source_id, "type": source_type},
        "meta": {"mechanism": MECHANISM, "ingest_ts": _iso(now), **(meta or {})},
        "values": values,
    }


# ── the gateway ──────────────────────────────────────────────────────────────────────────


class Gateway:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log = logging.getLogger("vib-gateway")
        self.model = BearingModel(cfg)
        self.stopping = threading.Event()
        self.started = time.time()

        self._busy: set[int] = set()
        self._busy_lock = threading.Lock()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.gw_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        self.client.username_pw_set(cfg.username, cfg.password)
        # Paho's own backoff is exponential with jitter and applies to the initial connect
        # too, so there is nothing worth hand-rolling here. No depends_on in compose either
        # -- the broker not being up yet is this app's problem to absorb.
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

        # No Last Will and no birth/death. This gateway publishes no liveness at all -- a
        # collect is answered by its waveform arriving on the response topic, or by nothing.

    # ---- mqtt plumbing

    def _publish(self, topic: str, document: dict, qos: int = 1, retain: bool = False) -> None:
        self.client.publish(
            topic, json.dumps(document, separators=(",", ":")), qos=qos, retain=retain
        )

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            self.log.error("connect refused: %s", reason_code)
            return

        self.log.info("connected to %s:%s as %s", self.cfg.broker_host, self.cfg.broker_port,
                      self.cfg.username)
        client.subscribe(self.cfg.control_topic, qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if not self.stopping.is_set():
            self.log.warning("disconnected (%s) -- paho will retry with backoff", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        try:
            request = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.log.warning("undecodable command on %s", message.topic)
            return
        if not isinstance(request, dict):
            self.log.warning("command on %s was not a JSON object", message.topic)
            return

        # Every gateway on the fleet hears every command. Silence is the correct response to
        # somebody else's serial -- no ack, no log line worth reading.
        if str(request.get("gwSerial", "")) != self.cfg.gw_serial:
            return

        self._handle_collect(request)

    # ---- command handling

    def _reject(self, request: dict, reason: str, channel_index=None) -> None:
        """Log-only. There is no ack topic, so a rejected collect is visible in this
        container's log and nowhere else."""
        self.log.info("rejected collect: %s (ch%s) %s", reason, channel_index, request)

    def _handle_collect(self, request: dict) -> None:
        if request.get("type") != "observerrequest":
            return self._reject(request, "malformed-request")
        if request.get("action") != "wiredcollectnow":
            return self._reject(request, "unsupported-action")

        index = request.get("channelIndex")
        if not isinstance(index, int) or isinstance(index, bool) or not (
            0 <= index < self.cfg.channel_count
        ):
            return self._reject(request, "unknown-channel", index)
        if index not in self.cfg.channels:
            return self._reject(request, "not-provisioned", index)

        settings = request.get("settings") or {}
        lor = settings.get("lor")
        if not isinstance(lor, int) or isinstance(lor, bool) or lor < 512 \
                or lor > self.cfg.max_lor or (lor & (lor - 1)) != 0:
            return self._reject(request, "invalid-lor", index)

        with self._busy_lock:
            if index in self._busy:
                return self._reject(request, "busy", index)
            self._busy.add(index)

        # Off the network thread: a 65536-point synthesis plus its capture wait must never
        # stall paho's loop, or the keepalive goes with it.
        cell, device = self.cfg.channels[index]
        threading.Thread(
            target=self._capture,
            args=(index, cell, device, lor, settings),
            name=f"capture-ch{index}",
            daemon=True,
        ).start()

    def _capture(self, index: int, cell: str, device: str, lor: int, settings: dict) -> None:
        try:
            fs = self.cfg.sample_rate_hz
            duration_s = lor / fs
            capture_start = time.time()

            # A real gateway cannot return a record faster than the record is long, and then
            # spends radio time on top. Honouring that is what makes the request/response
            # genuinely asynchronous rather than a same-millisecond echo.
            if self.stopping.wait(duration_s + self.cfg.capture_overhead_s):
                return

            samples = self.model.block(lor)
            # The response goes on the single fleet topic, NOT under the sensor's cell.
            # gw_serial and channel_index are what the vibration-gw-listener event stream
            # demuxes on to find the tag, so they are the contract -- cell and device ride
            # along for a human reading the firehose, and nothing keys off them.
            self._publish(
                self.cfg.response_waveform_topic,
                {
                    "datatype": "wiredCollection",
                    "wiredChannel": index,
                    "gwSerial": self.cfg.gw_serial,
                    "timestamp": _iso(capture_start),
                    "sensorType": "accel",
                    "sampleRate": fs,
                    "data": [round(value, 5) for value in samples],
                },
            )
            self.log.info("published %s-sample waveform for ch%s (%s)", lor, index, device)
        except Exception:
            self.log.exception("capture failed on ch%s", index)
        finally:
            with self._busy_lock:
                self._busy.discard(index)

    # ---- telemetry

    def _telemetry_once(self) -> None:
        fs = self.cfg.sample_rate_hz
        block = self.model.block(min(1024, fs))
        peak = max(abs(value) for value in block)
        rms_g = math.sqrt(sum(value * value for value in block) / len(block))
        severity = self.model.severity()
        hours_up = (time.time() - self.started) / 3600.0

        for index, (cell, device) in sorted(self.cfg.channels.items()):
            self._publish(
                self.cfg.device_topic(cell, device, "telemetry"),
                envelope(device, "vibration-sensor", {
                    "rms_velocity_mm_s": round(velocity_rms_mm_s(block, fs), 3),
                    "peak_accel_g": round(peak, 4),
                    "crest_factor": round(peak / rms_g, 2) if rms_g else 0.0,
                    "temperature_c": round(42.0 + 6.0 * severity + random.gauss(0.0, 0.15), 2),
                    "battery_v": round(max(3.0, 3.6 - hours_up * 0.0005), 3),
                    "shaft_rpm": self.cfg.shaft_rpm,
                }, meta={
                    "gw_serial": self.cfg.gw_serial,
                    "channel_index": index,
                }),
                qos=0,  # periodic and disposable -- QoS 0 is the honest choice for it
            )

    # ---- lifecycle

    def run(self) -> None:
        self.client.connect_async(self.cfg.broker_host, self.cfg.broker_port, keepalive=30)
        self.client.loop_start()
        self.log.info(
            "gateway %s (serial %s) up: channels %s provisioned of %s, control topic %s",
            self.cfg.gw_id, self.cfg.gw_serial, sorted(self.cfg.channels),
            self.cfg.channel_count, self.cfg.control_topic,
        )
        while not self.stopping.wait(self.cfg.telemetry_interval_s):
            try:
                self._telemetry_once()
            except Exception:
                self.log.exception("telemetry tick failed")

    def shutdown(self) -> None:
        """Graceful death. Nothing is announced -- this gateway publishes no liveness, so
        going away is silent and a subscriber notices only by the telemetry stopping."""
        self.stopping.set()
        self.client.loop_stop()
        self.client.disconnect()
        self.log.info("shutdown complete")


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )
    gateway = Gateway(Config())
    signal.signal(signal.SIGTERM, lambda *_: gateway.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: gateway.stopping.set())
    try:
        gateway.run()
    finally:
        gateway.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
