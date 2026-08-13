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

    Event Stream "vibration-gw-control"
        transform  ->  vibsim.build_collect_response(event.data)
        MQTT handler ->  …/vibration-gw/response/waveform
    Event Stream "vibration-gw-listener"
        handler    ->  vibsim.route_waveform(event.data)

There is no periodic telemetry and no birth/death. A Last Will is registered in the MQTT
CONNECT packet, so only the client that owns the session can set one -- and Ignition's
session belongs to the Transmission module. A collect is answered by the waveform or by
nothing at all.

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

# The gateway is a radio concentrator, not process equipment -- it does not sit in a cell.
# Two topics, one area root:
#
#   <AREA_ROOT>/<FLEET_ID>/cmd/collect        broadcast in; every gateway hears it and
#                                             self-selects on gwSerial
#   <AREA_ROOT>/<FLEET_ID>/response/waveform  the answer to that command, on one fleet topic.
#                                             gwSerial + channelIndex are echoed in the body
#                                             and the listener demuxes them to the right tag.
AREA_ROOT = "icc26/site1/upstream"
FLEET_ID = "vibration-gw"
CONTROL_TOPIC = "%s/%s/cmd/collect" % (AREA_ROOT, FLEET_ID)
RESPONSE_WAVEFORM_TOPIC = "%s/%s/response/waveform" % (AREA_ROOT, FLEET_ID)

# Demux table for the fleet response topic. One row per provisioned sensor.
# tag_path is the vibration_sensor UDT instance root; route_waveform writes
# waveform/* relative to it. A new sensor is a row here, not a new subscription.
# Kept in this module on purpose — pattern 1 does not depend on Postgres.
SENSOR_CHANNELS = (
    {
        "gw_serial": "12345678",
        "channel_index": 0,
        "tag_path": "[default]icc26/site1/upstream/bioreactors/br-201"
                    "/asset_data/agitator_vibration",
    },
)
SENSOR_TAGS = {}
for _row in SENSOR_CHANNELS:
    SENSOR_TAGS[(_row["gw_serial"], _row["channel_index"])] = _row["tag_path"]

# channel index -> (cell, device id). Used to decide which channels are provisioned.
# Anything absent is physically present but unprovisioned. Both ids must exist in
# plant.equipment (compose/postgres/initdb/03-seed.sql).
CHANNELS = {0: ("br-201", "agitator-vib")}
CHANNEL_COUNT = 4

SAMPLE_RATE_HZ = 32000  # vendor sampleRate on wiredCollection responses
MAX_LOR = 65536        # 4096 is ~34 KB on the wire; 32768 is ~267 KB
SHAFT_RPM = 1780.0

# Bearing model. See the header of services/sim-vibration/app.py for the derivation --
# this is a Jython port of a model that was validated against a naive DFT offline.
BPFO_RATIO = 3.585     # 8-ball deep-groove bearing, outer race -> 106.4 Hz at 1780 rpm
RESONANCE_HZ = 1500.0  # housing mode rung by each impact; ~21 samples/cycle at 32000
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

# Capture-time sleep is off: the event stream transform returns immediately and the MQTT
# handler publishes. Set True only if you want the transform to block for the record length.
SIMULATE_CAPTURE_TIME = False

# Box-and-whisker check on each routed capture: outlier_count / n must be at or below this
# for waveform/box_whisker/check_ok. Mild healthy noise stays under; growing BPFO impacts
# push the fraction up as DEFECT_SEVERITY ramps.
MAX_OUTLIER_FRACTION = 0.05

LOGGER_NAME = "vibsim"

# Module-level state. Jython holds this for the life of the script module, so it resets on
# a script save or a gateway restart -- which is harmless for the defect ramp.
_started_ms = [None]


# ── time ─────────────────────────────────────────────────────────────────────────────────


