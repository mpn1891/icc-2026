"""Pattern 7 -- the composite. The first thing in this stack that READS.

Six acquisition mechanisms publish. This one subscribes to one of them, asks two
questions of what two of the others already stored, and -- **only when something
is wrong** -- puts the answer on the wire as a single deviation event.
**07's only job is to be the thing that asks.**

**Nothing here is clever, and that is the design.** This module does no
arithmetic, holds no process knowledge and computes no flags.
`qualified_window` was computed by `bes_batch` at insert time -- its `QUALIFIED`
tuple is the only copy of the batch protocol -- and `status` by
`particle_counter_poll` against `config/excursion_threshold`, the only copy of
the cleanroom limit. Both
travel with the fact that produced them (docs/00-architecture.md, "Derived flags
travel with the fact that produced them"). If this module ever tests
`operation == "GROWTH"` or compares a particle count to a number, the stack has
two copies of a rule and they will drift.

**The two lookups do not key on the same column.** This is the detail most
likely to be got wrong, so it is stated here as well as at each query:

  - `bes.batch_event` keys on the SAMPLE's `equipment_id` -- `br-201`, carried
    on the review message, parsed by pattern 1 out of its own topic.
  - `em.reading` has no `equipment_id` column at all. It keys on `device_id`, a
    constant: the particle counter is one instrument for the room, not one per
    vessel. Its `location` reads "USP Suite A - BR-201 sample port" -- that
    string is how the room ties to the vessel, and it is prose for a human. It
    is carried into the composite so a reader can see the association. It is
    **not** a join key and nothing here parses it.

**Publish only a deviation.** Until 2026-09-06 this module published one
composite per review, always. It now publishes only when the sample violated
something, and says what -- `values.violations`, a list that is never empty on a
message that exists. A clean sample produces no message at all, so silence on
`icc26/site1/qc/deviation` is the compliant case and every message on it is a
finding. The composite is still assembled in full and still carries both blocks
and both reasons, because a deviation document that omitted the context a
reviewer needs would be a worse record than none.

**The gate reads flags; it computes nothing.** This is what keeps the rule above
compatible with the rule below. `status` came from `particle_counter_poll` against
`config/excursion_threshold` and `disposition` from the analyst in the LIMS. 07
tests those two flags for a value it does not own, and holds no threshold, no
spec and no batch protocol of its own. See `_violations`.

**A lookup that finds nothing still produces a `null` block and a `_reason`
beside it** -- never a missing key, never a silent default. What changed is only
whether the document is published, not its shape.

**The particle counter rule is nearest either side, no tolerance.** A reading three
seconds *after* the valve closed is better evidence than one twenty-five seconds
before, so the search is not restricted to the past, and there is no cutoff at
which 07 refuses to answer. It reports the nearest reading and always reports
its age, and lets the reader judge -- which makes `age_s` load-bearing, so it
sits at the top of the block. A forty-minute-old reading must not be able to
read as current. Pattern 6's timer bounds the normal case to <= 27.2 s. The
`i3x` source below is the one exception, and it is a bounded one: nearest either
side still, inside a window, because the query it is made of has to have one.

**Suppression happens in the FILTER, not the transform, and that is not a
style choice.** Measured 2026-09-06: a transform returning `None` does not
suppress anything -- `transformEncoder` is `ignition.string`, so the handler
published the literal four-byte string `None` onto the topic. The Event Stream
filter is the only stage that can stop a message, so the gate is
`sample_chain.is_deviation(event.data)` there and `build()` is reached only for
samples already known to be deviations. Both entry points run the same two
lookups; at demo volume four lookups per review -- SQL queries, or HTTP round
trips on the `i3x` source -- is not worth a shared cache, and a stateless pair
cannot race the way a memo between two stages could.

**07 publishes nothing itself.** It returns a document; Event Stream
`07_chain/lims-review` hands that to the Transmission handler. Same shape as
patterns 3 and 6, and it is what keeps 07 a genuine backbone subscriber --
docs/talk-tracks/04-lims-webhook.md says this pattern and 07 are the only two
there are.

**Every lookup has two implementations and `CONTEXT_SOURCE` picks one.** `sql`
reads `bes.batch_event` and `em.reading` directly and the vessel's name off its
tag, which is what 07 has always done. `i3x` asks the vendored CESMII i3X server
for the same three facts over HTTP -- `/objects/history` on the bioreactor and
on the particle counter, `/objects/value` for the equipment identifier -- and
gets them out of the tag model instead of out of Postgres. The blocks come back
the same shape, key for key, because the point of the switch is that a consumer
cannot tell which one ran: flipping one constant is the whole gesture and no
other line of this module knows it happened. What the switch demonstrates is
that the composite's inputs are *available to anyone*, through a vendor-neutral
API a third-party client can call, and not only to code that shares this
gateway's database credentials.

**Two behaviours genuinely change with it, and neither is a bug.** First, the
environment lookup grows a window. `em.reading` has none -- nearest either side,
however far -- but `/objects/history` is a range query and must be given
bounds, so `EM_WINDOW_S` is an hour. The demo case (<= 27.2 s) is untouched, and
a simulator stopped since yesterday now becomes `environment_unverifiable`
rather than a reading with an enormous `age_s`. Both answers are honest; the
talk track says which one fired. Second, `batch_context.as_of` becomes the
historian's stamp for the row rather than `bes.batch_event.occurred_at` -- near
neighbours, but two different clocks, and the diff between the two sources will
show it.

**The reliability is asymmetric, and that is the price of vendor neutrality.**
The `sql` source reads the systems of record: `bes.batch_event` is tailed out of
the WAL by Debezium and `em.reading` is written before pattern 6 publishes
anything, so both are complete by construction. The `i3x` source reads this
gateway's own copy, and tag history has a gap wherever the gateway did -- a
restart, a stopped container, a tag phase 1 forgot to historise. That is a real
loss of fidelity, accepted deliberately in exchange for a lookup any i3X client
could make against any i3X server, and it is said out loud rather than
discovered later.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "sample_chain"

# `sql` | `i3x` -- which copy of the context 07 reads. See the module docstring
# for what changes with it and what deliberately does not. **The stage setting
# is "i3x"**: flipping this one line, live, is the demonstration, and every
# other line of this module is written so that it can be flipped back mid-demo
# without anything downstream noticing. An unrecognised value falls through to
# `sql`, because the systems of record are the safe default.
CONTEXT_SOURCE = "sql"

# The two windows the `i3x` source needs and the `sql` source does not.
# `_BATCH_QUERY` is an unbounded lookbehind with a LIMIT 1 and `_EM_QUERY`
# searches the whole table; `/objects/history` is a range query and refuses to
# be asked an open-ended question, so a bound has to be chosen rather than
# avoided. 24 h is comfortably more than one batch's worth of advances. 1 h is
# the environment rule argued in the module docstring -- short enough that a
# stalled simulator reads as unverifiable, long enough that the demo's 27.2 s
# never comes near it.
BATCH_LOOKBACK_S = 24 * 60 * 60
EM_WINDOW_S = 60 * 60

# NOT `pg_db`. That one is the historian's own store -- the `ignition` database
# as user `ignition`, holding sqlth_*/sqlt_data_* and nothing this project
# writes -- so it will pass a glance in the dropdown and then read nowhere
# useful. docs/00-architecture.md is emphatic about this.
# `pg_db` is not deletable either, which the pre-07 plan got wrong: the
# pg-historian provider and System/Gateway/StoreAndForward/pg_db/Pipelines/
# TagHistory are both bound to it. Do not select it; do not remove it.
DATASOURCE = "ICC26"

# The room's instrument, not the vessel's. One counter serves USP Suite A, so
# this is a constant and not derived from the sample. See the module docstring.
EM_DEVICE_ID = "particle-counter-01"

# The vessel as an object rather than as a value: what the `i3x` source asks
# about, and the parent of EQUIPMENT_TAG below. One copy of the path, not two.
BIOREACTOR_TAG = "[default]icc26/site1/upstream/bioreactors/%s"

# The counter as an object. `particle_counter_poll.BASE` is this same string;
# there the device id is the path's last segment and here the path is built from
# the device id, so the two cannot drift apart in either direction.
EM_TAG = "[default]icc26/site1/env_monitoring/%s" % EM_DEVICE_ID

# The plant model's own name for the asset, read rather than joined.
# `plant.equipment` is deliberately NOT on 07's path: it still holds `BR-201` in
# the wrong case and four `vib-*` leftovers, and a join added to tidy that up
# would put a fifth spelling of the vessel into a GxP document.
# docs/plans/07-sample-chain.md decision 4. **Do not add a join.**
# The `i3x` source reads the same member out of the object's value document
# instead of reading this leaf; both are the tag, neither is a join.
EQUIPMENT_TAG = BIOREACTOR_TAG + "/asset_data/equipment_identifier"

# What the reactor was doing. **The `sql` source only** --
# `_batch_context_i3x` asks the same question of `/objects/history` and does not
# touch this. Keyed on the SAMPLE's equipment_id, and served by
# ix_batch_event_lookup (equipment_id, occurred_at DESC, id DESC).
#
# The `id DESC` tie-break is not optional. One manual_advance writes
# `operation_end` and `operation_start` in one transaction sharing one
# occurred_at to the millisecond, so ordering on occurred_at alone leaves it to
# the planner which of the two 07 reads -- and the wrong one names the outgoing
# operation. The incoming row takes the higher id and must win.
# docs/plans/05-cdc-batch-event.md; verified live on rows 40 and 41.
_BATCH_QUERY = ("SELECT batch_id, operation, event_type, "
                "       payload->>'qualified_window', occurred_at "
                "FROM   bes.batch_event "
                "WHERE  equipment_id = ? "
                "  AND  occurred_at <= ? "
                "ORDER  BY occurred_at DESC, id DESC "
                "LIMIT  1")

# What the room was doing. **The `sql` source only** -- `_environment_i3x` asks
# the same question of `/objects/history`, within a window this query does not
# need. Keyed on a DEVICE, not on the reactor.
#
# The ordering is nearest EITHER SIDE, so it deliberately does not use
# ix_em_reading_lookup -- that index is ordered and this expression is not, and
# at demo volumes the sequential scan is irrelevant. If it ever matters the
# shape is two indexed queries, nearest-before and nearest-after, and pick the
# closer in Python. Not worth writing before it matters.
_EM_QUERY = ("SELECT status, channels, environment, occurred_at, location "
             "FROM   em.reading "
             "WHERE  device_id = ? "
             "ORDER  BY abs(extract(epoch FROM (occurred_at - ?))) "
             "LIMIT  1")


# -- small helpers ------------------------------------------------------------

def _iso(date=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if date is None:
        date = Date()
    return formatter.format(date)


def _parse_iso(text):
    """'2026-08-30T18:25:54.330Z' as a java.util.Date.

    Every timestamp 07 is handed came out of the house `_iso()` -- the LIMS
    writes the identical format from Python -- so one parser covers all of them.
    """
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    return formatter.parse(str(text))


def _decode(raw):
    """A jsonb column, which pgjdbc hands back as a JSON *string*, not an object.

    Carried through verbatim once decoded. 07 does not reshape what pattern 6
    stored -- a channel list that arrived as counts leaves as counts.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return system.util.jsonDecode(str(raw))
    except Exception:
        system.util.getLogger(LOGGER_NAME).warnf(
            "could not decode a jsonb column: %s", str(raw)[:200])
        return None


