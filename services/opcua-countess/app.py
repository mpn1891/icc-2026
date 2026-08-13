#!/usr/bin/env python3
"""A simulated Countess 3 FL automated cell counter, served over OPC UA.

Implements the information model in docs/reference/countess-3fl-opcua-model.md, which maps all
71 columns of Appendix E of the Thermo Fisher user guide (MAN0019567) into an address space.
Read that document first -- it carries the reasoning, the column mapping and the provenance
note. This file is its executable half, and README.md here lists where the two differ.

The real instrument has no OPC UA server; it writes CSV to USB, SMB or Thermo Fisher Connect.
Nothing here is a vendor artifact.

Four things are deliberate and easy to mistake for oversights:

  * CountCompletedCounter is written LAST, after every result node and after State. It is the
    only node a client has to subscribe to. Subscribing to the 71 leaves instead means guessing
    when a count is finished, and guessing wrong on the first value that happens to repeat.

  * Every leaf of a result carries the SAME SourceTimestamp -- the acquisition instant, not the
    write instant. That is what makes a set of reads provably one count rather than a plausible
    mixture of two.

  * A field with nothing to report is written null with StatusCode Bad_NoData, never 0.0. "We
    did not look through this cube" and "we looked and found no fluorescing cells" are different
    facts, and a viability trend built on the wrong one is wrong in a way nobody catches. The
    CSV cannot express the difference; OPC UA can, which is the argument for the whole model.

  * A failed count raises CountFailedEventType and does NOT increment the counter. A
    counter-driven client correctly sees nothing; a client polling LastResult on a timer
    republishes the previous count as though it were new. That contrast is the pattern-3 talk
    point, so the simulator can produce it on demand (InjectFailure).

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
from datetime import datetime, timezone

from asyncua import Server, ua

NAMESPACE_URI = "http://icc26.demo/UA/Countess3FL/"
LOG = logging.getLogger("countess")

# How long a count sits in Running before its result lands, and the only artificial delay in
# this server -- everything after it is ~100 ms, set by the command subscription's interval.
# The real instrument takes under 30 s (Appendix B, p. 85); 5 is a demo choice, long enough to
# watch State sit in Running and the counter lag the trigger, short enough not to narrate dead
# air. Fixed rather than a range so a rehearsal is predictable.
RUN_DURATION_S = 5.0


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
        # published port. asyncua rewrites the hostname in the endpoint descriptions it hands
        # back to whatever address the client asked on, so a client is never told to go to
        # 0.0.0.0 -- see set_match_discovery_client_ip in build().
        self.bind_host = _env("OPCUA_BIND_HOST", "0.0.0.0")
        self.port = _env_int("OPCUA_PORT", 4840)
        self.endpoint_path = _env("OPCUA_ENDPOINT_PATH", "/countess/")

        self.device_id = _env("DEVICE_ID", "Countess-01")
        self.serial_number = _env("SERIAL_NUMBER", "C3FL-2026-0417")
        self.software_revision = _env("SOFTWARE_REVISION", "1.4.212")
        self.device_revision = _env("DEVICE_REVISION", "3.0")

        # Cube 2 is empty by default. That keeps the absent-vs-zero case (model doc section 8)
        # live on stage instead of hypothetical -- Cube 2's result fields sit at Bad_NoData
        # while Cube 1's carry real numbers, in the same result, at the same timestamp.
        self.cube1_installed = _env_bool("CUBE1_INSTALLED", True)
        self.cube2_installed = _env_bool("CUBE2_INSTALLED", False)
        self.cube1_name = _env("CUBE1_NAME", "GFP")
        self.cube2_name = _env("CUBE2_NAME", "RFP")

        self.protocols = [p.strip() for p in _env(
            "PROTOCOLS", "CHO viability,HEK GFP transfection,PBMC viability"
        ).split(",") if p.strip()]
        self.protocol = _env("ACTIVE_PROTOCOL", self.protocols[0])

        self.first_count_delay_s = _env_float("FIRST_COUNT_DELAY_S", 15.0)
        self.count_interval_s = _env_float("COUNT_INTERVAL_S", 180.0)
        self.result_history = _env_int("RESULT_HISTORY", 10)

        # Every Nth count runs brightfield-only even though a cube is fitted, so the "cube
        # installed but not used" status (Bad_NoData on a node that had a value last count)
        # shows up without anyone touching the container. 0 disables.
        self.bf_only_every_n = _env_int("BF_ONLY_EVERY_N", 4)
        self.failure_rate = _env_float("FAILURE_RATE", 0.0)

        self.concentration_start = _env_float("CONCENTRATION_START", 1.2e6)
        self.viability_start = _env_float("VIABILITY_START", 96.0)
        self.viability_end = _env_float("VIABILITY_END", 88.0)
        self.viability_span_counts = _env_int("VIABILITY_SPAN_COUNTS", 40)

        self.seed = _env_int("RANDOM_SEED", 0)


# ── Appendix E, as a table ───────────────────────────────────────────────────────────────
#
# The single source of truth for the address space AND for every write. The letter in each row
# is the Appendix E column it came from, so this table can be diffed against section 7 of
# docs/reference/countess-3fl-opcua-model.md line for line.

V = ua.VariantType
UM, PCT, CONC = "um", "pct", "conc"  # engineering-unit keys, see EU_INFO

# Gate columns are listed per gate because Appendix E orders them differently for the
# brightfield gates (size -> brightness -> circularity) and the cube gates (brightness ->
# size -> circularity). Anything that reads the CSV positionally has to know that; anything
# reading this server does not, which is half the point of modelling it.
GATE_COLUMNS = {
    "LiveGate": dict(SizeMin="AI", SizeMax="AJ", BrightnessMin="AK",
                     BrightnessMax="AL", CircularityMin="AM", CircularityMax="AN"),
    "DeadGate": dict(SizeMin="AO", SizeMax="AP", BrightnessMin="AQ",
                     BrightnessMax="AR", CircularityMin="AS", CircularityMax="AT"),
    "BrightfieldGate": dict(SizeMin="AY", SizeMax="AZ", BrightnessMin="BA",
                            BrightnessMax="BB", CircularityMin="BC", CircularityMax="BD"),
    "Cube1Gate": dict(SizeMin="BG", SizeMax="BH", BrightnessMin="BE",
                      BrightnessMax="BF", CircularityMin="BI", CircularityMax="BJ"),
    "Cube2Gate": dict(SizeMin="BM", SizeMax="BN", BrightnessMin="BK",
                      BrightnessMax="BL", CircularityMin="BO", CircularityMax="BP"),
}

GATE_FIELD_ORDER = ["SizeMin", "SizeMax", "BrightnessMin", "BrightnessMax",
                    "CircularityMin", "CircularityMax"]


def _result_fields() -> list[tuple[str, V, str | None, str]]:
    """(browse path relative to a result object, variant type, EU key, Appendix E column)."""
    fields: list[tuple[str, V, str | None, str]] = [
        # identification -- A-E
        ("CountId", V.UInt32, None, "A"),
        ("SessionId", V.UInt32, None, "B"),
        ("SampleName", V.String, None, "C"),
        ("AcquisitionTime", V.DateTime, None, "D"),
        ("CountMode", V.Int32, None, "E"),
        # sample -- F-J
        ("Sample/Type", V.Int32, None, "F"),
        ("Sample/StainCorrectionApplied", V.Boolean, None, "G"),
        ("Sample/PreDilutionCorrectionApplied", V.Boolean, None, "H"),
        ("Sample/TotalConcentration", V.Double, CONC, "I"),
        ("Sample/TotalCellsCounted", V.UInt32, None, "J"),
        # brightfield / live-dead -- K-Q
        ("Brightfield/LiveConcentration", V.Double, CONC, "K"),
        ("Brightfield/LiveCellsCounted", V.UInt32, None, "L"),
        ("Brightfield/DeadConcentration", V.Double, CONC, "M"),
        ("Brightfield/DeadCellsCounted", V.UInt32, None, "N"),
        ("Brightfield/ViabilityPercent", V.Double, PCT, "O"),
        ("Brightfield/LiveAverageSize", V.Double, UM, "P"),
        ("Brightfield/DeadAverageSize", V.Double, UM, "Q"),
        # fluorescence -- R-AD. Regrouped per cube; the CSV scatters AC/AD away from the
        # rest of each cube's fields because the format grew by appending.
        ("Fluorescence/Cube1/CubeName", V.String, None, "R"),
        ("Fluorescence/Cube1/Concentration", V.Double, CONC, "S"),
        ("Fluorescence/Cube1/PercentOfBrightfield", V.Double, PCT, "T"),
        ("Fluorescence/Cube1/CellsCounted", V.UInt32, None, "U"),
        ("Fluorescence/Cube1/AverageSize", V.Double, UM, "AC"),
        ("Fluorescence/Cube2/CubeName", V.String, None, "V"),
        ("Fluorescence/Cube2/Concentration", V.Double, CONC, "W"),
        ("Fluorescence/Cube2/PercentOfBrightfield", V.Double, PCT, "X"),
        ("Fluorescence/Cube2/CellsCounted", V.UInt32, None, "Y"),
        ("Fluorescence/Cube2/AverageSize", V.Double, UM, "AD"),
        ("Fluorescence/Combined/Concentration", V.Double, CONC, "Z"),
        ("Fluorescence/Combined/PercentOfBrightfield", V.Double, PCT, "AA"),
        ("Fluorescence/Combined/CellsCounted", V.UInt32, None, "AB"),
        # trailer -- BQ, BR, BS
        ("AggregationPercent", V.Double, PCT, "BS"),
        ("Protocol/ProtocolName", V.String, None, "BQ"),
        ("Protocol/SoftwareRevision", V.String, None, "BR"),
        # as-run settings -- AE-AH, AU-AX
        ("Settings/FocusValue", V.Int32, None, "AE"),
        ("Settings/FocusMotorValue", V.Int32, None, "AF"),
        ("Settings/Illumination/BrightfieldLightIntensity", V.Double, PCT, "AG"),
        ("Settings/Illumination/BrightfieldLedIntensity", V.Double, None, "AH"),
        ("Settings/Illumination/Cube1LightIntensity", V.Double, PCT, "AU"),
        ("Settings/Illumination/Cube1LedIntensity", V.Double, None, "AV"),
        ("Settings/Illumination/Cube2LightIntensity", V.Double, PCT, "AW"),
        ("Settings/Illumination/Cube2LedIntensity", V.Double, None, "AX"),
    ]
    # as-run gates -- AI-AT, AY-BP
    for gate, columns in GATE_COLUMNS.items():
        for field in GATE_FIELD_ORDER:
            # Brightness and circularity are UI slider values. Appendix E states no range and
            # no unit for them, so they get neither here. Do not invent 0-100 or 0-1.
            eu = UM if field.startswith("Size") else None
            fields.append((f"Settings/{gate}/{field}", V.Double, eu, columns[field]))
    return fields


RESULT_FIELDS = _result_fields()

# Everything under here is absent, not zero, on a brightfield-only count.
FL_PREFIX = "Fluorescence/"

DEFAULT_VALUE = {
    V.UInt32: 0, V.Int32: 0, V.Double: 0.0, V.Boolean: False, V.String: "",
    V.DateTime: datetime(1970, 1, 1, tzinfo=timezone.utc),
}

# Appendix B, p. 85: concentration 1e4-1e7 cells/mL, particle diameter 4-60 um.
EU_INFO = {
    UM: (ua.EUInformation(
        NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact",
        UnitId=13384,  # UNECE common code "4H", read as ASCII
        DisplayName=ua.LocalizedText("µm"),
        Description=ua.LocalizedText("micrometre"),
    ), ua.Range(Low=4.0, High=60.0)),
    PCT: (ua.EUInformation(
        NamespaceUri="http://www.opcfoundation.org/UA/units/un/cefact",
        UnitId=20529,  # UNECE common code "P1"
        DisplayName=ua.LocalizedText("%"),
        Description=ua.LocalizedText("percent"),
    ), ua.Range(Low=0.0, High=100.0)),
    # No UNECE common code exists for cells per millilitre, so it takes a locally assigned
    # unitId under this model's own namespace. That is the mechanism the spec provides for
    # non-UNECE units, and it is more correct than borrowing an unrelated code.
    CONC: (ua.EUInformation(
        NamespaceUri=NAMESPACE_URI,
        UnitId=1,
        DisplayName=ua.LocalizedText("cells/mL"),
        Description=ua.LocalizedText("cells per millilitre"),
    ), ua.Range(Low=1.0e4, High=1.0e7)),
}

COUNT_MODE = {"BrightfieldBased": 0, "FluorescenceBased": 1}
SAMPLE_TYPE = {"Brightfield": 0, "Fluorescence": 1}
UNIT_STATE = {"Idle": 0, "Running": 1, "Completed": 2, "Aborted": 3, "Error": 4}


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
    "no value", and the variable's DataType attribute still tells a client what would be here.
    """
    return ua.DataValue(Value=ua.Variant(None, V.Null), StatusCode_=ua.StatusCode(status),
                        SourceTimestamp=ts)


