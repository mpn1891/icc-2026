"""Pattern 5 -- the batch engine. Writes bes.batch_event, and publishes nothing.

**The invariant.** This module must never import or call
`system.cirruslink.transmission.publish`, or anything else that reaches the
broker. Pattern 5's entire claim on stage is that the writer does not know MQTT
exists: it writes a row, Debezium tails the WAL out of band as user `cdc`, and a
message appears that nobody here asked for. Stop Debezium and this keeps working
with a silent topic -- that is the failure demo. One publish call from here and
the pattern is gone.

**How it fires.** A `valueChanged` script on `bioreactor/batch_data/manual_advance`
(tag-type-definition/default/udts.json) calls `advance()` on the rising edge.
Somebody types a batch_id in Tag Explorer and clicks the boolean; the reactor
steps IDLE -> CIP -> SIP -> INOC -> GROWTH -> HARVEST -> IDLE. Auto-cycling on a
dwell was the earlier design and was dropped 2026-08-26: manual is deterministic
on stage, which is what parking the reactor in GROWTH before badging the valve
requires.

**Two rows per click, one transaction, one timestamp.** An advance closes the
outgoing operation and opens the incoming one. Both rows share `occurred_at`, so
pattern 7's "what was running at time T" lookup has to tie-break on `id` --
`ORDER BY occurred_at DESC, id DESC`. That is in 02-schema.sql's comment too.

This module is gateway-scoped when called from a tag event script, so it resolves
only if Gateway Settings -> Gateway Scripting Project is `icc-2026`.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.util import Date

LOGGER_NAME = "bes_batch"

# NOT `pg_db`. That one points at the `postgres` database as user `ignition` --
# wrong database, wrong user -- and it will pass a glance in the dropdown and
# then write nowhere useful. docs/00-architecture.md is emphatic about this.
DATASOURCE = "ICC26"

# ISA-88 operations, in the order the protocol runs them. These are *operations*,
# not phases: a phase is the smallest element that does process action ("Add
# Water", "Agitate"), one level below this.
SEQUENCE = ["CIP", "SIP", "INOC", "GROWTH", "HARVEST"]
IDLE = "IDLE"

# The batch protocol qualifies sampling for GROWTH only. Pulling material during
# CIP/SIP makes no sense, and INOC/HARVEST are outside the characterized
# production phase.
#
# **This tuple is the only copy of that rule.** Pattern 7 must not test
# `operation == "GROWTH"` itself -- it reads the flag this module wrote. See
# docs/00-architecture.md, "Derived flags travel with the fact that produced
# them": if the aggregator held a second copy of the batch protocol, the two
# copies would drift.
QUALIFIED = ("GROWTH",)

# `?::jsonb` is what lets pgjdbc bind the payload as text and have Postgres cast
# it. If the driver ever argues, the fallback is `stringtype=unspecified` in the
# datasource's extra connection properties -- targeted cast first, global switch
# second.
_INSERT = ("INSERT INTO bes.batch_event "
           "(batch_id, equipment_id, event_type, operation, payload, occurred_at) "
           "VALUES (?, ?, ?, ?, ?::jsonb, ?)")


def _payload(qualified):
    """The jsonb column, built here so the flag is in the WAL Debezium tails.

    Computing it in the cdc-sink instead would put it on the wire without it ever
    having been in the change event -- a flag the CDC demo did not observe.
    """
    return system.util.jsonEncode({"qualified_window": bool(qualified)})


def _next_operation(current):
    """The operation after `current`, or None when the batch is over.

    IDLE (or anything unrecognised, including a null tag on a fresh gateway)
    starts the sequence. HARVEST returns None, which the caller turns into a
    batch_end row and a return to IDLE.
    """
    if current in SEQUENCE:
        index = SEQUENCE.index(current)
        if index + 1 < len(SEQUENCE):
            return SEQUENCE[index + 1]
        return None
    return SEQUENCE[0]


def _equipment_id(base):
    """'br-201' from '[default]icc26/.../bioreactors/br-201/batch_data'.

    The topic form, taken from the tag path -- NOT plant.equipment's 'BR-201'.
    `batch_data` lives on the bioreactor *type*, so br-202 has a live
    manual_advance button too, and without this a click over there would write
    rows the sink publishes onto br-201's topic.
    """
    parts = [p for p in base.split("/") if p]
    if len(parts) < 2:
        return ""
    return parts[-2]


def advance(base):
    """One manual operation advance. `base` is the batch_data folder path.

    Writes the rows first and the tags second, so the tag can never claim an
    operation the database does not have. Resets manual_advance on every exit
    path, including the refusals -- a button that stays stuck down is worse than
    one that did nothing.
    """
    logger = system.util.getLogger(LOGGER_NAME)

    batch_tag = base + "/batch_id"
    operation_tag = base + "/operation"
    flag_tag = base + "/manual_advance"

    values = system.tag.readBlocking([batch_tag, operation_tag])
    batch_id = values[0].value
    current = values[1].value

    if batch_id is None or not str(batch_id).strip():
        logger.warnf("advance refused at %s: no batch_id set", base)
        system.tag.writeBlocking([flag_tag], [False])
        return

    batch_id = str(batch_id).strip()
    equipment_id = _equipment_id(base)
    if not equipment_id:
        logger.warnf("advance refused at %s: cannot derive equipment_id", base)
        system.tag.writeBlocking([flag_tag], [False])
        return

    current = str(current) if current is not None else IDLE
    next_operation = _next_operation(current)

    # One instant for the whole transition. Both rows carry it.
    occurred_at = Date()

    rows = []
    if current in SEQUENCE:
        # qualified_window is false on every operation_end: the flag describes
        # the interval that BEGINS at occurred_at, and nothing is running
        # between an end and the next start. An operation_end for GROWTH
        # carrying true would tell pattern 7 the sample was qualified when it
        # was not.
        rows.append(("operation_end", current, False))

    if next_operation is None:
        rows.append(("batch_end", None, False))
    else:
        rows.append(("operation_start", next_operation, next_operation in QUALIFIED))

    tx = None
    try:
        tx = system.db.beginTransaction(database=DATASOURCE, timeout=5000)
        for event_type, operation, qualified in rows:
            system.db.runPrepUpdate(
                _INSERT,
                [batch_id, equipment_id, event_type, operation,
                 _payload(qualified), occurred_at],
                tx=tx)
        system.db.commitTransaction(tx)
    except Exception:
        if tx is not None:
            try:
                system.db.rollbackTransaction(tx)
            except Exception:
                pass
        # Leave `operation` alone -- the tag still reflects what the database
        # holds, which is the state that is actually true.
        logger.errorf("advance failed for %s on %s; no rows written",
                      batch_id, equipment_id)
        system.tag.writeBlocking([flag_tag], [False])
        raise
    finally:
        if tx is not None:
            try:
                system.db.closeTransaction(tx)
            except Exception:
                pass

    # Only now. The record exists; the tag may follow it.
    new_operation = next_operation if next_operation is not None else IDLE
    system.tag.writeBlocking([operation_tag, flag_tag], [new_operation, False])

    logger.infof("%s %s: %s -> %s (%d rows)",
                 equipment_id, batch_id, current, new_operation, len(rows))