def _qualified(raw):
    """`payload->>'qualified_window'` arrives as the text 'true' or 'false'.

    Read, never computed. `bes_batch.QUALIFIED` is the only copy of the rule.
    """
    return str(raw).strip().lower() == "true"


def _sample_instant(values):
    """The instant material left the reactor -- the axis both lookups use.

    `collection.sample_completion` is the valve close: pattern 1's fact, and the
    only one of the candidates that describes the *sample* rather than the
    analysis or the review. `collected_at` (when the analyzer ran) stands in
    behind it only so a sample that somehow reached review without a close time
    still resolves to a real instant instead of to now().

    Returns (java.util.Date, the ISO string it came from).
    """
    collection = values.get("collection") or {}
    for text in (collection.get("sample_completion"),
                 values.get("collected_at")):
        if text:
            try:
                return _parse_iso(text), str(text)
            except Exception:
                system.util.getLogger(LOGGER_NAME).warnf(
                    "unparseable sample instant %s", str(text))
    return None, None


def _equipment_identifier_sql(equipment_id):
    """The plant model's name for the vessel, off the bioreactor UDT.

    A tag read, not a join -- see EQUIPMENT_TAG. Null when the tag is missing or
    bad-quality, because a composite that cannot name the asset is still worth
    publishing and the bare `equipment_id` beside it says the same thing.
    """
    if not equipment_id:
        return None
    try:
        qualified = system.tag.readBlocking([EQUIPMENT_TAG % equipment_id])[0]
        if qualified.quality.isGood() and qualified.value is not None:
            return str(qualified.value)
        system.util.getLogger(LOGGER_NAME).warnf(
            "equipment_identifier for %s read back %s",
            str(equipment_id), str(qualified.quality))
    except Exception as exc:
        system.util.getLogger(LOGGER_NAME).warnf(
            "equipment_identifier for %s unreadable -- %s: %s",
            str(equipment_id), type(exc).__name__, str(exc))
    return None


