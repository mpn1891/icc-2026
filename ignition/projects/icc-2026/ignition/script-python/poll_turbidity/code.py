"""Pattern 6 -- poll AP Connect's measurement catalog and publish onto the backbone.

AP Connect does not call us. It files a completed measurement in its own database and
that is the end of its interest in the subject. So we keep a high-water mark on
`measurement.measurement_no` -- the vendor's own "strictly, consecutively increasing"
item number -- and ask, once a minute, what is newer than that.

Pattern 5 tails the WAL of the same table onto the same topic. The two documents must be
indistinguishable apart from `meta.mechanism` and `meta.ingest_ts`; `ingest_ts` minus `ts`
is the poll lag, and next to CDC's milliseconds that gap is the whole comparison. Sixty
seconds is deliberate -- a two-second timer would hide the thing the pattern is about.

Ignition never writes to the apconnect catalog. `measure_now()` POSTs to the simulator,
which does the INSERT as the application would, so the SELECT-only grant stays true.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

import sys

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "poll_turbidity"

DATASOURCE = "APCONNECT"
BROKER = "chariot_broker"
TOPIC = "icc26/site1/downstream/tff-301/turbidity-01/telemetry"
MECHANISM = "poll"
SOURCE_ID = "turbidity-01"
SOURCE_TYPE = "turbidity-meter"

# Rows per tick. A long stall drains over several ticks rather than freezing the
# gateway in one -- and that burst across ticks is the catch-up visual.
BATCH = 100

TAG_ROOT = "[default]icc26/site1/downstream/tff-301/turbidity-01/"
WATERMARK_TAG = TAG_ROOT + "poll_watermark"
ENABLED_TAG = TAG_ROOT + "poll_enabled"
JUMP_TAG = TAG_ROOT + "poll_jump"
MEASURE_TAG = TAG_ROOT + "measure_now"

# The simulator's trigger endpoint. In-network name and port, not the host mapping.
MEASURE_URL = "http://sim-apconnect:8080/measure"

# Must stay identical to VARIANT_MAP in services/cdc-mapper/app.py. Two copies,
# on purpose -- no shared library across Jython and CPython -- so change both.
# If these two maps drift, patterns 5 and 6 publish different documents for one
# measurement and spec 06 checkpoint 4 fails.
VARIANT_MAP = {
    "Haze/Haze":               "haze_ebc",
    "Haze/HazeNTU":            "haze_ntu",
    "Haze/S25S0":              "s25_s0",
    "Haze/S90S0":              "s90_s0",
    "Haze/AbsorbanceS0":       "absorbance_s0",
    "Density/CellTemperature": "cell_temperature_c",
}

# One-shot probe latch. See _probe().
_probed = [False]


def _iso(value=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe, hence per-call.

    Accepts None (meaning now), a java.util.Date -- which java.sql.Timestamp extends, so a
    timestamptz column off the JDBC driver arrives ready to format -- a string already in
    ISO form, or epoch milliseconds.
    """
    if isinstance(value, (str, unicode)):
        return value
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if value is None:
        value = Date()
    elif not isinstance(value, Date):
        value = Date(long(value))
    return formatter.format(value)


def _drop_nones(values):
    """Absent stays absent: omit the key rather than sending 0 or null.

    Same rule as patterns 3, 4 and 5. A CANCELED or FAILURE measurement carries no
    reading at all, so the haze keys are simply not there.
    """
    out = {}
    for key in values:
        if values[key] is not None:
            out[key] = values[key]
    return out


def _typename(value):
    """Best-effort class name for the one-time probe. Java and Jython objects answer
    different questions, so ask both."""
    if value is None:
        return "None"
    getter = getattr(value, "getClass", None)
    if getter is not None:
        try:
            return getter().getName()
        except Exception:
            pass
    return type(value).__name__


def _rows_as_dicts(result):
    """Normalise whatever system.db.runPrepQuery hands back into a list of plain dicts.

    This is expected to be a PyDataSet, whose rows support row["column"]. It may instead
    be a basic Dataset, which supports neither iteration nor lookup by name. Rather than
    bet the pattern on which one, unwrap to the underlying Dataset when there is one and
    index positionally off the column names -- that works on both, and it means a wrong
    guess about the return type cannot break the poll.
    """
    if result is None:
        return [], []
    unwrap = getattr(result, "getUnderlyingDataset", None)
    dataset = unwrap() if unwrap is not None else result

    names = []
    for i in range(dataset.getColumnCount()):
        names.append(dataset.getColumnName(i))

    rows = []
    for r in range(dataset.getRowCount()):
        row = {}
        for i in range(len(names)):
            row[names[i].lower()] = dataset.getValueAt(r, i)
        rows.append(row)
    return names, rows


def _probe(logger, result, names, rows):
    """Log, exactly once, what the driver actually handed back.

    Every statement in this module about the shape of a JDBC result was inferred when it
    was written; none of it had been run against a gateway. One INFO line on the first
    tick that returns rows turns those guesses into facts. Read it after the first run and
    correct the notes in docs/06-poll-turbidity.md.
    """
    if _probed[0]:
        return
    _probed[0] = True
    try:
        first = rows[0] if rows else {}
        logger.infof(
            "probe: runPrepQuery returned %s; columns %s; completed_ts is %s;"
            " result_values is %s; sample of result_values %s",
            _typename(result),
            names,
            _typename(first.get("completed_ts")),
            _typename(first.get("result_values")),
            str(first.get("result_values"))[:200],
        )
    except Exception:
        logger.warn("probe failed -- harmless, but the shapes stay unknown",
                    sys.exc_info()[1])


