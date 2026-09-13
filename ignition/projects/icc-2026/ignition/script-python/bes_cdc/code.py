"""Pattern 5 -- turn a Debezium change event into one message on the backbone.

The other half of `bes_batch`. That module writes rows and knows nothing about
MQTT; Debezium Server tails the WAL as user `cdc` and POSTs each change event to
the `cdc-sink` WebDev endpoint; this module puts it on the wire through
Transmission.

**`ts` and `values`, and nothing else.** Pattern 5 carried the full envelope
until 2026-09-13 -- it was the last one to -- and gave it up on the same
argument patterns 4, 6 and 7 did that day: a
document that has to name its own mechanism is a document whose address is not
doing its job. docs/00-architecture.md, "Payload envelope".

Nothing in the chain from the writer to here is something the writer asked for.
That is the pattern, and the failure demo is what proves it: stop the Debezium
container and clicking `manual_advance` still writes rows, still advances the
reactor, and produces nothing on the topic.

**Two topics, and the split is the point.** An INSERT is a batch event and goes
to the operational topic, where nothing in the payload says how it arrived.
`bes.batch_event` is append-only, so an UPDATE or DELETE is not a batch event at
all -- it is somebody amending the record after the fact, and it goes to the
audit topic instead, carrying the `before` image Postgres writes into the WAL
because of REPLICA IDENTITY FULL. That is the one place `op` and the LSN belong
in a payload in this stack: on the audit topic the database operation is the
subject, not the transport. compose/postgres/initdb/04-cdc.sql.

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
TOKEN = "icc26-cdc-token"

# The topic is built per-event from values.equipment_id, so a click on br-202
# cannot land on br-201's address. Device-addressed, like every other topic in
# the namespace, and nothing in it says "CDC". Nothing in the payload does
# either, now that `meta` is gone -- the demo's claim is that a subscriber
# cannot tell which mechanism carried a message, and pattern 5 no longer ships
# the one field that would have let them.
TOPIC_TEMPLATE = "icc26/site1/upstream/%s/batch/event"

# The audit stream is per-table, not per-device: somebody watching for amended
# records wants one subscription, not one per reactor. `equipment_id` rides in
# the payload rather than the address, which also means a DELETE still publishes
# when the row named no equipment -- on the insert path a missing equipment_id
# is a 400, because there it *is* the address.
#
# Off the operational tree on purpose. Everything under upstream/ and qc/ is a
# thing that happened in the plant, and a subscriber reading those topics still
# cannot tell which mechanism carried them. This one is about the database, so
# it is the one topic in the namespace allowed to say `op`.
AUDIT_TOPIC = "icc26/site1/audit/bes/batch-event"


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


def _field_value(key, value):
    """One column, in the form the audit message shows it."""
    if key == "payload":
        return _decode_payload(value)
    if key == "occurred_at" and value is not None:
        return _to_millis(str(value))
    return value


def _row(image):
    """A whole change-event row, every column in its display form."""
    if not isinstance(image, dict):
        return {}
    out = {}
    for key in image.keys():
        out[key] = _field_value(key, image.get(key))
    return out


def _changed(before, after):
    """{column: {"from": ..., "to": ...}} for every column whose value moved.

    Compared raw and displayed normalized, which is deliberately not the same
    pass: `occurred_at` is trimmed to milliseconds for the wire like every other
    timestamp in this stack, and comparing the trimmed form would hide an edit
    smaller than a millisecond.
    """
    fields = {}
    keys = set(before.keys()) | set(after.keys())
    for key in keys:
        old = before.get(key)
        new = after.get(key)
        if old == new:
            continue
        fields[key] = {"from": _field_value(key, old),
                       "to": _field_value(key, new)}
    return fields


def _publish_audit(request, logger, event, op):
    """An UPDATE or DELETE on an append-only table -- publish it as an amendment.

    `ts` is the commit time from `source.ts_ms`, and deliberately **not** the
    row's occurred_at. On an UPDATE that moves occurred_at, the new value is the
    very thing being changed; stamping the audit record with it would date the
    evidence to whatever somebody just typed in.
    """
    source = event.get("source") or {}
    before = _row(event.get("before"))
    after = _row(event.get("after"))

    # REPLICA IDENTITY FULL is what puts the pre-image in the WAL. Without it
    # `before` carries the primary key and nothing else, and this message has
    # almost nothing to say. 04-cdc.sql sets it; if this ever fires, look there
    # before looking anywhere else.
    if not before:
        logger.warnf("cdc sink: op=%s with no `before` image -- is REPLICA "
                     "IDENTITY still FULL on bes.batch_event?", op)

    row = after if op == "u" else before
    values = {
        "op": op,
        "row_id": row.get("id"),
        "batch_id": row.get("batch_id"),
        "equipment_id": row.get("equipment_id"),
        # The log position, which on this topic is evidence rather than
        # transport trivia: it is what ties an amendment to a point in the
        # write-ahead log that somebody can go and read for themselves.
        "lsn": source.get("lsn"),
    }
    if op == "u":
        # Raw images in, so the comparison sees full precision. _changed does
        # the normalizing on the way out.
        values["changed"] = _changed(event.get("before") or {},
                                     event.get("after") or {})
    else:
        values["deleted"] = before

    # `op` and `lsn` stay, because they are in `values` and always were: on the
    # audit topic the database operation is what the message is *about*, which
    # is not the same thing as metadata describing how it travelled. Dropping
    # `seq` costs nothing here -- `values.row_id` is the same number.
    envelope = {
        "ts": _timestamp(source.get("ts_ms")),
        "values": values,
    }

    # Retain false, for the reason it is false everywhere in this repo: a
    # retained amendment replays to every reconnecting subscriber and presents
    # a week-old edit as one that just happened.
    system.cirruslink.transmission.publish(
        BROKER, AUDIT_TOPIC, system.util.jsonEncode(envelope), 1, False)

    logger.infof("cdc sink published op=%s on row %s (lsn %s) to %s",
                 op, str(values["row_id"]), str(source.get("lsn")), AUDIT_TOPIC)
    return _json(request, 200, {"ok": True, "published": True,
                                "topic": AUDIT_TOPIC, "op": op})


def handle(request):
    """Validate, route on the operation, publish one message. WebDev response.

        valid token, op='c'        200  publishes to the batch event topic
        valid token, op='u' | 'd'  200  publishes to the audit topic
        missing or wrong token     401  no
        any other op               200  no -- and says so
        empty body (tombstone)     200  no
        unparseable body           400  no
    """
    logger = system.util.getLogger(LOGGER_NAME)

    if _param(request, "token") != TOKEN:
        logger.warn("cdc sink rejected: missing or wrong token")
        return _json(request, 401, {"ok": False, "error": "unauthorized"})

    event, error = _body(request)
    if event is None:
        if error == "empty body":
            # A Kafka tombstone, which means nothing to an HTTP sink.
            # `tombstones.on.delete=false` in application.properties stops
            # Debezium sending them at all; this branch exists so that a config
            # drift reads as one info line rather than a 400 on every delete.
            logger.info("cdc sink saw a tombstone -- nothing published")
            return _json(request, 200, {"ok": True, "published": False,
                                        "op": "tombstone"})
        logger.warnf("cdc sink rejected: %s", error)
        return _json(request, 400, {"ok": False, "error": error})

    op = str(event.get("op") or "")
    if op in ("u", "d"):
        # bes.batch_event is append-only, so this is not a batch event -- it is
        # somebody amending the record. It publishes, with its pre-image, on the
        # audit topic. Announcing it is the point: the operational topic stays a
        # stream of things that happened in the plant, and the amendment lands
        # somewhere a subscriber can actually see it. Refusing it silently, which
        # is what this did until now, is the weaker answer.
        return _publish_audit(request, logger, event, op)
    if op != "c":
        # 'r' is a snapshot read and should never appear (snapshot.mode=no_data);
        # 't' is a truncate, skipped at the source. Either one arriving means the
        # Debezium config has moved, so say so rather than swallow it.
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
        # `meta.ingest_ts` used to sit beside this, and the gap between the two
        # was the CDC latency on stage. With the envelope gone that number is in
        # the gateway log line below and in Debezium's, not on the wire.
        "ts": _timestamp(after.get("occurred_at")),
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
    # investigating can find it -- and there is no longer an envelope on the
    # operational topic for it to ride in.
    logger.infof("cdc sink published %s/%s (row %s, lsn %s) to %s",
                 envelope["values"]["event_type"],
                 envelope["values"]["operation"],
                 str(after.get("id")), str(source.get("lsn")), topic)
    return _json(request, 200, {"ok": True, "published": True, "topic": topic})
