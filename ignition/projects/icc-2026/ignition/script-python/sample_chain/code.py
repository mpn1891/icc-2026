"""Pattern 7 -- the composite. The first thing in this stack that READS.

Six acquisition mechanisms publish. This one subscribes to one of them, asks two
questions of what two of the others already stored, and puts the answer on the
wire as a single document. **07's only job is to be the thing that asks.**

**Nothing here is clever, and that is the design.** This module does no
arithmetic, holds no process knowledge and computes no flags.
`qualified_window` was computed by `bes_batch` at insert time -- its `QUALIFIED`
tuple is the only copy of the batch protocol -- and `status` by `metone_poll`
against `config/excursion_threshold`, the only copy of the cleanroom limit. Both
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

**Always publish.** A lookup that finds nothing produces a `null` block and a
`_reason` beside it -- never a missing key, never a silent default. A gap is a
finding, and 07 still speaks. docs/plans/00-master-plan.md states this rule for
the MET ONE section; it applies to both lookups equally.

**The MET ONE rule is nearest either side, no tolerance.** A reading three
seconds *after* the valve closed is better evidence than one twenty-five seconds
before, so the search is not restricted to the past, and there is no cutoff at
which 07 refuses to answer. It reports the nearest reading and always reports
its age, and lets the reader judge -- which makes `age_s` load-bearing, so it
sits at the top of the block. A forty-minute-old reading must not be able to
read as current. Pattern 6's timer bounds the normal case to <= 27.2 s.

**07 publishes nothing itself.** It returns a document; Event Stream
`07_chain/lims-review` hands that to the Transmission handler. Same shape as
patterns 3 and 6, and it is what keeps 07 a genuine backbone subscriber --
docs/talk-tracks/04-lims-webhook.md says this pattern and 07 are the only two
there are.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "sample_chain"

# NOT `pg_db`. That one points at the `postgres` database as user `ignition` --
# wrong database, wrong user -- and it will pass a glance in the dropdown and
# then read nowhere useful. docs/00-architecture.md is emphatic about this.
# `pg_db` is not deletable either, which the pre-07 plan got wrong: the
# pg-historian provider and System/Gateway/StoreAndForward/pg_db/Pipelines/
# TagHistory are both bound to it. Do not select it; do not remove it.
DATASOURCE = "ICC26"

MECHANISM = "aggregate"
# id == type, matching pattern 5's {"id": "bes", "type": "bes"}. There is no
# `sample-chain` device and no `aggregate` area -- this source is software, and
# saying so is more honest than borrowing a vessel's name.
SOURCE = {"id": "sample-chain", "type": MECHANISM}

# The room's instrument, not the vessel's. One counter serves USP Suite A, so
# this is a constant and not derived from the sample. See the module docstring.
EM_DEVICE_ID = "particle-counter-01"

# The plant model's own name for the asset, read rather than joined.
# `plant.equipment` is deliberately NOT on 07's path: it still holds `BR-201` in
# the wrong case and four `vib-*` leftovers, and a join added to tidy that up
# would put a fifth spelling of the vessel into a GxP document.
# docs/plans/07-sample-chain.md decision 4. **Do not add a join.**
EQUIPMENT_TAG = ("[default]icc26/site1/upstream/bioreactors/%s"
                 "/asset_data/equipment_identifier")

# What the reactor was doing. Keyed on the SAMPLE's equipment_id, and served by
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

# What the room was doing. Keyed on a DEVICE, not on the reactor.
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


def _equipment_identifier(equipment_id):
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


# -- the two lookups ----------------------------------------------------------

def _batch_context(equipment_id, instant):
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


def _environment(instant):
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
        # The MET ONE simulator is stopped, or nobody pressed Start. This is the
        # case that matters on stage: the block is null, the reason says why,
        # and the composite publishes anyway.
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
        # OURS, not the instrument's -- `metone_poll` set it against
        # config/excursion_threshold. Read, never recomputed.
        "status": row[0],
        "occurred_at": _iso(occurred_at),
        "channels": _decode(row[1]),
        "conditions": _decode(row[2]),
    }, None


# -- the Event Stream transform -----------------------------------------------

def build(document):
    """The composite, from one pattern-4 review message. Returns a JSON string.

    Called from `07_chain/lims-review`'s transform with what the MQTT Engine
    source read off `icc26/site1/qc/lims/sample-result`. The source encoder is
    `ignition.string`, so what arrives is the raw payload as text; a dict is
    accepted anyway, because what an Event Stream hands a transform is not worth
    being brittle about.

    `ts` is the acquisition instant and `meta.ingest_ts` is when 07 assembled
    the record. The gap between them is the whole document's provenance, visible
    on stage in one message -- the same rule docs/plans/04-lims-webhook.md
    states for the review itself.

    `seq` is the outbox delivery id off the review message. 07 has no counter of
    its own and mints nothing: it holds no table, and an in-memory counter would
    restart at 1 on every gateway restart and tell a subscriber nothing -- the
    reason `bes_cdc` gives for using its row id. The review and the composite
    sharing one `seq` is not a collision; it is the statement that this document
    is that review, answered.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    if document is None:
        return None
    if not isinstance(document, dict):
        try:
            document = system.util.jsonDecode(str(document))
        except Exception:
            logger.warnf("event data was neither a dict nor JSON: %s",
                         str(document)[:200])
            return None
    if not isinstance(document, dict):
        logger.warnf("event data decoded to %s, not an object",
                     type(document).__name__)
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

    # Batch identity comes off the batch_event row, not the review message.
    batch_id = batch_context.pop("batch_id") if batch_context else None

    envelope = {
        "ts": instant_iso or document.get("ts"),
        "seq": document.get("seq"),
        "source": SOURCE,
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso(),
            # The sample id pattern 1 minted at the valve. It is the same string
            # in the analyzer result, the LIMS review and here, which is what lets
            # one sample be found under four mechanisms in one mosquitto_sub.
            "correlation_id": sample_id,
        },
        "values": {
            "sample_id": sample_id,
            "equipment_id": equipment_id,
            "equipment_identifier": _equipment_identifier(equipment_id),
            "batch_id": batch_id,
            "disposition": values.get("disposition"),
            "analyst": values.get("analyst"),

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

    logger.infof("composite for %s: %s, %s, batch %s, environment %s",
                 str(sample_id),
                 str(values.get("disposition")),
                 str(batch_context.get("operation")) if batch_context
                 else "no batch context",
                 str(batch_id),
                 "%s at %ss" % (environment.get("status"),
                                environment.get("age_s"))
                 if environment else "none")
    return system.util.jsonEncode(envelope)
