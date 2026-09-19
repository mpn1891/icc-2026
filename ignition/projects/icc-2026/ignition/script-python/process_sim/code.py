"""The process simulator -- what makes a bioreactor's i3X object move.

**Why this exists.** The i3X server materialises exactly four kinds of object:
UDT instances, the folders on the way to them, the provider root and alarms.
An atomic tag is never an object; it is folded into its parent's JSON Schema as
a property. So for a vessel's temperature, pressure and dissolved oxygen to show
up in the Explorer as *things* -- each with a HasComponent edge from the vessel,
each with its own history and its own subscription target -- they have to be UDT
instances: `process_data/temperature`, `pressure` and `do`, typed from the
`biorx_components` folder (`temperature`, `pressure`, `dissolved_oxygen`), all
inheriting from `biorx_components/process_value`. That base holds `pv` and a
nested `limits` instance of `process_limits` (`low_limit`, `high_limit`): the
server re-sends an object's whole value document on any member change, so the
band is its own object under the value rather than two more members beside
`pv`, and a consumer hears `{pv}` alone unless it asks for depth. And an object
whose value never changes is a worse demo than no object at all. Nothing in the
stack writes those tags -- there is no instrument behind br-201 -- so this
module does.

**The operation drives the numbers.** `batch_data/operation` is the reactor's
ISA-88 operation, stepped by hand through `manual_advance` (pattern 5, `bes_batch`).
Each operation has a profile here: a target and a band for each of the three
values. On every tick, `pv` settles toward the target with a first-order lag plus
a little noise, and the limits are written to the band whenever they differ from
it. The consequence on stage: click `manual_advance` from CIP to SIP and the
Explorer's history panel shows temperature ramping from 80 to 121 over the next
few ticks with the band stepping around it, which is a structure-and-history beat
in one gesture. The operation-to-profile rule lives only here; the tags carry
the result, never the rule (docs/00-architecture.md, "Derived flags travel with
the fact that produced them").

**Stateless on purpose.** The only state is `pv` itself, read back each tick, so
a gateway restart resumes from wherever the tag was rather than snapping. Every
bioreactor instance under BIOREACTORS is simulated, br-202 included: its
`operation` is on the type, so it has one, and process values it never sampled
for are not what makes it the "looks live and is not" vessel -- the sample path
is.

Cadence is `attributes.delay` on the `process-sim` timer, not anything here.

Jython 2.7: no f-strings, no type hints, `%` formatting.
"""

import random

LOGGER_NAME = "process_sim"

BIOREACTORS = "[default]icc26/site1/upstream/bioreactors"
BIOREACTOR_TYPE = "bioreactor"

# Nested instance names, and the members every one of them inherits, relative
# to the instance. The limits sit one level down, inside the nested `limits`
# object (see the module docstring for why).
LOOPS = ("temperature", "pressure", "do")
PV = "pv"
LOW = "limits/low_limit"
HIGH = "limits/high_limit"
MEMBERS = (PV, LOW, HIGH)

# Per operation, per loop: (target, low_limit, high_limit). Temperature in
# degC, pressure in bar gauge, DO in percent of air saturation. Plausible for a
# mammalian-cell vessel and nothing more: CIP is hot caustic, SIP is saturated
# steam, INOC/GROWTH are the 37 degC hold with DO pulled down by the culture,
# HARVEST is chilled, IDLE is a vented vessel at room temperature.
PROFILES = {
    "IDLE":    {"temperature":        (22.0, 15.0, 30.0),
                "pressure":           (0.00, -0.05, 0.10),
                "do":                 (100.0, 90.0, 110.0)},
    "CIP":     {"temperature":        (80.0, 75.0, 85.0),
                "pressure":           (0.50, 0.30, 0.80),
                "do":                 (5.0, 0.0, 15.0)},
    "SIP":     {"temperature":        (121.0, 119.0, 124.0),
                "pressure":           (2.10, 1.90, 2.30),
                "do":                 (0.0, 0.0, 5.0)},
    "INOC":    {"temperature":        (37.0, 36.5, 37.5),
                "pressure":           (0.10, 0.05, 0.20),
                "do":                 (60.0, 40.0, 80.0)},
    "GROWTH":  {"temperature":        (37.0, 36.5, 37.5),
                "pressure":           (0.15, 0.05, 0.25),
                "do":                 (40.0, 30.0, 50.0)},
    "HARVEST": {"temperature":        (8.0, 4.0, 12.0),
                "pressure":           (0.05, 0.00, 0.15),
                "do":                 (40.0, 20.0, 60.0)},
}
DEFAULT_OPERATION = "IDLE"

