"""Pattern 3 -- assemble an analyzer result envelope from Ignition tags.

Nova publishes off HistoricalSampleResults/SampleTime, a vendor field. The tag script on
that DateTime hands this module the result-folder path; we read the historical UDT siblings
already bound and return one JSON document. Nothing under ICC26Extensions is read.

meta.correlation_id is sample_id. Pattern 4's HTTPS path stamps the same value
on the same topic (mechanism=webhook), so one sample is two colours on the firehose.
The LIMS is not in that chain any more.

Jython 2.7: no f-strings, no type hints, integer division is floor division.
"""

from java.text import SimpleDateFormat
from java.util import Date, TimeZone

LOGGER_NAME = "opcua_event"

SOURCE_ID = "novaflex-01"
SOURCE_TYPE = "analyzer"
MECHANISM = "opcua-event"

# Relative to the result folder the tag script passes in.
_FIELDS = (
    "sample_id",
    "batch_id",
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

_seq = [0]


def _iso(date=None):
    """ISO-8601 in UTC with milliseconds. SimpleDateFormat is not thread-safe, hence per-call."""
    formatter = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")
    formatter.setTimeZone(TimeZone.getTimeZone("UTC"))
    if date is None:
        date = Date()
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


def _next_seq():
    _seq[0] += 1
    return _seq[0]


def build_novaflex_result(result_folder):
    """Read HistoricalSampleResults tags under result_folder and return the envelope JSON.

    result_folder is the Ignition path of the UDT result folder, e.g.
    [default]icc26/site1/qc/analyzers/novaflex-01/result -- the tag script strips
    /sample_time before handing it over. Returns None if SampleTime itself is empty
    so the MQTT handler does not publish a hollow document.
    """
    logger = system.util.getLogger(LOGGER_NAME)
    if not result_folder:
        logger.warn("novaflex result skipped: no result folder path")
        return None

    paths = [result_folder + "/" + name for name in _FIELDS]
    qualified = system.tag.readBlocking(paths)
    by_name = dict(zip(_FIELDS, qualified))

    sample_time = _value(by_name["sample_time"])
    if sample_time is None:
        logger.warn("novaflex result skipped: SampleTime is not Good")
        return None

    envelope = {
        "ts": sample_time,
        "seq": _next_seq(),
        "source": {"id": SOURCE_ID, "type": SOURCE_TYPE},
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso(),
            "correlation_id": _value(by_name["sample_id"]),
        },
        "values": {
            "sample_id": _value(by_name["sample_id"]),
            "batch_id": _value(by_name["batch_id"]),
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
    return system.util.jsonEncode(envelope)
