"""Pattern 4 -- wrap a NovaFlex HTTPS POST in the pattern-3 envelope.

The instrument POSTs its own result shape to an Event Stream HTTP source. This module is the
filter and the transform on that stream: it checks the shared secret, then rebuilds the body
as the SAME envelope opcua_event.build_novaflex_result already publishes, with
meta.mechanism = "webhook". Transmission publishes both onto
icc26/site1/qc/analyzers/novaflex-01/result.

A subscriber reading that topic gets two documents with identical `values` keys and cannot
tell which arrived by OPC UA and which by HTTP -- except by reading meta.mechanism, which is
exactly the claim the namespace makes. That is the whole reason this file duplicates the
`values` tree instead of inventing a smaller one.

AUTHORED BLIND. At the time of writing the gateway was not running and the Event Stream HTTP
source had never been opened in the UI, so the shape of `event` is INFERRED, not observed.
Three specific unknowns, all handled defensively below and all listed in the deviations table
in docs/04-novaflex-webhook.md:

  1. Where the request headers live on the event object. _headers() tries several spellings.
  2. Whether `event.data` arrives already decoded (a dict) or as a string. _body() takes both.
  3. Whether a rejected event can produce HTTP 401 or only a dropped 200. This module cannot
     tell -- it has no access to the servlet response, unlike the WebDev handler in
     lims_webhook. Rejection here means "no MQTT message", and the HTTP status is whatever
     the source decided before user code ran.

_describe() logs the real shape of the first event at INFO. Read that line once against a
live gateway and the guesses above become facts; fix this file and delete the guesswork.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "webhook_event"

SOURCE_ID = "novaflex-01"
SOURCE_TYPE = "analyzer"
MECHANISM = "webhook"

SECRET_HEADER = "X-Webhook-Secret"
SECRET = "icc26-webhook-secret"

# Fail CLOSED when no headers can be found on the event at all.
#
# If the HTTP source turns out not to expose request headers to user code, every POST is
# rejected and pattern 4 goes silent -- which is a loud, diagnosable failure with a log line
# naming the remedy, and is the correct posture for an authentication check. The alternative
# (accept everything when the header is unreadable) would make checkpoint 4 pass by accident.
# Flip this to True only as a deliberate, documented stage workaround.
ALLOW_WHEN_NO_HEADERS = False

_seq = [0]
_described = [False]


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


def _drop_nones(value):
    """Recursively remove null-valued keys, and any object left empty by that.

    The absent-vs-zero rule. False and 0 survive -- they are measurements; only None is
    absence. Note this differs from pattern 3, which publishes an explicit JSON null for a
    Bad-quality tag: the OPC path knows the node exists and reads Bad, whereas here the key
    was simply never in the request. See the deviations table.
    """
    if not isinstance(value, dict):
        return value
    cleaned = {}
    for key in value:
        item = _drop_nones(value[key])
        if item is None:
            continue
        if isinstance(item, dict) and len(item) == 0:
            continue
        cleaned[key] = item
    return cleaned


def _describe(event, logger):
    """Log the real shape of the first event, once. The point of this file's first run."""
    if _described[0]:
        return
    _described[0] = True
    try:
        logger.infof("webhook event shape: type=%s attributes=%s",
                     str(type(event)), str(dir(event)))
        data = getattr(event, "data", None)
        logger.infof("webhook event.data: type=%s", str(type(data)))
        meta = getattr(event, "metadata", None)
        if meta is None:
            meta = getattr(event, "meta", None)
        logger.infof("webhook event.metadata: type=%s attributes=%s",
                     str(type(meta)), str(dir(meta)) if meta is not None else "None")
    except Exception:
        logger.warn("could not describe the webhook event shape")


def _headers(event):
    """Best effort at the inbound request headers. Returns a dict, possibly empty.

    GUESSED. Tries event.metadata.headers, event.metadata as a header map, event.meta.*, and
    event.headers. One of these is probably right; _describe() tells you which.
    """
    candidates = []
    for name in ("metadata", "meta"):
        holder = getattr(event, name, None)
        if holder is None:
            continue
        if isinstance(holder, dict):
            if "headers" in holder:
                candidates.append(holder.get("headers"))
            candidates.append(holder)
        else:
            candidates.append(getattr(holder, "headers", None))
            candidates.append(getattr(holder, "getHeaders", None))
    candidates.append(getattr(event, "headers", None))

    for candidate in candidates:
        if candidate is None:
            continue
        if callable(candidate):
            try:
                candidate = candidate()
            except Exception:
                continue
        if isinstance(candidate, dict):
            if len(candidate) > 0:
                return candidate
            continue
        try:
            converted = dict(candidate)
        except Exception:
            continue
        if len(converted) > 0:
            return converted
    return {}


