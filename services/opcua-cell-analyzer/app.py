#!/usr/bin/env python3
"""A simulated cell-culture analyzer, served over OPC UA.

Implements the information model in docs/reference/novaflex2-opcua-model.md, which transcribes
Section 9 of the vendor's OPC Server Instructions for Use
Manual (LPN 60644B, 2024-03). Read that document first -- it carries the provenance note, the
tag mapping and the list of places the published table contradicts itself.

READ THIS BEFORE COMPARING WITH opcua-countess. The two servers look different on purpose:

  * The Countess 3 FL has NO vendor OPC server. Its address space is ours, so it is shaped the
    way OPC UA wants -- DeviceSet, FunctionalUnitSet, ResultSet, DI and LADS browse names.

  * The analyzer HAS a real vendor OPC UA server, and this file reproduces what that server
    actually publishes: two flat trees of string-NodeId tags, OPCSystemObjects (read) and
    OPCSystemCommands (write), inherited from the OPC DA server they were derived from. It is
    not what a greenfield OPC UA model would look like. That is precisely why it is worth
    showing -- this is what integrating a real instrument looks like.

Three facts about the vendor server drive everything below, and each is a talk point:

  * There is NO completion counter and NO event. Section 9 lists no monotonic sequence tag, no
    "sample ready" flag, and the server raises no OPC UA events. SampleResults simply changes
    underneath any client watching it. A client therefore cannot tell a new sample from a
    re-read without diffing TimeStamp itself -- and cannot tell two samples apart at all if
    they happen to carry equal values. ICC26Extensions still exists as a labelled demo
    branch (counter, state, ResultJson) so the gap is visible in the address space; the
    Ignition publish does not use it. Pattern 3 keys off HistoricalSampleResults/SampleTime,
    a vendor field, and this simulator writes that node last so the trigger fires after
    every other historical leaf has settled.

  * There are NO methods. Every action the analyzer exposes is a writable Boolean you set to 1 --
    ESMScheduleAnalysis, GasCalibration, ClearWells. §6.1 of the Countess model doc argued that
    command bits are what ships because a SCADA tag cannot invoke a method; this instrument is
    that argument, from a vendor, in a shipping product. So the stage trigger here is the
    vendor's own ESMScheduleAnalysis bit. Nothing was invented to make it work.

  * Absent-vs-zero has vendor support. ModuleInformation/Modules/{CDV,Chemistry,Gas,Osmo} is a
    Boolean per module saying whether it took part in THIS analysis. So a result can say, in
    the vendor's own vocabulary, "the osmometer was not used" (Modules/Osmo = False, Good)
    while Osmo/Result carries no value at all (Bad_NoData) rather than a lying 0.0.

Two further deliberate choices, same as the Countess and for the same reasons:

  * SampleCompleteCounter is written LAST, after every result leaf and after State.
  * Every leaf of one result carries the SAME SourceTimestamp -- the acquisition instant, not
    the write instant -- so a set of reads is provably one sample.

asyncua is the only dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import random
import signal
import sys
from datetime import datetime, timedelta, timezone

from asyncua import Server, ua

import webui

NAMESPACE_URI = "http://icc26.demo/UA/CellAnalyzer/"
LOG = logging.getLogger("cell-analyzer")

# How long an analysis sits in Running before its result lands. A real instrument running the full
# panel (pH/gas + chemistry + osmolality + cell density) takes several minutes; 8 s is a demo
# choice, long enough to watch State sit in Running and the counter lag the trigger, short
# enough not to narrate dead air. Fixed rather than a range so a rehearsal is predictable.
RUN_DURATION_S = 8.0
QC_DURATION_S = 5.0


# ── config ───────────────────────────────────────────────────────────────────────────────


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        logging.warning("%s is not a number, using %s", name, default)
        return default


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, default))


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


class Config:
    def __init__(self) -> None:
        # asyncua binds to the host in the endpoint URL, so 0.0.0.0 is what makes the server
        # reachable both from other containers by DNS name and from the host through the
        # published port. See set_match_discovery_client_ip in main_async().
        self.bind_host = _env("OPCUA_BIND_HOST", "0.0.0.0")
        self.port = _env_int("OPCUA_PORT", 4840)
        self.endpoint_path = _env("OPCUA_ENDPOINT_PATH", "/cell-analyzer/")

        # The manual prints browse paths as `OPCSystemObjects -> Item` and prints the NodeId
        # identifier the same way (§1.2.2: `s = OPCSystemObjects -> Example.Item.Name`), so the
        # arrow appears to BE the separator rather than typography. It is not possible to be
        # certain from a PDF, and a real instrument is the only way to settle it -- hence a knob.
        # Set NODE_SEPARATOR=. if a live instrument turns out to use dotted item ids.
        self.separator = _env("NODE_SEPARATOR", "->")

        self.analyzer_id = _env("ANALYZER_ID", "CELL-ANALYZER-01")
        self.location = _env("LOCATION", "Site 1 / QC Lab")
        self.software_version = _env("SOFTWARE_VERSION", "4.3.1")
        self.serial_number = _env("SERIAL_NUMBER", "FX2-2026-0119")

        # Which measurement modules are fitted. The osmometer is absent by default: that keeps
        # the absent-vs-zero case (model doc section 8) live on stage instead of hypothetical.
        # Osmo/Result then sits at Bad_NoData while the chemistry and gas analytes carry real
        # numbers, in the same result, at the same timestamp -- and the vendor's own
        # ModuleInformation/Modules/Osmo says False beside it, Good.
        self.gas_installed = _env_bool("GAS_INSTALLED", True)
        self.chem_installed = _env_bool("CHEM_INSTALLED", True)
        self.cdv_installed = _env_bool("CDV_INSTALLED", True)
        self.osmo_installed = _env_bool("OSMO_INSTALLED", False)

        # Sampling hardware. Bank B absent by default for the same reason -- its ten RSM status
        # tags stay Bad_NoData beside bank A's live ones.
        self.esm_installed = _env_bool("ESM_INSTALLED", True)
        self.autosampler_installed = _env_bool("AUTOSAMPLER_INSTALLED", True)
        self.autosampler_bank_b = _env_bool("AUTOSAMPLER_BANK_B", False)
        self.retain_collector_installed = _env_bool("RETAIN_COLLECTOR_INSTALLED", False)

        self.sample_types = [s.strip() for s in _env(
            "SAMPLE_TYPES", "Default,CHO Fed-Batch,HEK Perfusion,Spent Media"
        ).split(",") if s.strip()]
        self.sample_type = _env("ACTIVE_SAMPLE_TYPE", self.sample_types[0])

        # Both 0 mean "never start an analysis on your own": wait indefinitely for
        # a trigger. That is the shipped setting since 2026-08-26, because the
        # sample id now arrives by transcription -- a free-running analyzer
        # invents ids nobody typed, and every result it produces lands unmatched
        # in the LIMS. Set them non-zero to get the old self-driving behaviour.
        self.first_sample_delay_s = _env_float("FIRST_SAMPLE_DELAY_S", 0.0)
        self.sample_interval_s = _env_float("SAMPLE_INTERVAL_S", 0.0)

        # The instrument's own sample-login touchscreen. See webui.py: this is
        # where a person types the valve's sample id in, which is the only thing
        # joining pattern 1 to pattern 3.
        self.http_port = _env_int("HTTP_PORT", 8087)

        # Every Nth free-running cycle runs an onboard QC instead of a sample. QC writes
        # QCResults and increments QcCompleteCounter -- it does NOT touch SampleResults or
        # SampleCompleteCounter. That is the manual's own warning made visible: the note at the
        # top of section 9 says HistoricalSampleResults excludes QC. 0 disables.
        self.qc_every_n = _env_int("QC_EVERY_N", 6)

        # Probability that one analyte's sensor errors on a given sample. The sample still
        # completes and still increments the counter -- one bad sensor does not void a run on a
        # real analyzer -- but that analyte's Result is Bad while its ErrorStatus carries text.
        self.sensor_error_rate = _env_float("SENSOR_ERROR_RATE", 0.0)
        # Probability the whole analysis fails (dispense timeout). Counter does NOT increment.
        self.failure_rate = _env_float("FAILURE_RATE", 0.0)

        # The manual says "write a 1 to this tag to initiate ..." and never says whether the
        # server clears it again. A bit that does not self-clear must be cleared by the client
        # before it can fire twice, which is a real integration difference, so it is a knob.
        # Default true keeps the trigger a safe one-shot for the demo.
        self.command_auto_clear = _env_bool("COMMAND_AUTO_CLEAR", True)

        # Culture trajectory. The point of the walk is that the trend charts are worth looking
        # at: glucose falls as lactate and ammonia rise, viable density peaks then declines.
        self.culture_span_samples = _env_int("CULTURE_SPAN_SAMPLES", 60)
        self.viability_start = _env_float("VIABILITY_START", 98.0)
        self.viability_end = _env_float("VIABILITY_END", 84.0)
        self.density_start = _env_float("DENSITY_START", 0.4)     # 10^6 cells/mL
        self.density_peak = _env_float("DENSITY_PEAK", 12.0)
        self.glucose_start = _env_float("GLUCOSE_START", 6.0)     # g/L
        self.glucose_end = _env_float("GLUCOSE_END", 1.4)
        self.lactate_start = _env_float("LACTATE_START", 0.2)     # g/L
        self.lactate_end = _env_float("LACTATE_END", 2.6)

        self.seed = _env_int("RANDOM_SEED", 0)


# ── Section 9, as tables ─────────────────────────────────────────────────────────────────
#
# The single source of truth for the address space AND for every write. These tables can be
# diffed against docs/reference/novaflex2-opcua-model.md line for line, and that doc against
# the vendor PDF. Paths use "/" here; the configured NODE_SEPARATOR is substituted when the
# NodeId is built, so changing the separator does not touch a single table.

V = ua.VariantType

GAS_PARAMS = ("pH", "pCO2", "pO2")
CHEM_PARAMS = ("Na", "K", "Ca", "NH4", "Gln", "Glu", "Gluc", "Lac")

# Three lists of thirteen that disagree on their last member. Section 9 uses TotalDensity in
# the range tables, CDV in the alert/warning tables and Density in the unit-of-measure table,
# all for the cell-density channel. Reproduced rather than harmonised: a client browsing a real
# instrument meets all three spellings, and "helpfully" unifying them here would hide that.
RANGE_PARAMS = GAS_PARAMS + CHEM_PARAMS + ("Osmo", "TotalDensity")
ALERT_PARAMS = GAS_PARAMS + CHEM_PARAMS + ("Osmo", "CDV")
UNIT_PARAMS = GAS_PARAMS + CHEM_PARAMS + ("Osmo", "Density")

# The analyzer publishes units as String tags, one per analyte, rather than as OPC UA
# EUInformation properties. That is the vendor's answer to units and it is reproduced verbatim
# -- no EngineeringUnits/EURange properties are attached to the vendor branch. See the model
# doc's deviations table for what a properly modelled server would do instead.
UNITS = {
    "pH": "",                      # pH is a logarithmic ratio: genuinely unitless
    "pCO2": "mmHg", "pO2": "mmHg",
    "Na": "mmol/L", "K": "mmol/L", "Ca": "mmol/L", "NH4": "mmol/L",
    "Gln": "mmol/L", "Glu": "mmol/L",
    "Gluc": "g/L", "Lac": "g/L",
    "Osmo": "mOsm/kg",
    "Density": "10^6 cells/mL", "TotalDensity": "10^6 cells/mL", "CDV": "10^6 cells/mL",
}

# Consumables. Six packs share one nine-field shape; the two sensor cards share a seven-field
# shape that has no Empty/FluidRemaining (a card is not a fluid) and adds Hydrated.
PACK_FIELDS = [
    ("Empty", V.Boolean), ("ExpirationDate", V.DateTime), ("Expired", V.Boolean),
    ("FluidRemaining", V.Int32), ("InstallationDate", V.DateTime), ("Installed", V.Boolean),
    ("LotNumber", V.String), ("SamplesRemaining", V.Int32),
    ("SamplesRemainingPercent", V.Int32),
]
CARD_FIELDS = [
    ("ExpirationDate", V.DateTime), ("Expired", V.Boolean), ("Hydrated", V.Boolean),
    ("InstallationDate", V.DateTime), ("Installed", V.Boolean), ("LotNumber", V.String),
    ("SamplesRemaining", V.Int32),
]
PACKS = ("CDVPackStatus", "ChemPackStatus", "ChemQCPackStatus",
         "GasPackStatus", "GasQCPackStatus", "ESMPackStatus")
CARDS = ("ChemCard", "GasCard")

# Calibration status lives under a DP_* wrapper repeated three deep -- DP_GasCal/GasCal/GasCal
# -- which is an artifact of the DA server's object naming and is reproduced as published.
CAL_BRANCHES = (
    ("DP_GasCal/GasCal/GasCal", GAS_PARAMS),
    ("DP_ChemCal/ChemCal/ChemCal", CHEM_PARAMS),
    ("DP_OsmoCal/OsmoCal/OsmoCal", None),
    ("DP_CdvCal/CdvCal/CdvCal", None),
)

RSM_FIELDS = [
    ("ExpirationDate", V.DateTime), ("FluidRemaining", V.Int32), ("Initialized", V.Boolean),
    ("PackStatus", V.String), ("ReactorPrimed", V.Boolean), ("SampleLineStatus", V.String),
    ("Status", V.String),
]

# Sample metadata written by the client BEFORE the trigger bit, and echoed back on the result.
# Note the vendor spells this CdvDilutionRatio on the command side and CellDensityDilutionRatio
# on the result side, for the same quantity. Reproduced, not harmonised.
SAMPLE_INFORMATION = [
    ("BatchID", V.String), ("CellType", V.String), ("PreDilutionMultiplier", V.Double),
    ("SampleID", V.String), ("SpargingO2", V.Double), ("VesselID", V.String),
    ("VesselPressure", V.Double), ("VesselTemperature", V.Double),
]
COMMAND_SAMPLE_INFORMATION = [
    ("BatchID", V.String), ("CdvDilutionRatio", V.String), ("CellInspection", V.String),
    ("CellType", V.String), ("ChemistryDilutionRatio", V.String),
    ("PreDilutionMultiplier", V.Double), ("SampleID", V.String), ("SpargingO2", V.Double),
    ("VesselID", V.String), ("VesselPressure", V.Double), ("VesselTemperature", V.Double),
]

CALCULATED_RESULTS = [
    ("CO2Saturation", V.Double), ("HCO3", V.Double), ("O2Saturation", V.Double),
    ("pCO2Corrected", V.Double), ("pHCorrected", V.Double), ("pO2Corrected", V.Double),
]
# Five unit tags for six calculated values. pHCorrected has none, which is correct rather than
# an omission -- pH is unitless -- but note that the vendor still ships a pH Units tag under
# Gas. Consistency was not the priority when this list grew.
CALCULATED_UNITS = ("CO2SaturationUnits", "HCO3Units", "O2SaturationUnits",
                    "pCO2CorrectedUnits", "pO2CorrectedUnits")

CELL_DENSITY_FIELDS = [
    ("AvgLiveDiameter", V.Double), ("GoodImageCount", V.Int32), ("LiveStdDeviation", V.Double),
    ("TotalCellCount", V.Int32), ("TotalDensity", V.Double), ("TotalDensityUnits", V.String),
    ("TotalLiveCount", V.Int32), ("Viability", V.Double), ("ViableDensity", V.Double),
    ("ViableDensityUnits", V.String),
]


def _result_fields(historical: bool) -> list[tuple[str, V]]:
    """The SampleResults / HistoricalSampleResults tree, section 9 pp. 9-9..9-15 and 9-20..9-27.

    The two trees are identical except that the historical one also carries the sample-retain
    fields. The manual's own guidance (the IMPORTANT note opening section 9) is to read the
    historical one, because it captures every analysis regardless of how it was initiated.
    """
    fields: list[tuple[str, V]] = [
        ("StartTags/AutosamplerPort", V.String),
        ("StartTags/SampleSource", V.String),
        ("StartTags/DispenseVolume", V.Int32),
        ("StartTags/Operator", V.String),
        ("StartTags/SampleType", V.String),
        ("StartTags/TrayLocation", V.Int32),
        ("StartTags/ModuleInformation/CellDensityDilutionRatio", V.String),
        ("StartTags/ModuleInformation/CellInspection", V.String),
        ("StartTags/ModuleInformation/ChemistryDilutionRatio", V.String),
        ("StartTags/ModuleInformation/Modules/CDV", V.Boolean),
        ("StartTags/ModuleInformation/Modules/Chemistry", V.Boolean),
        ("StartTags/ModuleInformation/Modules/Gas", V.Boolean),
        ("StartTags/ModuleInformation/Modules/Osmo", V.Boolean),
    ]
    if historical:
        fields += [("StartTags/FollowWithRetain", V.Boolean),
                   ("StartTags/RetainVolume", V.Double)]
    fields += [(f"StartTags/SampleInformation/{name}", vtype)
               for name, vtype in SAMPLE_INFORMATION]

    # Four numbers per analyte describing the range and correlation applied to THIS analysis.
    # OffsetIntercept 0 and OffsetMultiplier 1 mean "no correlation applied" -- the vendor says
    # so explicitly, which is why they are published rather than left implicit.
    for limit in ("LowerLimit", "UpperLimit", "OffsetIntercept", "OffsetMultiplier"):
        fields += [(f"StartTags/Ranges/{p}/{limit}", V.Double) for p in RANGE_PARAMS]

    fields += [
        ("CellDensity/FlowTimeData/FlowTime", V.Double),
        ("Chem/FlowTimeData/FlowTime", V.Double),
        ("Gas/FlowTimeData/FlowTime", V.Double),
        ("ModifiedTime", V.DateTime),
        ("SampleTime", V.DateTime),
        ("TimeInTray", V.String),
        ("TimeStamp", V.DateTime),
        ("Errors", V.String),
    ]
    for param in GAS_PARAMS:
        fields += [(f"Gas/{param}/Result", V.Double), (f"Gas/{param}/Units", V.String),
                   (f"Gas/{param}/ErrorStatus", V.String)]
    for param in CHEM_PARAMS:
        fields += [(f"Chem/{param}/Result", V.Double), (f"Chem/{param}/Units", V.String),
                   (f"Chem/{param}/ErrorStatus", V.String)]
    fields += [("Osmo/Result", V.Double), ("Osmo/Units", V.String),
               ("Osmo/ErrorStatus", V.String)]

    fields += [(f"CalculatedResults/{name}", vtype) for name, vtype in CALCULATED_RESULTS]
    fields += [(f"CalculatedResults/{name}", V.String) for name in CALCULATED_UNITS]
    fields += [(f"CellDensity/{name}", vtype) for name, vtype in CELL_DENSITY_FIELDS]

    if historical:
        fields.append(("RetainCount", V.Int32))
    return fields


def _qc_fields() -> list[tuple[str, V]]:
    """QCResults, section 9 pp. 9-16..9-20.

    Not a subset of the sample tree: QC has its own start tags (lot, level, expiry), no
    ModuleInformation, and no CalculatedResults. It also has no ModifiedTime.
    """
    fields: list[tuple[str, V]] = [
        ("StartTags/ExpirationDate", V.DateTime),
        ("StartTags/Level", V.String),
        ("StartTags/LotNumber", V.String),
        ("StartTags/Operator", V.String),
    ]
    for limit in ("LowerLimit", "UpperLimit", "OffsetIntercept", "OffsetMultiplier"):
        fields += [(f"StartTags/Ranges/{p}/{limit}", V.Double) for p in RANGE_PARAMS]
    fields += [
        ("CellDensity/FlowTimeData/FlowTime", V.Double),
        ("Chem/FlowTimeData/FlowTime", V.Double),
        ("Gas/FlowTimeData/FlowTime", V.Double),
        ("SampleTime", V.DateTime),
        ("TimeStamp", V.DateTime),
        ("Errors", V.String),
    ]
    for param in GAS_PARAMS:
        fields += [(f"Gas/{param}/Result", V.Double), (f"Gas/{param}/Units", V.String),
                   (f"Gas/{param}/ErrorStatus", V.String)]
    for param in CHEM_PARAMS:
        fields += [(f"Chem/{param}/Result", V.Double), (f"Chem/{param}/Units", V.String),
                   (f"Chem/{param}/ErrorStatus", V.String)]
    fields += [("Osmo/Result", V.Double), ("Osmo/Units", V.String),
               ("Osmo/ErrorStatus", V.String)]
    # The cell-density QC block is smaller than the sample one: density and image count only.
    fields += [("CellDensity/TotalDensity", V.Double), ("CellDensity/GoodImageCount", V.Int32),
               ("CellDensity/Units", V.String), ("CellDensity/ErrorStatus", V.String)]
    return fields


SAMPLE_RESULT_FIELDS = _result_fields(historical=False)
HISTORICAL_RESULT_FIELDS = _result_fields(historical=True)
QC_RESULT_FIELDS = _qc_fields()

# Commands whose folder and tag share a name: <-OPCSystemCommands->ClearWells->ClearWells.
SIMPLE_COMMANDS = (
    "DeproWells", "ClearWells", "ClearScheduledTasks",
    "ChemistryCalibration", "GasCalibration",
    "ChemistryQcLevel1", "ChemistryQcLevel2", "GasQcLevel1", "GasQcLevel2",
    "AdjustIntensity",
    "AutosamplerDeproSystem", "AutosamplerInitializeRSM", "AutosamplerInitializeSTM",
    "AutosamplerTerminate",
    "ESMClean", "ESMInitialize", "ESMTerminate",
    "EXT_OLSTerminate",
)

# Commands that carry arguments in sibling tags. The contract is always the same and always
# implicit: write the arguments first, then write the Boolean. Nothing is atomic.
PORTED_COMMANDS = ("AutosamplerCleanup", "AutosamplerPrimePack", "AutosamplerPrimeReactor")

SYNC_EVENTS = ("ESMRequestDispenseRemaining", "ESMRequestInitialDispense",
               "EXT_OLSRequestSample", "EXT_OLSSampleAspirated")

# ICC26Extensions/State. Not a vendor enum -- the analyzer publishes no analyzer state tag at all,
# only ActiveTasks as free text. Modelled on the LADS functional-unit state machine, same as
# the Countess, so the two analyzers read alike where the demo touches them.
UNIT_STATE = {"Idle": 0, "Running": 1, "Completed": 2, "Aborted": 3, "Error": 4,
              "Calibrating": 5, "QualityControl": 6, "Maintenance": 7}

DEFAULT_VALUE = {
    V.UInt32: 0, V.Int32: 0, V.Double: 0.0, V.Boolean: False, V.String: "",
    V.DateTime: datetime(1970, 1, 1, tzinfo=timezone.utc),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── typed leaves ─────────────────────────────────────────────────────────────────────────


def _good(value, vtype: V, ts: datetime) -> ua.DataValue:
    return ua.DataValue(Value=ua.Variant(value, vtype), SourceTimestamp=ts)


def _absent(ts: datetime, status: int = ua.StatusCodes.BadNoData) -> ua.DataValue:
    """A value that is not there, with the reason attached.

    The Variant is untyped rather than a typed null: asyncua permits a null value only for
    Null, String, DateTime, ExtensionObject and ByteString variants, so a typed null Double is
    not constructible. That is fine and arguably right -- a Null variant is how OPC UA says
    "no value", and the variable's DataType attribute still tells a client what belongs here.
    """
    return ua.DataValue(Value=ua.Variant(None, V.Null), StatusCode_=ua.StatusCode(status),
                        SourceTimestamp=ts)


class Leaf:
    """A variable plus the VariantType it was created with.

    Carrying the type alongside the node keeps every write correctly typed without reading the
    DataType attribute first, and lets a whole result be written from a plain dict.
    """

    __slots__ = ("node", "vtype")

    def __init__(self, node, vtype: V) -> None:
        self.node = node
        self.vtype = vtype

    async def write(self, value, ts: datetime) -> None:
        await self.node.write_value(_good(value, self.vtype, ts))

    async def clear(self, ts: datetime, status: int = ua.StatusCodes.BadNoData) -> None:
        await self.node.write_value(_absent(ts, status))


class Branch:
    """One subtree of leaves that is written as a unit."""

    def __init__(self, node, leaves: dict[str, Leaf]) -> None:
        self.node = node
        self.leaves = leaves

    async def apply(self, values: dict[str, object], ts: datetime,
                    defer: tuple[str, ...] = ()) -> None:
        """Write the whole branch. Paths absent from `values` are cleared to Bad_NoData.

        This is what keeps absent-vs-zero correct by construction rather than by remembering:
        a module that did not run simply contributes no keys, and its leaves go Bad.
        Every leaf gets the same SourceTimestamp on purpose -- see the module docstring.

        `defer` paths are written last. HistoricalSampleResults uses that for SampleTime so
        an Ignition tag-change on that vendor field cannot fire before the rest of the result
        is on the wire.
        """
        deferred = set(defer)
        for path, leaf in self.leaves.items():
            if path in deferred:
                continue
            if path in values:
                await leaf.write(values[path], ts)
            else:
                await leaf.clear(ts)
        for path in defer:
            leaf = self.leaves.get(path)
            if leaf is None:
                continue
            if path in values:
                await leaf.write(values[path], ts)
            else:
                await leaf.clear(ts)

    async def clear_all(self, ts: datetime, status: int = ua.StatusCodes.BadNoData) -> None:
        for leaf in self.leaves.values():
            await leaf.clear(ts, status)


class _CommandHandler:
    """Server-side subscription handler for the writable command bits.

    The work is dispatched to a task rather than awaited here: this runs inside the
    subscription's own callback, and writing to a node from there re-enters the service that
    is currently delivering the notification.
    """

    def __init__(self, analyzer: "CellAnalyzer") -> None:
        self.analyzer = analyzer

    async def datachange_notification(self, node, value, data) -> None:
        if value:
            asyncio.create_task(self.analyzer.on_command(node))


def _nest(flat: dict[str, object]) -> dict:
    """{"Gas/pH/Result": 7.1} -> {"Gas": {"pH": {"Result": 7.1}}}, mirroring the address space."""
    out: dict = {}
    for path, value in flat.items():
        node = out
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _duration(seconds: float) -> str:
    """The vendor publishes UpTime and TimeInTray as free-text strings, not durations."""
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}d {hours:02d}:{minutes:02d}:{secs:02d}"


# ── the instrument ───────────────────────────────────────────────────────────────────────


class CellAnalyzer:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.rng = random.Random(cfg.seed or None)
        self.stopping = asyncio.Event()
        self.trigger = asyncio.Event()

        self.started_at = _now()
        self.sample_no = 0
        self.completed = 0
        self.qc_completed = 0
        self.cycle = 0
        self.tray_started_at = _now()

        self.pending_source = ""       # which command bit asked for the next analysis
        self.pending_command = "ESMScheduleAnalysis"
        self.pending_port = ""
        self.abort_requested = False
        self.inject_failure = False

        self.idx = 0
        self.server: Server | None = None
        self.command_leaves: dict[str, Leaf] = {}   # path -> Leaf, everything writable
        self.ext: dict[str, Leaf] = {}
        self.system: dict[str, Leaf] = {}
        self.sample_branch: Branch | None = None
        self.historical_branch: Branch | None = None
        self.qc_branch: Branch | None = None
        self.event_ok = None
        self.event_fail = None
        self.event_qc = None

    # ---- address space

    def _ident(self, path: str) -> str:
        """Table path -> vendor NodeId identifier, e.g. OPCSystemObjects->Gas->pH->Result."""
        return path.replace("/", self.cfg.separator)

    def _nid(self, path: str) -> ua.NodeId:
        return ua.NodeId(self._ident(path), self.idx)

    def _qname(self, name: str) -> ua.QualifiedName:
        return ua.QualifiedName(name, self.idx)

    async def _add_object(self, parent, path: str, name: str):
        return await parent.add_object(self._nid(path), self._qname(name))

    async def _add_var(self, parent, path: str, name: str, vtype: V, value=None,
                       datatype: ua.NodeId | None = None, writable: bool = False) -> Leaf:
        if value is None:
            value = DEFAULT_VALUE[vtype]
        node = await parent.add_variable(
            self._nid(path), self._qname(name), ua.Variant(value, vtype),
            varianttype=vtype, datatype=datatype,
        )
        if writable:
            await node.set_writable()
        return Leaf(node, vtype)

    async def _add_branch(self, parent, root_path: str, name: str,
                          fields: list[tuple[str, V]], writable: bool = False) -> Branch:
        """Build a subtree from a (relative path, variant type) table.

        Intermediate objects are created on first use, so the table alone decides the shape --
        which is what lets the table be diffed against the model doc, and the model doc against
        the vendor PDF, without anybody reading this function.
        """
        root = await self._add_object(parent, root_path, name)
        folders = {"": root}
        leaves: dict[str, Leaf] = {}
        for field_path, vtype in fields:
            parts = field_path.split("/")
            branch = ""
            node = root
            for part in parts[:-1]:
                branch = f"{branch}/{part}" if branch else part
                if branch not in folders:
                    folders[branch] = await self._add_object(
                        node, f"{root_path}/{branch}", part)
                node = folders[branch]
            leaves[field_path] = await self._add_var(
                node, f"{root_path}/{field_path}", parts[-1], vtype, writable=writable)
        return Branch(root, leaves)

    async def _add_event_type(self, node_id: int, name: str, fields: list[tuple[str, V]]):
        """An event type with a NodeId we chose.

        Server.create_custom_event_type() would be the one-liner, but it auto-assigns the
        NodeId, so the type can only be found by browsing Types/EventTypes. A client that wants
        to filter on one should be able to name it, so the type is assembled here instead --
        the same calls asyncua makes internally, with the identifier pinned.
        """
        base_type = self.server.get_node(ua.NodeId(ua.ObjectIds.BaseEventType))
        etype = await base_type.add_object_type(ua.NodeId(node_id, self.idx), self._qname(name))
        for field, vtype in fields:
            await etype.add_property(
                self._nid(f"{name}/{field}"), self._qname(field),
                ua.get_default_value(vtype), varianttype=vtype)
        return etype

    async def _build_system_objects(self, objects) -> None:
        """OPCSystemObjects -- everything the vendor publishes read-only."""
        cfg = self.cfg
        root = await self._add_object(objects, "OPCSystemObjects", "OPCSystemObjects")
        self.system_root = root

        # System information, section 9 p. 9-2.
        info = [
            ("ActiveTasks/Task", V.String, "Idle"),
            ("CoreHeartbeat/UpTime", V.String, _duration(0)),
            ("DateTime/DateTime", V.DateTime, _now()),
            ("SampleTypeNames/SampleTypeNames", V.String, ", ".join(cfg.sample_types)),
            ("SampleTypes/SampleTypes", V.String, ", ".join(cfg.sample_types)),
            ("ScheduledTasks/Task", V.String, ""),
            ("Settings/AnalyzerID", V.String, cfg.analyzer_id),
            ("Settings/Location", V.String, cfg.location),
            ("SoftwareVersion/SoftwareVersion", V.String, cfg.software_version),
            ("TimeSync/LastSync/LocalTimeZone", V.String, "America/Chicago"),
            ("TimeSync/LastSync/LocalTZOffset", V.String, "-05:00"),
        ]
        folders: dict[str, object] = {}
        for path, vtype, value in info:
            parts = path.split("/")
            node = root
            branch = ""
            for part in parts[:-1]:
                branch = f"{branch}/{part}" if branch else part
                if branch not in folders:
                    folders[branch] = await self._add_object(
                        node, f"OPCSystemObjects/{branch}", part)
                node = folders[branch]
            self.system[path] = await self._add_var(
                node, f"OPCSystemObjects/{path}", parts[-1], vtype, value=value)

        # Consumables. Installed dates and lots are fixed at startup; the fluid and sample
        # counters are decremented per analysis, which is what makes them worth subscribing to.
        self.consumables: dict[str, Branch] = {}
        for pack in PACKS:
            self.consumables[pack] = await self._add_branch(
                root, f"OPCSystemObjects/{pack}", pack, PACK_FIELDS)
        for card in CARDS:
            self.consumables[card] = await self._add_branch(
                root, f"OPCSystemObjects/{card}", card, CARD_FIELDS)

        self.modules_branch = await self._add_branch(
            root, "OPCSystemObjects/Modules", "Modules",
            [(f"InstalledUnits/{n}", V.String)
             for n in ("Autosampler", "CDV", "ESM", "Osmo", "RetainCollector")])

        self.osmo_state = await self._add_branch(
            root, "OPCSystemObjects/OsmoState", "OsmoState",
            [("CleanTubes", V.Int32), ("CalibrationStatus", V.String)])

        self.wells = await self._add_branch(
            root, "OPCSystemObjects/Resources", "Resources",
            [(f"Wells/{n}", V.String) for n in ("CDVWell", "ChemistryWell", "WasteWell")])

        # Per-sensor alert and warning flags. False means "no alert, sensor available".
        self.parameters = await self._add_branch(
            root, "OPCSystemObjects/Parameters", "Parameters",
            [(f"{p}/{f}", V.Boolean) for p in ALERT_PARAMS for f in ("Alert", "Warning")])

        cal_fields: list[tuple[str, V]] = []
        for prefix, params in CAL_BRANCHES:
            if params is None:
                cal_fields.append((f"{prefix}/CalibrationStatus", V.String))
            else:
                cal_fields += [(f"{prefix}/{p}/CalibrationStatus", V.String) for p in params]
        self.calibration = {}
        for prefix, params in CAL_BRANCHES:
            top = prefix.split("/")[0]
            sub = [(p.split("/", 1)[1], t) for p, t in cal_fields if p.startswith(top + "/")]
            self.calibration[top] = await self._add_branch(
                root, f"OPCSystemObjects/{top}", top, sub)

        self.param_config = await self._add_branch(
            root, "OPCSystemObjects/ParametersConfiguration", "ParametersConfiguration",
            [(f"{p}/Units", V.String) for p in UNIT_PARAMS])

        # The three result trees. Everything above is instrument state; these are the data.
        self.sample_branch = await self._add_branch(
            root, "OPCSystemObjects/SampleResults", "SampleResults", SAMPLE_RESULT_FIELDS)
        self.historical_branch = await self._add_branch(
            root, "OPCSystemObjects/HistoricalSampleResults", "HistoricalSampleResults",
            HISTORICAL_RESULT_FIELDS)
        self.qc_branch = await self._add_branch(
            root, "OPCSystemObjects/QCResults", "QCResults", QC_RESULT_FIELDS)

        self.automation_events = await self._add_branch(
            root, "OPCSystemObjects/AutomationEvents", "AutomationEvents",
            [(f"Automation/{n}", V.DateTime) for n in SYNC_EVENTS])

        autosampler: list[tuple[str, V]] = []
        for bank in ("A", "B"):
            autosampler += [(f"AutosamplerBank_{bank}/Initialized", V.Boolean),
                            (f"AutosamplerBank_{bank}/Status", V.String)]
        for bank in ("A", "B"):
            for slot in range(1, 6):
                autosampler += [(f"RSM_{bank}{slot}/{name}", vtype)
                                for name, vtype in RSM_FIELDS]
        self.autosampler = await self._add_branch(
            root, "OPCSystemObjects/AutosamplerStatus", "AutosamplerStatus", autosampler)

    async def _build_system_commands(self, objects) -> None:
        """OPCSystemCommands -- every writable tag, all Boolean triggers and their arguments.

        There is not a single OPC UA Method on this instrument. Everything is a bit you set.
        """
        fields: list[tuple[str, V]] = [(f"{name}/{name}", V.Boolean) for name in SIMPLE_COMMANDS]
        for name in PORTED_COMMANDS:
            fields += [(f"{name}/AutosamplerPort", V.String), (f"{name}/{name}", V.Boolean)]

        fields += [
            ("AutosamplerScheduleAnalysis/AutosamplerPort", V.String),
            ("AutosamplerScheduleAnalysis/AutosamplerScheduleAnalysis", V.Boolean),
            ("AutosamplerScheduleAnalysis/DueTime", V.DateTime),
            ("AutosamplerScheduleAnalysis/Operator", V.String),
            ("AutosamplerScheduleAnalysis/SampleType", V.String),
            ("AutosamplerScheduleAnalysis/RetainVolume", V.Double),
            ("AutosamplerScheduleAnalysis/NumberOfRetains", V.Double),
            ("AutosamplerScheduleAnalysis/FollowWithRetain", V.Boolean),
            ("ESMScheduleAnalysis/DueTime", V.DateTime),
            ("ESMScheduleAnalysis/ESMScheduleAnalysis", V.Boolean),
            ("ESMScheduleAnalysis/Operator", V.String),
            ("ESMScheduleAnalysis/SampleType", V.String),
            ("EXT_OLSScheduleAnalysis/DispenseTimeout", V.Int32),
            ("EXT_OLSScheduleAnalysis/DueTime", V.DateTime),
            ("EXT_OLSScheduleAnalysis/EXT_OLSScheduleAnalysis", V.Boolean),
            ("EXT_OLSScheduleAnalysis/Operator", V.String),
            ("EXT_OLSScheduleAnalysis/SampleType", V.String),
            ("SetSyncEvent/Event", V.String),
            ("SetSyncEvent/SetSyncEvent", V.Boolean),
        ]
        for scheduler in ("AutosamplerScheduleAnalysis", "ESMScheduleAnalysis",
                          "EXT_OLSScheduleAnalysis"):
            fields += [(f"{scheduler}/SampleInformation/{name}", vtype)
                       for name, vtype in COMMAND_SAMPLE_INFORMATION]

        branch = await self._add_branch(
            objects, "OPCSystemCommands", "OPCSystemCommands", fields, writable=True)
        self.commands = branch
        self.command_leaves = branch.leaves

        # Vendor defaults, stated in section 9: operator "Auto", sample type "Default",
        # sparging 20.9 %, vessel pressure 0 psi, vessel temperature 37 C, no predilution.
        ts = _now()
        for scheduler in ("AutosamplerScheduleAnalysis", "ESMScheduleAnalysis",
                          "EXT_OLSScheduleAnalysis"):
            await branch.leaves[f"{scheduler}/Operator"].write("Auto", ts)
            await branch.leaves[f"{scheduler}/SampleType"].write(self.cfg.sample_type, ts)
            info = f"{scheduler}/SampleInformation"
            await branch.leaves[f"{info}/SpargingO2"].write(20.9, ts)
            await branch.leaves[f"{info}/VesselPressure"].write(0.0, ts)
            await branch.leaves[f"{info}/VesselTemperature"].write(37.0, ts)
            await branch.leaves[f"{info}/PreDilutionMultiplier"].write(1.0, ts)
            await branch.leaves[f"{info}/CdvDilutionRatio"].write("1:1", ts)
            await branch.leaves[f"{info}/ChemistryDilutionRatio"].write("1:1", ts)
        await branch.leaves["EXT_OLSScheduleAnalysis/DispenseTimeout"].write(25, ts)
        await branch.leaves["AutosamplerCleanup/AutosamplerPort"].write("A1", ts)
        await branch.leaves["AutosamplerPrimePack/AutosamplerPort"].write("A1", ts)
        await branch.leaves["AutosamplerPrimeReactor/AutosamplerPort"].write("A1", ts)
        await branch.leaves["AutosamplerScheduleAnalysis/AutosamplerPort"].write("A1", ts)
        await branch.leaves["SetSyncEvent/Event"].write(SYNC_EVENTS[0], ts)

    async def _build_extensions(self, objects) -> None:
        """ICC26Extensions -- NOT part of the analyzer. Everything a counter-driven client needs.

        Kept in a separate top-level object with an unmistakable name so that nobody browsing
        this server, or reading a tag export taken from it, can mistake our additions for
        something the vendor ships. The vendor server has no counter, no state tag, no event and no
        whole-result document; all four live here.
        """
        root = await self._add_object(objects, "ICC26Extensions", "ICC26Extensions")

        await self._add_var(
            root, "ICC26Extensions/README", "README", V.String,
            value=("Not part of the cell analyzer. The vendor OPC server publishes no "
                   "completion counter, no state variable, no events and no whole-result "
                   "document; these nodes are added by the ICC-2026 demo simulator. "
                   "See docs/reference/novaflex2-opcua-model.md section 6."))

        self.ext["SampleCompleteCounter"] = await self._add_var(
            root, "ICC26Extensions/SampleCompleteCounter", "SampleCompleteCounter", V.UInt32)
        self.ext["QcCompleteCounter"] = await self._add_var(
            root, "ICC26Extensions/QcCompleteCounter", "QcCompleteCounter", V.UInt32)
        self.ext["State"] = await self._add_var(
            root, "ICC26Extensions/State", "State", V.Int32, value=UNIT_STATE["Idle"],
            datatype=self.state_type.nodeid)
        self.ext["ResultJson"] = await self._add_var(
            root, "ICC26Extensions/ResultJson", "ResultJson", V.String)
        self.ext["QcResultJson"] = await self._add_var(
            root, "ICC26Extensions/QcResultJson", "QcResultJson", V.String)
        self.ext["LastError"] = await self._add_var(
            root, "ICC26Extensions/LastError", "LastError", V.String)
        # Demo-only, and the analyzer has no equivalent button. Say so on stage.
        self.ext["InjectFailure"] = await self._add_var(
            root, "ICC26Extensions/InjectFailure", "InjectFailure", V.Boolean,
            value=False, writable=True)

    async def build(self, server: Server) -> None:
        self.server = server
        self.idx = await server.register_namespace(NAMESPACE_URI)
        objects = server.nodes.objects

        enum_base = server.get_node(ua.NodeId(ua.ObjectIds.Enumeration))
        self.state_type = await enum_base.add_data_type(
            self._nid("DataType/FunctionalUnitStateEnum"), self._qname("FunctionalUnitStateEnum"))
        await self.state_type.add_property(
            self._nid("DataType/FunctionalUnitStateEnum/EnumStrings"),
            ua.QualifiedName("EnumStrings", 0),
            [ua.LocalizedText(k) for k in sorted(UNIT_STATE, key=UNIT_STATE.get)],
            varianttype=V.LocalizedText)

        await self._build_system_objects(objects)
        await self._build_system_commands(objects)
        await self._build_extensions(objects)

        event_ok = await self._add_event_type(
            3000, "SampleCompletedEventType",
            [("SampleID", V.String), ("BatchID", V.String), ("VesselID", V.String),
             ("SampleSource", V.String), ("ResultJson", V.String)])
        event_fail = await self._add_event_type(
            3001, "SampleFailedEventType",
            [("SampleID", V.String), ("BatchID", V.String), ("VesselID", V.String),
             ("Reason", V.String)])
        event_qc = await self._add_event_type(
            3002, "QcCompletedEventType",
            [("Level", V.String), ("LotNumber", V.String), ("Passed", V.Boolean),
             ("ResultJson", V.String)])
        # Built now, once, before serving: creating a generator is what sets the emitting
        # node's EventNotifier bit, and a client that subscribes before the first sample has to
        # find that bit already set or its monitored item matches nothing.
        self.event_ok = await server.get_event_generator(event_ok, self.system_root)
        self.event_fail = await server.get_event_generator(event_fail, self.system_root)
        self.event_qc = await server.get_event_generator(event_qc, self.system_root)

        await self._seed_instrument_state()

        # Nothing has been analyzed yet, so the result trees are Bad_NoData rather than trees
        # of zeros. A client that cannot tell those apart finds out here, not on stage.
        ts = _now()
        await self.sample_branch.clear_all(ts)
        await self.historical_branch.clear_all(ts)
        await self.qc_branch.clear_all(ts)

    async def _seed_instrument_state(self) -> None:
        """Consumables, modules, wells and calibration -- the slowly-changing half."""
        cfg = self.cfg
        ts = _now()
        rng = self.rng

        for name in PACKS:
            installed = True
            if name == "ESMPackStatus":
                installed = cfg.esm_installed
            elif name.startswith("CDV"):
                installed = cfg.cdv_installed
            elif name.startswith("Chem"):
                installed = cfg.chem_installed
            elif name.startswith("Gas"):
                installed = cfg.gas_installed
            if not installed:
                # A pack that is not there reports nothing, not zeros. Installed=False is the
                # one honest fact available, so it is the one field written Good.
                await self.consumables[name].apply({"Installed": False}, ts)
                continue
            remaining = rng.randint(240, 480)
            await self.consumables[name].apply({
                "Empty": False,
                "ExpirationDate": ts + timedelta(days=rng.randint(40, 180)),
                "Expired": False,
                "FluidRemaining": rng.randint(55, 95),
                "InstallationDate": ts - timedelta(days=rng.randint(2, 25)),
                "Installed": True,
                "LotNumber": f"{rng.randint(100000, 999999)}",
                "SamplesRemaining": remaining,
                "SamplesRemainingPercent": min(100, int(remaining / 5)),
            }, ts)

        for name in CARDS:
            installed = cfg.chem_installed if name == "ChemCard" else cfg.gas_installed
            if not installed:
                await self.consumables[name].apply({"Installed": False}, ts)
                continue
            await self.consumables[name].apply({
                "ExpirationDate": ts + timedelta(days=rng.randint(20, 90)),
                "Expired": False,
                "Hydrated": True,
                "InstallationDate": ts - timedelta(days=rng.randint(1, 20)),
                "Installed": True,
                "LotNumber": f"{rng.randint(100000, 999999)}",
                "SamplesRemaining": rng.randint(300, 900),
            }, ts)

        # "Ready" is what the vendor documents this tag as displaying when the unit is present.
        # A unit that is not fitted contributes no key at all and reads Bad_NoData.
        units = {}
        if cfg.autosampler_installed:
            units["InstalledUnits/Autosampler"] = "Ready"
        if cfg.cdv_installed:
            units["InstalledUnits/CDV"] = "Ready"
        if cfg.esm_installed:
            units["InstalledUnits/ESM"] = "Ready"
        if cfg.osmo_installed:
            units["InstalledUnits/Osmo"] = "Ready"
        if cfg.retain_collector_installed:
            units["InstalledUnits/RetainCollector"] = "Ready"
        await self.modules_branch.apply(units, ts)

        if cfg.osmo_installed:
            await self.osmo_state.apply(
                {"CleanTubes": rng.randint(40, 90), "CalibrationStatus": "Calibrated"}, ts)
        else:
            await self.osmo_state.clear_all(ts)

        await self.wells.apply({f"Wells/{n}": "WellState.Clear"
                                for n in ("CDVWell", "ChemistryWell", "WasteWell")}, ts)

        # No alerts and no warnings on a healthy instrument. False is a real measured fact
        # here, unlike a 0.0 concentration, so these are written Good rather than left absent.
        alerts = {}
        for param in ALERT_PARAMS:
            live = self._param_installed(param)
            if live:
                alerts[f"{param}/Alert"] = False
                alerts[f"{param}/Warning"] = False
        await self.parameters.apply(alerts, ts)

        for prefix, params in CAL_BRANCHES:
            top = prefix.split("/")[0]
            rel = prefix.split("/", 1)[1]
            installed = {"DP_GasCal": cfg.gas_installed, "DP_ChemCal": cfg.chem_installed,
                         "DP_OsmoCal": cfg.osmo_installed, "DP_CdvCal": cfg.cdv_installed}[top]
            if not installed:
                await self.calibration[top].clear_all(ts)
                continue
            if params is None:
                await self.calibration[top].apply({f"{rel}/CalibrationStatus": "Calibrated"}, ts)
            else:
                await self.calibration[top].apply(
                    {f"{rel}/{p}/CalibrationStatus": "Calibrated" for p in params}, ts)

        await self.param_config.apply(
            {f"{p}/Units": UNITS[p] for p in UNIT_PARAMS if self._param_installed(p)}, ts)

        # Automation events have not happened yet. Bad_NoData, not 1970.
        await self.automation_events.clear_all(ts)

        autosampler: dict[str, object] = {}
        if cfg.autosampler_installed:
            banks = ["A"] + (["B"] if cfg.autosampler_bank_b else [])
            for bank in banks:
                autosampler[f"AutosamplerBank_{bank}/Initialized"] = True
                autosampler[f"AutosamplerBank_{bank}/Status"] = "Ready"
                for slot in range(1, 6):
                    rsm = f"RSM_{bank}{slot}"
                    autosampler.update({
                        f"{rsm}/ExpirationDate": ts + timedelta(days=rng.randint(30, 150)),
                        f"{rsm}/FluidRemaining": rng.randint(40, 100),
                        f"{rsm}/Initialized": True,
                        f"{rsm}/PackStatus": "Ready",
                        f"{rsm}/ReactorPrimed": True,
                        f"{rsm}/SampleLineStatus": "Ready",
                        f"{rsm}/Status": "Ready",
                    })
        await self.autosampler.apply(autosampler, ts)

    def _param_installed(self, param: str) -> bool:
        cfg = self.cfg
        if param in GAS_PARAMS:
            return cfg.gas_installed
        if param in CHEM_PARAMS:
            return cfg.chem_installed
        if param == "Osmo":
            return cfg.osmo_installed
        return cfg.cdv_installed   # CDV, Density, TotalDensity

    # ---- commands

    async def watch_commands(self) -> None:
        """Subscribe the server to every writable node it publishes.

        Called after the server is serving, not during build(): the subscription service is not
        running until then. One subscription covers all of OPCSystemCommands plus InjectFailure
        -- the handler sorts out which bit fired, exactly as the real instrument must.
        """
        self.command_sub = await self.server.create_subscription(200, _CommandHandler(self))
        self.by_node: dict[str, str] = {}
        for path, leaf in self.command_leaves.items():
            if leaf.vtype is not V.Boolean:
                continue     # argument tags are read at trigger time, not watched
            await self.command_sub.subscribe_data_change(leaf.node)
            self.by_node[leaf.node.nodeid.to_string()] = path
        inject = self.ext["InjectFailure"]
        await self.command_sub.subscribe_data_change(inject.node)
        self.by_node[inject.node.nodeid.to_string()] = "ICC26Extensions/InjectFailure"
        LOG.info("watching %s command bits", len(self.by_node))

    async def on_command(self, node) -> None:
        path = self.by_node.get(node.nodeid.to_string())
        if path is None:
            return

        # One-shot, cleared before the work starts, so the next run needs a fresh rising edge
        # and a client that never resets the bit still gets exactly one run per write. The
        # clear is itself a data change, which the handler ignores because the value is False.
        # Whether a real instrument does this is not documented -- see COMMAND_AUTO_CLEAR.
        if path == "ICC26Extensions/InjectFailure":
            await self.ext["InjectFailure"].write(False, _now())
            self.inject_failure = True
            self.trigger.set()
            LOG.info("InjectFailure armed -- next analysis fails without incrementing")
            return

        if self.cfg.command_auto_clear:
            await self.command_leaves[path].write(False, _now())

        name = path.split("/")[-1]
        if name in ("ESMScheduleAnalysis", "EXT_OLSScheduleAnalysis",
                    "AutosamplerScheduleAnalysis"):
            await self._schedule_analysis(name)
        elif name in ("ESMTerminate", "EXT_OLSTerminate", "AutosamplerTerminate"):
            await self._terminate(name)
        elif name in ("ChemistryQcLevel1", "ChemistryQcLevel2",
                      "GasQcLevel1", "GasQcLevel2"):
            await self._schedule_qc(name)
        elif name in ("ChemistryCalibration", "GasCalibration"):
            await self._run_maintenance(name, "Calibrating", 4.0)
        elif name == "SetSyncEvent":
            await self._set_sync_event()
        else:
            await self._run_maintenance(name, "Maintenance", 3.0)

    async def _schedule_analysis(self, command: str) -> None:
        state = await self.ext["State"].node.read_value()
        if state == UNIT_STATE["Running"]:
            # The vendor documents no rejection path -- there is no status code to return from
            # a tag write. All a client gets is that nothing happened. That asymmetry with the
            # Countess's Bad_InvalidState is the point of §6.1 of the Countess model doc.
            LOG.warning("%s ignored: an analysis is already running", command)
            return

        # The vendor contract: DueTime in the past or blank means run now. Anything else is a
        # scheduled analysis, which this simulator logs and then runs immediately anyway.
        due = await self.command_leaves[f"{command}/DueTime"].node.read_value()
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due and due > _now():
            LOG.info("%s scheduled for %s -- running now instead (simulator)", command,
                     due.isoformat())

        self.pending_source = {"ESMScheduleAnalysis": "ESM",
                               "EXT_OLSScheduleAnalysis": "EXT_OLS",
                               "AutosamplerScheduleAnalysis": "OLS"}[command]
        self.pending_command = command
        if command == "AutosamplerScheduleAnalysis":
            self.pending_port = await self.command_leaves[
                "AutosamplerScheduleAnalysis/AutosamplerPort"].node.read_value() or ""
        else:
            self.pending_port = ""
        self.abort_requested = False
        self.trigger.set()
        LOG.info("%s -> analysis %s (source %s)", command, self.sample_no + 1,
                 self.pending_source)

    async def _terminate(self, command: str) -> None:
        state = await self.ext["State"].node.read_value()
        if state != UNIT_STATE["Running"]:
            LOG.warning("%s ignored: nothing is running", command)
            return
        self.abort_requested = True
        LOG.info("%s requested", command)

    async def _schedule_qc(self, command: str) -> None:
        state = await self.ext["State"].node.read_value()
        if state == UNIT_STATE["Running"]:
            LOG.warning("%s ignored: an analysis is already running", command)
            return
        level = "Level 2" if command.endswith("2") else "Level 1"
        module = "Chemistry" if command.startswith("Chemistry") else "Gas"
        asyncio.create_task(self._run_qc(level, module))

    async def _run_maintenance(self, name: str, state: str, seconds: float) -> None:
        """Calibrations, cleaning and priming: state, ActiveTasks, a delay, back to Idle."""
        current = await self.ext["State"].node.read_value()
        if current == UNIT_STATE["Running"]:
            LOG.warning("%s ignored: an analysis is already running", name)
            return
        await self._set_state(state)
        await self.system["ActiveTasks/Task"].write(name, _now())
        LOG.info("%s running for %.0fs", name, seconds)
        try:
            await asyncio.wait_for(self.stopping.wait(), timeout=seconds)
            return
        except asyncio.TimeoutError:
            pass
        await self.system["ActiveTasks/Task"].write("Idle", _now())
        await self._set_state("Idle")
        LOG.info("%s complete", name)

    async def _set_sync_event(self) -> None:
        """SetSyncEvent stamps one of four automation-event tags with the current time."""
        name = await self.command_leaves["SetSyncEvent/Event"].node.read_value()
        if name not in SYNC_EVENTS:
            LOG.warning("SetSyncEvent ignored: %r is not one of %s", name, list(SYNC_EVENTS))
            return
        ts = _now()
        await self.automation_events.leaves[f"Automation/{name}"].write(ts, ts)
        LOG.info("SetSyncEvent %s", name)

    # ---- the culture

    def _culture(self) -> dict[str, float]:
        """A fed-batch CHO culture, sampled once per analysis.

        Not a kinetic model and not trying to be -- it exists so the trend charts move the way
        an audience expects: glucose consumed while lactate and ammonia accumulate, viable
        density peaking then falling as viability declines. Deterministic given RANDOM_SEED.
        """
        cfg = self.cfg
        rng = self.rng
        span = max(1, cfg.culture_span_samples)
        t = min(1.0, self.completed / span)

        def lerp(a: float, b: float, frac: float) -> float:
            return a + (b - a) * frac

        # Viable density rises to a peak at ~70 % of the run, then declines.
        peak_at = 0.7
        if t <= peak_at:
            density = lerp(cfg.density_start, cfg.density_peak, math.sin(t / peak_at * math.pi / 2))
        else:
            density = lerp(cfg.density_peak, cfg.density_peak * 0.55,
                           (t - peak_at) / (1 - peak_at))
        viability = lerp(cfg.viability_start, cfg.viability_end, t ** 2)

        return {
            "viable_density": max(0.05, density * rng.gauss(1.0, 0.03)),
            "viability": min(100.0, max(0.0, viability + rng.gauss(0.0, 0.5))),
            "Gluc": max(0.05, lerp(cfg.glucose_start, cfg.glucose_end, t) + rng.gauss(0, 0.08)),
            "Lac": max(0.0, lerp(cfg.lactate_start, cfg.lactate_end, t) + rng.gauss(0, 0.05)),
            "Gln": max(0.0, lerp(4.0, 0.4, t) + rng.gauss(0, 0.08)),
            "Glu": max(0.0, lerp(0.5, 2.4, t) + rng.gauss(0, 0.06)),
            "NH4": max(0.0, lerp(0.9, 5.8, t) + rng.gauss(0, 0.1)),
            "Na": lerp(145.0, 158.0, t) + rng.gauss(0, 0.8),
            "K": lerp(4.4, 6.1, t) + rng.gauss(0, 0.08),
            "Ca": 1.10 + rng.gauss(0, 0.03),
            "pH": lerp(7.16, 6.96, t) + rng.gauss(0, 0.015),
            "pCO2": lerp(45.0, 78.0, t) + rng.gauss(0, 1.5),
            "pO2": lerp(125.0, 62.0, t) + rng.gauss(0, 3.0),
            "Osmo": lerp(298.0, 382.0, t) + rng.gauss(0, 2.5),
            "diameter": lerp(16.6, 14.4, t) + rng.gauss(0, 0.15),
        }

    async def _snapshot_commands(self, command: str) -> dict[str, object]:
        """Read back every argument tag belonging to one scheduler command.

        Taken at the moment the analysis starts, which is the only defensible instant: the
        vendor contract is "write the arguments, then write the bit", and nothing about it is
        atomic. Two clients interleaving their metadata writes produce a spliced sample and
        neither is told -- a real property of the instrument, faithfully reproduced.
        """
        cache: dict[str, object] = {}
        for path, leaf in self.command_leaves.items():
            if path.startswith(f"{command}/"):
                cache[path] = await leaf.node.read_value()
        return cache

    def _ranges(self) -> dict[str, object]:
        """Measurement limits and correlation factors as configured for this analysis."""
        limits = {
            "pH": (6.60, 7.80), "pCO2": (5.0, 200.0), "pO2": (5.0, 400.0),
            "Na": (40.0, 200.0), "K": (0.2, 25.0), "Ca": (0.10, 5.00), "NH4": (0.02, 25.0),
            "Gln": (0.10, 12.0), "Glu": (0.05, 12.0), "Gluc": (0.05, 20.0),
            "Lac": (0.05, 20.0), "Osmo": (0.0, 2000.0), "TotalDensity": (0.05, 50.0),
        }
        values: dict[str, object] = {}
        for param in RANGE_PARAMS:
            if not self._param_installed(param):
                continue
            low, high = limits[param]
            values[f"StartTags/Ranges/{param}/LowerLimit"] = low
            values[f"StartTags/Ranges/{param}/UpperLimit"] = high
            # 0 and 1 are the vendor's documented "no correlation applied" values.
            values[f"StartTags/Ranges/{param}/OffsetIntercept"] = 0.0
            values[f"StartTags/Ranges/{param}/OffsetMultiplier"] = 1.0
        return values

    def _synthesize(self, ts: datetime, source: str, port: str,
                    info: dict[str, object]) -> tuple[dict[str, object], list[str]]:
        """One analysis, as the flat path -> value dict the result branches are written from.

        A path that is NOT in the returned dict is cleared to Bad_NoData by Branch.apply. That
        is how a missing osmometer, an unfitted chemistry card and an errored sensor all end up
        correctly absent rather than zero, without a single special case at write time.
        """
        cfg = self.cfg
        rng = self.rng
        culture = self._culture()
        errors: list[str] = []

        values: dict[str, object] = {
            "StartTags/SampleSource": source,
            "StartTags/DispenseVolume": 500,
            "StartTags/Operator": info.get("Operator") or "Auto",
            "StartTags/SampleType": info.get("SampleType") or cfg.sample_type,
            "StartTags/TrayLocation": rng.randint(1, 30),
            "StartTags/ModuleInformation/CellDensityDilutionRatio":
                info.get("CdvDilutionRatio") or "1:1",
            "StartTags/ModuleInformation/CellInspection": info.get("CellInspection") or "Standard",
            "StartTags/ModuleInformation/ChemistryDilutionRatio":
                info.get("ChemistryDilutionRatio") or "1:1",
            # The vendor's own absent-vs-zero mechanism: these say which modules took part.
            # False here is Good and true information; the corresponding Result is Bad_NoData.
            "StartTags/ModuleInformation/Modules/CDV": cfg.cdv_installed,
            "StartTags/ModuleInformation/Modules/Chemistry": cfg.chem_installed,
            "StartTags/ModuleInformation/Modules/Gas": cfg.gas_installed,
            "StartTags/ModuleInformation/Modules/Osmo": cfg.osmo_installed,
            "StartTags/SampleInformation/BatchID": info.get("BatchID") or "BR-2026-014",
            "StartTags/SampleInformation/CellType": info.get("CellType") or "CHO-K1",
            "StartTags/SampleInformation/PreDilutionMultiplier":
                float(info.get("PreDilutionMultiplier") or 1.0),
            "StartTags/SampleInformation/SampleID":
                info.get("SampleID") or f"S-{self.sample_no:05d}",
            "StartTags/SampleInformation/SpargingO2": float(info.get("SpargingO2") or 20.9),
            "StartTags/SampleInformation/VesselID": info.get("VesselID") or "BRX-2000-A",
            "StartTags/SampleInformation/VesselPressure": float(info.get("VesselPressure") or 0.0),
            "StartTags/SampleInformation/VesselTemperature":
                float(info.get("VesselTemperature") or 37.0),
            "ModifiedTime": ts,
            "SampleTime": ts,
            "TimeStamp": ts,
            "TimeInTray": _duration((ts - self.tray_started_at).total_seconds()),
        }
        if port:
            values["StartTags/AutosamplerPort"] = port
        values.update(self._ranges())

        temperature = values["StartTags/SampleInformation/VesselTemperature"]

        # Which analytes report at all, before error injection.
        readings: dict[str, float] = {}
        if cfg.gas_installed:
            readings.update({p: culture[p] for p in GAS_PARAMS})
        if cfg.chem_installed:
            readings.update({p: culture[p] for p in CHEM_PARAMS})
        if cfg.osmo_installed:
            readings["Osmo"] = culture["Osmo"]

        errored = set()
        for param in list(readings):
            if rng.random() < cfg.sensor_error_rate:
                errored.add(param)
                errors.append(f"{param}: sensor out of range")

        digits = {"pH": 2, "Ca": 2, "K": 2, "Gln": 2, "Glu": 2, "Gluc": 2, "Lac": 2, "NH4": 2}
        for param, value in readings.items():
            branch = "Gas" if param in GAS_PARAMS else "Chem" if param in CHEM_PARAMS else None
            prefix = f"{branch}/{param}" if branch else "Osmo"
            # Units stay Good even for an errored sensor: the unit of measure is a property of
            # the channel, not of this reading, and blanking it would lose real information.
            values[f"{prefix}/Units"] = UNITS[param]
            if param in errored:
                values[f"{prefix}/ErrorStatus"] = "Sensor error"
                continue
            values[f"{prefix}/ErrorStatus"] = ""
            values[f"{prefix}/Result"] = round(value, digits.get(param, 1))

        # Calculated results need pH and pCO2; if either errored there is nothing to derive.
        if cfg.gas_installed and not ({"pH", "pCO2"} & errored):
            ph = readings["pH"]
            pco2 = readings["pCO2"]
            # Henderson-Hasselbalch, the same relation a blood-gas analyzer uses.
            hco3 = 0.0307 * pco2 * (10 ** (ph - 6.105))
            values["CalculatedResults/HCO3"] = round(hco3, 1)
            values["CalculatedResults/HCO3Units"] = "mmol/L"
            # Temperature corrections. At the default 37 C these are identities, which makes
            # them easy to sanity-check on stage: change VesselTemperature and watch them move.
            values["CalculatedResults/pHCorrected"] = round(ph - 0.0147 * (temperature - 37.0), 2)
            values["CalculatedResults/pCO2Corrected"] = round(
                pco2 * (10 ** (0.019 * (temperature - 37.0))), 1)
            values["CalculatedResults/pCO2CorrectedUnits"] = "mmHg"
            # The manual does not define CO2Saturation. Reported here as the fraction of total
            # CO2 carried as bicarbonate; do not read clinical meaning into it. See the model
            # doc's defects table.
            total_co2 = hco3 + 0.0307 * pco2
            values["CalculatedResults/CO2Saturation"] = round(hco3 / total_co2 * 100.0, 1)
            values["CalculatedResults/CO2SaturationUnits"] = "%"
            if "pO2" not in errored:
                po2 = readings["pO2"]
                # Severinghaus.
                so2 = 100.0 / (23400.0 / (po2 ** 3 + 150.0 * po2) + 1.0)
                values["CalculatedResults/O2Saturation"] = round(so2, 1)
                values["CalculatedResults/O2SaturationUnits"] = "%"
                values["CalculatedResults/pO2Corrected"] = round(
                    po2 * (10 ** (0.0244 * (temperature - 37.0))), 1)
                values["CalculatedResults/pO2CorrectedUnits"] = "mmHg"

        if cfg.cdv_installed:
            viable = culture["viable_density"]
            viability = culture["viability"]
            total = viable / (viability / 100.0)
            images = rng.randint(8, 16)
            counted = int(total * rng.uniform(900, 1400))
            values.update({
                "CellDensity/AvgLiveDiameter": round(culture["diameter"], 1),
                "CellDensity/GoodImageCount": images,
                "CellDensity/LiveStdDeviation": round(viable * rng.uniform(0.02, 0.06), 3),
                "CellDensity/TotalCellCount": counted,
                "CellDensity/TotalDensity": round(total, 2),
                "CellDensity/TotalDensityUnits": UNITS["TotalDensity"],
                "CellDensity/TotalLiveCount": int(counted * viability / 100.0),
                "CellDensity/Viability": round(viability, 1),
                "CellDensity/ViableDensity": round(viable, 2),
                "CellDensity/ViableDensityUnits": UNITS["Density"],
                "CellDensity/FlowTimeData/FlowTime": round(rng.gauss(44.0, 2.0), 1),
            })
        if cfg.chem_installed:
            values["Chem/FlowTimeData/FlowTime"] = round(rng.gauss(58.0, 2.5), 1)
        if cfg.gas_installed:
            values["Gas/FlowTimeData/FlowTime"] = round(rng.gauss(31.0, 1.5), 1)

        values["Errors"] = "; ".join(errors)
        return values, errors

    # ---- the analysis

    async def _set_state(self, name: str) -> None:
        await self.ext["State"].write(UNIT_STATE[name], _now())

    async def _consume(self, samples: int = 1) -> None:
        """Decrement the consumables one analysis' worth, so the status tags actually move."""
        ts = _now()
        for name, branch in self.consumables.items():
            leaf = branch.leaves.get("SamplesRemaining")
            if leaf is None:
                continue
            try:
                current = await leaf.node.read_value()
            except Exception:
                continue
            if current is None:
                continue
            remaining = max(0, int(current) - samples)
            await leaf.write(remaining, ts)
            percent = branch.leaves.get("SamplesRemainingPercent")
            if percent is not None:
                await percent.write(min(100, remaining // 5), ts)
            empty = branch.leaves.get("Empty")
            if empty is not None:
                await empty.write(remaining == 0, ts)

    async def _run_sample(self) -> None:
        cfg = self.cfg
        self.sample_no += 1
        source = self.pending_source or "Manual"
        command = self.pending_command
        port = self.pending_port
        self.pending_source = ""
        self.pending_port = ""

        info_raw = await self._snapshot_commands(command)
        info = {name: info_raw.get(f"{command}/SampleInformation/{name}")
                for name, _ in COMMAND_SAMPLE_INFORMATION}
        info["Operator"] = info_raw.get(f"{command}/Operator")
        info["SampleType"] = info_raw.get(f"{command}/SampleType")

        await self._set_state("Running")
        await self.system["ActiveTasks/Task"].write(f"Sample analysis ({source})", _now())
        LOG.info("analysis %s (%s) running for %.1fs", self.sample_no, source, RUN_DURATION_S)
        try:
            # Waits on the stop event rather than sleeping, so SIGTERM still exits at once
            # mid-analysis instead of holding the container for the rest of the duration.
            await asyncio.wait_for(self.stopping.wait(), timeout=RUN_DURATION_S)
            return
        except asyncio.TimeoutError:
            pass

        if self.abort_requested:
            self.abort_requested = False
            await self.system["ActiveTasks/Task"].write("Idle", _now())
            await self._set_state("Aborted")
            LOG.info("analysis %s terminated -- counter not incremented", self.sample_no)
            return

        if self.inject_failure or self.rng.random() < cfg.failure_rate:
            self.inject_failure = False
            reason = "Dispense Timeout: sample not received within 25 minutes"
            await self.system["ActiveTasks/Task"].write("Idle", _now())
            await self.ext["LastError"].write(reason, _now())
            await self._set_state("Error")
            await self._raise_failed(info, reason)
            LOG.warning("analysis %s FAILED (%s) -- counter not incremented",
                        self.sample_no, reason)
            return

        ts = _now()
        values, errors = self._synthesize(ts, source, port, info)

        # Order matters: every result leaf, then SampleTime last on the historical tree.
        # Ignition publishes off HistoricalSampleResults/SampleTime (a vendor field), so that
        # node has to move after the rest of the result is settled. The live SampleResults
        # tree is not the trigger. ICC26Extensions/SampleCompleteCounter still increments
        # after State for the address-space demo; the MQTT path does not read it.
        await self.sample_branch.apply(values, ts)
        historical = dict(values)
        follow = bool(info_raw.get(f"{command}/FollowWithRetain")) if \
            f"{command}/FollowWithRetain" in info_raw else False
        if cfg.retain_collector_installed:
            historical["StartTags/FollowWithRetain"] = follow
            historical["StartTags/RetainVolume"] = float(
                info_raw.get(f"{command}/RetainVolume") or 0.0)
            historical["RetainCount"] = int(info_raw.get(f"{command}/NumberOfRetains") or 0)
        await self.historical_branch.apply(historical, ts, defer=("SampleTime",))

        await self._consume()
        await self._flag_alerts(errors, ts)
        await self.system["ActiveTasks/Task"].write("Idle", ts)
        await self.ext["ResultJson"].write(
            json.dumps(_nest(historical), separators=(",", ":")), ts)
        await self.ext["LastError"].write("; ".join(errors), ts)
        await self._set_state("Completed")
        self.completed += 1
        await self.ext["SampleCompleteCounter"].write(self.completed, ts)

        await self._raise_completed(info, source, historical)
        LOG.info("analysis %s complete: %s, %s -- counter=%s",
                 self.sample_no,
                 f"{values.get('CellDensity/ViableDensity', '--')} x10^6/mL viable"
                 if cfg.cdv_installed else "no CDV",
                 f"{len(errors)} sensor error(s)" if errors else "all sensors good",
                 self.completed)

    async def _flag_alerts(self, errors: list[str], ts: datetime) -> None:
        """Mirror this sample's sensor errors into Parameters/<x>/Alert."""
        failed = {e.split(":")[0] for e in errors}
        for param in ALERT_PARAMS:
            if not self._param_installed(param):
                continue
            await self.parameters.leaves[f"{param}/Alert"].write(param in failed, ts)

    async def _run_qc(self, level: str, module: str) -> None:
        """An onboard QC analysis.

        Writes QCResults and increments QcCompleteCounter. It does NOT touch SampleResults or
        SampleCompleteCounter -- which is the manual's own note at the top of section 9 made
        visible, and the reason a client that watches only SampleResults silently misses QC.
        """
        cfg = self.cfg
        rng = self.rng
        await self._set_state("QualityControl")
        await self.system["ActiveTasks/Task"].write(f"{module} QC {level}", _now())
        LOG.info("QC %s (%s) running for %.1fs", level, module, QC_DURATION_S)
        try:
            await asyncio.wait_for(self.stopping.wait(), timeout=QC_DURATION_S)
            return
        except asyncio.TimeoutError:
            pass

        ts = _now()
        # QC material sits at fixed target values, not on the culture trajectory -- that is the
        # whole point of a control. Level 2 is the high control.
        high = level.endswith("2")
        targets = {
            "pH": 7.60 if high else 7.10, "pCO2": 25.0 if high else 60.0,
            "pO2": 160.0 if high else 60.0, "Na": 160.0 if high else 130.0,
            "K": 7.0 if high else 3.0, "Ca": 1.60 if high else 0.80,
            "NH4": 8.0 if high else 1.5, "Gln": 8.0 if high else 1.5,
            "Glu": 6.0 if high else 1.0, "Gluc": 12.0 if high else 2.0,
            "Lac": 10.0 if high else 1.0, "Osmo": 400.0 if high else 260.0,
        }
        values: dict[str, object] = {
            "StartTags/ExpirationDate": ts + timedelta(days=rng.randint(30, 200)),
            "StartTags/Level": level,
            "StartTags/LotNumber": f"QC{rng.randint(10000, 99999)}",
            "StartTags/Operator": "Auto",
            "SampleTime": ts,
            "TimeStamp": ts,
        }
        limits = self._ranges()
        # QC ranges are the control's acceptance window, not the instrument's measuring range.
        for param in RANGE_PARAMS:
            if param == "TotalDensity" or param not in targets:
                continue
            if not self._param_installed(param):
                continue
            target = targets[param]
            values[f"StartTags/Ranges/{param}/LowerLimit"] = round(target * 0.90, 3)
            values[f"StartTags/Ranges/{param}/UpperLimit"] = round(target * 1.10, 3)
            values[f"StartTags/Ranges/{param}/OffsetIntercept"] = 0.0
            values[f"StartTags/Ranges/{param}/OffsetMultiplier"] = 1.0
        if self._param_installed("TotalDensity"):
            for key, val in limits.items():
                if "/TotalDensity/" in key:
                    values[key] = val

        passed = True
        digits = {"pH": 2, "Ca": 2, "K": 2, "Gln": 2, "Glu": 2, "Gluc": 2, "Lac": 2, "NH4": 2}
        for param, target in targets.items():
            if not self._param_installed(param):
                continue
            branch = "Gas" if param in GAS_PARAMS else "Chem" if param in CHEM_PARAMS else None
            prefix = f"{branch}/{param}" if branch else "Osmo"
            measured = target * rng.gauss(1.0, 0.025)
            values[f"{prefix}/Result"] = round(measured, digits.get(param, 1))
            values[f"{prefix}/Units"] = UNITS[param]
            within = abs(measured - target) <= target * 0.10
            values[f"{prefix}/ErrorStatus"] = "" if within else "Out of range"
            passed = passed and within

        if self.cfg.cdv_installed:
            density = 5.0 if high else 1.0
            values["CellDensity/TotalDensity"] = round(density * rng.gauss(1.0, 0.03), 2)
            values["CellDensity/GoodImageCount"] = rng.randint(8, 16)
            values["CellDensity/Units"] = UNITS["Density"]
            values["CellDensity/ErrorStatus"] = ""
        if self.cfg.chem_installed:
            values["Chem/FlowTimeData/FlowTime"] = round(rng.gauss(58.0, 2.5), 1)
        if self.cfg.gas_installed:
            values["Gas/FlowTimeData/FlowTime"] = round(rng.gauss(31.0, 1.5), 1)
        if self.cfg.cdv_installed:
            values["CellDensity/FlowTimeData/FlowTime"] = round(rng.gauss(44.0, 2.0), 1)
        values["Errors"] = "" if passed else "One or more analytes outside QC limits"

        await self.qc_branch.apply(values, ts)
        await self._consume()
        await self.system["ActiveTasks/Task"].write("Idle", ts)
        await self.ext["QcResultJson"].write(
            json.dumps(_nest(values), separators=(",", ":")), ts)
        await self._set_state("Completed")
        self.qc_completed += 1
        await self.ext["QcCompleteCounter"].write(self.qc_completed, ts)

        await self._raise_qc(level, str(values["StartTags/LotNumber"]), passed, values)
        LOG.info("QC %s complete: %s -- qc counter=%s, sample counter untouched (%s)",
                 level, "PASS" if passed else "FAIL", self.qc_completed, self.completed)

    async def _raise_completed(self, info: dict, source: str, values: dict) -> None:
        generator = self.event_ok
        generator.event.SampleID = str(values.get("StartTags/SampleInformation/SampleID", ""))
        generator.event.BatchID = str(values.get("StartTags/SampleInformation/BatchID", ""))
        generator.event.VesselID = str(values.get("StartTags/SampleInformation/VesselID", ""))
        generator.event.SampleSource = source
        generator.event.ResultJson = json.dumps(_nest(values), separators=(",", ":"))
        generator.event.Severity = 100
        generator.event.Message = ua.LocalizedText(
            f"Sample {generator.event.SampleID} complete")
        await generator.trigger()

    async def _raise_failed(self, info: dict, reason: str) -> None:
        generator = self.event_fail
        generator.event.SampleID = str(info.get("SampleID") or f"S-{self.sample_no:05d}")
        generator.event.BatchID = str(info.get("BatchID") or "")
        generator.event.VesselID = str(info.get("VesselID") or "")
        generator.event.Reason = reason
        generator.event.Severity = 500
        generator.event.Message = ua.LocalizedText(f"Analysis failed: {reason}")
        await generator.trigger()

    async def _raise_qc(self, level: str, lot: str, passed: bool, values: dict) -> None:
        generator = self.event_qc
        generator.event.Level = level
        generator.event.LotNumber = lot
        generator.event.Passed = passed
        generator.event.ResultJson = json.dumps(_nest(values), separators=(",", ":"))
        generator.event.Severity = 100 if passed else 500
        generator.event.Message = ua.LocalizedText(
            f"QC {level} {'passed' if passed else 'FAILED'}")
        await generator.trigger()

    # ---- lifecycle

    async def heartbeat(self) -> None:
        """CoreHeartbeat/UpTime and DateTime/DateTime.

        The vendor's own liveness pair -- section 6 tells you to subscribe to exactly these two
        to confirm the server is updating, so they have to actually tick or the manual's
        acceptance test fails against this simulator.
        """
        while not self.stopping.is_set():
            ts = _now()
            await self.system["CoreHeartbeat/UpTime"].write(
                _duration((ts - self.started_at).total_seconds()), ts)
            await self.system["DateTime/DateTime"].write(ts, ts)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                continue

    async def run(self) -> None:
        delay = self.cfg.first_sample_delay_s
        while not self.stopping.is_set():
            try:
                # timeout=None, not timeout=0: a zero timeout expires immediately
                # and spins this loop hot. None is what "only run when somebody
                # asks" has to mean -- wait for the trigger, indefinitely.
                await asyncio.wait_for(self.trigger.wait(),
                                       timeout=delay if delay > 0 else None)
            except asyncio.TimeoutError:
                pass
            self.trigger.clear()
            if self.stopping.is_set():
                return
            self.cycle += 1
            try:
                if (self.cfg.qc_every_n and not self.pending_source
                        and self.cycle % self.cfg.qc_every_n == 0):
                    # Free-running cycles occasionally run QC instead of a sample. An operator
                    # trigger (pending_source set) is always a sample -- nobody asks for a
                    # sample and gets a control.
                    await self._run_qc("Level 1", "Chemistry")
                else:
                    await self._run_sample()
            except Exception:
                LOG.exception("analysis failed unexpectedly")
                await self._set_state("Error")
            delay = self.cfg.sample_interval_s


async def main_async() -> int:
    cfg = Config()
    server = Server()
    await server.init()

    endpoint = f"opc.tcp://{cfg.bind_host}:{cfg.port}{cfg.endpoint_path}"
    server.set_endpoint(endpoint)
    server.set_server_name("ICC26 Cell Analyzer (simulated)")
    # Anonymous, unencrypted. The real instrument offers Basic256Sha256 Sign & Encrypt down to None
    # (manual §1.4.3.2) and a production integration should use the former; this is a demo
    # instrument on a private compose network, and a certificate exchange before the gateway
    # will browse is a twenty-minute detour nobody watching wants to sit through.
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    # asyncua binds to the endpoint's host but hands back endpoint descriptions carrying the
    # address the *client* asked on, so binding 0.0.0.0 does not tell Ignition to connect to
    # 0.0.0.0. Set explicitly rather than relying on the default staying true.
    if hasattr(server, "set_match_discovery_client_ip"):
        server.set_match_discovery_client_ip(True)

    analyzer = CellAnalyzer(cfg)
    await analyzer.build(server)

    loop = asyncio.get_running_loop()
    for signame in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, analyzer.stopping.set)
        except NotImplementedError:  # Windows, outside the container
            signal.signal(sig, lambda *_: analyzer.stopping.set())

    console = webui.Console(analyzer, loop)
    page_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")

    async with server:
        await analyzer.watch_commands()
        # After watch_commands(): the page's Run button writes the command bits,
        # and those do nothing until the server is subscribed to its own nodes.
        webui.serve(cfg.http_port, page_path, console)
        LOG.info("serving %s (namespace %s, ns=%s, separator %r)",
                 endpoint, NAMESPACE_URI, analyzer.idx, cfg.separator)
        LOG.info("modules: gas %s, chem %s, cdv %s, osmo %s -- %s sample fields, %s QC fields",
                 "yes" if cfg.gas_installed else "NO",
                 "yes" if cfg.chem_installed else "NO",
                 "yes" if cfg.cdv_installed else "NO",
                 "yes" if cfg.osmo_installed else "NO",
                 len(HISTORICAL_RESULT_FIELDS), len(QC_RESULT_FIELDS))
        tasks = [asyncio.create_task(analyzer.run()), asyncio.create_task(analyzer.heartbeat())]
        await analyzer.stopping.wait()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
    LOG.info("shutdown complete")
    return 0


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, _env("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    )
    # asyncua logs every session, subscription and publish request at INFO. Useful once, noise
    # forever after -- the demo's own log lines are the ones worth reading.
    logging.getLogger("asyncua").setLevel(
        getattr(logging, _env("ASYNCUA_LOG_LEVEL", "WARNING").upper(), logging.WARNING))
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
