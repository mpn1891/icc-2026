"""Pattern 1 -- simulated wireless vibration gateway, hosted inside Ignition.

Stands in for a 4-channel wireless gateway (serial 12345678) wired to the agitator
drive-end bearing on BR-201. Only channel 0 is provisioned; a collect aimed at 1-3 is
rejected to the gateway log and nothing is published, which is the pattern's negative path.

The gateway publishes no ack and no state -- a collect is answered by the waveform itself
arriving on the response topic, or by nothing at all.

Everything published here is plain MQTT on the ISA-95 namespace -- your topic, your JSON.
`system.cirruslink.transmission.publish` is a general publish API reached by RPC into the
gateway-scoped module; nothing about this pattern is Sparkplug.

Wiring (see docs/plans/01-native-mqtt.md):

    Gateway Timer Script, 5000 ms   ->  vibsim.telemetry_tick()
    Event Stream "vibration-gw-control" ->  vibsim.handle_collect(event.data)

There is no birth/death here on purpose. A Last Will is registered in the MQTT CONNECT
packet, so only the client that owns the session can set one -- and Ignition's session
belongs to the Transmission module. Publish APIs send messages on a session that already
exists. The pattern runs as a live stream instead.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

import math
import random

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

# ── configuration ────────────────────────────────────────────────────────────────────────
# The counterpart of the container build's environment variables. Edit here and the change
# takes effect on the next script save -- no gateway restart.

BROKER = "chariot_broker"

GW_SERIAL = "12345678"
GW_ID = "vib-gw-01"

# The gateway is a radio concentrator, not process equipment -- it does not sit in a cell.
# Three topics, one area root:
#
#   <AREA_ROOT>/<FLEET_ID>/cmd/collect        broadcast in; every gateway hears it and
#                                             self-selects on gwSerial
#   <AREA_ROOT>/<FLEET_ID>/response/waveform  the answer to that command, on one fleet topic.
#                                             gwSerial + channelIndex are echoed in meta and
#                                             the event stream demuxes them to the right tag.
#   <AREA_ROOT>/<cell>/<device>/telemetry     unsolicited periodic data, addressed at the
#                                             machine the sensor is bolted to
AREA_ROOT = "icc26/site1/upstream"
FLEET_ID = "vibration-gw"
CONTROL_TOPIC = "%s/%s/cmd/collect" % (AREA_ROOT, FLEET_ID)
RESPONSE_WAVEFORM_TOPIC = "%s/%s/response/waveform" % (AREA_ROOT, FLEET_ID)

# (gw_serial, channel_index) -> UDT instance base path. This is the demux table: the response
# topic carries every gateway's waveforms, and this is what turns the two payload fields back
# into one sensor's tags. A sensor missing from here is dropped with a log line -- deliberately
# not an error, because another gateway's traffic on the shared topic is not this map's problem.
SENSOR_TAGS = {
    ("12345678", 0): "[default]icc26/site/area/process_area/reactors/reactor1"
                     "/asset_data/agitator_vibration",
}

# channel index -> (cell, device id). One gateway serves several skids, so the cell is per
# channel and NOT a property of the gateway. Anything absent is physically present but
# unprovisioned. Both ids must exist in plant.equipment (compose/postgres/initdb/03-seed.sql).
CHANNELS = {0: ("br-201", "agitator-vib")}
CHANNEL_COUNT = 4

SAMPLE_RATE_HZ = 6400  # Fmax 2.5 kHz at the standard 2.56x
MAX_LOR = 65536        # 4096 is ~34 KB on the wire; 32768 is ~267 KB
SHAFT_RPM = 1780.0

# Bearing model. See the header of services/sim-vibration/app.py for the derivation --
# this is a Jython port of a model that was validated against a naive DFT offline.
BPFO_RATIO = 3.585     # 8-ball deep-groove bearing, outer race -> 106.4 Hz at 1780 rpm
RESONANCE_HZ = 1500.0  # housing mode rung by each impact; ~4.3 samples/cycle at 6400
DAMPING = 0.05
SLIP = 0.01            # rolling-element slip, +/- 1% -- what stops an FFT looking synthetic
LOAD_ZONE_MODULATION = 0.6

A_1X = 0.08    # g, imbalance
A_2X = 0.03    # g, misalignment
A_3X = 0.015   # g
NOISE_G = 0.012

DEFECT_SEVERITY_START = 0.05
DEFECT_SEVERITY_END = 0.9
DEFECT_RAMP_S = 1800.0  # set 0.0 to pin severity at END for a reproducible stage run

# A collect blocks its caller for the record length, because a real device cannot return a
# record faster than the record is long. At lor=4096 that is 0.64 s on the event stream
# handler thread. Set False if that ever backs the stream up.
SIMULATE_CAPTURE_TIME = True

GRAVITY_MM_S2 = 9806.65
LOGGER_NAME = "vibsim"

# Module-level state. Jython holds this for the life of the script module, so it resets on
# a script save or a gateway restart -- which is harmless for both of these.
_seq = [0]
_started_ms = [None]


# ── envelope ─────────────────────────────────────────────────────────────────────────────


def _iso(date=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe, hence per-call."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if date is None:
        date = Date()
    return formatter.format(date)


def _next_seq():
    _seq[0] += 1
    return _seq[0]


def _envelope(source_id, source_type, values, ts=None, meta=None):
    """The standard envelope from docs/00-architecture.md.

    `ts` is when the thing happened -- for a waveform that is the capture start, which is
    deliberately not the publish time. `meta.ingest_ts` is when the payload was built, so the
    gap between the two is the capture duration and is visible on the firehose.
    """
    metadata = {"mechanism": "native-mqtt", "ingest_ts": _iso()}
    if meta:
        metadata.update(meta)
    return {
        "ts": _iso(ts),
        "seq": _next_seq(),
        "source": {"id": source_id, "type": source_type},
        "meta": metadata,
        "values": values,
    }


def _device_topic(cell, device, message_type):
    """Sensor data is addressed at the machine the sensor is mounted on, never at the
    gateway -- one concentrator serves several cells and is not a location itself."""
    return "%s/%s/%s/%s" % (AREA_ROOT, cell, device, message_type)


def _publish(topic, document, qos=1, retain=False):
    """Plain MQTT publish -- arbitrary topic, arbitrary JSON, no Sparkplug encoding.

    publish() neither raises nor returns a status when Transmission drops the message; it
    logs its own warning under ClientsManager. A clean return means handed to the module,
    NOT delivered to the broker.
    """
    system.cirruslink.transmission.publish(
        BROKER, topic, system.util.jsonEncode(document), qos, retain
    )


# ── the bearing ──────────────────────────────────────────────────────────────────────────


def _severity():
    """Defect growth, 0..1. Ramps so a gateway left running through a rehearsal shows a
    visibly worse bearing than one just started. DEFECT_RAMP_S = 0 pins it."""
    now = system.date.now().getTime()
    if _started_ms[0] is None:
        _started_ms[0] = now
    if DEFECT_RAMP_S <= 0:
        return DEFECT_SEVERITY_END
    elapsed = (now - _started_ms[0]) / 1000.0
    fraction = min(1.0, elapsed / DEFECT_RAMP_S)
    return DEFECT_SEVERITY_START + fraction * (DEFECT_SEVERITY_END - DEFECT_SEVERITY_START)


def bearing_block(n):
    """n samples of acceleration in g at SAMPLE_RATE_HZ.

    Outer-race defect: imbalance at 1x, misalignment at 2x and 3x, plus a BPFO impulse train
    where each impact rings the housing resonance. Impacts are modulated at shaft frequency
    (the defect passes through the load zone once per revolution) and jittered by
    rolling-element slip. An FFT shows a BPFO peak with +/-1x sidebands.
    """
    fs = float(SAMPLE_RATE_HZ)
    dt = 1.0 / fs
    f_shaft = SHAFT_RPM / 60.0
    severity = _severity()

    # Fresh phases per block -- consecutive captures of a real machine are not phase-locked.
    p1 = random.uniform(0.0, 2.0 * math.pi)
    p2 = random.uniform(0.0, 2.0 * math.pi)
    p3 = random.uniform(0.0, 2.0 * math.pi)

    w1 = 2.0 * math.pi * f_shaft
    samples = [0.0] * n
    for i in range(n):
        t = i * dt
        samples[i] = (
            A_1X * math.sin(w1 * t + p1)
            + A_2X * math.sin(2.0 * w1 * t + p2)
            + A_3X * math.sin(3.0 * w1 * t + p3)
            + random.gauss(0.0, NOISE_G)
        )

    # BPFO impulse train. tau is the resonance decay constant; five of them is where the ring
    # has died into the noise, so that is where each impulse response is truncated.
    bpfo = BPFO_RATIO * f_shaft
    tau = 1.0 / (DAMPING * 2.0 * math.pi * RESONANCE_HZ)
    window = min(n, int(5.0 * tau * fs) + 1)
    w_res = 2.0 * math.pi * RESONANCE_HZ
    duration = n * dt

    k = 0
    while True:
        t_k = (k / bpfo) * (1.0 + random.uniform(-SLIP, SLIP))
        k += 1
        if t_k >= duration:
            break
        amplitude = severity * (1.0 + LOAD_ZONE_MODULATION * math.sin(w1 * t_k))
        start = int(t_k * fs)
        for j in range(min(window, n - start)):
            t = j * dt
            samples[start + j] += amplitude * math.exp(-t / tau) * math.sin(w_res * t)

    return samples


def velocity_rms_mm_s(accel_g, fs):
    """Overall velocity RMS the way an analyser gets it: integrate, detrend, RMS.

    Integrating acceleration introduces a ramp that has nothing to do with the machine, so
    the linear trend comes back out before the RMS. Deriving this rather than inventing a
    number is what keeps telemetry consistent with the samples actually published.
    """
    if len(accel_g) < 2:
        return 0.0

    dt = 1.0 / fs
    velocity = []
    running = 0.0
    previous = accel_g[0] * GRAVITY_MM_S2
    for value in accel_g[1:]:
        current = value * GRAVITY_MM_S2
        running += 0.5 * (previous + current) * dt
        velocity.append(running)
        previous = current

    n = len(velocity)
    mean_i = (n - 1) / 2.0
    mean_v = sum(velocity) / n
    denominator = sum([(i - mean_i) ** 2 for i in range(n)])
    if denominator:
        numerator = sum([(i - mean_i) * (velocity[i] - mean_v) for i in range(n)])
        slope = numerator / denominator
    else:
        slope = 0.0

    total = 0.0
    for i in range(n):
        residual = velocity[i] - (mean_v + slope * (i - mean_i))
        total += residual * residual
    return math.sqrt(total / n)


# ── telemetry ────────────────────────────────────────────────────────────────────────────


def telemetry_tick():
    """Gateway Timer Script, 5000 ms, fixed delay, gateway scope.

    Telemetry is computed from the same model as the waveform -- a short block reduced to
    statistics -- so when the defect grows, RMS climbs AND the next waveform shows deeper
    impacts. One story instead of two.
    """
    block = bearing_block(1024)
    peak = max([abs(v) for v in block])
    rms_g = math.sqrt(sum([v * v for v in block]) / len(block))
    severity = _severity()

    if rms_g:
        crest = round(peak / rms_g, 2)
    else:
        crest = 0.0

    for index in sorted(CHANNELS.keys()):
        cell, device = CHANNELS[index]
        _publish(
            _device_topic(cell, device, "telemetry"),
            _envelope(device, "vibration-sensor", {
                "rms_velocity_mm_s": round(velocity_rms_mm_s(block, SAMPLE_RATE_HZ), 3),
                "peak_accel_g": round(peak, 4),
                "crest_factor": crest,
                "temperature_c": round(42.0 + 6.0 * severity + random.gauss(0.0, 0.15), 2),
                "shaft_rpm": SHAFT_RPM,
            }, meta={
                "gw_serial": GW_SERIAL,
                "channel_index": index,
            }),
            qos=0,  # periodic and disposable -- QoS 0 is the honest choice for it
        )


# ── collect command ──────────────────────────────────────────────────────────────────────


def _as_int(value):
    """Coerce a decoded-JSON number to a Python int, or None if it isn't one.

    Depends what the event stream hands us: a Python int, a java.lang.Integer, or a
    java.lang.Long all mean the same thing here. Booleans are rejected -- Python's bool is a
    subclass of int, so `channelIndex: true` would otherwise sail through as channel 1.
    """
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_mapping(payload):
    """Return something with .get(), or None.

    The `ignition.jsonObject` encoder may deliver a Python dict, a java.util.Map, or -- if the
    stream is configured for a string encoder instead -- raw JSON text. Duck-typing on .get()
    covers the first two; the third is decoded explicitly. isinstance(x, dict) would reject a
    perfectly good java.util.Map, which is exactly the bug this avoids.
    """
    if payload is None:
        return None
    if isinstance(payload, basestring):  # noqa: F821 -- Jython 2.7
        try:
            payload = system.util.jsonDecode(payload)
        except Exception:
            return None
    if hasattr(payload, "get"):
        return payload
    return None


def _reject(request, reason, channel_index=None):
    """Log-only. The gateway does not answer on the wire -- there is no ack topic, so a
    rejected collect is visible in the gateway log and nowhere else."""
    system.util.getLogger(LOGGER_NAME).infof(
        "rejected collect: %s (ch%s)", reason, channel_index
    )


def handle_collect(request):
    """Event Stream handler for CONTROL_TOPIC. `request` is the decoded JSON object.

    The control topic is fleet-addressed: every gateway on site subscribes to it and ignores
    payloads whose gwSerial is not its own, then resolves the sensor from channelIndex. That
    is how the real hardware is configured, and it is the one place in this demo where the
    device address lives in the payload instead of the topic.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    request = _as_mapping(request)
    if request is None:
        logger.warn("collect payload was not a decodable JSON object")
        return

    # Silence is the correct response to somebody else's serial -- no ack, no log line.
    if str(request.get("gwSerial", "")) != GW_SERIAL:
        return

    if str(request.get("type")) != "observerrequest":
        return _reject(request, "malformed-request")
    if str(request.get("action")) != "wiredcollectnow":
        return _reject(request, "unsupported-action")

    index = _as_int(request.get("channelIndex"))
    if index is None or index < 0 or index >= CHANNEL_COUNT:
        return _reject(request, "unknown-channel", index)
    if index not in CHANNELS:
        return _reject(request, "not-provisioned", index)

    settings = _as_mapping(request.get("settings"))
    lor = _as_int(settings.get("lor")) if settings is not None else None
    if lor is None or lor < 512 or lor > MAX_LOR or (lor & (lor - 1)) != 0:
        return _reject(request, "invalid-lor", index)

    cell, device = CHANNELS[index]
    duration_s = float(lor) / SAMPLE_RATE_HZ

    capture_start = system.date.now()
    if SIMULATE_CAPTURE_TIME:
        # A device cannot return a record faster than the record is long. Honouring that is
        # what makes the request/response genuinely asynchronous rather than an echo.
        system.util.sleep(int(duration_s * 1000))

    samples = bearing_block(lor)
    # The response goes on the single fleet topic, NOT under the sensor's cell. gw_serial and
    # channel_index are what the pm-sensor-listener event stream demuxes on to find the tag,
    # so they are the contract -- `cell` and `device` ride along for a human reading the
    # firehose, and nothing keys off them.
    _publish(
        RESPONSE_WAVEFORM_TOPIC,
        _envelope(device, "vibration-sensor", {
            "unit": "g",
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "sample_count": lor,
            "duration_s": round(duration_s, 4),
            "shaft_rpm": SHAFT_RPM,
            "samples": [round(v, 5) for v in samples],
        }, ts=capture_start, meta={
            "gw_serial": GW_SERIAL,
            "channel_index": index,
            "cell": cell,
            "device": device,
            "request": settings,
        }),
    )
    logger.infof("published %s-sample waveform for ch%s (%s)", lor, index, device)


