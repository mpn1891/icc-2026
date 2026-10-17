"""The i3X read client -- the one place this project speaks somebody else's API.

Three endpoints of the CESMII i3X 1.0 server vendored into
`ignition/projects/i3x`, wrapped thinly enough that `sample_chain` can ask the
model for the same two facts it used to read out of Postgres. **Read-only, and
that is the server's decision, not this module's**: `/info` reports
`update.current: false` and `update.history: false`, so there is nothing to
write even if a caller wanted to.

**The gateway calls itself over HTTP, on purpose.** A loopback round trip
through WebDev -- TLS, basic auth, JSON, the lot -- is slower than
`system.tag.readBlocking` and buys exactly one thing: it is the *same* request
the desktop i3X Explorer makes from another machine. Anything that works here
works for a third-party client, which is the whole argument the switch in
pattern 7 exists to make. A shortcut through the tag system would prove nothing.

**Port 8043, never 8088.** Ignition answers :8088 with a 302 to :8043 and Java
HTTP clients do not follow a redirect across schemes, so the plain-HTTP port
fails as an empty body rather than as an error anybody could read.
`bypass_cert_validation` is measured-correct on 8.3.8 against the gateway's
self-signed certificate -- right on a container's own loopback, wrong in a
plant, and the gateway logs a warning every time so nobody forgets which one
this is. Credentials are module constants, as in `particle_counter_poll` and
`lims_webhook`; the house convention is that a demo's secrets are visible rather
than hidden badly.

**Every call returns `(result, reason)` and nothing raises.** Five different
failures -- a refused connection, a timeout, a non-2xx status, a body that is
not JSON, and a server that answered 200 with `success: false` -- collapse into
one sentence, because the only thing the caller does with it is put it in a
`..._reason` key beside a null block. That is `sample_chain`'s contract for its
own lookups, kept identical here so the two compose without translation.

**The bulk endpoints are asked about one object at a time.** `/objects/value`
and `/objects/history` take a list and answer with a list of per-item results;
pattern 7 never has a second question, so these helpers return the first item's
result and treat a per-item failure as a failure of the call. Passing more ids
is allowed and the extras are simply not returned -- the day something needs
them, return `results` and leave this contract alone.

`_iso` and `_parse_iso` are copied from `sample_chain` rather than imported,
because `sample_chain` imports *this* module: the dependency has to run one way
and the lower layer cannot be the one that reaches up. They are the same
SimpleDateFormat pattern the server itself uses -- `i3x.ignition.DATE_FORMAT`,
`"yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"`, character for character -- which is why
`startTime`/`endTime` need no conversion beyond formatting and why a row's
`timestamp` can be handed back to the caller untouched.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Base64, Date, TimeZone

LOGGER_NAME = "i3x_client"

# The vendored server, reached on the gateway's own loopback. HTTPS on 8043 --
# see the module docstring for why :8088 is not an option.
BASE_URL = "https://localhost:8043/system/webdev/i3x"
USERNAME = "admin"
PASSWORD = "password"

# Five seconds. Both lookups sit between a review arriving and a deviation being
# published, so a stalled i3X server has to become a reason string quickly
# rather than hold the Event Stream open: the composite publishes either way.
HTTP_TIMEOUT_MS = 5000

# `Accept-Encoding: identity` is deliberate. The server gzips its response when
# the client asks for it, writing raw gzip bytes to the servlet stream, and
# whether this client would then decompress them is unverified. Nothing here is
# big enough for the trade to matter, so it asks for plain bytes and the
# question never arises.
_HEADERS = {"Content-Type": "application/json",
            "Accept": "application/json",
            "Accept-Encoding": "identity"}


# -- small helpers ------------------------------------------------------------

def _iso(date=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if date is None:
        date = Date()
    return formatter.format(date)


def _parse_iso(text):
    """'2026-08-30T18:25:54.330Z' as a java.util.Date."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    return formatter.parse(str(text))


def _client():
    return system.net.httpClient(timeout=HTTP_TIMEOUT_MS,
                                 bypass_cert_validation=True,
                                 username=USERNAME,
                                 password=PASSWORD)


def element_id(tag_path):
    """The i3X id of a tag path: base64url, no padding, `[default]` included.

    **Computing this locally is a demo shortcut the spec discourages**, and it
    is the one non-portable line in this client. An elementId is meant to be
    opaque: a client discovers it from `GET /objects` and carries it around, and
    a server free to change its encoding is exactly what "opaque" buys. This
    mirrors `i3x.utils.pathToElementId` in the vendored server instead, which is
    a fact about *that* server and true of no other -- accepted because a
    discovery round trip per lookup would buy nothing at demo scale, and to be
    rejected the moment this talks to a server somebody else wrote.

    The `[default]` provider prefix is part of the encoded string. Dropping it
    produces a well-formed id that resolves to nothing.
    """
    return Base64.getUrlEncoder().withoutPadding().encodeToString(str(tag_path))


# -- the response envelope ----------------------------------------------------

def _document(response):
    """The response body as a dict, or None if it is not one."""
    try:
        document = response.getJson()
    except Exception:
        return None
    return document if isinstance(document, dict) else None


def _explain(detail):
    """An i3X ErrorDetail as a trailing ' -- ...', or '' when there is none."""
    if isinstance(detail, dict):
        return " -- %s" % str(detail.get("detail") or detail.get("title"))
    return ""