# -- the two lookups, against the systems of record ---------------------------

def _batch_context_sql(equipment_id, instant):
    """What the reactor was doing when the sample was drawn. (block, reason).

    The block carries `batch_id` out with it: batch identity comes from the
    batch system, off the same row, in the same query, and never from the review
    message -- `values.batch_id` is empty on every sample pattern 1 mints, and
    there are four conventions live in this stack. `build()` lifts it to the top
    level of `values`, where it identifies the sample's batch rather than
    describing the reactor's context.

    `as_of` is the row's occurred_at, not the sample's. The gap between the two
    is how long the reactor had been in that operation when the valve opened,
    and it is visible in the document without anybody computing it.
    """
    if not equipment_id:
        return None, "the review message carried no equipment_id"
    if instant is None:
        return None, "the review message carried no usable sample instant"

    rows = system.db.runPrepQuery(_BATCH_QUERY, [equipment_id, instant],
                                  database=DATASOURCE)
    if not rows:
        # Not an error. A sample drawn before this reactor's first advance has
        # no batch context, and saying so is the honest answer.
        return None, ("no bes.batch_event row for %s at or before %s"
                      % (str(equipment_id), _iso(instant)))

    row = rows[0]
    return {
        "batch_id": row[0],
        # Never empty after 2026-08-30: `batch_end` writes IDLE rather than a
        # null operation, so this is always a real ISA-88 operation or IDLE.
        # 07 needs no filter and no reason string for it.
        "operation": row[1],
        "qualified_window": _qualified(row[3]),
        "event_type": row[2],
        "as_of": _iso(row[4]),
    }, None