def _header(headers, name):
    """Case-insensitive header lookup. HTTP header maps are inconsistently cased."""
    wanted = name.lower()
    for key in headers:
        if str(key).lower() == wanted:
            value = headers[key]
            if isinstance(value, (list, tuple)) and len(value) > 0:
                return str(value[0])
            return str(value)
    return ""


def _body(event):
    """The POSTed JSON object, or None if it cannot be read as one."""
    data = getattr(event, "data", None)
    if data is None:
        data = event
    if isinstance(data, (str, unicode)):
        try:
            data = system.util.jsonDecode(data)
        except Exception:
            return None
    if isinstance(data, dict):
        return data
    try:
        return dict(data)
    except Exception:
        return None


def _secret_ok(event, logger):
    headers = _headers(event)
    if len(headers) == 0:
        if ALLOW_WHEN_NO_HEADERS:
            logger.warn("novaflex webhook: no headers on the event -- accepting anyway "
                        "because ALLOW_WHEN_NO_HEADERS is True")
            return True
        logger.warn("novaflex webhook rejected: no request headers reachable on the event. "
                    "Read the 'webhook event shape' line above, fix _headers() in "
                    "webhook_event, then `python tasks.py scan`.")
        return False
    supplied = _header(headers, SECRET_HEADER)
    if not supplied:
        auth = _header(headers, "Authorization")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
    if supplied != SECRET:
        logger.warn("novaflex webhook rejected: missing or wrong " + SECRET_HEADER)
        return False
    return True


def accept(event):
    """Event Stream FILTER. True publishes, False drops before the transform runs.

    The secret check lives here rather than in the transform on purpose. A filter returning
    False is Event Streams' own documented way to drop an event; relying on the transform
    returning None would depend on how the handler treats a null payload, and a Transmission
    handler that published the string "null" onto the result topic would be a bad thing to
    discover on stage. The transform re-checks anyway -- belt and braces, cheap.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    _describe(event, logger)
    if not _secret_ok(event, logger):
        return False
    body = _body(event)
    if not body:
        logger.warn("novaflex webhook rejected: body was not a JSON object")
        return False
    if not body.get("SampleID"):
        logger.warn("novaflex webhook rejected: no SampleID in the body")
        return False
    if not body.get("SampleTime"):
        logger.warn("novaflex webhook rejected: no SampleTime in the body")
        return False
    return True


def build_novaflex_result(event):
    """Event Stream TRANSFORM. Vendor POST body -> the pattern-3 envelope, as a JSON string.

    Returns None to drop, which should never happen in practice because accept() already
    rejected everything this can reject.

    ts is the vendor SampleTime, exactly as pattern 3 uses the vendor SampleTime tag.
    meta.ingest_ts is when Ignition received the POST. meta.correlation_id is sample_id, the
    same field pattern 3 stamps, so one sample id is traceable across two mechanisms on the
    firehose -- when the two simulators have been lined up by hand, which is not automatic.
    See docs/04-novaflex-webhook.md.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    if not _secret_ok(event, logger):
        return None
    body = _body(event)
    if not body or not body.get("SampleID") or not body.get("SampleTime"):
        return None

    gas = body.get("Gas") or {}
    chem = body.get("Chem") or {}
    cell = body.get("CellDensity") or {}
    calc = body.get("Calculated") or {}
    mods = body.get("Modules") or {}

    envelope = {
        "ts": body.get("SampleTime"),
        "seq": _next_seq(),
        "source": {"id": SOURCE_ID, "type": SOURCE_TYPE},
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso(),
            "correlation_id": body.get("SampleID"),
        },
        "values": {
            "sample_id": body.get("SampleID"),
            "batch_id": body.get("BatchID"),
            "vessel_id": body.get("VesselID"),
            "cell_type": body.get("CellType"),
            "sample_source": body.get("SampleSource"),
            "operator": body.get("Operator"),
            "gas": {
                "ph": gas.get("pH"),
                "pco2": gas.get("pCO2"),
                "po2": gas.get("pO2"),
            },
            "chem": {
                "na": chem.get("Na"),
                "k": chem.get("K"),
                "ca": chem.get("Ca"),
                "nh4": chem.get("NH4"),
                "gln": chem.get("Gln"),
                "glu": chem.get("Glu"),
                "gluc": chem.get("Gluc"),
                "lac": chem.get("Lac"),
            },
            "osmo": body.get("Osmo"),
            "cell_density": {
                "total_density": cell.get("TotalDensity"),
                "viable_density": cell.get("ViableDensity"),
                "viability_percent": cell.get("Viability"),
                "avg_live_diameter_um": cell.get("AvgLiveDiameter"),
            },
            "calculated": {
                "hco3": calc.get("HCO3"),
                "o2_saturation": calc.get("O2Saturation"),
                "co2_saturation": calc.get("CO2Saturation"),
            },
            "modules_used": {
                "cdv": mods.get("CDV"),
                "chemistry": mods.get("Chemistry"),
                "gas": mods.get("Gas"),
                "osmo": mods.get("Osmo"),
            },
        },
    }
    payload = system.util.jsonEncode(_drop_nones(envelope))
    logger.infof("novaflex webhook accepted %s", str(body.get("SampleID")))
    return payload


