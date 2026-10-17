"""Pattern 6 -- poll a particle counter, store the reading, publish it.

**Nothing pushes.** The instrument does not know this gateway exists. It samples
on its own clock, holds results in a rolling buffer, and the `06-poll` gateway
timer calls `poll()` every 30 s to find out what happened. The honest consequence
is a **detection gap**: for up to one poll interval plus one sample duration the
room can be out of spec and the backbone can be silent about it. That gap is
characterized, not hidden -- `ts` is the instrument's clock and
`values.ingest_ts` is ours, so every message on the wire shows it.

**The cursor is a watermark the vendor did not call one.** `getSamples(cursor,
limit)` returns records *after* a bookmark, oldest-first, plus a fresh bookmark.
Because new analyses get ever-increasing ids appended to the end, "everything
after bookmark 42" and "everything new since I last looked" are the same
sentence -- the vendor shipped a change feed and documented it as pagination.
`hasMore: true` means the server truncated at `limit`, so this walks pages until
it is false, then stores the bookmark in a tag.

**Four things about the ordering below are decisions, not accidents:**

1. **Store before publish.** A publish failure leaves a stored reading that never
   reached the backbone -- recoverable, and pattern 7 can still find it. The
   reverse leaves a message on the wire the store cannot corroborate, which is
   worse in a GxP argument.
2. **The watermark advances only after a page is fully processed**, so a mid-page
   exception re-reads the page rather than skipping it.
3. **The dedupe is per record, not per page.** `state/last_sequence` skips what
   we already handled, and `em.reading`'s UNIQUE (device_id, analysis_id) is the
   enforced second lock -- an insert that affects no rows means the analysis was
   already stored, so it is not published again.
4. **`state/cursor` is written once per poll, not once per page**, so a partial
   walk leaves the watermark where the last complete page ended.

**The excursion flag is ours and lives here.** The instrument reports counts and
has no idea what a cleanroom limit is. `config/excursion_threshold` on the UDT is
the only copy of the rule; pattern 7 reads `status` and must never compare counts
itself. docs/00-architecture.md, "Derived flags travel with the fact that
produced them".

**The reading is stored twice over, in one row.** `em.reading`'s columns are the
reading as a row and pattern 7's `sql` source reads them; `em.reading.document`
is the same reading as an OBJECT -- the counter instance's `current/` member
document, in the shape the i3X server's `/objects/value` returns -- and the i3X
history branch hands it back untouched. Both come off the one `members` list
`_members` builds, which also goes to the tags, so the row, the document and the
live object cannot say three different things. migrate-13, and the reason is in
`_document`.

**The stale-cursor trap is deliberate.** Restart the simulator and its sequence
numbers begin at 1 again while `state/cursor` still points past the end. The
server answers "nothing after 45" -- correctly -- and this poll runs perfectly
while publishing nothing, with every health check green. Clearing `state/cursor`
in Tag Explorer is the recovery, and an empty cursor also resets the
`last_sequence` guard for that poll (see `_watermark`), so one gesture is the
whole fix.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "particle_counter_poll"

# The instrument. HTTPS with a self-signed certificate generated at container
# start; `bypass_cert_validation` is measured-correct on 8.3.8 and the gateway
# logs a warning every time, which is the right amount of noise for a trade this
# size. Right for a simulator on a private compose network, wrong in a plant.
ENDPOINT = "https://sim-particle-counter:8443/graphql"
USERNAME = "admin"
PASSWORD = "password"
HTTP_TIMEOUT_MS = 10000

# NOT `pg_db`. That one is the historian's own store -- the `ignition` database
# as user `ignition`, holding sqlth_*/sqlt_data_* and nothing this project
# writes -- so it will pass a glance in the dropdown and then write nowhere useful.
# docs/00-architecture.md is emphatic about this.
DATASOURCE = "ICC26"

PROJECT = "icc-2026"
STREAM = "06_poll/particle-counter-result"

# The tag path mirrors the topic exactly, which every pattern does and this one
# keeps through two moves: into qc/analyzers on 2026-08-25, out to
# env_monitoring on 2026-09-13. The device id is the last segment, taken from
# the path the same way bes_batch takes equipment_id from its own -- so it is
# still `particle-counter-01` after both moves, and `em.reading` never noticed
# either one.
BASE = "[default]icc26/site1/env_monitoring/particle-counter-01"

# 50 records is ~8 minutes of backlog at a 10 s sample. Small enough that the
# hasMore branch is exercised by any real stall rather than being dead code.
PAGE_LIMIT = 50
# A walk that will not terminate is a bug, not a backlog. 20 pages is 1000
# records, well past the simulator's buffer cap.
MAX_PAGES = 20

# The channel the threshold applies to. ONE channel, on purpose: a single raw
# count cannot also threshold 5.0 um, whose ISO 7 limit is two orders of
# magnitude lower, and a second number here would be a second copy of the
# cleanroom spec. See the excursion_threshold tag's documentation.
EXCURSION_CHANNEL_UM = 0.5

NORMAL = "normal"
EXCURSION = "excursion"

_AUTH_QUERY = ("mutation($u: String!, $p: String!) "
               "{ authenticate(username: $u, password: $p) }")

_SAMPLES_QUERY = """
query getSamples($cursor: String, $limit: Int) {
  getSamples(cursor: $cursor, limit: $limit) {
    samples {
      id deviceId deviceName sequenceNumber startedAt completedAt status
      results {
        channels { sizeUm particleCount }
        totalVolume { units value }
        environment {
          flowRate { average { units value } }
          temperature { average { units value } }
          humidity { average { units value } }
        }
      }
      operator { name username role }
    }
    pagination { nextCursor hasMore }
  }
}
"""

_INSERT = ("INSERT INTO em.reading "
           "(device_id, analysis_id, sequence_number, location, operator, status, "
           " total_volume_l, channels, environment, occurred_at, document) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?::jsonb, ?::jsonb, ?, ?::jsonb) "
           "ON CONFLICT (device_id, analysis_id) DO NOTHING")

# The cached bearer token. A dict rather than a module global with `global`, so
# there is one obvious place the session lives. Tokens are short-lived (~5 min)
# by the simulator's design and this re-authenticates on any 401 rather than
# tracking expiry -- so the re-auth path is exercised on every demo instead of
# being dead code that fails in a year.
_SESSION = {"token": None}


# ── small helpers ────────────────────────────────────────────────────────────

def _iso(date=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if date is None:
        date = Date()
    return formatter.format(date)


def _parse_iso(text):
    """The instrument's '2026-08-29T14:03:22.145Z' as a java.util.Date."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    return formatter.parse(str(text))