def _project_values(raw):
    """Variant array -> flat dict. Absent stays absent; never substitute 0.

    `result_values` is jsonb. Over JDBC that is normally a String, but the PostgreSQL
    driver may hand over a PGobject wrapping one, so anything that is not already a
    sequence gets str()'d before decoding.
    """
    if raw is None:
        return {}
    if not isinstance(raw, (list, tuple)):
        if not isinstance(raw, (str, unicode)):
            raw = str(raw)
        raw = system.util.jsonDecode(raw)

    out = {}
    for variant in raw or []:
        key = VARIANT_MAP.get(variant.get("id"))
        if key is None:
            continue
        value = variant.get("value")
        # A QUANTITY variant expands value into {numeric, unit, quantity}. Decoders
        # differ on whether that is a Jython dict or a java.util.Map, so duck-type it.
        if hasattr(value, "get"):
            value = value.get("numeric")
        if value is not None:
            out[key] = float(value)
    return out


def measure_now():
    """Rising edge on measure_now: ask the instrument's application to file one
    measurement, then reset the tag so it can be pressed again.

    This is a stage prop for "an operator pressed Start". It is deliberately an HTTP call
    to the simulator and NOT an INSERT: Ignition holds SELECT only on the apconnect
    catalog, and that has to stay true (checkpoint 10).
    """
    logger = system.util.getLogger(LOGGER_NAME)
    try:
        client = system.net.httpClient(timeout=5000)
        response = client.post(MEASURE_URL,
                               headers={"Content-Type": "application/json"},
                               data="{}")
        if response.good:
            # .text, not .body -- body is a byte array and logs as [B@1a2b3c.
            logger.infof("measure_now: simulator filed a measurement -- %s", response.text)
        else:
            logger.warnf("measure_now: simulator returned %s", response.statusCode)
    except Exception:
        # The button failing is not the demo. Log it and let the operator retry.
        logger.warn("measure_now: could not reach the simulator", sys.exc_info()[1])
    finally:
        system.tag.writeBlocking([MEASURE_TAG], [False])


def tick():
    """One poll. Wired to a 60 s gateway timer.

    Catch-up is the default: everything with measurement_no > watermark is published, in
    order, however late. The jump alternative is the poll_jump flag below -- live code,
    because showing the two side by side is the point of the pattern.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    control = system.tag.readBlocking([ENABLED_TAG, JUMP_TAG, WATERMARK_TAG])
    enabled = control[0].value
    jump = control[1].value
    watermark = control[2].value

    if enabled is None:
        # Guard, not decoration: without it an unscanned tag folder reads as None,
        # the watermark falls back to 0, and every tick republishes the whole table.
        logger.warnf("poll skipped: %s is not readable -- are the memory tags scanned in?",
                     ENABLED_TAG)
        return
    if enabled is False:
        return

    last = int(watermark or 0)

    if jump:
        # The other implementation, one flag away: skip the backlog entirely. Late
        # becomes lost, and CDC on the same topic still has the rows this drops.
        high = system.db.runScalarQuery(
            "SELECT COALESCE(max(measurement_no), 0) FROM measurement", DATASOURCE)
        high = int(high or 0)
        if high != last:
            system.tag.writeBlocking([WATERMARK_TAG], [high])
            logger.infof("poll_jump: watermark advanced %s -> %s without publishing",
                         last, high)
        return

    result = system.db.runPrepQuery(
        "SELECT measurement_no, id, status, completed_ts, sample_name,"
        "       instrument_serial, result_values"
        "  FROM measurement WHERE measurement_no > ?"
        " ORDER BY measurement_no LIMIT ?",
        [last, BATCH], DATASOURCE)

    names, rows = _rows_as_dicts(result)
    if not rows:
        return
    _probe(logger, result, names, rows)

    mark = last
    published = 0
    for row in rows:
        number = int(row["measurement_no"])
        guid = str(row["id"])

        values = {
            "measurement_no": number,
            "measurement_id": guid,
            "status": row["status"],
            "sample_name": row["sample_name"],
            "instrument_serial": row["instrument_serial"],
        }
        values.update(_project_values(row["result_values"]))

        envelope = {
            "ts": _iso(row["completed_ts"]),
            "seq": number,
            "source": {"id": SOURCE_ID, "type": SOURCE_TYPE},
            "meta": {
                "mechanism": MECHANISM,
                "ingest_ts": _iso(),
                "correlation_id": guid,
            },
            "values": _drop_nones(values),
        }

        payload = system.util.jsonEncode(envelope)
        try:
            system.cirruslink.transmission.publish(BROKER, TOPIC, payload, 1, False)
        except Exception:
            # Stop at the first failure and leave the watermark below this row, so the
            # next tick retries it. At-least-once, in order, nothing skipped.
            logger.warn("poll stopped at measurement_no %s -- not advancing past it"
                        % number, sys.exc_info()[1])
            break
        mark = number
        published += 1

    if published:
        # publish() neither raises nor returns a status when MQTT Transmission drops the
        # message -- it logs its own warning under ClientsManager. So this line means
        # handed to the module, NOT delivered to the broker.
        logger.infof("poll handed %s measurement(s) to %s, watermark %s -> %s",
                     published, TOPIC, last, mark)
        system.tag.writeBlocking([WATERMARK_TAG], [mark])