# ── waveform ingest ──────────────────────────────────────────────────────────────────────


def _parse_iso(text):
    """Inverse of _iso(). The 'Z' is a literal in the pattern, so the timezone has to be set
    explicitly -- parsed without it, SimpleDateFormat would read UTC as local time."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    return formatter.parse(text)


def route_waveform(payload):
    """Event Stream handler for RESPONSE_WAVEFORM_TOPIC. `payload` is the decoded JSON object.

    Every gateway's waveforms arrive on one topic, so this is where the fan-out happens:
    (gwSerial, channelIndex) -> SENSOR_TAGS -> one UDT instance's waveform/* tags. The topic
    stays flat and the routing lives here, which is the whole point of the response-topic
    shape -- a new sensor is one row in SENSOR_TAGS, not a new subscription.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    payload = _as_mapping(payload)
    if payload is None:
        logger.warn("waveform payload was not a decodable JSON object")
        return

    meta = _as_mapping(payload.get("meta")) or {}
    values = _as_mapping(payload.get("values"))
    if values is None:
        logger.warn("waveform payload had no values block")
        return

    gw_serial = str(meta.get("gw_serial", ""))
    index = _as_int(meta.get("channel_index"))
    base = SENSOR_TAGS.get((gw_serial, index))
    if base is None:
        logger.infof("no tag mapping for gw=%s ch=%s -- dropped", gw_serial, index)
        return

    # captured_at is the capture START, off the envelope's ts -- deliberately not the publish
    # time and not now(). It is what the PM system keys on, and it pairs with last_request_ts.
    try:
        captured_at = _parse_iso(str(payload.get("ts")))
    except Exception:
        logger.warnf("unparseable ts %s on gw=%s ch=%s -- using now()",
                     payload.get("ts"), gw_serial, index)
        captured_at = system.date.now()

    system.tag.writeBlocking(
        [
            base + "/waveform/latest",
            base + "/waveform/captured_at",
            base + "/waveform/sample_count",
            base + "/waveform/sample_rate_hz",
        ],
        [
            system.util.jsonEncode(values),
            captured_at,
            _as_int(values.get("sample_count")),
            _as_int(values.get("sample_rate_hz")),
        ],
    )
    logger.infof("routed %s-sample waveform to %s (gw=%s ch=%s)",
                 values.get("sample_count"), base, gw_serial, index)