def _environment_sql(instant):
    """What the room was doing at the sample instant. (block, reason).

    Nearest either side, no tolerance, age always reported -- see the module
    docstring. `age_s` is the distance and `nearest_side` says which side of the
    sample the reading fell on, rather than one signed number whose sign
    silently changes what it means.

    `conditions` is `em.reading.environment` renamed, because this block is
    already called environment and a key named after its own parent tells a
    reader nothing. Nothing else about it is touched: flow, temperature and
    humidity are pattern 6's averages exactly as stored.
    """
    if instant is None:
        return None, "the review message carried no usable sample instant"

    rows = system.db.runPrepQuery(_EM_QUERY, [EM_DEVICE_ID, instant],
                                  database=DATASOURCE)
    if not rows:
        # The particle counter simulator is stopped, or nobody pressed Start.
        # This is the case that matters on stage: the block is null, the reason
        # says why, and the composite publishes anyway.
        return None, "no em.reading row for device %s" % EM_DEVICE_ID

    row = rows[0]
    occurred_at = row[3]
    offset_ms = occurred_at.getTime() - instant.getTime()
    return {
        # First, and deliberately. A reading is evidence about the sample only
        # if it is close to it in time, and burying its age inside the block
        # would let a forty-minute-old count read as current.
        "age_s": round(abs(offset_ms) / 1000.0, 1),
        "nearest_side": "after" if offset_ms >= 0 else "before",
        "device_id": EM_DEVICE_ID,
        # Prose, not a join key. See the module docstring.
        "location": row[4],
        # OURS, not the instrument's -- `particle_counter_poll` set it against
        # config/excursion_threshold. Read, never recomputed.
        "status": row[0],
        "occurred_at": _iso(occurred_at),
        "channels": _decode(row[1]),
        "conditions": _decode(row[2]),
    }, None


# -- the same three lookups, over i3X -----------------------------------------

# `i3x_client` is referenced with no import statement, which is how one project
# library module reaches another: the script manager injects the top-level names
# into every module's globals at call time. The dependency runs one way only --
# `i3x_client` copies `_iso`/`_parse_iso` rather than importing them back out of
# here, and it must stay that way.

# Warned-once flags, a dict rather than module globals and a `global` statement,
# so there is one obvious place this module's small amount of state lives --
# `particle_counter_poll._SESSION` does the same. Once, not once per row: an
# encoding this module does not recognise is unrecognised for every row in the
# range, and a warning per row would bury the one that matters.
_WARNED = {"row_instant": False}


def _i3x_history(tag_path, start, end):
    """`/objects/history` on one object, flattened to rows. (rows, reason).

    A row is `(the flat value dict, the row's own RFC 3339 timestamp)`, oldest
    first. The server drops rows in which none of this object's own tags has a
    point, so an empty list means the window really is empty -- not that a
    sibling object happened to move.

    `maxDepth` is 1 everywhere here. Folders do not count toward it but nested
    UDT instances do, so 1 is this object's own tags and nothing of
    `sample_valve`'s or `last_review`'s -- which is what both lookups want, and
    it keeps the flat leaf-name keys from having anything to collide with.
    """
    result, reason = i3x_client.history([i3x_client.element_id(tag_path)],
                                        start, end, max_depth=1)
    if reason:
        return None, reason

    rows = []
    for entry in (result or {}).get("values") or []:
        value = entry.get("value")
        if isinstance(value, dict):
            rows.append((value, entry.get("timestamp")))
    return rows, None


def _row_instant(raw, stamp):
    """The instant a history row is *about*, from a DateTime tag inside it.

    A DateTime in a row's value went out through `system.util.jsonEncode`, and
    that encoding is not part of the i3X spec. **Measured 2026-09-18 against
    the vendored server: it is epoch milliseconds**, a bare number --
    `"ts": 1789481859912` on `/objects/value` and the same on history rows.
    That is the first thing tried. The house ISO string is accepted too, in
    case a server ever formats instead, and the row's own `timestamp` -- which
    the server formatted itself, in the identical pattern -- is the fallback.

    **The fallback is a degradation, not an equivalent.** `current/ts` is when
    the instrument finished the analysis; the row stamp is when pattern 6's poll
    wrote the tag, up to one poll interval later. Falling back moves every
    reading later by that much, which shows up as `age_s`, so it warns.
    """
    if raw is not None:
        if isinstance(raw, (int, long, float)):
            return Date(long(raw))
        try:
            return _parse_iso(raw)
        except Exception:
            if not _WARNED["row_instant"]:
                _WARNED["row_instant"] = True
                system.util.getLogger(LOGGER_NAME).warnf(
                    "an i3X history row encoded its DateTime as %s, which is "
                    "not the house format; falling back to the row's own "
                    "timestamp, which is a different clock", str(raw)[:80])
    try:
        return _parse_iso(stamp)
    except Exception:
        return None


