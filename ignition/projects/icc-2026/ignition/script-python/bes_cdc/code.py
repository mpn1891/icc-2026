"""Pattern 5 -- turn a Debezium change event into one message on the backbone.

The other half of `bes_batch`. That module writes rows and knows nothing about
MQTT; Debezium Server tails the WAL as user `cdc` and POSTs each change event to
the `cdc-sink` WebDev endpoint; this module puts it on the wire as
`mechanism=cdc` through Transmission.

Nothing in the chain from the writer to here is something the writer asked for.
That is the pattern, and the failure demo is what proves it: stop the Debezium
container and clicking `manual_advance` still writes rows, still advances the
reactor, and produces nothing on the topic.

**Auth is a query-string token, not a header.** Debezium Server's support for
custom request headers is version-dependent, and a demo should not be one image
bump away from silently losing its authentication. The token rides in the URL
that `compose/debezium/application.properties` configures.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "bes_cdc"

BROKER = "chariot_broker"
MECHANISM = "cdc"
TOKEN = "icc26-cdc-token"

# The topic is built per-event from values.equipment_id, so a click on br-202
# cannot land on br-201's address. Device-addressed, like every other topic in
# the namespace, and nothing in it says "CDC" -- that is the whole point of
# meta.mechanism existing.
TOPIC_TEMPLATE = "icc26/site1/upstream/%s/batch/event"


def _iso(date=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if date is None:
        date = Date()
    return formatter.format(date)


def _param(request, name):
    """A query-string parameter, tolerating the list-valued spellings."""
    params = request.get("params") or {}
    value = params.get(name)
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)


def _status(request, code):
    response = request.get("servletResponse")
    if response is not None:
        response.setStatus(int(code))
        response.setContentType("application/json; charset=utf-8")


def _json(request, code, body):
    _status(request, code)
    return {"json": system.util.jsonEncode(body)}


def _body(request):
    """JSON object from a JSON POST, or None if the body is unparseable."""
    posted = request.get("postData")
    if posted is None:
        posted = request.get("data")
    if posted is None:
        return None, "empty body"
    if isinstance(posted, dict):
        return posted, None
    try:
        decoded = system.util.jsonDecode(str(posted))
    except Exception:
        return None, "body was not JSON"
    if not isinstance(decoded, dict):
        return None, "body was not a JSON object"
    return decoded, None


def _to_millis(text):
    """'2026-08-26T21:39:15.740000Z' -> '2026-08-26T21:39:15.740Z'.

    Debezium hands back six-digit microseconds; every other pattern publishes
    milliseconds through _iso(). Trim rather than let pattern 5 be the one
    message on the bus with a different precision.

    An absent fraction becomes '.000'. A '+hh:mm' offset is dropped rather than
    applied -- Debezium normalizes timestamptz to UTC before emitting, so there
    is nothing to convert, and carrying the offset would just be a second way of
    spelling Z.
    """
    body = text[:-1] if text.endswith("Z") else text
    if "+" in body:
        body = body.split("+", 1)[0]
    if "." in body:
        base, fraction = body.split(".", 1)
        fraction = (fraction + "000")[:3]
    else:
        base, fraction = body, "000"
    return base + "." + fraction + "Z"


def _timestamp(raw):
    """A Debezium timestamptz, normalized to the house ISO-8601 format.

    **Measured 2026-08-26: it arrives as a string**, not the int64 microseconds
    the connector docs led us to expect -- "2026-08-26T21:39:15.740000Z". The
    numeric branches are kept anyway, because `time.precision.mode` changes the
    encoding and a config edit should not silently move every timestamp by a
    factor of 1000. Guessing wrong reads as plausible until somebody checks the
    year.

    The threshold: anything past ~1e14 is microseconds (1e14 ms would be year
    5138), anything smaller is milliseconds.
    """
    if raw is None:
        return _iso()
    try:
        number = int(raw)
    except Exception:
        return _to_millis(str(raw))
    if number > 100000000000000:
        number = number // 1000
    return _iso(Date(number))


def _decode_payload(raw):
    """The jsonb column, which Debezium delivers as a JSON *string*, not an object."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        decoded = system.util.jsonDecode(str(raw))
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def handle(request):
    """Validate, filter to inserts, publish one message. WebDev response.

        valid token, op='c'       200  publishes
        missing or wrong token    401  no
        op != 'c'                 200  no -- and says so
        unparseable body          400  no
    """
    logger = system.util.getLogger(LOGGER_NAME)

    if _param(request, "token") != TOKEN:
        logger.warn("cdc sink rejected: missing or wrong token")
        return _json(request, 401, {"ok": False, "error": "unauthorized"})

    event, error = _body(request)
    if event is None:
        logger.warnf("cdc sink rejected: %s", error)
        return _json(request, 400, {"ok": False, "error": error})

    op = str(event.get("op") or "")
    if op != "c":
        # bes.batch_event is append-only, so an UPDATE or DELETE here is somebody
        # editing history. Not a batch event, and worth being able to say so on
        # stage rather than filtering it away silently.
        logger.infof("cdc sink saw op=%s -- not an insert, nothing published", op)
        return _json(request, 200, {"ok": True, "published": False, "op": op})

    after = event.get("after")
    if not isinstance(after, dict):
        logger.warn("cdc sink: op=c with no `after` row")
        return _json(request, 400, {"ok": False, "error": "no after image"})

    equipment_id = str(after.get("equipment_id") or "").strip()
    if not equipment_id:
        logger.warn("cdc sink: row carries no equipment_id, cannot address a topic")
        return _json(request, 400, {"ok": False, "error": "no equipment_id"})

    payload = _decode_payload(after.get("payload"))
    source = event.get("source") or {}

    envelope = {
        # The instant the operation changed, not the instant we heard about it.
        # The gap between this and meta.ingest_ts is the CDC latency, and it is
        # visible on stage -- same shape of gap pattern 4 makes a point of.
        "ts": _timestamp(after.get("occurred_at")),
        # The database row id, exactly as pattern 4 uses its outbox id. Durable
        # and monotonic: an in-memory counter would restart at 1 on every gateway
        # restart and tell a subscriber nothing.
        "seq": after.get("id"),
        # The batch execution system this stands in for -- named in the payload,
        # because there is no `bes` area in the namespace. An area is a place; a
        # BES is software. docs/00-architecture.md.
        #
        # id == type, matching pattern 4's {"id": "lims", "type": "lims"}.
        "source": {"id": "bes", "type": "bes"},
        # Exactly the documented keys, and no more. An earlier revision carried
        # meta.op and meta.lsn here because the log position is the one field no
        # other mechanism can produce -- but no other pattern extends `meta`, and
        # a payload that advertises its own transport is a strange thing for a
        # demo whose claim is that a subscriber cannot tell how anything arrived.
        # The LSN is still in the gateway log line below, and in Debezium's.
        #
        # No correlation_id: pattern 5 has nothing to correlate to. Pattern 7
        # joins it by time. docs/00-architecture.md § Payload envelope.
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso(),
        },
        "values": {
            "batch_id": after.get("batch_id"),
            "equipment_id": equipment_id,
            "event_type": after.get("event_type"),
            "operation": after.get("operation"),
            "qualified_window": bool(payload.get("qualified_window")),
        },
    }

    topic = TOPIC_TEMPLATE % equipment_id
    # Retain false. A retained batch event replays a stale operation to every
    # reconnecting subscriber and presents it as current -- the same hazard
    # docs/plans/04-lims-webhook.md documents for the valve's sample-complete.
    # The current operation is what the tag is for.
    system.cirruslink.transmission.publish(
        BROKER, topic, system.util.jsonEncode(envelope), 1, False)

    # The LSN is logged rather than published. It is the best evidence that this
    # message came off the write-ahead log, so it belongs where somebody
    # investigating can find it -- not in an envelope every other pattern keeps
    # to three meta keys.
    logger.infof("cdc sink published %s/%s (row %s, lsn %s) to %s",
                 envelope["values"]["event_type"],
                 envelope["values"]["operation"],
                 str(envelope["seq"]), str(source.get("lsn")), topic)
    return _json(request, 200, {"ok": True, "published": True, "topic": topic})
