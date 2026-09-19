"""The two places the backbone writes back into the typed tag model.

Everything else in this stack publishes. This module is the return leg: it takes
a document that already went out onto MQTT and lands it in a UDT instance, so
that a vendor-neutral client -- the i3X server reads the same tag model the
Designer does -- can ask for "br-201" and get the vessel's last reviewed
analysis as part of the object, rather than having to subscribe to a broker and
reassemble it.

**The deviation round-trips through the broker. The review cannot.** 07 could
have written its deviation from `sample_chain.build()` before returning it, and
does not: the deviation lands here because a second Event Stream reads 07's own
output back off `icc26/site1/qc/deviation`, the same way the `icc26-deviation`
namespace already reads it back, untyped. That keeps "07 publishes nothing
itself" true, and any i3X consumer publishing a well-formed document on that
topic lands in the model on exactly the same path ours does. There the model is
fed from the wire, and the wire stays the contract.

The review was fed the same way until 2026-09-19, by a stream on
`icc26/site1/qc/lims/sample-result`, and cannot be again. **MQTT Engine
registers one Event Stream source per topic string**, 07's `lims-review` stream
already holds that one, and the second registrant is starved in silence -- no
subscription, no log line, and which of the two wins reshuffles on every restart
of the Engine client. So `write_review` is called by `lims_webhook.handle`,
in the same function that publishes the review and immediately after it. The
cost is that a review published by somebody else does not land in the model;
the deviation is where this stack still shows what being fed from the wire buys.

**Reviewed, not raw.** The review is nested under the vessel
(`qc_data/last_review`) rather than parked in a flat QC folder, because the i3X
server derives its relationships from UDT nesting: nesting is what makes
br-201's full-depth read, its history and a subscription target all reachable
from one object. It has to be the *reviewed* result and not the analyzer's --
the LIMS already resolved the vessel from pattern 1's topic, whereas the raw
analyzer result knows the vessel only by whatever was transcribed at the
instrument.

**The shape is fixed; nulls are written, never skipped.** A member that has no
value this time is written `None`, so an object read at full depth always
returns the same keys and a consumer never has to tell "absent" from "unknown".
One `system.tag.writeBlocking` per document does the whole instance, which also
means the instance is never half-updated between two reads.

**Nothing here decides anything.** `qualified_window` was computed by
`bes_batch`, `status` by `particle_counter_poll`, `disposition` by an analyst
and `violations` by `sample_chain`. This module flattens those into scalars a
tag can hold and re-encodes the whole envelope into `document` beside them. The
scalars are for browsing, charting and history; `document` is what makes a
later i3X consumer equivalent to the MQTT one, because it is the message.

**Never raises.** `write_deviation` is called from an Event Stream handler, and
a handler that throws aborts the stream for a message that has already been
published and is not coming back. `write_review` is called from the WebDev
handler that has just published the review, where a throw would be a 500 -- and
the LIMS outbox answers a 500 by POSTing again, republishing the review and
firing 07 a second time. An unusable document, an unknown vessel or a missing
tag is logged with `warnf` and dropped.

**The Event Stream Script handler is a batch handler.** Its user code is the
body of `onEventsReceived(events, state)` -- a *list* -- not of a per-event
function, so `07_chain/deviation-to-model` loops and calls `write_deviation`
once per event. Verified against `ScriptHandlerConfig` / `ScriptHandler` in
EventStream-module 1.3.8; the handler type id is `ignition.script` and its only
config key is `userCode`.

Jython 2.7: no f-strings, no type hints, `%` formatting.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "model_feed"

# The vessel's review, nested in the `bioreactor` type. `%s` is
# `values.equipment_id` off the review message -- pattern 1 parsed it out of its
# own topic and the LIMS carried it through, so this module resolves the target
# by reading a field rather than by holding a table of reactors. Same prefix
# `sample_chain.EQUIPMENT_TAG` uses; deliberately not imported from there,
# because a writer and a reader that must both survive the other being disabled
# should not be one import apart.
REVIEW_TAG = ("[default]icc26/site1/upstream/bioreactors/%s"
              "/qc_data/last_review/")

# One instance, not one per vessel. A deviation is a QC record about the site's
# quality system, and the vessel it concerns is a field on it (`equipment_id`,
# `equipment_identifier`) rather than a place in the path. The last deviation is
# the tag's value; every deviation is its history.
DEVIATION_TAG = "[default]icc26/site1/qc/deviation-01/"

# The three analytes pattern 3 reports. Named here rather than read off the
# message because a UDT member is a fixed name: an analyte that stopped being
# reported has to read `None`, not vanish. A fourth analyte needs a member.
GLUCOSE = "glucose"
LACTATE = "lactate"
OSMOLALITY = "osmolality"

# The demo's Postgres, for the review-event store. NOT `pg_db`. That one is the
# historian's own store -- the `ignition` database as user `ignition`, holding
# sqlth_*/sqlt_data_* and nothing this project writes -- so it will pass a
# glance in the dropdown and then write nowhere useful.
# docs/00-architecture.md is emphatic about this.
DATASOURCE = "ICC26"

# `ON CONFLICT DO NOTHING` because a review can still be offered twice -- the
# identity of one is the sample plus the instant a person signed it, not the
# sample alone. It stopped being the primary dedupe when the review moved off
# the Event Stream: the webhook answers a repeated idempotency key with 409
# before this runs. A genuine re-review of the same sample is still a second
# row. See migrate-11 for the key.
_REVIEW_INSERT = ("INSERT INTO lims.review_event "
                  "(sample_id, equipment_id, ts, verified_at, document) "
                  "VALUES (?, ?, ?, ?, ?::jsonb) "
                  "ON CONFLICT (sample_id, verified_at) DO NOTHING")


# -- small helpers ------------------------------------------------------------

def _parse_iso(text):
    """'2026-08-30T18:25:54.330Z' as a java.util.Date, or None.

    A copy of `sample_chain._parse_iso`, on purpose. Every timestamp on this
    backbone came out of the house `_iso()` -- the LIMS writes the identical
    format from Python -- so one parser covers all of them, and the duplication
    buys `model_feed` independence from `sample_chain`: the deviation writer
    must keep working with 07 disabled, and 07 must keep working with the model
    feed removed.

    Unlike the original this one swallows the failure and warns. A DateTime
    member whose string would not parse is written null; the unparsed text is
    still in `document`, so nothing is lost.
    """
    if text is None:
        return None
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    try:
        return formatter.parse(str(text))
    except Exception:
        system.util.getLogger(LOGGER_NAME).warnf(
            "unparseable timestamp %s -- writing null", str(text)[:64])
        return None


def _as_document(data, logger):
    """The backbone message as a dict, or None if it is not usable.

    Both source encoders are `ignition.string`, so what an Event Stream hands a
    handler is the raw payload as text. A dict is accepted anyway, because what
    a stream hands a handler is not worth being brittle about -- the same
    tolerance `sample_chain._as_document` has, for the same reason.
    """
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


def _block(values, name):
    """A nested object out of `values`, always a dict so callers can `.get`."""
    block = values.get(name)
    if isinstance(block, dict):
        return block
    return {}


def _text(raw):
    """A String member's value. None stays None; everything else is a string."""
    if raw is None:
        return None
    if isinstance(raw, basestring):
        return raw
    return str(raw)