def _channels(value):
    """`ch_0_3: 41` back into `[{"size_um": 0.3, "count": 41}, ...]`.

    The exact inverse of `particle_counter_poll._channel_tag`, which is the only
    place the naming rule is written down. Reading the keys back rather than
    listing the six sizes here means a seventh channel added to the UDT arrives
    in the deviation without this module being edited -- and means this module
    still holds no opinion about which particle sizes exist.

    Ordered by size ascending. `em.reading.channels` keeps the vendor's own
    order and a history row cannot, being a dict; ascending is the order the
    vendor uses and the UDT lists, so the two sources agree in practice.
    """
    channels = []
    for key in value:
        name = str(key)
        if not name.startswith("ch_"):
            continue
        count = value[key]
        if count is None:
            # Before this channel's first stored point, or never historised.
            # A channel with no count is not a channel reading of zero.
            continue
        try:
            size = float(name[3:].replace("_", "."))
        except ValueError:
            continue
        channels.append({"size_um": size, "count": count})
    channels.sort(key=lambda channel: channel["size_um"])
    return channels


def _batch_context_i3x(equipment_id, instant):
    """The same block, off the bioreactor object's history. (block, reason).

    The question `_BATCH_QUERY` asks, put to a range query, because that is the
    only shape `/objects/history` has. The window ends at the sample instant and
    starts `BATCH_LOOKBACK_S` before it, and the answer is its **last** row:
    rows are forward-filled, so the last row at or before the sample carries
    whatever the four `batch_data` tags had last changed to, which is exactly
    what `ORDER BY occurred_at DESC LIMIT 1` returns.

    **`as_of` is a different clock here, and that is expected.** The `sql`
    source reports `bes.batch_event.occurred_at`, the instant the batch system
    recorded; this reports the historian's stamp for the row, the instant this
    gateway's tag changed. They differ by the CDC hop and they should. The
    string needs no reformatting: the server's `formatUtc` writes the same
    pattern `_iso` does.

    There is no `id DESC` tie-break to make and none is needed. `bes_batch`
    writes both rows of a manual advance in one transaction and leaves the tags
    holding the *incoming* operation, so the historian only ever saw the winner.
    """
    if not equipment_id:
        return None, "the review message carried no equipment_id"
    if instant is None:
        return None, "the review message carried no usable sample instant"

    start = Date(instant.getTime() - BATCH_LOOKBACK_S * 1000)
    rows, reason = _i3x_history(BIOREACTOR_TAG % equipment_id, start, instant)
    if reason:
        return None, reason
    if not rows:
        # Wider than an empty `bes.batch_event`, and the trap in this source.
        # The server builds a row only at an instant where some tag actually
        # has a stored point in the range: the value carried IN from before the
        # window seeds the forward-fill but never creates a row of its own
        # (`i3x.handlers._queryHistory`). So a reactor that has sat in one
        # operation for longer than BATCH_LOOKBACK_S has no rows at all and
        # reports no context, where `_BATCH_QUERY` -- which has no floor --
        # still finds its last advance. Advance the reactor inside the window,
        # or raise the window. Do not "fix" it by reading the live tag: the
        # point of this source is that it reads the same history any i3X client
        # would.
        return None, ("no tag history for %s at or before %s"
                      % (str(equipment_id), _iso(instant)))

    value, stamp = rows[-1]
    return {
        # May be "" after a batch_end, and is passed through exactly as the
        # `sql` source passes it: an empty batch id is a fact about the reactor.
        "batch_id": value.get("batch_id"),
        "operation": value.get("operation"),
        # A real Boolean tag here rather than `payload->>'qualified_window'`'s
        # text, so `_qualified` has nothing to do. Null -- the tag with no
        # stored point yet in this range -- reads as False, which is the answer
        # `_qualified` gives for a row without the payload key.
        "qualified_window": bool(value.get("qualified_window")),
        "event_type": value.get("event_type"),
        "as_of": stamp,
    }, None