def _iso(date=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe, hence per-call."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if date is None:
        date = Date()
    return formatter.format(date)


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
    """Return a plain Python dict, or None.

    The `ignition.jsonObject` encoder delivers a PyJsonObjectAdapter (or sometimes a
    java.util.Map / raw JSON text). Adapters implement .get(key) but not Python's
    .get(key, default), so callers that pass a default silently mis-read fields and
    drop the message. Round-tripping through jsonEncode/jsonDecode yields a real dict.
    """
    if payload is None:
        return None
    if isinstance(payload, basestring):  # noqa: F821 -- Jython 2.7
        try:
            return system.util.jsonDecode(payload)
        except Exception:
            return None
    if hasattr(payload, "get") or hasattr(payload, "__iter__"):
        try:
            return system.util.jsonDecode(system.util.jsonEncode(payload))
        except Exception:
            if hasattr(payload, "get"):
                return payload
            return None
    return None


def _reject(request, reason, channel_index=None):
    """Log-only. The gateway does not answer on the wire -- there is no ack topic, so a
    rejected collect is visible in the gateway log and nowhere else."""
    system.util.getLogger(LOGGER_NAME).infof(
        "rejected collect: %s (ch%s)", reason, channel_index
    )


def _wired_collection(channel_index, gw_serial, timestamp, samples):
    """Vendor wiredCollection JSON with `data` last so metadata stays readable on the wire.

    Built as a string: Jython dicts and some JSON encoders scramble key order, which buries
    the small fields under thousands of sample lines.
    """
    return (
        '{"datatype":"wiredCollection"'
        ',"wiredChannel":%d'
        ',"gwSerial":%s'
        ',"timestamp":%s'
        ',"sensorType":"accel"'
        ',"sampleRate":%d'
        ',"data":%s}'
    ) % (
        int(channel_index),
        system.util.jsonEncode(str(gw_serial)),
        system.util.jsonEncode(str(timestamp)),
        int(SAMPLE_RATE_HZ),
        system.util.jsonEncode([round(v, 5) for v in samples]),
    )


def build_collect_response(request):
    """Event Stream transform for CONTROL_TOPIC. Returns a vendor wiredCollection, or None.

    The control topic is fleet-addressed: every gateway on site subscribes to it and ignores
    payloads whose gwSerial is not its own, then resolves the sensor from channelIndex. That
    is how the real hardware is configured, and it is the one place in this demo where the
    device address lives in the payload instead of the topic.

    Returns an ordered JSON *string* (data last). None means no response (wrong serial,
    reject, or undecodable).
    """
    logger = system.util.getLogger(LOGGER_NAME)

    request = _as_mapping(request)
    if request is None:
        logger.warn("collect payload was not a decodable JSON object")
        return None

    # Silence is the correct response to somebody else's serial -- no ack, no log line.
    requested_serial = str(request.get("gwSerial", ""))
    if requested_serial != GW_SERIAL:
        return None

    if str(request.get("type")) != "observerrequest":
        _reject(request, "malformed-request")
        return None
    if str(request.get("action")) != "wiredcollectnow":
        _reject(request, "unsupported-action")
        return None

    index = _as_int(request.get("channelIndex"))
    if index is None or index < 0 or index >= CHANNEL_COUNT:
        _reject(request, "unknown-channel", index)
        return None
    if index not in CHANNELS:
        _reject(request, "not-provisioned", index)
        return None

    settings = _as_mapping(request.get("settings"))
    lor = _as_int(settings.get("lor")) if settings is not None else None
    if lor is None or lor < 512 or lor > MAX_LOR or (lor & (lor - 1)) != 0:
        _reject(request, "invalid-lor", index)
        return None

    cell, device = CHANNELS[index]
    duration_s = float(lor) / SAMPLE_RATE_HZ

    capture_start = system.date.now()
    if SIMULATE_CAPTURE_TIME:
        # A device cannot return a record faster than the record is long. Honouring that is
        # what makes the request/response genuinely asynchronous rather than an echo.
        system.util.sleep(int(duration_s * 1000))

    samples = bearing_block(lor)
    document = _wired_collection(
        index, requested_serial, _iso(capture_start), samples
    )
    logger.infof("built %s-sample wiredCollection for ch%s (%s)", lor, index, device)
    return document


def handle_collect(request):
    """Build + publish via Transmission API. Prefer the event-stream transform + MQTT handler."""
    document = build_collect_response(request)
    if document is not None:
        # build_collect_response already returns JSON text — do not jsonEncode again.
        system.cirruslink.transmission.publish(
            BROKER, RESPONSE_WAVEFORM_TOPIC, document, 1, False
        )


# ── waveform ingest ──────────────────────────────────────────────────────────────────────


def _parse_iso(text):
    """Inverse of _iso(). The 'Z' is a literal in the pattern, so the timezone has to be set
    explicitly -- parsed without it, SimpleDateFormat would read UTC as local time."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    return formatter.parse(text)


def _percentile(sorted_vals, p):
    """Linear-interpolated percentile. `sorted_vals` ascending, `p` in [0, 100]."""
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    rank = (p / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = rank - lo
    return float(sorted_vals[lo]) * (1.0 - frac) + float(sorted_vals[hi]) * frac


def _box_whisker(samples):
    """Five-number summary + Tukey fences + outlier check for one capture.

    Returns None if there are no usable samples. check_ok is True when the outlier
    fraction is at or below MAX_OUTLIER_FRACTION -- the demo hook is that BPFO impacts
    drive that fraction up as defect severity grows.
    """
    cleaned = []
    for v in samples or []:
        try:
            cleaned.append(float(v))
        except (TypeError, ValueError):
            continue
    n = len(cleaned)
    if n == 0:
        return None

    cleaned.sort()
    q1 = _percentile(cleaned, 25.0)
    median = _percentile(cleaned, 50.0)
    q3 = _percentile(cleaned, 75.0)
    iqr = q3 - q1
    fence_low = q1 - 1.5 * iqr
    fence_high = q3 + 1.5 * iqr

    outliers = 0
    for v in cleaned:
        if v < fence_low or v > fence_high:
            outliers += 1

    return {
        "min": cleaned[0],
        "q1": q1,
        "median": median,
        "q3": q3,
        "max": cleaned[-1],
        "iqr": iqr,
        "fence_low": fence_low,
        "fence_high": fence_high,
        "outlier_count": outliers,
        "check_ok": (float(outliers) / float(n)) <= MAX_OUTLIER_FRACTION,
    }


def lookup_sensor_tag_path(gw_serial, channel_index):
    """SENSOR_CHANNELS row for this (gwSerial, channelIndex), or None.

    tag_path is the sensor UDT root the listener writes waveform/* under.
    """
    if channel_index is None:
        return None
    return SENSOR_TAGS.get((str(gw_serial), int(channel_index)))


def route_waveform(payload):
    """Event Stream handler for RESPONSE_WAVEFORM_TOPIC. `payload` is the decoded JSON object.

    Every gateway's waveforms arrive on one topic, so this is where the fan-out happens:
    (gwSerial, wiredChannel) -> SENSOR_CHANNELS.tag_path -> one UDT instance's waveform/*
    tags. The topic stays flat and the routing lives here, which is the whole point of
    the response-topic shape -- a new sensor is one row in SENSOR_CHANNELS, not a new
    subscription.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    payload = _as_mapping(payload)
    if payload is None:
        logger.warn("waveform payload was not a decodable JSON object")
        return

    if str(payload.get("datatype", "")) != "wiredCollection":
        logger.warn("waveform payload was not a wiredCollection")
        return

    gw_serial = str(payload.get("gwSerial", ""))
    index = _as_int(payload.get("wiredChannel"))
    base = lookup_sensor_tag_path(gw_serial, index)
    if base is None:
        logger.infof("no tag mapping for gw=%s ch=%s -- dropped", gw_serial, index)
        return

    data = payload.get("data")
    if data is None:
        logger.warnf("wiredCollection missing data on gw=%s ch=%s", gw_serial, index)
        return

    sample_count = len(data) if hasattr(data, "__len__") else None
    sample_rate = _as_int(payload.get("sampleRate"))

    # timestamp is the capture START -- deliberately not the publish time and not now().
    # It is what the PM system keys on, and it pairs with last_request_ts.
    try:
        captured_at = _parse_iso(str(payload.get("timestamp")))
    except Exception:
        logger.warnf("unparseable timestamp %s on gw=%s ch=%s -- using now()",
                     payload.get("timestamp"), gw_serial, index)
        captured_at = system.date.now()

    paths = [
        base + "/waveform/latest",
        base + "/waveform/captured_at",
        base + "/waveform/sample_count",
        base + "/waveform/sample_rate_hz",
    ]
    writes = [
        system.util.jsonEncode(payload),
        captured_at,
        sample_count,
        sample_rate,
    ]

    stats = _box_whisker(data)
    if stats is not None:
        bw = base + "/waveform/box_whisker"
        paths.extend([
            bw + "/min",
            bw + "/q1",
            bw + "/median",
            bw + "/q3",
            bw + "/max",
            bw + "/iqr",
            bw + "/fence_low",
            bw + "/fence_high",
            bw + "/outlier_count",
            bw + "/check_ok",
        ])
        writes.extend([
            stats["min"],
            stats["q1"],
            stats["median"],
            stats["q3"],
            stats["max"],
            stats["iqr"],
            stats["fence_low"],
            stats["fence_high"],
            stats["outlier_count"],
            stats["check_ok"],
        ])
    else:
        logger.warnf("no usable samples for box-whisker on gw=%s ch=%s", gw_serial, index)

    system.tag.writeBlocking(paths, writes)
    logger.infof("routed %s-sample waveform to %s (gw=%s ch=%s check_ok=%s)",
                 sample_count, base, gw_serial, index,
                 None if stats is None else stats["check_ok"])