def _number(raw):
    """A Float8 member's value, or None. A number that will not float is null."""
    if raw is None:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _flag(raw):
    """A Boolean member's value, or None.

    `qualified_window` arrives as a JSON boolean, but `bes_batch` stores it in a
    jsonb payload and 07 read it back through `_qualified` as text once already,
    so both spellings are accepted. Absent stays absent: a deviation whose batch
    context was null must not read as "not qualified".
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() == "true"


def _analyte(values, analyte):
    """`values.results[analyte == name].value` as a float, or None.

    Missing is null, not zero. A sample the analyzer never ran and a sample that
    measured zero are different facts and the tag must not conflate them.
    """
    for row in values.get("results") or []:
        if isinstance(row, dict) and _text(row.get("analyte")) == analyte:
            return _number(row.get("value"))
    return None


def _write(prefix, members, label, logger):
    """One writeBlocking for the whole instance. Returns True on a clean write.

    Per-member failures are reported and swallowed. The usual cause is that the
    UDT member does not exist -- a model feed running ahead of a tag model, or a
    typo -- and the right response to that is a log line naming the member, not
    an exception that aborts an Event Stream over a message that has already
    been published.
    """
    paths = []
    values = []
    for name, value in members:
        paths.append(prefix + name)
        values.append(value)
    try:
        results = system.tag.writeBlocking(paths, values)
    except Exception as exc:
        logger.warnf("%s not written -- %s: %s",
                     label, type(exc).__name__, str(exc))
        return False

    bad = []
    for index, quality in enumerate(results or []):
        try:
            good = quality.isGood()
        except Exception:
            good = True
        if not good:
            bad.append("%s (%s)" % (paths[index], str(quality)))
    if bad:
        logger.warnf("%s wrote %s of %s members; bad: %s",
                     label, len(paths) - len(bad), len(paths),
                     ", ".join(bad))
        return False
    return True


def _millis(value):
    """A java.util.Date as epoch milliseconds, or the value untouched.

    **This is the i3X server's DateTime encoding, not a choice made here.** A
    DateTime member goes out of `/objects/value` as a bare number of epoch
    millis -- measured 2026-09-18 against the vendored server, and what
    `sample_chain._row_instant` tries first. The stored document has to use the
    same encoding or an object read from history would not match the same
    object read live, which is the whole point of storing it pre-shaped.
    """
    if isinstance(value, Date):
        return value.getTime()
    return value


def _store_review(members, logger):
    """The review as a row in `lims.review_event`. Rows written: 1 or 0.

    **Takes the same `members` list that goes to the tag**, so the stored
    document and the tag's members cannot drift: there is one definition of what
    a review looks like and it is the list in `write_review`. The i3X history
    branch hands this document straight back, which is only sound because
    nothing reshapes it on the way out -- the `i3x` project cannot import this
    module, since neither project inherits the other.

    Zero rows means the review was already stored -- a QoS 1 redelivery -- which
    is not a failure and is not logged as one.

    Failures are reported and swallowed, as in `_write`. A Postgres that is down
    must not cost the live model its review: the tag write follows this one and
    does not depend on it.
    """
    document = {}
    for name, value in members:
        document[name] = _millis(value)

    sample_id = document.get("sample_id")
    equipment_id = document.get("equipment_id")
    ts = None
    verified_at = None
    for name, value in members:
        if name == "ts":
            ts = value
        elif name == "verified_at":
            verified_at = value

    # All four are NOT NULL in the table. A message missing any of them is a
    # message we cannot identify a review by, and inventing a value would put an
    # unidentifiable row in the store that no later review could supersede.
    if not sample_id or not equipment_id or ts is None or verified_at is None:
        logger.warnf("review not stored -- need sample_id, equipment_id, ts and "
                     "verified_at; got %s/%s ts=%s verified_at=%s",
                     str(sample_id), str(equipment_id), str(ts), str(verified_at))
        return 0

    try:
        return system.db.runPrepUpdate(
            _REVIEW_INSERT,
            [sample_id, equipment_id, ts, verified_at,
             system.util.jsonEncode(document)],
            DATASOURCE)
    except Exception as exc:
        logger.warnf("review %s not stored -- %s: %s",
                     str(sample_id), type(exc).__name__, str(exc))
        return 0


# -- the two entry points -----------------------------------------------------

def write_review(data):
    """A pattern 4 review onto the vessel's `qc_data/last_review`.

    Called from `lims_webhook.handle`, immediately after the review is published
    and inside the same request. **Not from an Event Stream:** MQTT Engine
    registers one source per topic string, 07's `lims-review` stream holds
    `icc26/site1/qc/lims/sample-result`, and a second stream on that topic gets
    nothing and says nothing about it -- measured 2026-09-18, with the winner
    changing across restarts. The namespace filters stay as they are: an Engine
    namespace whose string *differs* from a stream's source still delivers two
    copies to the stream, which is a separate trap and why `icc26-lims` is
    spelled exactly like 07's topic.

    Routing is on `values.equipment_id`, so a third vessel appears in the model
    the moment its UDT instance exists and needs no edit here. An empty or
    unknown vessel is a warning and a dropped write: guessing a reactor would
    put a review on the wrong object's record, which is worse than losing it
    from the model while it is still on the wire and in the LIMS.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    document = _as_document(data, logger)
    if document is None:
        return False

    values = document.get("values") or {}
    if not isinstance(values, dict):
        logger.warn("review message carried no values object")
        return False

    equipment_id = _text(values.get("equipment_id"))
    if not equipment_id:
        logger.warn("review carried no equipment_id; nothing to write it to")
        return False

    sample_id = _text(values.get("sample_id"))
    collection = _block(values, "collection")

    members = [
        # The message itself, re-encoded from the decoded form so a payload that
        # arrived as text and one that arrived as a dict store identically.
        ("document", system.util.jsonEncode(document)),

        ("sample_id", sample_id),
        ("equipment_id", equipment_id),
        ("disposition", _text(values.get("disposition"))),
        ("analyst", _text(values.get("analyst"))),

        # `ts` is the acquisition instant, `verified_at` is when a person
        # clicked, and the gap between them is the whole pattern. Both are
        # members so the gap is visible in the object and chartable from its
        # history, not only readable inside `document`.
        ("ts", _parse_iso(document.get("ts"))),
        ("collected_at", _parse_iso(values.get("collected_at"))),
        ("verified_at", _parse_iso(values.get("verified_at"))),

        # Pattern 1's contribution, carried through review. Three fields of it,
        # not the block: who drew the sample, when the valve closed and how the
        # cycle ended are what a reviewer looks for beside the numbers.
        ("sample_completion", _parse_iso(collection.get("sample_completion"))),
        ("badge_holder", _text(collection.get("badge_holder"))),
        ("cycle_result", _text(collection.get("cycle_result"))),

        # Pattern 3's numbers, flattened by analyte. A list cannot be a tag and
        # a trend needs a scalar; the list is still in `document` verbatim.
        ("glucose_g_l", _analyte(values, GLUCOSE)),
        ("lactate_g_l", _analyte(values, LACTATE)),
        ("osmolality_mosm_kg", _analyte(values, OSMOLALITY)),
    ]

    # The store first, then the model. `lims.review_event` is the record of the
    # review and the tag is a projection of the latest one, so the record is
    # what must not be lost -- and storing first means anything reading history
    # back can never see a review the store has not got. Deliberately NOT gated
    # on the result: a redelivery writes 0 rows and must still refresh the tag.
    _store_review(members, logger)

    written = _write(REVIEW_TAG % equipment_id, members,
                     "review %s for %s" % (str(sample_id), equipment_id),
                     logger)
    if written:
        logger.infof("review %s landed on %s/qc_data/last_review",
                     str(sample_id), equipment_id)
    return written