def _environment_i3x(instant):
    """The same block, off the particle counter object's history.

    Nearest either side still, but now inside a window: `/objects/history` is a
    range query, so `EM_WINDOW_S` bounds how far "nearest" may reach. That is
    the first of the two behaviour changes in the module docstring, and it is
    the only rule in this module that the source changes.

    **The search is nearest to the row's own `ts`, not to the row's stamp.**
    `current/ts` is the instrument's completedAt, which is the instant
    `em.reading.occurred_at` holds and therefore the instant the `sql` source
    compares against. Using the historian's stamp instead would silently move
    every reading later by one poll interval and quietly inflate `age_s`.

    Pattern 6 writes every `current/*` tag in one `writeBlocking`, so one
    analysis is one row and forward-filling has nothing to smear: no row here
    carries one analysis's counts beside another's timestamp.
    """
    if instant is None:
        return None, "the review message carried no usable sample instant"

    span_ms = EM_WINDOW_S * 1000
    rows, reason = _i3x_history(EM_TAG,
                                Date(instant.getTime() - span_ms),
                                Date(instant.getTime() + span_ms))
    if reason:
        return None, reason

    nearest = None
    nearest_offset = None
    for value, stamp in rows:
        occurred_at = _row_instant(value.get("ts"), stamp)
        if occurred_at is None:
            continue
        offset_ms = occurred_at.getTime() - instant.getTime()
        if nearest_offset is None or abs(offset_ms) < abs(nearest_offset):
            nearest = (value, occurred_at)
            nearest_offset = offset_ms

    if nearest is None:
        # Wider than the `sql` source's "no rows for this device at all": this
        # also fires for a simulator that has been stopped longer than the
        # window, which is the case the window exists to catch. `_violations`
        # turns it into environment_unverifiable either way.
        return None, ("no tag history for %s within %ss of %s"
                      % (EM_DEVICE_ID, str(EM_WINDOW_S), _iso(instant)))

    value, occurred_at = nearest
    return {
        # Same order, same keys, same meanings as the `sql` block -- `age_s`
        # first for the same reason. A consumer cannot tell the two apart.
        "age_s": round(abs(nearest_offset) / 1000.0, 1),
        "nearest_side": "after" if nearest_offset >= 0 else "before",
        "device_id": EM_DEVICE_ID,
        # Prose, not a join key. See the module docstring.
        "location": value.get("location"),
        # OURS, not the instrument's -- `particle_counter_poll` set it against
        # config/excursion_threshold. Read, never recomputed.
        "status": value.get("status"),
        "occurred_at": _iso(occurred_at),
        "channels": _channels(value),
        # `em.reading.environment` renamed by the `sql` source; here the same
        # three numbers come off `current/conditions/*`, which phase 1 added to
        # the UDT precisely so this block could be rebuilt from the model.
        "conditions": {
            "flow_rate_lpm": value.get("flow_rate_lpm"),
            "temperature_c": value.get("temperature_c"),
            "humidity_pct": value.get("humidity_pct"),
        },
    }, None


def _equipment_identifier_i3x(equipment_id):
    """The plant model's name for the vessel, as an i3X object value.

    `/objects/value` hands back the whole UDT instance as a nested document, so
    this reads `value.asset_data.equipment_identifier` rather than the leaf tag
    `_equipment_identifier_sql` reads. Same tag, still not a join.

    **Do not go looking for `process_data` beside it.** The server pops any
    top-level folder containing a nested UDT instance out of the parent's value
    in its entirety (`i3x.utils.removeChildren` removes `parts[0]`), so
    `sample_valve` takes `process_data` with it and `last_review` takes
    `qc_data`. `asset_data` and `batch_data` hold only plain tags and survive.

    Null on any failure, for the reason the `sql` source is: a composite that
    cannot name the asset is still worth publishing, and `equipment_id` beside
    it already says the same thing.
    """
    if not equipment_id:
        return None

    logger = system.util.getLogger(LOGGER_NAME)
    result, reason = i3x_client.value(
        [i3x_client.element_id(BIOREACTOR_TAG % equipment_id)])
    if reason:
        logger.warnf("equipment_identifier for %s unreadable -- %s",
                     str(equipment_id), str(reason))
        return None

    document = (result or {}).get("value")
    if not isinstance(document, dict):
        logger.warnf("i3x value for %s carried no value document",
                     str(equipment_id))
        return None

    identifier = (document.get("asset_data") or {}).get("equipment_identifier")
    if identifier is None:
        logger.warnf(
            "i3x value for %s carried no asset_data/equipment_identifier",
            str(equipment_id))
        return None
    return str(identifier)


# -- which source answers -----------------------------------------------------

# One `if` per lookup and nothing else in them. The pairs above are complete
# alternatives rather than variations: each returns the same keys in the same
# order under the same (block, reason) contract, so `_assess` and `build` are
# written against the contract and never against a source. That is what makes
# `CONTEXT_SOURCE` safe to flip between two reviews on stage.

def _batch_context(equipment_id, instant):
    """What the reactor was doing when the sample was drawn. (block, reason)."""
    if CONTEXT_SOURCE == "i3x":
        return _batch_context_i3x(equipment_id, instant)
    return _batch_context_sql(equipment_id, instant)