# Fraction of the remaining gap to the target closed per tick. 0.25 at a 5 s
# tick puts a CIP -> SIP temperature step within a degree of target in about a
# minute, which is slow enough to watch and fast enough to not bore a room.
LAG = 0.25

# Noise sigma as a fraction of the band width. Small enough that a settled
# value stays inside its limits; this simulator is not the excursion demo.
NOISE = 0.04


def tick():
    """One pass over every bioreactor. Called by the process-sim timer.

    Catches everything: a simulator that throws in a shared timer thread takes
    the cadence down with it, and a warning in the log is the right outcome for
    a vessel with a broken tag, not a stopped clock for all of them.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    try:
        vessels = _bioreactors()
    except Exception as exc:
        logger.warnf("browse of %s failed -- %s", BIOREACTORS, str(exc))
        return 0

    written = 0
    for vessel in vessels:
        try:
            written += _tick_vessel(vessel)
        except Exception as exc:
            logger.warnf("%s skipped -- %s", vessel, str(exc))
    return written


def _bioreactors():
    """Full paths of every bioreactor instance directly under BIOREACTORS.

    Filters on `typeId` when the browse result carries one; only bioreactors
    live in that folder, so a result without it is taken rather than dropped.
    """
    found = []
    for row in system.tag.browse(BIOREACTORS, {"tagType": "UdtInstance"}).getResults():
        try:
            type_id = row["typeId"]
        except Exception:
            type_id = None
        if type_id is None or str(type_id) == BIOREACTOR_TYPE:
            found.append(str(row["fullPath"]))
    return found


def _tick_vessel(vessel):
    """Advance one vessel's three loops one step toward the operation's profile.

    One read and one write per vessel, not per loop: the three loops are one
    consistent view of the operation, and nine round trips per vessel per tick
    is the kind of thing that shows up in a gateway's tag write metrics.
    """
    paths = [vessel + "/batch_data/operation"]
    for loop in LOOPS:
        for member in MEMBERS:
            paths.append("%s/process_data/%s/%s" % (vessel, loop, member))
    values = system.tag.readBlocking(paths)

    operation = values[0].value
    operation = str(operation).strip().upper() if operation is not None else ""
    profile = PROFILES.get(operation)
    if profile is None:
        profile = PROFILES[DEFAULT_OPERATION]

    write_paths = []
    write_values = []
    i = 1
    for loop in LOOPS:
        target, low, high = profile[loop]
        current = values[i].value
        cur_low = values[i + 1].value
        cur_high = values[i + 2].value
        i += 3

        # A tag that has never been written starts at the target: the first
        # tick after a fresh gateway should not ramp from zero.
        if current is None:
            current = target
        next_pv = _step(float(current), target, low, high)

        write_paths.append("%s/process_data/%s/%s" % (vessel, loop, PV))
        write_values.append(round(next_pv, 3))
        if cur_low is None or float(cur_low) != low:
            write_paths.append("%s/process_data/%s/%s" % (vessel, loop, LOW))
            write_values.append(low)
        if cur_high is None or float(cur_high) != high:
            write_paths.append("%s/process_data/%s/%s" % (vessel, loop, HIGH))
            write_values.append(high)

    system.tag.writeBlocking(write_paths, write_values)
    return len(write_paths)


def _step(current, target, low, high):
    """First-order lag toward target, plus band-scaled noise."""
    band = high - low
    if band <= 0:
        band = abs(target) if target else 1.0
    return current + LAG * (target - current) + random.gauss(0.0, NOISE * band)