def _device_id(base):
    """'particle-counter-01' from the tag path -- the topic form."""
    parts = [p for p in str(base).split("/") if p]
    return parts[-1] if parts else ""


def _channel_tag(size_um):
    """0.5 -> 'ch_0_5', 10.0 -> 'ch_10_0'. One naming rule, applied everywhere."""
    return "ch_" + ("%.1f" % float(size_um)).replace(".", "_")


def _client():
    return system.net.httpClient(timeout=HTTP_TIMEOUT_MS,
                                 bypass_cert_validation=True)


# ── the vendor API ───────────────────────────────────────────────────────────

class _Unauthorized(Exception):
    """A 401. Not an error condition -- the token expired, so mint another."""


def _post(client, query, variables, token):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    response = client.post(
        ENDPOINT,
        data=system.util.jsonEncode({"query": query, "variables": variables}),
        headers=headers)
    status = response.getStatusCode()
    if status == 401:
        raise _Unauthorized("the instrument returned 401")
    if status < 200 or status >= 300:
        raise Exception("the instrument returned HTTP %d" % status)
    document = response.getJson()
    errors = document.get("errors") if isinstance(document, dict) else None
    if errors:
        raise Exception("GraphQL error: %s" % str(errors))
    return document["data"]


def _authenticate(client):
    data = _post(client, _AUTH_QUERY, {"u": USERNAME, "p": PASSWORD}, None)
    token = data.get("authenticate")
    if not token:
        raise Exception("authentication rejected -- check the credentials")
    _SESSION["token"] = token
    return token