def _environment(instant):
    """What the room was doing at the sample instant. (block, reason)."""
    if CONTEXT_SOURCE == "i3x":
        return _environment_i3x(instant)
    return _environment_sql(instant)


def _equipment_identifier(equipment_id):
    """The plant model's name for the vessel, or None."""
    if CONTEXT_SOURCE == "i3x":
        return _equipment_identifier_i3x(equipment_id)
    return _equipment_identifier_sql(equipment_id)


# -- the deviation rule -------------------------------------------------------

# The two flags that make a sample a deviation, and the only two 07 reads.
# Neither is computed here: `particle_counter_poll` set `status` against
# config/excursion_threshold, and an analyst set `disposition` in the LIMS. 07
# compares each against a constant whose meaning it does not own, which is the
# whole difference between reading a flag and holding a second copy of a rule.
# docs/00-architecture.md, "Derived flags travel with the fact that produced
# them".
EXCURSION_STATUS = "excursion"
FAILED_DISPOSITION = "fail"

# `qualified_window` is deliberately NOT a trigger. `bes_batch.QUALIFIED` is
# ("GROWTH",) out of five operations, so gating on it would fire on most samples
# and a topic where the deviation is the normal case tells a reader nothing. It
# stays in `batch_context` where a reviewer can see it, and it is reported, not
# acted on. docs/plans/07-sample-chain.md decision 9.


def _violations(values, environment, environment_reason):
    """What is wrong with this sample. A list, empty when nothing is.

    Each entry names its own evidence: `code` is what a subscriber routes on,
    `detail` is prose for whoever reads the deviation, and `source` names the
    field the flag came off so nobody has to guess which module owns the rule.
    Order is fixed -- environment first, review second -- so two deviations of
    the same kind compare cleanly.
    """
    found = []

    if environment is None:
        # On the `sql` source, only reachable on an empty `em.reading` for this
        # device -- NOT on a stopped simulator. Nearest-either-side has no
        # cutoff, so a stalled sim returns a stale reading with a large `age_s`,
        # not nothing (07's spec, § As built, CP6). On the `i3x` source it is
        # also what a simulator stopped for longer than EM_WINDOW_S produces,
        # which is the first of the two behaviour changes in the module
        # docstring and the one place the sources disagree on purpose. Either
        # way, a sample whose room cannot be evidenced at all is not one anybody
        # can release.
        found.append({
            "code": "environment_unverifiable",
            "detail": environment_reason or "no environmental reading available",
            "source": "em.reading",
        })
    elif str(environment.get("status")) == EXCURSION_STATUS:
        found.append({
            "code": "environmental_excursion",
            "detail": ("particle counter reported %s, %ss %s the sample"
                       % (EXCURSION_STATUS, str(environment.get("age_s")),
                          str(environment.get("nearest_side")))),
            "source": "em.reading.status",
        })

    if str(values.get("disposition")).strip().lower() == FAILED_DISPOSITION:
        found.append({
            "code": "failed_review",
            "detail": ("analyst %s recorded disposition %s"
                       % (str(values.get("analyst") or "unknown"),
                          FAILED_DISPOSITION)),
            "source": "lims.sample.disposition",
        })

    return found


# -- the two Event Stream entry points ----------------------------------------

def _as_document(data, logger):
    """The review message as a dict, or None if it is not usable."""
    if data is None:
        return None
    if not isinstance(data, dict):
        try:
            data = system.util.jsonDecode(str(data))
        except Exception:
            logger.warnf("event data was neither a dict nor JSON: %s",
                         str(data)[:200])
            return None
    if not isinstance(data, dict):
        logger.warnf("event data decoded to %s, not an object",
                     type(data).__name__)
        return None
    return data


def _assess(data, logger):
    """Both lookups plus the verdict. Returns a dict, or None if unusable.

    Shared by `is_deviation` and `build` so the filter and the transform cannot
    reach different conclusions about the same message from different code.
    """
    document = _as_document(data, logger)
    if document is None:
        return None

    values = document.get("values") or {}
    sample_id = values.get("sample_id")
    if not sample_id:
        # Nothing to correlate, nothing to look up, and nothing a GxP record
        # could be attached to afterwards. There is no composite to build.
        logger.warn("review message carried no sample_id; no composite built")
        return None

    equipment_id = values.get("equipment_id")
    instant, instant_iso = _sample_instant(values)
    batch_context, batch_reason = _batch_context(equipment_id, instant)
    environment, environment_reason = _environment(instant)

    return {
        "document": document,
        "values": values,
        "sample_id": sample_id,
        "equipment_id": equipment_id,
        "instant_iso": instant_iso,
        "batch_context": batch_context,
        "batch_reason": batch_reason,
        "environment": environment,
        "environment_reason": environment_reason,
        "violations": _violations(values, environment, environment_reason),
    }


