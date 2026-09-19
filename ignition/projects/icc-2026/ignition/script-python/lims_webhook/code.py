"""Pattern 4 -- receive a LIMS approval webhook and publish onto the backbone.

The LIMS has no MQTT publish rights. This module is the other half of that
decision: Ignition accepts the HTTP callback, manufactures exactly-once from
the outbox's at-least-once POSTs, and Transmission publishes as ign-transmission.

**It does not decorate the document.** What the LIMS posted is what goes on the
wire, byte for byte in content -- no `seq` minted here, no `source` filled in,
no `meta.mechanism` stamped on the way past. Pattern 4's envelope is `ts` and
`values`, the same shape patterns 1, 2 and 3 publish, and a consumer reads the
mechanism off the topic it subscribed to. This module authenticates and
deduplicates; it does not author.

**It also lands the review in the typed tag model**, by calling
`model_feed.write_review` after the publish. That is not this module authoring
anything -- the document is unchanged and already on the wire when the call is
made -- it is where the model feed had to go once MQTT Engine turned out to
register one Event Stream source per topic string, 07's `lims-review` stream
being the one that holds this topic. See `model_feed`.

Dedupe is a module-level bounded dict (last ~500 keys), not a table. A gateway
restart loses the window, which is honest and acceptable; a durable version
needs the ICC26 JDBC datasource, which does not exist yet.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from collections import OrderedDict

LOGGER_NAME = "lims_webhook"

BROKER = "chariot_broker"
TOPIC = "icc26/site1/qc/lims/sample-result"
SECRET = "icc26-webhook-secret"

# Last ~500 idempotency keys. An outbox delivers at least once, so a redelivery
# after a 200 was lost in flight is normal operation. The 409 path is what
# makes it look exactly-once.
_MAX_KEYS = 500
_seen = OrderedDict()


def _header(request, name):
    """WebDev header maps are inconsistently cased; try a few spellings."""
    headers = request.get("headers") or {}
    wanted = name.lower()
    for key in headers:
        if str(key).lower() == wanted:
            value = headers[key]
            if isinstance(value, (list, tuple)) and value:
                return str(value[0])
            return str(value)
    return ""


def _seen_before(key):
    if key in _seen:
        return True
    _seen[key] = True
    while len(_seen) > _MAX_KEYS:
        _seen.popitem(last=False)
    return False


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


def handle(request):
    """Validate secret and idempotency key, publish once, return a WebDev response.

    Status table from the spec:

        valid secret, new key     200  publishes
        missing or wrong secret   401  no
        key already seen          409  no
        unparseable body          400  no
    """
    logger = system.util.getLogger(LOGGER_NAME)
    secret = _header(request, "X-Webhook-Secret")
    if not secret:
        auth = _header(request, "Authorization")
        if auth.lower().startswith("bearer "):
            secret = auth[7:].strip()
    if secret != SECRET:
        logger.warn("lims webhook rejected: missing or wrong secret")
        return _json(request, 401, {"ok": False, "error": "unauthorized"})

    key = _header(request, "X-Idempotency-Key").strip()
    envelope, error = _body(request)
    if envelope is None:
        logger.warnf("lims webhook rejected: %s", error)
        return _json(request, 400, {"ok": False, "error": error})

    values = envelope.get("values") or {}
    if not key:
        key = str(values.get("sample_id") or "").strip()
    if not key:
        return _json(request, 400, {"ok": False, "error": "idempotency key required"})

    if _seen_before(key):
        logger.infof("lims webhook replay of %s -- 409, not published", key)
        return _json(request, 409, {"ok": False, "error": "replay", "key": key})

    # Published exactly as received. See the module docstring: the envelope is
    # the LIMS's document, and the only thing this handler is entitled to say
    # about it is whether it is authentic and whether it has been seen before.
    payload = system.util.jsonEncode(envelope)
    system.cirruslink.transmission.publish(BROKER, TOPIC, payload, 1, False)
    logger.infof("lims webhook published %s to %s", key, TOPIC)

    # The typed tag model, after the publish and never before it: the model must
    # not hold a review that did not reach the wire. `model_feed` documents that
    # it never raises, and this is the belt -- a throw here would be a 500, which
    # the outbox answers by POSTing again, republishing the review and firing 07
    # a second time. A bare except because a Java throwable is not a Python one.
    try:
        model_feed.write_review(envelope)
    except:
        import traceback
        logger.warnf("lims webhook published %s but the model feed failed: %s",
                     key, traceback.format_exc().splitlines()[-1])

    return _json(request, 200, {"ok": True, "key": key, "topic": TOPIC})