def _get_samples(client, cursor, limit):
    """One page, re-authenticating once if the cached token has expired."""
    token = _SESSION["token"] or _authenticate(client)
    try:
        data = _post(client, _SAMPLES_QUERY,
                     {"cursor": cursor, "limit": limit}, token)
    except _Unauthorized:
        system.util.getLogger(LOGGER_NAME).info(
            "token expired; re-authenticating")
        data = _post(client, _SAMPLES_QUERY, {"cursor": cursor, "limit": limit},
                     _authenticate(client))
    return data["getSamples"]


# ── one analysis, turned into our record ─────────────────────────────────────

def _build(sample, device_id, threshold):
    """The vendor's record plus the two fields we author: status and location.

    `location` rides in the vendor's `deviceName` because the vendor record has
    no location field and that is its only free text. A portable counter labelled
    with its sampling point is what happens in the field, so it is defensible; it
    is still us putting our meaning in the vendor's box.
    """
    results = sample.get("results") or {}
    channels = []
    excursion_count = None
    for channel in results.get("channels") or []:
        size = float(channel.get("sizeUm"))
        count = int(channel.get("particleCount") or 0)
        channels.append({"size_um": size, "count": count})
        if abs(size - EXCURSION_CHANNEL_UM) < 1e-9:
            excursion_count = count

    environment = results.get("environment") or {}

    def average(section):
        node = (environment.get(section) or {}).get("average") or {}
        return node.get("value")

    volume = (results.get("totalVolume") or {}).get("value")
    operator = (sample.get("operator") or {}).get("name")

    # The whole of the cleanroom rule, in one comparison, in one place. An
    # analysis whose 0.5 um channel is missing cannot be judged, so it is
    # reported normal rather than silently excursion -- but the log says so.
    if excursion_count is None:
        system.util.getLogger(LOGGER_NAME).warnf(
            "analysis %s carries no %.1f um channel; status defaults to normal",
            str(sample.get("sequenceNumber")), EXCURSION_CHANNEL_UM)
        status = NORMAL
    else:
        status = EXCURSION if excursion_count > threshold else NORMAL

    return {
        "device_id": device_id,
        # The vendor's own uuid for this analysis. Stable across an instrument
        # restart, which `sequence_number` is NOT -- see `_store`.
        "analysis_id": sample.get("id"),
        "sequence_number": sample.get("sequenceNumber"),
        "status": status,
        "location": sample.get("deviceName"),
        "operator": operator,
        "started_at": sample.get("startedAt"),
        "completed_at": sample.get("completedAt"),
        "total_volume_l": volume,
        "channels": channels,
        "flow_rate_lpm": average("flowRate"),
        "temperature_c": average("temperature"),
        "humidity_pct": average("humidity"),
        "ingest_ts": _iso(),
    }


def _environment(record):
    """The three conditions the analysis was drawn under, under one set of names.

    One dict, two sinks: `em.reading.environment` as jsonb and
    `current/conditions/*` as tags. Pattern 7 rebuilds the published block from
    whichever of the two it read, so the key names have to match on both sides
    -- and this is the only place they are written down.
    """
    return {
        "flow_rate_lpm": record["flow_rate_lpm"],
        "temperature_c": record["temperature_c"],
        "humidity_pct": record["humidity_pct"],
    }


def _millis(value):
    """A java.util.Date as epoch milliseconds, or the value untouched.

    **This is the i3X server's DateTime encoding, not a choice made here.** A
    DateTime member goes out of `/objects/value` as a bare number of epoch
    millis -- measured 2026-09-18 against the vendored server, and what
    `sample_chain._row_instant` tries first. The stored document has to use the
    same encoding or the object read from history would not match the object
    read live, which is the whole point of storing it pre-shaped.

    Copied from `model_feed._millis` rather than imported, as `_iso` and
    `_parse_iso` above are copied from `sample_chain`: two modules on opposite
    sides of the stack should not acquire an import edge over two lines, and the
    rule they both state belongs to the server, not to either of them.
    """
    if isinstance(value, Date):
        return value.getTime()
    return value