class Leaf:
    """A variable plus the VariantType it was created with.

    Carrying the type alongside the node keeps every write correctly typed without a read of
    the DataType attribute first, and lets a whole result be written from a plain dict.
    """

    __slots__ = ("node", "vtype")

    def __init__(self, node, vtype: V) -> None:
        self.node = node
        self.vtype = vtype

    async def write(self, value, ts: datetime) -> None:
        await self.node.write_value(_good(value, self.vtype, ts))

    async def clear(self, ts: datetime, status: int = ua.StatusCodes.BadNoData) -> None:
        await self.node.write_value(_absent(ts, status))


class ResultObject:
    """One CountResultType instance: the Appendix E row as an object tree."""

    def __init__(self, node, leaves: dict[str, Leaf], json_node) -> None:
        self.node = node
        self.leaves = leaves
        self.json_node = json_node

    async def apply(self, values: dict[str, object], ts: datetime) -> None:
        """Write a whole count. Paths absent from `values` are cleared to Bad_NoData.

        Every leaf gets the same SourceTimestamp on purpose -- see the module docstring.
        """
        for path, leaf in self.leaves.items():
            if path in values:
                await leaf.write(values[path], ts)
            else:
                await leaf.clear(ts)

        document = json.dumps(_nest(values), separators=(",", ":"))
        await self.json_node.write_value(_good(document, V.String, ts))

    async def clear_all(self, ts: datetime, status: int = ua.StatusCodes.BadNoData) -> None:
        for leaf in self.leaves.values():
            await leaf.clear(ts, status)
        await self.json_node.write_value(_absent(ts, status))


