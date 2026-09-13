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
    return system.util.jsonEncode(document)