def _members(record):
    """The analysis as `(member path, value)` pairs, relative to the instance.

    **One list, three sinks**: the tag write in `_write_current`, the stored
    document in `_document`, and through that the i3X history row. The idiom is
    `model_feed.write_review`'s, for the same reason -- the shape of this object
    is defined in exactly one place, so the live object and its history cannot
    drift apart no matter which one a consumer reads.

    The paths are the real member paths, folders and all, because that is what
    makes `_document` able to nest by construction rather than by a second
    statement of the layout.

    `current/` only. `state/` is the poll's own cursor and watermark and
    `config/` is the cleanroom rule; neither is a fact about the analysis, and
    an environmental record that carries our bookkeeping inside it is a record
    somebody has to explain.
    """
    members = [
        # The instrument's completedAt -- the instant the analysis is ABOUT, and
        # the one pattern 7 searches against. Not `ingest_ts`, which is ours.
        ("current/ts", _parse_iso(record["completed_at"])),
        ("current/sequence_number", record["sequence_number"]),
        # OURS, not the instrument's: `_build` set it against
        # config/excursion_threshold. The flag travels with the fact.
        ("current/status", record["status"]),
        ("current/location", record["location"]),
        ("current/operator", record["operator"]),
        ("current/total_volume_l", record["total_volume_l"]),
    ]
    # The same three values `_store` puts in em.reading.environment, under the
    # same names, so whichever source pattern 7 reads it rebuilds one block.
    for name, value in _environment(record).items():
        members.append(("current/conditions/" + name, value))
    for channel in record["channels"]:
        members.append(("current/" + _channel_tag(channel["size_um"]),
                        channel["count"]))
    return members


def _document(members):
    """The members nested by their own paths: the reading as an i3X object.

    `[("current/conditions/humidity_pct", 43.5), ...]` becomes
    `{"current": {"conditions": {"humidity_pct": 43.5}, ...}}`, which is exactly
    what `POST /objects/value` returns for this instance's `current` folder --
    the server builds its value document out of the same tag paths, so nesting
    on "/" here is not an imitation of its layout, it *is* the layout.

    **Stored pre-shaped so the read side can hand it back untouched.** The
    vendored `i3x` project cannot import this module -- neither project inherits
    the other -- so a mapping written over there would be a second copy of this
    function, free to drift the first time a member is added. `_counterHistory`
    in `i3x/handlers/code.py` therefore does no mapping at all, and that is only
    sound because of this function. See `ignition/projects/i3x/PROVENANCE.md`.

    A member whose value is None is written as a null, never skipped: the
    object's shape must not depend on whether the instrument reported a field.
    """
    document = {}
    for path, value in members:
        parts = str(path).split("/")
        node = document
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _millis(value)
    return document


def _store(record, members):
    """INSERT INTO em.reading. Returns the number of rows written: 1 or 0.

    **Zero means the analysis was already stored**, and because storing happens
    before publishing, an analysis already in the table is one the backbone has
    already seen. So the caller does not publish it again.

    The unique key is (device_id, analysis_id), the vendor's own uuid -- NOT
    (device_id, sequence_number). Sequence numbers restart at 1 when the
    instrument restarts, so keying on them would make every reading of a fresh
    run collide with the previous run's rows and be dropped in silence, which is
    precisely the failure the stale-cursor demo is supposed to recover FROM.

    **Takes the same `members` list the tags get**, and stores the document it
    nests into alongside the columns. The columns are the reading as a row and
    the document is the reading as an object; both are written in one statement
    so no reader can ever find one without the other. migrate-13.
    """
    return system.db.runPrepUpdate(
        _INSERT,
        [record["device_id"],
         record["analysis_id"],
         record["sequence_number"],
         record["location"],
         record["operator"],
         record["status"],
         record["total_volume_l"],
         system.util.jsonEncode(record["channels"]),
         system.util.jsonEncode(_environment(record)),
         _parse_iso(record["completed_at"]),
         system.util.jsonEncode(_document(members))],
        database=DATASOURCE)