class _CommandHandler:
    """Server-side subscription handler for the command bit.

    The work is dispatched to a task rather than awaited here: this runs inside the
    subscription's own callback, and writing to a node from there re-enters the service that
    is currently delivering the notification.
    """

    def __init__(self, countess: "Countess") -> None:
        self.countess = countess

    async def datachange_notification(self, node, value, data) -> None:
        if value:
            asyncio.create_task(self.countess.on_start_request())


def _nest(flat: dict[str, object]) -> dict:
    """{"Sample/Type": 1} -> {"Sample": {"Type": 1}}, mirroring the address space exactly."""
    out: dict = {}
    for path, value in flat.items():
        node = out
        parts = path.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value.isoformat() if isinstance(value, datetime) else value
    return out


# ── the instrument ───────────────────────────────────────────────────────────────────────


class Countess:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.rng = random.Random(cfg.seed or None)
        self.stopping = asyncio.Event()
        self.trigger = asyncio.Event()  # StartCount, or the free-running timer

        self.session_id = self.rng.randint(1, 999)
        self.count_id = 0
        self.completed = 0
        self.concentration = cfg.concentration_start
        self.pending_sample = ""
        self.abort_requested = False
        self.inject_failure = False

        self.idx = 0
        self.server: Server | None = None
        self.results: ResultObject | None = None
        self.result_ring: list = []
        self.channels: dict[str, dict[str, object]] = {}
        self.unit_nodes: dict[str, object] = {}
        self.event_ok = None
        self.event_fail = None

    # ---- address space

    def _nid(self, path: str) -> ua.NodeId:
        return ua.NodeId(path, self.idx)

    def _qname(self, name: str) -> ua.QualifiedName:
        return ua.QualifiedName(name, self.idx)

    async def _add_object(self, parent, path: str, name: str):
        return await parent.add_object(self._nid(path), self._qname(name))

    async def _add_var(self, parent, path: str, name: str, vtype: V, value=None,
                       eu: str | None = None, datatype: ua.NodeId | None = None,
                       writable: bool = False) -> Leaf:
        if value is None:
            value = DEFAULT_VALUE[vtype]
        node = await parent.add_variable(
            self._nid(path), self._qname(name), ua.Variant(value, vtype),
            varianttype=vtype, datatype=datatype,
        )
        if eu:
            info, rng = EU_INFO[eu]
            # AnalogItemType defines these as properties; the type definition itself is not
            # applied here (asyncua's add_variable takes no typedefinition and Ignition does
            # not need one to read them). See README "Deviations".
            await node.add_property(self._nid(path + ".EngineeringUnits"),
                                    ua.QualifiedName("EngineeringUnits", 0), info)
            await node.add_property(self._nid(path + ".EURange"),
                                    ua.QualifiedName("EURange", 0), rng)
        if writable:
            await node.set_writable()
        return Leaf(node, vtype)

    async def _add_event_type(self, node_id: int, name: str, fields: list[tuple[str, V]]):
        """An event type with a NodeId we chose.

        Server.create_custom_event_type() would be the one-liner, but it auto-assigns the
        NodeId, so the type can only be found by browsing Types/EventTypes. The model doc
        allocates i=3000+ for event types and a client that wants to filter on one should be
        able to name it, so the type is assembled here instead -- same three calls asyncua
        makes internally, with the identifier pinned.
        """
        base_type = self.server.get_node(ua.NodeId(ua.ObjectIds.BaseEventType))
        etype = await base_type.add_object_type(ua.NodeId(node_id, self.idx), self._qname(name))
        for field, vtype in fields:
            await etype.add_property(
                self._nid(f"{name}/{field}"), self._qname(field),
                ua.get_default_value(vtype), varianttype=vtype)
        return etype

    async def _add_enum_type(self, base, name: str, members: dict[str, int]):
        dt = await base.add_data_type(self._nid("DataType/" + name), self._qname(name))
        await dt.add_property(
            self._nid("DataType/" + name + "/EnumStrings"),
            ua.QualifiedName("EnumStrings", 0),
            [ua.LocalizedText(k) for k in sorted(members, key=members.get)],
            varianttype=V.LocalizedText,
        )
        return dt

    async def _add_result_object(self, parent, path: str, name: str) -> ResultObject:
        """Build a CountResultType instance from RESULT_FIELDS."""
        root = await self._add_object(parent, path, name)
        folders = {"": root}
        leaves: dict[str, Leaf] = {}

        for field_path, vtype, eu, _column in RESULT_FIELDS:
            parts = field_path.split("/")
            branch = ""
            node = root
            for part in parts[:-1]:
                branch = f"{branch}/{part}" if branch else part
                if branch not in folders:
                    folders[branch] = await self._add_object(
                        node, f"{path}/{branch}", part)
                node = folders[branch]
            datatype = None
            if field_path == "CountMode":
                datatype = self.count_mode_type.nodeid
            elif field_path == "Sample/Type":
                datatype = self.sample_type_type.nodeid
            leaves[field_path] = await self._add_var(
                node, f"{path}/{field_path}", parts[-1], vtype, eu=eu, datatype=datatype)

        # The model doc specifies this as CountResultDataType, a custom structure. It is a
        # JSON String here: Ignition cannot decode a custom structure into tags, and asyncua's
        # structure support wants a generated type dictionary that neither side would read.
        # The property that actually matters survives -- one read, one timestamp, no tearing
        # across 71 independently written leaves. See README "Deviations".
        json_node = await root.add_variable(
            self._nid(f"{path}/ResultJson"), self._qname("ResultJson"),
            ua.Variant("", V.String), varianttype=V.String,
        )
        return ResultObject(root, leaves, json_node)

    async def _add_channel(self, parent, path: str, name: str, cube_slot: int | None,
                           cube_name: str, installed: bool) -> dict:
        """A live optics channel: current settings, writable, read at acquisition time."""
        obj = await self._add_object(parent, path, name)
        channel: dict[str, object] = {}
        channel["LightIntensity"] = await self._add_var(
            obj, f"{path}/LightIntensity", "LightIntensity", V.Double,
            value=55.0 if cube_slot is None else 70.0, eu=PCT, writable=True)
        channel["LedIntensity"] = await self._add_var(
            obj, f"{path}/LedIntensity", "LedIntensity", V.Double,
            value=40.0, writable=True)

        if cube_slot is not None:
            channel["CubeName"] = await self._add_var(
                obj, f"{path}/CubeName", "CubeName", V.String, value=cube_name, writable=True)
            channel["SlotPosition"] = await self._add_var(
                obj, f"{path}/SlotPosition", "SlotPosition", V.Byte, value=cube_slot)
            channel["Installed"] = await self._add_var(
                obj, f"{path}/Installed", "Installed", V.Boolean, value=installed, writable=True)

        gate = await self._add_object(obj, f"{path}/Gate", "Gate")
        defaults = {"SizeMin": 6.0, "SizeMax": 40.0, "BrightnessMin": 20.0,
                    "BrightnessMax": 80.0, "CircularityMin": 0.6, "CircularityMax": 1.0}
        for field in GATE_FIELD_ORDER:
            channel["Gate/" + field] = await self._add_var(
                gate, f"{path}/Gate/{field}", field, V.Double,
                value=defaults[field], eu=UM if field.startswith("Size") else None,
                writable=True)
        return channel

    async def build(self, server: Server) -> None:
        self.server = server
        self.idx = await server.register_namespace(NAMESPACE_URI)
        cfg = self.cfg
        objects = server.nodes.objects

        enum_base = server.get_node(ua.NodeId(ua.ObjectIds.Enumeration))
        self.count_mode_type = await self._add_enum_type(enum_base, "CountModeEnum", COUNT_MODE)
        self.sample_type_type = await self._add_enum_type(enum_base, "SampleTypeEnum", SAMPLE_TYPE)
        self.unit_state_type = await self._add_enum_type(
            enum_base, "FunctionalUnitStateEnum", UNIT_STATE)

        # DeviceSet / Identification follow the DI (OPC 10000-100) browse names but live in
        # this namespace: loading Opc.Ua.Di.NodeSet2.xml would make them genuine ns=DI nodes
        # and is a one-line import_xml if it ever matters. See README "Deviations".
        device_set = await self._add_object(objects, "DeviceSet", "DeviceSet")
        device = await self._add_object(device_set, cfg.device_id, cfg.device_id)

        ident = await self._add_object(device, f"{cfg.device_id}/Identification", "Identification")
        for name, value in [
            ("Manufacturer", "Thermo Fisher Scientific"), ("Model", "Countess 3 FL"),
            ("SerialNumber", cfg.serial_number), ("SoftwareRevision", cfg.software_revision),
            ("DeviceRevision", cfg.device_revision),
        ]:
            await self._add_var(ident, f"{cfg.device_id}/Identification/{name}", name,
                                V.String, value=value)

        fu_set = await self._add_object(device, f"{cfg.device_id}/FunctionalUnitSet",
                                        "FunctionalUnitSet")
        unit_path = f"{cfg.device_id}/CellCounter"
        unit = await self._add_object(fu_set, unit_path, "CellCounter")
        self.unit = unit

        fn_set = await self._add_object(unit, f"{unit_path}/FunctionSet", "FunctionSet")
        self.channels["Brightfield"] = await self._add_channel(
            fn_set, f"{unit_path}/FunctionSet/Brightfield", "Brightfield", None, "", True)
        self.channels["Cube1"] = await self._add_channel(
            fn_set, f"{unit_path}/FunctionSet/Cube1", "Cube1", 1, cfg.cube1_name,
            cfg.cube1_installed)
        self.channels["Cube2"] = await self._add_channel(
            fn_set, f"{unit_path}/FunctionSet/Cube2", "Cube2", 2, cfg.cube2_name,
            cfg.cube2_installed)

        pm = await self._add_object(unit, f"{unit_path}/ProgramManager", "ProgramManager")
        active = await self._add_object(pm, f"{unit_path}/ProgramManager/ActiveProgram",
                                        "ActiveProgram")
        self.unit_nodes["ProtocolName"] = await self._add_var(
            active, f"{unit_path}/ProgramManager/ActiveProgram/ProtocolName", "ProtocolName",
            V.String, value=cfg.protocol, writable=True)
        protocol_set = await self._add_object(
            pm, f"{unit_path}/ProgramManager/ProtocolSet", "ProtocolSet")
        for protocol in cfg.protocols:
            slug = protocol.replace(" ", "-")
            obj = await self._add_object(
                protocol_set, f"{unit_path}/ProgramManager/ProtocolSet/{slug}", protocol)
            await self._add_var(
                obj, f"{unit_path}/ProgramManager/ProtocolSet/{slug}/ProtocolName",
                "ProtocolName", V.String, value=protocol)

        self.result_set = await self._add_object(unit, f"{unit_path}/ResultSet", "ResultSet")
        self.results = await self._add_result_object(
            self.result_set, f"{unit_path}/ResultSet/LastResult", "LastResult")

        self.unit_nodes["CountCompletedCounter"] = await self._add_var(
            unit, f"{unit_path}/CountCompletedCounter", "CountCompletedCounter", V.UInt32)
        self.unit_nodes["SessionId"] = await self._add_var(
            unit, f"{unit_path}/SessionId", "SessionId", V.UInt32, value=self.session_id)
        self.unit_nodes["State"] = await self._add_var(
            unit, f"{unit_path}/State", "State", V.Int32, value=UNIT_STATE["Idle"],
            datatype=self.unit_state_type.nodeid)

        # A command *variable* beside the StartCount *method*, on purpose. A method is the
        # OPC UA-native way to ask for something and the only one that can return a CountId or
        # a Bad_InvalidState. But a SCADA tag cannot call a method -- it can only write a
        # value -- so every HMI in the building would need a script to trigger a count. The
        # boolean is what a tag can actually drive. Both exist here, and the difference is
        # worth saying out loud: the method is better engineering, the bit is what ships.
        command = await self._add_object(unit, f"{unit_path}/Command", "Command")
        self.unit_nodes["StartRequest"] = await self._add_var(
            command, f"{unit_path}/Command/StartRequest", "StartRequest", V.Boolean,
            value=False, writable=True)
        self.unit_nodes["CommandSampleName"] = await self._add_var(
            command, f"{unit_path}/Command/SampleName", "SampleName", V.String,
            value="", writable=True)

        await device.add_method(
            self._nid(f"{cfg.device_id}/StartCount"), self._qname("StartCount"),
            self._on_start_count, [V.String, V.String], [V.UInt32])
        await device.add_method(
            self._nid(f"{cfg.device_id}/AbortCount"), self._qname("AbortCount"),
            self._on_abort_count, [], [])
        # Not on the real instrument, and not in the model doc's method list either -- it
        # exists so the failed-count contract can be demonstrated on cue. Say so on stage.
        await device.add_method(
            self._nid(f"{cfg.device_id}/InjectFailure"), self._qname("InjectFailure"),
            self._on_inject_failure, [], [])

        event_ok = await self._add_event_type(
            3000, "CountCompletedEventType",
            [("CountId", V.UInt32), ("SessionId", V.UInt32), ("SampleName", V.String),
             ("CountMode", V.Int32), ("ResultJson", V.String)])
        event_fail = await self._add_event_type(
            3001, "CountFailedEventType",
            [("CountId", V.UInt32), ("SessionId", V.UInt32), ("SampleName", V.String),
             ("Reason", V.String)])
        # Built now, not at trigger time: creating a generator is what sets the emitting node's
        # EventNotifier bit, and a client that subscribes before the first count has to find
        # that bit already set or its monitored item matches nothing.
        self.event_ok = await server.get_event_generator(event_ok, self.unit)
        self.event_fail = await server.get_event_generator(event_fail, self.unit)

        # Nothing has been counted yet, so LastResult is Bad_NoData rather than a tree of
        # zeros. A client that cannot tell those apart finds out here, not on stage.
        await self.results.clear_all(_now())

    # ---- command variable

    async def watch_commands(self) -> None:
        """Subscribe the server to its own command bit.

        Called after the server is serving, not during build(): the subscription service is
        not running until then.
        """
        self.command_sub = await self.server.create_subscription(200, _CommandHandler(self))
        await self.command_sub.subscribe_data_change(self.unit_nodes["StartRequest"].node)
        LOG.info("watching %s/Command/StartRequest", self.cfg.device_id)

    async def on_start_request(self) -> None:
        # One-shot. Cleared before anything else, so the next count needs a fresh rising edge
        # and a client that never resets the bit still gets exactly one count per write. The
        # clear is itself a data change, which the handler ignores because the value is False.
        await self.unit_nodes["StartRequest"].write(False, _now())

        state = await self.unit_nodes["State"].node.read_value()
        if state == UNIT_STATE["Running"]:
            LOG.warning("start request ignored: a count is already running")
            return

        name = await self.unit_nodes["CommandSampleName"].node.read_value()
        self.pending_sample = (name or "").strip()
        self.abort_requested = False
        self.trigger.set()
        LOG.info("start request -> count %s (%s)", self.count_id + 1,
                 self.pending_sample or "auto-named")

    # ---- methods

    def _variant_value(self, value):
        return value.Value if isinstance(value, ua.Variant) else value

    # A method that RAISES UaStatusCodeError does not return that status to the caller:
    # asyncua's MethodService catches every exception and answers BadUnexpectedError, losing
    # the reason. Returning a StatusCode is the path it honours, so "the slide is still being
    # read" reaches the client as Bad_InvalidState instead of a shrug.
    async def _on_start_count(self, parent, sample_name, protocol_name):
        state = await self.unit_nodes["State"].node.read_value()
        if state == UNIT_STATE["Running"]:
            return ua.StatusCode(ua.StatusCodes.BadInvalidState)

        protocol = (self._variant_value(protocol_name) or "").strip()
        if protocol and protocol not in self.cfg.protocols:
            return ua.StatusCode(ua.StatusCodes.BadNotFound)
        if protocol:
            await self.unit_nodes["ProtocolName"].write(protocol, _now())

        self.pending_sample = (self._variant_value(sample_name) or "").strip()
        self.abort_requested = False
        self.trigger.set()
        LOG.info("StartCount(%r, %r) -> count %s", self.pending_sample, protocol,
                 self.count_id + 1)
        return [ua.Variant(self.count_id + 1, V.UInt32)]

    async def _on_abort_count(self, parent):
        state = await self.unit_nodes["State"].node.read_value()
        if state != UNIT_STATE["Running"]:
            return ua.StatusCode(ua.StatusCodes.BadInvalidState)
        self.abort_requested = True
        LOG.info("AbortCount requested")
        return []

    async def _on_inject_failure(self, parent):
        self.inject_failure = True
        self.trigger.set()
        LOG.info("InjectFailure armed -- next count fails without incrementing the counter")
        return []

    # ---- the count

    async def _live_settings(self) -> dict[str, object]:
        """Read the current optics settings off the channel nodes.

        Deliberately read rather than taken from config: write LightIntensity in Ignition and
        the next result's frozen Settings snapshot shows the new value, which is the whole
        reason a result carries its settings at all.
        """
        values: dict[str, object] = {}
        bf, c1, c2 = (self.channels[k] for k in ("Brightfield", "Cube1", "Cube2"))

        values["Settings/Illumination/BrightfieldLightIntensity"] = \
            await bf["LightIntensity"].node.read_value()
        values["Settings/Illumination/BrightfieldLedIntensity"] = \
            await bf["LedIntensity"].node.read_value()
        for prefix, channel in (("Cube1", c1), ("Cube2", c2)):
            values[f"Settings/Illumination/{prefix}LightIntensity"] = \
                await channel["LightIntensity"].node.read_value()
            values[f"Settings/Illumination/{prefix}LedIntensity"] = \
                await channel["LedIntensity"].node.read_value()

        # Appendix E has one gate per population; the instrument has one per channel. The live
        # brightfield gate seeds both the Live and Dead gates, offset the way the UI sliders
        # sit when trypan blue separates the two populations.
        for gate, channel, offset in (("LiveGate", bf, 0.0), ("DeadGate", bf, -2.0),
                                      ("BrightfieldGate", bf, 0.0),
                                      ("Cube1Gate", c1, 0.0), ("Cube2Gate", c2, 0.0)):
            for field in GATE_FIELD_ORDER:
                value = await channel["Gate/" + field].node.read_value()
                if field.startswith("Size"):
                    value = max(0.0, value + offset)
                values[f"Settings/{gate}/{field}"] = value
        return values

    def _synthesize(self, count_id: int, sample_name: str, protocol: str,
                    fl_used: bool, ts: datetime) -> dict[str, object]:
        rng = self.rng
        cfg = self.cfg

        # Random walk in log space keeps the value inside the instrument's own 1e4-1e7 range
        # (Appendix B) without ever clipping to a suspiciously flat ceiling.
        self.concentration = min(1.0e7, max(1.0e4,
                                            self.concentration * math.exp(rng.gauss(0.0, 0.06))))
        concentration = self.concentration

        span = max(1, cfg.viability_span_counts)
        fraction = min(1.0, self.completed / span)
        viability = cfg.viability_start + fraction * (cfg.viability_end - cfg.viability_start)
        viability = min(100.0, max(0.0, viability + rng.gauss(0.0, 0.6)))

        total_cells = max(1, int(concentration / 1.0e6 * 1500 * rng.uniform(0.85, 1.15)))
        live_cells = int(round(total_cells * viability / 100.0))
        dead_cells = total_cells - live_cells

        values: dict[str, object] = {
            "CountId": count_id,
            "SessionId": self.session_id,
            "SampleName": sample_name,
            "AcquisitionTime": ts,
            "CountMode": COUNT_MODE["FluorescenceBased" if fl_used else "BrightfieldBased"],
            "Sample/Type": SAMPLE_TYPE["Fluorescence" if fl_used else "Brightfield"],
            "Sample/StainCorrectionApplied": True,
            "Sample/PreDilutionCorrectionApplied": False,
            "Sample/TotalConcentration": round(concentration, 1),
            "Sample/TotalCellsCounted": total_cells,
            "Brightfield/LiveConcentration": round(concentration * viability / 100.0, 1),
            "Brightfield/LiveCellsCounted": live_cells,
            "Brightfield/DeadConcentration": round(concentration * (1 - viability / 100.0), 1),
            "Brightfield/DeadCellsCounted": dead_cells,
            "Brightfield/ViabilityPercent": round(viability, 1),
            "Brightfield/LiveAverageSize": round(rng.gauss(17.2, 0.6), 1),
            "Brightfield/DeadAverageSize": round(rng.gauss(15.6, 0.7), 1),
            "AggregationPercent": round(max(0.0, rng.gauss(4.0, 1.5)), 1),
            "Protocol/ProtocolName": protocol,
            "Protocol/SoftwareRevision": cfg.software_revision,
            "Settings/FocusValue": rng.randint(8, 12),
        }
        values["Settings/FocusMotorValue"] = int(values["Settings/FocusValue"]) * 137 + \
            rng.randint(-4, 4)

        if not fl_used:
            # Every Fluorescence/* path stays out of `values`, so ResultObject.apply clears
            # them to Bad_NoData. Not zero. See the module docstring.
            return values

        percents = []
        for prefix, installed, cube_name in (
            ("Cube1", cfg.cube1_installed, cfg.cube1_name),
            ("Cube2", cfg.cube2_installed, cfg.cube2_name),
        ):
            if not installed:
                percents.append(None)
                continue
            percent = min(100.0, max(0.0, rng.gauss(78.0 if prefix == "Cube1" else 41.0, 3.0)))
            percents.append(percent)
            values[f"Fluorescence/{prefix}/CubeName"] = cube_name
            values[f"Fluorescence/{prefix}/Concentration"] = \
                round(concentration * percent / 100.0, 1)
            values[f"Fluorescence/{prefix}/PercentOfBrightfield"] = round(percent, 1)
            values[f"Fluorescence/{prefix}/CellsCounted"] = int(round(total_cells * percent / 100))
            values[f"Fluorescence/{prefix}/AverageSize"] = round(rng.gauss(17.5, 0.6), 1)

        # Cube 1+2 is the union of the two channels, not their sum -- double-positive cells are
        # counted once. With only one cube fitted there is no union to report, and reporting
        # cube 1's figure under a "1+2" name would invite the reader to believe two channels
        # were used, so it is left absent.
        if percents[0] is not None and percents[1] is not None:
            overlap = percents[0] * percents[1] / 100.0 * rng.uniform(0.7, 1.0)
            combined = min(100.0, percents[0] + percents[1] - overlap)
            values["Fluorescence/Combined/PercentOfBrightfield"] = round(combined, 1)
            values["Fluorescence/Combined/Concentration"] = \
                round(concentration * combined / 100.0, 1)
            values["Fluorescence/Combined/CellsCounted"] = \
                int(round(total_cells * combined / 100))
        return values

    async def _retain(self, count_id: int, values: dict[str, object], ts: datetime) -> None:
        """Add the count to the ResultSet ring, dropping the oldest."""
        if self.cfg.result_history <= 0:
            return
        path = f"{self.cfg.device_id}/CellCounter/ResultSet/{count_id}"
        result = await self._add_result_object(self.result_set, path, str(count_id))
        await result.apply(values, ts)
        self.result_ring.append(result)
        while len(self.result_ring) > self.cfg.result_history:
            oldest = self.result_ring.pop(0)
            await self.server.delete_nodes([oldest.node], recursive=True)

    async def _set_state(self, name: str) -> None:
        await self.unit_nodes["State"].write(UNIT_STATE[name], _now())

    async def _run_count(self) -> None:
        cfg = self.cfg
        self.count_id += 1
        count_id = self.count_id
        sample_name = self.pending_sample or f"SAMPLE-{count_id:04d}"
        self.pending_sample = ""

        await self._set_state("Running")
        LOG.info("count %s (%s) running for %.1fs", count_id, sample_name, RUN_DURATION_S)
        try:
            # Waits on the stop event rather than sleeping, so SIGTERM still exits at once
            # mid-count instead of holding the container for the rest of the duration.
            await asyncio.wait_for(self.stopping.wait(), timeout=RUN_DURATION_S)
            return  # shutting down mid-count
        except asyncio.TimeoutError:
            pass

        if self.abort_requested:
            self.abort_requested = False
            await self._set_state("Aborted")
            LOG.info("count %s aborted -- counter not incremented", count_id)
            return

        if self.inject_failure or self.rng.random() < cfg.failure_rate:
            self.inject_failure = False
            await self._set_state("Error")
            await self._raise_failed(count_id, sample_name, "Focus failed: no cells detected")
            LOG.warning("count %s FAILED -- counter not incremented", count_id)
            return

        ts = _now()
        fl_used = (cfg.cube1_installed or cfg.cube2_installed) and not (
            cfg.bf_only_every_n and count_id % cfg.bf_only_every_n == 0)
        protocol = await self.unit_nodes["ProtocolName"].node.read_value()

        values = self._synthesize(count_id, sample_name, protocol, fl_used, ts)
        values.update(await self._live_settings())

        # Order matters and is the contract: every result node, then State, then the counter.
        await self.results.apply(values, ts)
        await self._retain(count_id, values, ts)
        await self._set_state("Completed")
        self.completed += 1
        await self.unit_nodes["CountCompletedCounter"].write(self.completed, ts)

        await self._raise_completed(count_id, sample_name, values)
        LOG.info("count %s complete: %s cells, %.1f%% viable, %s -- counter=%s",
                 count_id, values["Sample/TotalCellsCounted"],
                 values["Brightfield/ViabilityPercent"],
                 "FL" if fl_used else "BF-only", self.completed)

    async def _raise_completed(self, count_id: int, sample_name: str,
                               values: dict[str, object]) -> None:
        generator = self.event_ok
        generator.event.CountId = count_id
        generator.event.SessionId = self.session_id
        generator.event.SampleName = sample_name
        generator.event.CountMode = int(values["CountMode"])
        generator.event.ResultJson = json.dumps(_nest(values), separators=(",", ":"))
        generator.event.Severity = 100
        generator.event.Message = ua.LocalizedText(f"Count {count_id} complete ({sample_name})")
        await generator.trigger()

    async def _raise_failed(self, count_id: int, sample_name: str, reason: str) -> None:
        generator = self.event_fail
        generator.event.CountId = count_id
        generator.event.SessionId = self.session_id
        generator.event.SampleName = sample_name
        generator.event.Reason = reason
        generator.event.Severity = 500
        generator.event.Message = ua.LocalizedText(f"Count {count_id} failed: {reason}")
        await generator.trigger()

    # ---- lifecycle

    async def run(self) -> None:
        delay = self.cfg.first_count_delay_s
        while not self.stopping.is_set():
            try:
                await asyncio.wait_for(self.trigger.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self.trigger.clear()
            if self.stopping.is_set():
                return
            try:
                await self._run_count()
            except Exception:
                LOG.exception("count failed unexpectedly")
                await self._set_state("Error")
            delay = self.cfg.count_interval_s


async def main_async() -> int:
    cfg = Config()
    server = Server()
    await server.init()

    endpoint = f"opc.tcp://{cfg.bind_host}:{cfg.port}{cfg.endpoint_path}"
    server.set_endpoint(endpoint)
    server.set_server_name("ICC26 Countess 3 FL (simulated)")
    # Anonymous, unencrypted. This is a demo instrument on a private compose network, and an
    # Ignition OPC UA connection that needs a certificate exchange before it will browse is a
    # twenty-minute detour nobody watching wants to sit through.
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    # asyncua binds to the endpoint's host but hands back endpoint descriptions carrying the
    # address the *client* asked on, so binding 0.0.0.0 does not tell Ignition to connect to
    # 0.0.0.0. Set explicitly rather than relying on the default staying true.
    if hasattr(server, "set_match_discovery_client_ip"):
        server.set_match_discovery_client_ip(True)

    countess = Countess(cfg)
    await countess.build(server)

    loop = asyncio.get_running_loop()
    for signame in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, signame, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, countess.stopping.set)
        except NotImplementedError:  # Windows, outside the container
            signal.signal(sig, lambda *_: countess.stopping.set())

    async with server:
        await countess.watch_commands()
        LOG.info("serving %s (namespace %s, ns=%s)", endpoint, NAMESPACE_URI, countess.idx)
        LOG.info("cube 1 %s (%s), cube 2 %s (%s), %s result fields",
                 "installed" if cfg.cube1_installed else "empty", cfg.cube1_name,
                 "installed" if cfg.cube2_installed else "empty", cfg.cube2_name,
                 len(RESULT_FIELDS))
        task = asyncio.create_task(countess.run())
        await countess.stopping.wait()
        task.cancel()
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