def _server_detail(document):
    """The ErrorDetail the server sent: top level, or on the first failed item.

    A bulk response reports `success: false` at the top when *any* item failed
    and puts the explanation on the item, so both places have to be looked at to
    turn one response into one sentence.
    """
    if not isinstance(document, dict):
        return None
    if document.get("responseDetail") is not None:
        return document.get("responseDetail")
    for item in document.get("results") or []:
        if isinstance(item, dict) and not item.get("success"):
            return item.get("responseDetail")
    return None


def _call(path, body=None):
    """One request, one `(document, reason)`. Never raises. `body` None is a GET.

    The order of the checks is the order the failures happen in, and the status
    is read before the body because a 401 or a 500 still carries a JSON
    `responseDetail` worth quoting back.
    """
    url = BASE_URL + path
    try:
        client = _client()
        if body is None:
            response = client.get(url, headers=_HEADERS)
        else:
            response = client.post(url, data=system.util.jsonEncode(body),
                                   headers=_HEADERS)
    except Exception as exc:
        # A timeout lands here, and so does a refused connection: the `i3x`
        # project disabled in the Designer, the gateway still starting, the
        # WebDev module missing. All of them are "the server did not answer".
        return None, ("i3x %s did not answer -- %s: %s"
                      % (path, type(exc).__name__, str(exc)))

    status = response.getStatusCode()
    document = _document(response)

    if status < 200 or status >= 300:
        return None, ("i3x %s returned HTTP %s%s"
                      % (path, str(status), _explain(_server_detail(document))))
    if document is None:
        return None, ("i3x %s answered %s with a body that is not JSON"
                      % (path, str(status)))
    if not document.get("success"):
        # A 200 that says false. Bulk endpoints do this whenever one item
        # failed, which for a one-object request means the object.
        return None, ("i3x %s reported failure%s"
                      % (path, _explain(_server_detail(document))))
    return document, None


def _first(document, path):
    """The one result out of a bulk response. (result, reason).

    The per-item guard is belt and braces: `_call` already refuses a response
    whose top-level `success` is false, and a bulk response with a failed item
    is one of those. It stays because "the envelope said true, the item said
    false" would otherwise return None as though it were an answer.
    """
    results = document.get("results")
    if not results:
        return None, "i3x %s returned no results" % path
    item = results[0]
    if not isinstance(item, dict):
        return None, "i3x %s returned a malformed result" % path
    if not item.get("success"):
        return None, ("i3x %s could not read %s%s"
                      % (path, str(item.get("elementId")),
                         _explain(item.get("responseDetail"))))
    return item.get("result"), None


# -- the three endpoints ------------------------------------------------------

def info():
    """`GET /info`: spec version, server version, capability matrix.

    The only endpoint that is not behind basic auth, so it is also the cheapest
    proof that the server is up and this client can reach it. Not on pattern 7's
    path -- a health check, and the first thing to run when a lookup starts
    coming back with a reason string.
    """
    document, reason = _call("/info")
    if reason:
        return None, reason
    # Not a bulk endpoint: one `result`, no per-item wrapper.
    return document.get("result"), None


def value(element_ids, max_depth=1):
    """`POST /objects/value`: the live value of the first object.

    The result is a CurrentValueResult -- `{"value": ..., "quality": ...,
    "timestamp": ..., "isComposition": ...}` -- and for a UDT instance `value`
    is the whole instance as a nested document, folder by folder.

    **`maxDepth` counts nested UDT instances, not folders.** At 1 the value
    carries this object's own members at whatever folder depth they sit; raising
    it adds composition children under `components`, keyed by their elementIds.
    """
    document, reason = _call("/objects/value",
                             {"elementIds": list(element_ids),
                              "maxDepth": max_depth})
    if reason:
        return None, reason
    return _first(document, "/objects/value")


def history(element_ids, start, end, max_depth=1):
    """`POST /objects/history` over [start, end]. (result, reason).

    `start` and `end` are java.util.Dates and both are required -- the server
    answers 400 without them. The result is a HistoricalValueResult,
    `{"values": [...], "isComposition": ...}`, each entry
    `{"value": {...}, "quality": ..., "timestamp": ...}`, oldest first.

    **The shape of `value` depends on where that object's history lives**, and
    the caller has to know which. For an object served from the tag historian --
    the bioreactor, the valves, the process values -- the keys are leaf tag
    names, flattened: the server builds them with `getTagNameFromPath`, so
    `batch_data/batch_id` arrives as `batch_id`. Two tags sharing a leaf name
    under different folders of one object would collide, which is worth knowing
    before adding one. Those rows are also forward-filled -- one row per distinct
    change instant across the object's tags, each tag carrying its last value as
    of that instant, null only before its first stored point in the range, or
    right through it if it is not historised at all.

    For an object the server's local fork serves from an **event store**, the
    value is the stored member document, nested exactly as `/objects/value`
    returns it, and nothing is forward-filled because nothing was taken apart:
    one row is one event. That is `lims_review`, `cell_analyzer` and, since
    migrate-13, `particle_counter`. `ignition/projects/i3x/PROVENANCE.md` is the
    list, and it is the server's decision, not this client's -- a caller that
    guesses wrong reads every key as `None` rather than erroring.
    """
    document, reason = _call("/objects/history",
                             {"elementIds": list(element_ids),
                              "startTime": _iso(start),
                              "endTime": _iso(end),
                              "maxDepth": max_depth})
    if reason:
        return None, reason
    return _first(document, "/objects/history")