def is_deviation(data):
    """The gate, called from the Event Stream **filter**. True publishes.

    A filter is the only stage that can stop a message -- see the module
    docstring -- so the decision lives here and `build()` never has to represent
    "nothing to say" as a return value the encoder would happily stringify.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    assessment = _assess(data, logger)
    if assessment is None:
        return False
    if not assessment["violations"]:
        logger.infof("%s is clean; no deviation published",
                     str(assessment["sample_id"]))
        return False
    return True


# -- the Event Stream transform -----------------------------------------------

def build(document):
    """A deviation, from one pattern-4 review message. Returns a JSON string.

    Only ever called for a message `is_deviation` already passed, so it always
    has something to say. It must never return None: the encoder would publish
    the string "None" onto the topic. See the module docstring.

    Called from `07_chain/lims-review`'s transform with what the MQTT Engine
    source read off `icc26/site1/qc/lims/sample-result`. The source encoder is
    `ignition.string`, so what arrives is the raw payload as text; a dict is
    accepted anyway, because what an Event Stream hands a transform is not worth
    being brittle about.

    The envelope is `ts` and `values` and nothing else -- no `seq`, no `source`,
    no `meta` -- which is the shape every other pattern on this backbone now
    publishes. A subscriber knows this document is 07's because it arrived on
    07's topic, the same way it knows the analyzer result is the analyzer's.

    `ts` is the acquisition instant and `values.assessed_at` is when 07 built
    the record. The gap between them is the whole document's provenance, visible
    on stage in one message -- the same rule docs/plans/04-lims-webhook.md
    states for the review itself, and the same relocation: the instant is a
    measured fact about this sample, so it sits in `values` beside the analyst
    and the disposition rather than in a metadata block.

    **07 no longer carries a `seq`.** It used to borrow the outbox delivery id
    off the review message, because it holds no table and an in-memory counter
    would restart at 1 on every gateway restart and tell a subscriber nothing.
    Pattern 4 stopped publishing one, and 07 mints nothing to replace it: the
    sample id is what identifies this document, and it is in `values` where a
    reader can see it.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    assessment = _assess(document, logger)
    if assessment is None:
        # Unreachable in normal operation: `is_deviation` returned False on this
        # same message and the filter already stopped it. Guarded anyway,
        # because returning None here would publish the STRING "None" -- the
        # 2026-09-06 finding in the module docstring, and the whole reason the
        # gate lives in the filter. An empty string publishes an empty message,
        # which is at least not a lie.
        logger.warn("build() reached with an unusable message; publishing nothing")
        return ""

    document = assessment["document"]
    values = assessment["values"]
    sample_id = assessment["sample_id"]
    equipment_id = assessment["equipment_id"]
    instant_iso = assessment["instant_iso"]
    batch_context = assessment["batch_context"]
    batch_reason = assessment["batch_reason"]
    environment = assessment["environment"]
    environment_reason = assessment["environment_reason"]
    violations = assessment["violations"]

    # Batch identity comes off the batch_event row, not the review message.
    batch_id = batch_context.pop("batch_id") if batch_context else None

    envelope = {
        "ts": instant_iso or document.get("ts"),
        "values": {
            # Why this message exists. Never empty -- a clean sample returns
            # above and produces no message at all.
            "violations": violations,

            "sample_id": sample_id,
            "equipment_id": equipment_id,
            "equipment_identifier": _equipment_identifier(equipment_id),
            "batch_id": batch_id,
            "disposition": values.get("disposition"),
            "analyst": values.get("analyst"),

            # When 07 assembled this. Read against `ts` -- the acquisition
            # instant -- it is the provenance gap the talk track reads out.
            # `sample_id` above is the thread across mechanisms now that no
            # document carries a `meta.correlation_id`; it was always the same
            # string the correlation id was copied from.
            "assessed_at": _iso(),

            # Pattern 1's contribution and pattern 3's, carried through the
            # review unchanged. 07 re-reports; it does not re-interpret.
            "collection": values.get("collection"),
            "results": values.get("results"),

            # Both blocks are always present, and so is the reason beside each.
            # A null reason on a populated block is the shape staying fixed: a
            # consumer reads the same four keys whether or not the lookups found
            # anything, and a gap is never a missing key.
            "batch_context": batch_context,
            "batch_context_reason": batch_reason,
            "environment": environment,
            "environment_reason": environment_reason,
        },
    }

    logger.infof("deviation for %s: %s -- %s, batch %s, environment %s",
                 str(sample_id),
                 ", ".join([v["code"] for v in violations]),
                 str(values.get("disposition")),
                 str(batch_id),
                 "%s at %ss" % (environment.get("status"),
                                environment.get("age_s"))
                 if environment else "none")
    return system.util.jsonEncode(envelope)