def write_deviation(data):
    """A pattern 7 deviation onto `qc/deviation-01`.

    Called from `07_chain/deviation-to-model`, which subscribes to
    `icc26/site1/qc/deviation` -- 07's own output, read back off the broker.
    The topic string is identical to the `icc26-deviation` namespace's filter
    for the same collapse-to-one-subscription reason as the review stream.

    Silence on that topic is the compliant case: a clean sample publishes
    nothing, so this runs only on findings and `deviation-01` holds the last
    one indefinitely. That is the intended reading -- the tag is "the most
    recent deviation", not "the current state of anything" -- and its history
    is the list of them.

    `violation_codes` is `values.violations[*].code` comma-joined in the order
    07 fixed (environment first, review second), which is what makes two
    deviations of the same kind compare as equal strings. The full violation
    objects, with their `detail` and `source`, stay in `document`.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    document = _as_document(data, logger)
    if document is None:
        return False

    values = document.get("values") or {}
    if not isinstance(values, dict):
        logger.warn("deviation message carried no values object")
        return False

    sample_id = _text(values.get("sample_id"))
    batch_context = _block(values, "batch_context")
    environment = _block(values, "environment")

    codes = []
    for violation in values.get("violations") or []:
        if isinstance(violation, dict) and violation.get("code") is not None:
            codes.append(_text(violation.get("code")))

    members = [
        ("document", system.util.jsonEncode(document)),

        ("sample_id", sample_id),
        ("equipment_id", _text(values.get("equipment_id"))),
        ("equipment_identifier", _text(values.get("equipment_identifier"))),
        ("batch_id", _text(values.get("batch_id"))),
        ("disposition", _text(values.get("disposition"))),
        ("analyst", _text(values.get("analyst"))),

        # Never empty on a message that exists -- 07 publishes only deviations.
        ("violation_codes", ",".join(codes) if codes else None),

        # The provenance gap again: `ts` is the acquisition instant and
        # `assessed_at` is when 07 assembled the record.
        ("ts", _parse_iso(document.get("ts"))),
        ("assessed_at", _parse_iso(values.get("assessed_at"))),

        # Both context blocks flattened to the field a reader routes on. A null
        # block means the lookup found nothing; `document` carries the
        # `_reason` string that says why.
        ("batch_operation", _text(batch_context.get("operation"))),
        ("qualified_window", _flag(batch_context.get("qualified_window"))),
        ("environment_status", _text(environment.get("status"))),
        ("environment_age_s", _number(environment.get("age_s"))),
    ]

    written = _write(DEVIATION_TAG, members,
                     "deviation %s" % str(sample_id), logger)
    if written:
        logger.infof("deviation %s landed on qc/deviation-01: %s",
                     str(sample_id), ",".join(codes) or "none")
    return written