BROKER = "chariot_broker"
TOPIC = "icc26/site1/qc/analyzers/novaflex-01/result"


def publish_directly(event):
    """BREAK-GLASS. Publish through Transmission from script instead of the stream handler.

    Not wired to anything. It exists because `system.cirruslink.transmission.publish` is the
    one call in this chain that IS proven against a real gateway -- lims_webhook used it and
    it was broker-verified on 2026-08-20. If the Event Stream's Transmission handler block
    turns out to be wrong in a way the file cannot express, set the stream's handler to a
    script handler calling this. See the WebDev fallback in docs/04-novaflex-webhook.md.
    """
    payload = build_novaflex_result(event)
    if payload is None:
        return False
    system.cirruslink.transmission.publish(BROKER, TOPIC, payload, 1, False)
    return True


# ── the WebDev fallback ──────────────────────────────────────────────────────────────────
#
# Not wired to anything either. It is here so the fallback is genuinely one step: if the
# blind-authored Event Stream HTTP source cannot be made to load, copy
# com.inductiveautomation.webdev/resources/lims/sample-result/ to .../novaflex/result/, point
# its doPost.py at handle_webdev, and repoint WEBHOOK_URL. That WebDev shape -- including the
# "resource-type": "python-resource" discriminator everything else 500s without -- was
# hand-authored as plain files and broker-verified on 2026-08-20.
#
# WebDev CAN set a real HTTP status, which the Event Stream transform cannot: it gets a
# servletResponse. So this path answers 401 on a bad secret rather than dropping a 200.


class _WebDevEvent:
    """Adapt WebDev's `request` dict onto the attribute shape the stream functions expect."""

    def __init__(self, request):
        self.data = request.get("postData")
        if self.data is None:
            self.data = request.get("data")
        self.metadata = _WebDevMetadata(request.get("headers") or {})


class _WebDevMetadata:
    def __init__(self, headers):
        self.headers = headers


def _webdev_status(request, code):
    response = request.get("servletResponse")
    if response is not None:
        response.setStatus(int(code))
        response.setContentType("application/json; charset=utf-8")


def handle_webdev(request):
    """WebDev doPost entry point for the fallback. Returns a WebDev response dict."""
    logger = system.util.getLogger(LOGGER_NAME)
    event = _WebDevEvent(request)
    _describe(event, logger)

    if not _secret_ok(event, logger):
        _webdev_status(request, 401)
        return {"json": system.util.jsonEncode({"ok": False, "error": "unauthorized"})}

    body = _body(event)
    if not body or not body.get("SampleID") or not body.get("SampleTime"):
        _webdev_status(request, 400)
        return {"json": system.util.jsonEncode(
            {"ok": False, "error": "SampleID and SampleTime are required"})}

    if not publish_directly(event):
        _webdev_status(request, 400)
        return {"json": system.util.jsonEncode({"ok": False, "error": "could not build"})}

    _webdev_status(request, 200)
    return {"json": system.util.jsonEncode(
        {"ok": True, "sample_id": body.get("SampleID"), "topic": TOPIC})}
