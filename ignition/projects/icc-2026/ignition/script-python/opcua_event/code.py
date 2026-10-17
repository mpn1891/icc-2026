"""Pattern 3 -- assemble an analyzer result document from Ignition tags.

The analyzer publishes off HistoricalSampleResults/SampleTime, a vendor field. The tag script on
that DateTime hands this module the result-folder path; we read the historical UDT siblings
already bound and return one JSON document. Nothing under ICC26Extensions is read.

What goes on the wire is the timestamp and the values the instrument produced -- no `seq`,
no `source`, no `meta`. An analyzer result is the instrument's document, not the site's, and
a consumer learns where it came from from the topic it arrived on. The sample id travels as
`values.sample_id`, which is what the LIMS ingests and what pattern 4 carries straight back out
under the same name, so the sample is still traceable across every topic it touches without this
pattern asserting a field it did not measure. (Pattern 4 re-stamped it as `meta.correlation_id`
until 2026-09-13; the correlation id was always a copy of `values.sample_id` and went with the
rest of the envelope.)

**`batch_id` is deliberately not published** (removed 2026-09-09). The vendor node is still in
the address space and still writable by an OPC client, but the instrument does not know a batch.
It echoes whatever was typed at its sample-login screen, and that field was removed from the
screen the same day, so the node only ever holds its startup fallback. Ignition stopped
subscribing to it on 2026-09-12: `command/batch_id` and `result/batch_id` came out of the UDT,
which took it from 57 bound tags to 52. A bound tag is a claim that something reads it, and for
three days nothing did.
Publishing a hardcoded constant as though it were a measurement is exactly the assertion the
paragraph above refuses. Batch identity is pattern 7's, resolved from the historian at the
sample instant: docs/plans/07-sample-chain.md section "The decisions 07 inherits", decision 2.

**`vessel_id` failed the same test until 2026-09-12 and was answered the other way.** Nothing
asked for it, so the vendor node sat empty and every run took the instrument's `BRX-2000-A`
fallback -- the batch_id problem exactly, one key further down. It keeps its place here because
a vessel is something the person at the sample port does know, so the sample-login screen now
carries a Vessel ID field defaulted to BR-201. What is on the wire is transcribed, and can be
transcribed wrong, which is the only condition under which this document is allowed to assert
anything it did not measure.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "opcua_event"

# Relative to the result folder the tag script passes in.
_FIELDS = (
    "sample_id",
    "vessel_id",
    "cell_type",
    "sample_source",
    "operator",
    "sample_time",
    "gas/ph",
    "gas/pco2",
    "gas/po2",
    "chem/na",
    "chem/k",
    "chem/ca",
    "chem/nh4",
    "chem/gln",
    "chem/glu",
    "chem/gluc",
    "chem/lac",
    "osmo",
    "cell_density/total_density",
    "cell_density/viable_density",
    "cell_density/viability_percent",
    "cell_density/avg_live_diameter_um",
    "calculated/hco3",
    "calculated/o2_saturation",
    "calculated/co2_saturation",
    "modules_used/cdv",
    "modules_used/chemistry",
    "modules_used/gas",
    "modules_used/osmo",
)


# The Ignition datasource the ICC26 database is reached through. NOT `pg_db`,
# which is the historian's own store and will pass a glance in the dropdown
# before writing nowhere useful.
DATASOURCE = "ICC26"

# `ON CONFLICT DO NOTHING` because the identity of a stored analysis is the
# analyzer plus the sample plus `modified_time` -- one analysis VERSION, not one
# analysis. `result/modified_time` runs again if CDV images are reanalyzed or the
# result is edited after the fact, so a revision is a genuine second row while a
# duplicate stream fire is a no-op. See migrate-12 for the key.
_RESULT_INSERT = ("INSERT INTO qc.analyzer_result "
                  "(device_id, sample_id, ts, modified_time, document) "
                  "VALUES (?, ?, ?, ?, ?::jsonb) "
                  "ON CONFLICT (device_id, sample_id, modified_time) DO NOTHING")


def _last_error():
    """The last line of the current traceback, for a one-line warn.

    Used with a bare `except`, so it has to work for a Java throwable as well as
    a Python exception; `traceback.format_exc()` renders both. Same idiom the
    i3x server's store branches use.
    """
    import traceback
    return traceback.format_exc().splitlines()[-1]


def _instance_path(result_folder):
    """The UDT instance path, given the result folder the tag script hands in.

    The tag script passes `.../cell-analyzer-01/result`; `command/*`,
    `analyzer_id`, `state`, `result_json`, `software_version`, `uptime` and
    `last_error` are one level up. Derived here rather than by changing the tag
    script, which lives in the UDT definition -- editing it means a type edit and
    an instance rebuild for no gain.
    """
    path = str(result_folder)
    if path.endswith("/result"):
        return path[:-len("/result")]
    return path


def _device_id(instance_path, logger):
    """The instance's `device_id` PARAMETER, or None.

    **The parameter, never the `analyzer_id` member and never the instance
    name.** The i3X history reader takes its key from `udtInstance["parameters"]`
    -- the same place `_coalesceMillis` reads `HistoryCoalesceMs` -- so the writer
    has to read it from there too, or the two sides key on different strings and
    every history query comes back empty, quietly.

    `analyzer_id` is the obvious-looking alternative and is the wrong one: it is
    an OPC-bound member reading the instrument's own `Settings/AnalyzerID` node.
    It happens to read `CELL-ANALYZER-01` today, the same as the parameter, but
    nothing holds those two together -- the instrument can be reconfigured, and
    the member can go Bad, and either would silently split the store in two.
    """
    try:
        config = system.tag.getConfiguration(instance_path, False)
        if not config:
            logger.warnf("no tag configuration at %s", str(instance_path))
            return None
        parameters = config[0].get("parameters", None)
        if not parameters or "device_id" not in parameters:
            logger.warnf("no device_id parameter on %s", str(instance_path))
            return None
        value = parameters["device_id"]
        # A parameter arrives either as a bare value or wrapped with its
        # dataType, exactly as the i3X server's own resolution allows for.
        if not isinstance(value, (str, unicode, int, long, float, bool)):
            value = value.value
        return str(value)
    except:
        # **Bare `except`, not `except Exception`.** Jython 2.7 does not make
        # java.lang.Throwable a subclass of Python's Exception, so a Java
        # exception -- which is what every failure below the scripting API
        # actually is -- walks straight through `except Exception` and out of
        # this function. Measured 2026-09-19: with the store's INSERT pointed at
        # a missing table, the analysis was never published at all, because the
        # throw reached the Event Stream and its `failureStrategy` is ABORT.
        # Every `except` in the vendored i3x server is bare for this reason.
        logger.warnf("device_id unreadable on %s -- %s",
                     str(instance_path), _last_error())
        return None


def _millis_deep(value):
    """A member document with every java.util.Date as epoch milliseconds.

    **This is the i3X server's DateTime encoding, not a choice made here**, and
    the same conversion `model_feed._millis` makes for the review store. A
    DateTime member goes out of `/objects/value` as a bare number of epoch
    millis, so the stored document has to use it too -- an object read from
    history that did not match the same object read live would defeat the whole
    reason for storing it pre-shaped.

    Deliberately NOT `_iso()`, which is what the published envelope uses. The
    wire and the store encode DateTimes differently, so they cannot come off one
    encoder: what they share is the read, not the formatting.
    """
    if isinstance(value, Date):
        return value.getTime()
    if isinstance(value, dict):
        converted = {}
        for key in value:
            converted[key] = _millis_deep(value[key])
        return converted
    if isinstance(value, (list, tuple)):
        return [_millis_deep(item) for item in value]
    return value


def _store_analyzer_result(instance_path, logger):
    """The analysis as a row in `qc.analyzer_result`. Rows written: 1 or 0.

    **The document is the whole UDT instance value, read in one call and handed
    over untouched.** `readBlocking` on the instance path returns the nested
    member document -- `result.chem.gluc`, `command.vessel_id`, `result_json`,
    all 52 members -- which is precisely what the i3X server serves from
    `/objects/value`: it reads the same tag and calls the same `toDict()`. Taking
    it whole rather than naming members here is what makes the stored shape and
    the live shape identical by construction instead of by a list somebody has to
    remember to update. The i3X history branch can then hand the row straight
    back, which is only sound because nothing reshapes it on the way out -- the
    `i3x` project cannot import this module, neither project inheriting the other.

    This is a second `readBlocking`, microseconds after the one the envelope is
    built from. That is safe for a reason that was measured rather than assumed:
    on 2026-09-19 a subscription on this instance took one analysis as a single
    push carrying all 29 changed members together -- every result leaf,
    `result_json`, `sample_complete_counter` and `state`->Completed at once --
    because Ignition coalesces the instrument's write batch into one UDT change
    event. There is no window in which half an analysis is readable, so the two
    reads cannot straddle one.

    **`uptime` is stored with the rest**, deliberately. It is a liveness tick
    frozen at write time and it is noise on a history row, but excluding it would
    make the stored document differ from what `/objects/value` returns today, and
    that identity is worth more than the tidiness. Revisit with the heartbeat
    split, not before.

    Failures are reported and swallowed. The Event Stream handler's
    `failureStrategy` is ABORT, so a throw here would mean the analysis is never
    published at all: a Postgres that is down must not cost the demo its publish.
    The same rule `model_feed._store_review` states.
    """
    device_id = _device_id(instance_path, logger)
    if device_id is None:
        logger.warnf("analysis at %s not stored -- no device_id", str(instance_path))
        return 0

    try:
        qualified = system.tag.readBlocking([instance_path])[0]
        if qualified is None or qualified.value is None:
            logger.warnf("analysis at %s not stored -- instance read no value",
                         str(instance_path))
            return 0
        document = _millis_deep(qualified.value.toDict())
    except:
        logger.warnf("analysis at %s not stored -- instance unreadable -- %s",
                     str(instance_path), _last_error())
        return 0

    result = document.get("result", None)
    if not isinstance(result, dict):
        logger.warnf("analysis at %s not stored -- no result folder in the document",
                     str(instance_path))
        return 0

    sample_id = result.get("sample_id", None)
    ts = result.get("sample_time", None)
    modified_time = result.get("modified_time", None)

    # All four are NOT NULL in the table, and `modified_time` is a third of the
    # key. A document missing any of them is one no later revision could
    # supersede, and inventing a value would put exactly that row in the store.
    if not device_id or not sample_id or ts is None or modified_time is None:
        logger.warnf("analysis not stored -- need device_id, sample_id, sample_time "
                     "and modified_time; got %s/%s ts=%s modified_time=%s",
                     str(device_id), str(sample_id), str(ts), str(modified_time))
        return 0

    try:
        return system.db.runPrepUpdate(
            _RESULT_INSERT,
            [device_id, sample_id, Date(ts), Date(modified_time),
             system.util.jsonEncode(document)],
            DATASOURCE)
    except:
        logger.warnf("analysis %s/%s not stored -- %s",
                     str(device_id), str(sample_id), _last_error())
        return 0


def _iso(date):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe, hence per-call."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    return formatter.format(date)


def _good(qv):
    if qv is None:
        return False
    quality = getattr(qv, "quality", None)
    if quality is None:
        return qv.value is not None
    try:
        return bool(quality.isGood())
    except Exception:
        return bool(getattr(quality, "good", False))


def _value(qv):
    """JSON-safe value: Bad/uncertain OPC quality becomes null, never 0."""
    if not _good(qv) or qv.value is None:
        return None
    value = qv.value
    if isinstance(value, Date):
        return _iso(value)
    return value


def build_cell_analyzer_result(result_folder):
    """Read HistoricalSampleResults tags under result_folder and return the result JSON.

    result_folder is the Ignition path of the UDT result folder, e.g.
    [default]icc26/site1/qc/analyzers/cell-analyzer-01/result -- the tag script strips
    /sample_time before handing it over. Returns None if SampleTime itself is empty
    so the MQTT handler does not publish a hollow document.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    if not result_folder:
        logger.warn("cell analyzer result skipped: no result folder path")
        return None

    paths = [result_folder + "/" + name for name in _FIELDS]
    qualified = system.tag.readBlocking(paths)
    by_name = dict(zip(_FIELDS, qualified))

    sample_time = _value(by_name["sample_time"])
    if sample_time is None:
        logger.warn("cell analyzer result skipped: SampleTime is not Good")
        return None

    document = {
        "ts": sample_time,
        "values": {
            "sample_id": _value(by_name["sample_id"]),
            "vessel_id": _value(by_name["vessel_id"]),
            "cell_type": _value(by_name["cell_type"]),
            "sample_source": _value(by_name["sample_source"]),
            "operator": _value(by_name["operator"]),
            "gas": {
                "ph": _value(by_name["gas/ph"]),
                "pco2": _value(by_name["gas/pco2"]),
                "po2": _value(by_name["gas/po2"]),
            },
            "chem": {
                "na": _value(by_name["chem/na"]),
                "k": _value(by_name["chem/k"]),
                "ca": _value(by_name["chem/ca"]),
                "nh4": _value(by_name["chem/nh4"]),
                "gln": _value(by_name["chem/gln"]),
                "glu": _value(by_name["chem/glu"]),
                "gluc": _value(by_name["chem/gluc"]),
                "lac": _value(by_name["chem/lac"]),
            },
            "osmo": _value(by_name["osmo"]),
            "cell_density": {
                "total_density": _value(by_name["cell_density/total_density"]),
                "viable_density": _value(by_name["cell_density/viable_density"]),
                "viability_percent": _value(by_name["cell_density/viability_percent"]),
                "avg_live_diameter_um": _value(by_name["cell_density/avg_live_diameter_um"]),
            },
            "calculated": {
                "hco3": _value(by_name["calculated/hco3"]),
                "o2_saturation": _value(by_name["calculated/o2_saturation"]),
                "co2_saturation": _value(by_name["calculated/co2_saturation"]),
            },
            "modules_used": {
                "cdv": _value(by_name["modules_used/cdv"]),
                "chemistry": _value(by_name["modules_used/chemistry"]),
                "gas": _value(by_name["modules_used/gas"]),
                "osmo": _value(by_name["modules_used/osmo"]),
            },
        },
    }

    # Store, then publish -- pattern 6's `poll` order, and the reason decision 3
    # put the write here rather than in a second handler: a second handler would
    # read the object again at a different instant, which is how a document ends
    # up not matching the analysis it is filed under. Wrapped so it cannot raise;
    # see _store_analyzer_result.
    _store_analyzer_result(_instance_path(result_folder), logger)

    return system.util.jsonEncode(document)