def _write_current(base, members):
    """The live view. Overwritten by every published analysis.

    **Takes the same `members` list `_store` was given**, so the tags and the
    stored document are the same values under the same names by construction,
    not by two lists kept in step. `_members` holds the paths; this function
    holds nothing but the prefix.

    **On tag history, third revision, and this one is the honest end of it.**
    History on `current/` was off originally, and the reason was good: an
    analysis is one event -- six channel counts, a status, a location, an
    operator, a volume and three conditions -- and the historian stores it as a
    dozen independent scalar series that merely share an instant. It was turned
    on 2026-09-18 because `POST /objects/history` was then the i3X read path for
    this object and the historian was the only thing behind it. Measured
    2026-09-19, that path returned four rows inside 32 ms for one analysis with
    every member null but `total_volume_l`: forward-fill reassembling states the
    object was never in. The objection was right the first time.

    Since migrate-13 the i3X history for this object comes from
    `em.reading.document` and does not touch the historian at all. So these tags
    are historised for **no reader**, and the reason they are still historised is
    only that `_counterHistory` returns an empty list on a query failure rather
    than falling back -- turning history off converts a Postgres outage from
    degraded into total. Land, measure, then flip; the same position the
    analyzer's 38 `result/` members are in, for the same reason.

    What the tags are genuinely for is unchanged and is not history: they are the
    object's live value on `/objects/value`, what an i3X subscription pushes, and
    what the Explorer shows. That is why every `current/` member is still written
    here, `operator` and `conditions/` included, and why `state/` and `config/`
    are still not -- those describe the poll, not the analysis.
    """
    paths = []
    values = []
    for path, value in members:
        paths.append(base + "/" + path)
        values.append(value)
    system.tag.writeBlocking(paths, values)


# ── the Event Stream transform ───────────────────────────────────────────────

def build_document(record):
    """The published document, built here rather than in Event Stream user code.

    Called from `06_poll/particle-counter-result`'s transform with what `poll()` published,
    which is a JSON **string**: `system.eventstream.publishEvent` coerces its
    data argument to String and raises TypeError on a dict (measured
    2026-08-29 -- the spec predicted a dict would travel). A dict is still
    accepted here, because what an Event Stream's source encoder hands a
    transform is not worth being brittle about.

    **`ts` + `values` only, the same shape pattern 3 publishes.** `seq`, `source`
    and `meta` were dropped 2026-09-13: nothing subscribes to this topic --
    pattern 7 reads `em.reading` out of Postgres -- so every one of them was a
    field written for a reader that does not exist. `values.sequence_number`
    stays, because that is the instrument's own monotonic number and a consumer
    diffing a replay needs it.

    **The ingest instant moved rather than went**, exactly as patterns 4 and 7 did
    on the same day -- `values.verified_at` and `values.assessed_at` there,
    `values.ingest_ts` here. It was the only published record of when the poll
    found a reading, and `ts` against it is the detection gap on the wire. It is
    a measured fact about the analysis, not metadata about the message, so
    `values` is where it belonged anyway. The stage beat is unchanged; it is read
    one level shallower.
    """
    if record is None:
        return None
    if not isinstance(record, dict):
        try:
            record = system.util.jsonDecode(str(record))
        except Exception:
            system.util.getLogger(LOGGER_NAME).warnf(
                "event data was neither a dict nor JSON: %s", str(record)[:200])
            return None

    envelope = {
        # The instrument's completedAt. `values.ingest_ts` is when the poll found
        # it, and those two differing by tens of seconds -- visible in every
        # message on mosquitto_sub -- is the detection gap on the wire without
        # anybody having to explain it.
        "ts": record.get("completed_at"),
        "values": {
            "ingest_ts": record.get("ingest_ts"),
            "sequence_number": record.get("sequence_number"),
            "status": record.get("status"),
            "location": record.get("location"),
            "operator": record.get("operator"),
            "started_at": record.get("started_at"),
            "completed_at": record.get("completed_at"),
            "total_volume_l": record.get("total_volume_l"),
            "channels": record.get("channels"),
            "flow_rate_lpm": record.get("flow_rate_lpm"),
            "temperature_c": record.get("temperature_c"),
            "humidity_pct": record.get("humidity_pct"),
        },
    }
    return system.util.jsonEncode(envelope)


# ── the poll ─────────────────────────────────────────────────────────────────

def _watermark(cursor, last_sequence):
    """The dedupe floor for this poll.

    An empty `state/cursor` means "start from the beginning", which is what
    somebody clearing that tag in Tag Explorer is asking for. Holding a sequence
    watermark from the previous run of the instrument would then skip every
    record of the replay they just asked for -- so clearing the cursor clears the
    floor too, and ONE gesture is the whole recovery from the stale-cursor trap.
    `em.reading`'s unique constraint is what keeps that safe.
    """
    if not cursor:
        return 0
    try:
        return int(last_sequence or 0)
    except Exception:
        return 0


def poll(base=BASE):
    """One pass: walk the cursor, store and publish everything new.

    Called from the `06-poll` gateway timer. The cadence is that timer's
    `attributes.delay`, not `config/poll_interval_s` -- which this function does
    not read; see the timer's own docstring. Returns the number of analyses
    published.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    config = system.tag.readBlocking([
        base + "/config/enabled",
        base + "/config/excursion_threshold",
        base + "/state/cursor",
        base + "/state/last_sequence",
    ])
    enabled = config[0].value
    threshold = config[1].value
    cursor = config[2].value
    last_sequence = _watermark(cursor, config[3].value)

    if not enabled:
        # The stall demo. The instrument keeps sampling and the backbone goes
        # quiet, with nothing failing and nothing alarming. Deliberately silent
        # in the log too -- an INFO line every 30 s would be the tell.
        return 0

    if threshold is None:
        logger.error("config/excursion_threshold is not set; refusing to poll")
        system.tag.writeBlocking([base + "/state/last_error"],
                                 ["excursion_threshold is not set"])
        return 0
    threshold = int(threshold)

    device_id = _device_id(base)
    published = 0
    stored = 0
    pages = 0

    try:
        client = _client()
        while pages < MAX_PAGES:
            page = _get_samples(client, cursor, PAGE_LIMIT)
            pages = pages + 1
            pagination = page.get("pagination") or {}

            for sample in page.get("samples") or []:
                sequence = int(sample.get("sequenceNumber") or 0)
                if sequence <= last_sequence:
                    continue

                record = _build(sample, device_id, threshold)
                # Built once, here, and handed to both sinks: the row and the
                # document go into `em.reading` together and the same values
                # land on the tags. See `_members`.
                members = _members(record)

                # Store, then publish. A publish failure leaves a stored reading
                # that never reached the backbone -- recoverable, and pattern 7
                # can still find it. The reverse leaves a message on the wire the
                # store cannot corroborate.
                rows = _store(record, members)
                if rows:
                    stored = stored + 1
                    # A JSON STRING, not the dict. `publishEvent` coerces its
                    # data argument to String and a dict raises TypeError --
                    # measured 2026-08-29, and it is why `build_document` below
                    # decodes what it is handed.
                    system.eventstream.publishEvent(
                        PROJECT, STREAM, system.util.jsonEncode(record), False)
                    _write_current(base, members)
                    published = published + 1
                else:
                    logger.infof("analysis %s was already stored; not republished",
                                 str(sequence))
                last_sequence = sequence

            # Only after the page is fully processed, so a mid-page exception
            # re-reads the page rather than skipping it.
            cursor = pagination.get("nextCursor") or cursor
            if not pagination.get("hasMore"):
                break
        else:
            logger.warnf("stopped after %d pages; the backlog is still draining",
                         MAX_PAGES)

        system.tag.writeBlocking(
            [base + "/state/cursor",
             base + "/state/last_sequence",
             base + "/state/last_poll_ts",
             base + "/state/last_error"],
            [cursor, last_sequence, Date(), ""])

        if published:
            logger.infof("%s: %d analysis(es) published, watermark %d, %d page(s)",
                         device_id, published, last_sequence, pages)
        return published

    except Exception as exc:
        # last_error is the surface an operator looks at. The cursor is NOT
        # written on this path: whatever was not fully processed gets re-read.
        message = "%s: %s" % (type(exc).__name__, str(exc))
        logger.errorf("poll failed for %s -- %s", device_id, message)
        system.tag.writeBlocking(
            [base + "/state/last_poll_ts", base + "/state/last_error"],
            [Date(), message])
        return published
