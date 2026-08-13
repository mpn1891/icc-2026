# Countess 3 FL — OPC UA information model

An OPC UA information model for the cell-count result structure defined in **Appendix E, "CSV
file format definition"** (columns A–BS, 71 fields) of
[MAN0019567](MAN0019567-Countess-3FL-Automated-Cell-Counter-UG.pdf).

## Provenance — read this first

- **This is not a vendor or OPC Foundation model.** Thermo Fisher does not publish an OPC UA
  server for the Countess 3 FL. The instrument exports results as CSV to USB, an SMB network
  drive (TCP 445), or Thermo Fisher Connect (§"Save results", p. 52; Appendix B networking, p. 86).
- Every field, unit, and semantic below is **derived from the published Appendix E table**. Where
  Appendix E is ambiguous or self-contradicting, that is called out in
  [Defects in the source table](#defects-in-the-source-table) rather than silently resolved.
- Purpose: give the ICC-2026 demo a realistic, standards-shaped OPC UA server to simulate —
  the same role `opcua-novaflex` plays for pattern 03. Ranges quoted as `EURange` come from
  Appendix B (p. 85): concentration 1×10⁴–1×10⁷ cells/mL, cell diameter 4–60 µm.

Structurally the model follows **OPC UA for Devices (OPC 10000-100, "DI")** for identification
and **OPC UA LADS (OPC 30500)** for the functional-unit / program / result decomposition, without
claiming conformance to either. If you want conformance later, the LADS mapping is noted per
type; the shape was chosen so that swap is mechanical.

---

## 1. Namespace and NodeId conventions

| Item | Value |
|---|---|
| Namespace URI | `http://icc26.demo/UA/Countess3FL/` |
| Namespace version | `1.0.0` |
| Publication date | 2026-08-13 |
| Preferred prefix | `c3fl` |
| Server endpoint (demo) | `opc.tcp://opcua-countess:4840` |

NodeId allocation inside the model namespace:

| Range | Contents |
|---|---|
| `i=1..99` | DataTypes (enums, structures) |
| `i=100..199` | Structure encodings (Default Binary / Default JSON) |
| `i=1000..1999` | ObjectTypes and VariableTypes |
| `i=2000..2999` | Type-definition component instance declarations |
| `i=3000..3999` | EventTypes and their fields |
| string ids | Instances only, e.g. `ns=2;s=Countess-01/Results/Last/Sample/TotalConcentration` |

String NodeIds for instances are deliberate: a hand-built `asyncua` server and an Ignition OPC UA
client both address them far more legibly than numeric ids, and the browse path is readable in a
tag path without a lookup table.

---

## 2. Address space

```
Objects
└── DeviceSet                                        (ns=DI)
    └── Countess-01 : Countess3FLDeviceType
        ├── Identification (DI)                      Manufacturer, Model, SerialNumber,
        │                                            SoftwareRevision, DeviceRevision
        ├── FunctionalUnitSet
        │   └── CellCounter : CellCounterUnitType
        │       ├── FunctionSet
        │       │   ├── Brightfield : BrightfieldChannelType
        │       │   ├── Cube1       : LightCubeChannelType    (optional — slot populated)
        │       │   └── Cube2       : LightCubeChannelType    (optional — slot populated)
        │       ├── ProgramManager
        │       │   ├── ActiveProgram → ProtocolName, State
        │       │   └── ProtocolSet   → <ProtocolName> : ProtocolType
        │       ├── ResultSet
        │       │   ├── LastResult    : CountResultType       ← subscribe target
        │       │   └── <CountId>     : CountResultType       (OptionalPlaceholder, ring of N)
        │       ├── Command
        │       │   ├── StartRequest      : Boolean  (writable, one-shot)
        │       │   └── SampleName        : String   (writable)
        │       ├── CountCompletedCounter : UInt32            ← trigger node
        │       ├── SessionId             : UInt32
        │       └── State                 : FunctionalUnitStateEnum
        └── Methods: StartCount, AbortCount
```

Two nodes carry the whole integration contract:

- **`CountCompletedCounter`** — monotonic `UInt32`, incremented **once, last**, after every field
  of `LastResult` has been written. It is the only node a client needs to subscribe to.
- **`LastResult`** — the complete, self-describing result of that count, including the as-run
  settings snapshot.

This is the same event-on-completion idiom pattern 03 uses for `SampleCompleteCounter`, and for
the same reason: a client that subscribes to 71 value-change notifications has to guess when a
count is finished. A client that subscribes to one counter never guesses.

---

## 3. DataTypes

### 3.1 Enumerations

**`CountModeEnum`** (`ns=2;i=1`) — Appendix E column E.

| Name | Value | Source string |
|---|---|---|
| `BrightfieldBased` | 0 | `BF-based` |
| `FluorescenceBased` | 1 | `FL-based` |

**`SampleTypeEnum`** (`ns=2;i=2`) — column F.

| Name | Value | Source string |
|---|---|---|
| `Brightfield` | 0 | `BF-Brightfield` |
| `Fluorescence` | 1 | `FL-Fluorescence` |

**`FunctionalUnitStateEnum`** (`ns=2;i=3`) — not in Appendix E; required to make the counter
trigger interpretable. Modeled on the LADS functional-unit state machine.

| Name | Value | Meaning |
|---|---|---|
| `Idle` | 0 | No slide, or slide inserted and not started |
| `Running` | 1 | Acquisition/analysis in progress (< 30 s, per Appendix B) |
| `Completed` | 2 | Result written, counter incremented |
| `Aborted` | 3 | Operator cancelled |
| `Error` | 4 | Focus failure, no cells found, hardware fault |

Enum-valued **variables** are typed with the enum DataType directly. If an HMI needs the string
without reading `EnumStrings`, promote the variable to `MultiStateValueDiscreteType`; the demo
server does not.

### 3.2 Structures

Structures exist so a result can be read, cached, and carried in an event as **one atomic value**.
The object hierarchy in §4 and these structures are two views of the same fields — a server
implementing both must keep them consistent.

```
CountResultDataType : Structure                       (ns=2;i=10)
    CountId                     UInt32
    SessionId                   UInt32
    SampleName                  String
    AcquisitionTime             UtcTime
    CountMode                   CountModeEnum
    Sample                      SampleSummaryDataType
    Brightfield                 BrightfieldResultDataType
    Fluorescence               [optional] FluorescenceResultDataType
    AggregationPercent          Double
    ProtocolName                String
    SoftwareRevision            String
    Settings                    CountSettingsDataType

SampleSummaryDataType : Structure                     (ns=2;i=11)
    Type                        SampleTypeEnum
    StainCorrectionApplied      Boolean
    PreDilutionCorrectionApplied Boolean
    TotalConcentration          Double          [cells/mL]
    TotalCellsCounted           UInt32

BrightfieldResultDataType : Structure                 (ns=2;i=12)
    LiveConcentration           Double          [cells/mL]
    LiveCellsCounted            UInt32
    DeadConcentration           Double          [cells/mL]
    DeadCellsCounted            UInt32
    ViabilityPercent            Double          [%]
    LiveAverageSize             Double          [µm]
    DeadAverageSize             Double          [µm]

FluorescenceResultDataType : Structure                (ns=2;i=13)
    Cube1                      [optional] CubeResultDataType
    Cube2                      [optional] CubeResultDataType
    Combined                   [optional] CubeCombinedDataType

CubeResultDataType : Structure                        (ns=2;i=14)
    CubeName                    String
    Concentration               Double          [cells/mL]
    PercentOfBrightfield        Double          [%]
    CellsCounted                UInt32
    AverageSize                 Double          [µm]

CubeCombinedDataType : Structure                      (ns=2;i=15)
    Concentration               Double          [cells/mL]
    PercentOfBrightfield        Double          [%]
    CellsCounted                UInt32

CountSettingsDataType : Structure                     (ns=2;i=16)
    FocusValue                  Int32
    FocusMotorValue             Int32
    Illumination                IlluminationSettingsDataType
    LiveGate                    GateDataType
    DeadGate                    GateDataType
    BrightfieldGate             GateDataType
    Cube1Gate                  [optional] GateDataType
    Cube2Gate                  [optional] GateDataType

IlluminationSettingsDataType : Structure              (ns=2;i=17)
    BrightfieldLightIntensity   Double          [%]  0–100
    BrightfieldLedIntensity     Double
    Cube1LightIntensity        [optional] Double [%] 0–100
    Cube1LedIntensity          [optional] Double
    Cube2LightIntensity        [optional] Double [%] 0–100
    Cube2LedIntensity          [optional] Double

GateDataType : Structure                              (ns=2;i=18)
    SizeMin                     Double          [µm]
    SizeMax                     Double          [µm]
    BrightnessMin               Double
    BrightnessMax               Double
    CircularityMin              Double
    CircularityMax              Double
```

`FluorescenceResultDataType` and the cube fields use `StructureWithOptionalFields` so a
brightfield-only count on a 3 FL, and any count on a non-FL Countess 3, encode the *absence* of
fluorescence rather than zeros. See [§8](#8-absent-vs-zero).

Every structure gets `Default Binary` and `Default JSON` encodings (`ns=2;i=100+`). JSON matters
here — it is what makes the MQTT projection in §10 a re-serialization rather than a re-modeling.

---

## 4. ObjectTypes

Modelling rules: **M** = Mandatory, **O** = Optional, **OP** = OptionalPlaceholder.

### 4.1 `Countess3FLDeviceType` (`ns=2;i=1000`)

Subtype of `DeviceType` (DI). Inherits `Manufacturer`, `Model`, `SerialNumber`,
`SoftwareRevision`, `DeviceRevision`, `DeviceHealth`.

| BrowseName | NodeClass | TypeDefinition / DataType | Rule | Notes |
|---|---|---|---|---|
| `FunctionalUnitSet` | Object | `FolderType` | M | |
| `Manufacturer` | Variable | `LocalizedText` | M | `"Thermo Fisher Scientific"` |
| `Model` | Variable | `LocalizedText` | M | `"Countess 3 FL"` |
| `SerialNumber` | Variable | `String` | M | |
| `SoftwareRevision` | Variable | `String` | M | **Current** firmware. Column BR is the **as-run** revision on the result — do not alias them. |
| `StartCount` | Method | — | O | See §6 |
| `AbortCount` | Method | — | O | See §6 |

### 4.2 `CellCounterUnitType` (`ns=2;i=1001`)

The single functional unit. LADS equivalent: `FunctionalUnitType`.

| BrowseName | NodeClass | TypeDefinition / DataType | Rule | Notes |
|---|---|---|---|---|
| `FunctionSet` | Object | `FolderType` | M | Optical channels |
| `ProgramManager` | Object | `ProgramManagerType` | M | Protocols (column BQ) |
| `ResultSet` | Object | `FolderType` | M | |
| `ResultSet/LastResult` | Object | `CountResultType` | M | Always the most recent completed count |
| `ResultSet/<CountId>` | Object | `CountResultType` | OP | Retained ring, N configurable (demo: 20) |
| `CountCompletedCounter` | Variable | `UInt32` | M | **Increment last.** Wraps at 2³²−1 → clients must compare `!=`, not `>` |
| `SessionId` | Variable | `UInt32` | M | Column B, live value |
| `State` | Variable | `FunctionalUnitStateEnum` | M | |
| `CountResultReady` | Event | `CountCompletedEventType` | M | §5 |

### 4.3 `CountResultType` (`ns=2;i=1002`)

The Appendix E row, as an object. **This is the type the whole document exists for.** Its
components are enumerated in [§7](#7-appendix-e-column-mapping) with their source columns.

| BrowseName | NodeClass | TypeDefinition | Rule |
|---|---|---|---|
| `CountId`, `SessionId`, `SampleName`, `AcquisitionTime`, `CountMode` | Variable | `BaseDataVariableType` | M |
| `Sample` | Object | `SampleSummaryType` | M |
| `Brightfield` | Object | `BrightfieldResultType` | M |
| `Fluorescence` | Object | `FluorescenceResultType` | O |
| `AggregationPercent` | Variable | `AnalogUnitType` | M |
| `Protocol` | Object | `ResultProtocolType` | M |
| `Settings` | Object | `CountSettingsType` | M |
| `AsDataType` | Variable | `CountResultDataType` | M | Whole row as one structured value — one read, one timestamp |
| `ResultFile` | Variable | `FileType`/`ByteString` | O | The original CSV line + header, for provenance |

`AsDataType` is not redundant. Reading 71 leaf nodes yields 71 independently timestamped values
with no guarantee they belong to the same count; reading `AsDataType` yields one value that
cannot tear. The leaf nodes exist for browsing, HMI binding, and historization.

### 4.4 Channel function types

`BrightfieldChannelType` (`ns=2;i=1010`) and `LightCubeChannelType` (`ns=2;i=1011`) hold the
**live, currently-configured** optics settings — writable where the instrument allows it. The
values in `CountResultType/Settings` are a frozen copy taken at acquisition.

| Type | BrowseName | DataType | Rule | Notes |
|---|---|---|---|---|
| both | `LightIntensity` | `Double` `[%]` | M | `EURange` 0–100 (Appendix E, AG) |
| both | `LedIntensity` | `Double` | M | |
| both | `Gate` | `GateDataType` | M | Live gate settings |
| `LightCubeChannelType` | `CubeName` | `String` | M | EVOS cube in the slot, e.g. `GFP`, `RFP`, `DAPI` (Appendix C) |
| `LightCubeChannelType` | `SlotPosition` | `Byte` | M | `1` = top, `2` = bottom |
| `LightCubeChannelType` | `Installed` | `Boolean` | M | False → the `CubeN` object is still browsable but its result fields are `Bad_NoData` |

`CubeName` is a `String`, not an enum: Appendix C lists a large and field-swappable cube set, and
an enum would need a firmware-coupled revision every time Thermo ships a cube.

---

## 5. Events

**`CountCompletedEventType`** (`ns=2;i=3000`), subtype of `BaseEventType`. Raised by the
`CellCounter` unit at the same instant `CountCompletedCounter` increments.

| Field | DataType | Notes |
|---|---|---|
| *(inherited)* | | `EventId`, `EventType`, `SourceNode`, `SourceName`, `Time`, `Severity`, `Message` |
| `CountId` | `UInt32` | Column A |
| `SessionId` | `UInt32` | Column B |
| `SampleName` | `String` | Column C |
| `CountMode` | `CountModeEnum` | Column E |
| `Result` | `CountResultDataType` | The whole row |
| `ResultNode` | `NodeId` | The `ResultSet/<CountId>` object, for follow-up browsing |
| `Severity` | `UInt16` | 100 nominal |

**`CountFailedEventType`** (`ns=2;i=3001`) — same header, plus `Reason : LocalizedText`. Fired
instead of `CountCompletedEventType` when `State → Error`; the counter does **not** increment,
so a counter-only client correctly sees nothing.

Both the counter and the event are specified on purpose. The event is the better OPC UA answer;
the counter is the one that survives a client that doesn't do event subscriptions — including
some historian and PLC-side clients. A server that offers only events strands them.

---

## 6. Methods (optional)

```
StartCount(
    [in]  SampleName    String
    [in]  ProtocolName  String        -- must exist in ProgramManager/ProtocolSet
    [out] CountId       UInt32
) -> Bad_InvalidState if State != Idle; Bad_NotFound if protocol unknown

AbortCount() -> Bad_InvalidState if State != Running
```

The real instrument is started at its touchscreen by an operator holding a slide; `StartCount`
exists so the demo has a stage trigger. Mark it `Optional` and say so out loud when presenting —
"we added a method the hardware doesn't have" is a more honest talk point than pretending the
model is a straight capture.

### 6.1 The command variables, and why both exist

`CellCounter/Command/StartRequest` (`Boolean`, writable) does the same job as `StartCount`.
Writing `true` starts a count; the server clears it back to `false` the instant it accepts the
request. `Command/SampleName` (`String`, writable) names the next count.

| | Method `StartCount` | Variable `Command/StartRequest` |
|---|---|---|
| Returns a `CountId` | yes | no |
| Reports refusal | `Bad_InvalidState`, `Bad_NotFound` | ignored, server-side log only |
| Callable from a plain SCADA tag | **no** | **yes** |
| Arguments | typed, in one atomic call | separate writes, no atomicity |

The method is the better engineering: one call, typed arguments, a real status code back. The
boolean is what a tag can actually drive — a SCADA tag write cannot invoke a method, so every
HMI wanting to trigger a count would need a script behind a button. That gap is why command
bits are everywhere in the field, and it is worth saying out loud rather than pretending the
method is sufficient.

Semantics that make the bit safe to expose:

- **One-shot.** Cleared before the count starts, so a client that never resets it still gets
  exactly one count per write, and the next count needs a fresh rising edge.
- **Rejected while running.** A write during `State = Running` is ignored and logged. Nothing
  queues; check `State` first if that matters.
- **`SampleName` is a request, not a record.** Set it before the trigger. Read the as-run name
  back from the result, never from the command node.

---

## 7. Appendix E column mapping

All 71 columns (A–BS) are mapped. Browse paths are relative to a `CountResultType` instance, e.g.
`ns=2;s=Countess-01/Results/Last/Sample/TotalConcentration`. Column letters appear in every row.

### 7.1 Identification — columns A–E

| Col | Appendix E name | Browse path | DataType | EU |
|---|---|---|---|---|
| A | Count ID | `CountId` | `UInt32` | — |
| B | Session ID | `SessionId` | `UInt32` | — |
| C | Sample name | `SampleName` | `String` | — |
| D | Date & Time | `AcquisitionTime` | `UtcTime` | — |
| E | Count mode | `CountMode` | `CountModeEnum` | — |

Column D is a local-time string in the CSV. **Convert to UTC at the source**, do not carry local
time into the address space — `UtcTime` is a `DateTime` and OPC UA has no timezone type.

### 7.2 Sample — columns F–J

| Col | Appendix E name | Browse path | DataType | EU |
|---|---|---|---|---|
| F | Type | `Sample/Type` | `SampleTypeEnum` | — |
| G | 1:1 Stain corrected | `Sample/StainCorrectionApplied` | `Boolean` | — |
| H | Pre-Dilution corrected | `Sample/PreDilutionCorrectionApplied` | `Boolean` | — |
| I | Total concentration | `Sample/TotalConcentration` | `Double` | cells/mL |
| J | Total cells counted | `Sample/TotalCellsCounted` | `UInt32` | — |

`EURange` for every concentration: **1×10⁴ – 1×10⁷** (Appendix B). A value outside it is a real
out-of-spec condition, not a scaling artifact — set `Uncertain_EngineeringUnitsExceeded`.

### 7.3 Brightfield / live-dead result — columns K–Q

| Col | Appendix E name | Browse path | DataType | EU |
|---|---|---|---|---|
| K | Live concentration | `Brightfield/LiveConcentration` | `Double` | cells/mL |
| L | Live cells counted | `Brightfield/LiveCellsCounted` | `UInt32` | — |
| M | Dead concentration | `Brightfield/DeadConcentration` | `Double` | cells/mL |
| N | Dead cells counted | `Brightfield/DeadCellsCounted` | `UInt32` | — |
| O | Viability (%) | `Brightfield/ViabilityPercent` | `Double` | % (0–100) |
| P | Live average size (µm) | `Brightfield/LiveAverageSize` | `Double` | µm (4–60) |
| Q | Dead average size (µm) | `Brightfield/DeadAverageSize` | `Double` | µm (4–60) |

Viability is trypan-blue-based (Appendix E, O). It is derivable — `L / (L + N) × 100` — but
**publish the instrument's value, do not recompute it**: rounding and the instrument's own
aggregate handling will not match your arithmetic, and a QC record that disagrees with the
instrument printout is worse than no record.

### 7.4 Fluorescence result — columns R–AD

| Col | Appendix E name | Browse path | DataType | EU |
|---|---|---|---|---|
| R | Cube 1 name | `Fluorescence/Cube1/CubeName` | `String` | — |
| S | Cube 1 concentration | `Fluorescence/Cube1/Concentration` | `Double` | cells/mL |
| T | Cube 1 (%) | `Fluorescence/Cube1/PercentOfBrightfield` | `Double` | % |
| U | Cube 1 cells counted | `Fluorescence/Cube1/CellsCounted` | `UInt32` | — |
| AC | Cube 1 average size (µm) | `Fluorescence/Cube1/AverageSize` | `Double` | µm |
| V | Cube 2 name | `Fluorescence/Cube2/CubeName` | `String` | — |
| W | Cube 2 concentration | `Fluorescence/Cube2/Concentration` | `Double` | cells/mL |
| X | Cube 2 (%) | `Fluorescence/Cube2/PercentOfBrightfield` | `Double` | % |
| Y | Cube 2 cells counted | `Fluorescence/Cube2/CellsCounted` | `UInt32` | — |
| AD | Cube 2 average size (µm) | `Fluorescence/Cube2/AverageSize` | `Double` | µm |
| Z | Cube 1+2 concentration | `Fluorescence/Combined/Concentration` | `Double` | cells/mL |
| AA | Cube 1+2 (%) | `Fluorescence/Combined/PercentOfBrightfield` | `Double` | % |
| AB | Cube 1+2 cells counted | `Fluorescence/Combined/CellsCounted` | `UInt32` | — |

The CSV interleaves the cubes' average sizes (AC, AD) far away from the rest of each cube's
fields, because the format grew by appending. The model regroups them per cube — **the one place
this spec deliberately departs from column order.** Anything that reads the CSV positionally
must not assume the model's grouping.

`Cube 1+2` is the *union* of cells fluorescing in either channel, not a sum: `Z ≤ S + W` whenever
any cell is double-positive. Do not synthesize it.

### 7.5 Acquisition settings — columns AE–AH, AU–AX

| Col | Appendix E name | Browse path | DataType | EU |
|---|---|---|---|---|
| AE | Focus value | `Settings/FocusValue` | `Int32` | — |
| AF | Focus motor value | `Settings/FocusMotorValue` | `Int32` | — |
| AG | BF light intensity | `Settings/Illumination/BrightfieldLightIntensity` | `Double` | % (0–100) |
| AH | BF LED intensity | `Settings/Illumination/BrightfieldLedIntensity` | `Double` | — |
| AU | Cube 1 light intensity | `Settings/Illumination/Cube1LightIntensity` | `Double` | % (0–100) |
| AV | Cube 1 LED intensity | `Settings/Illumination/Cube1LedIntensity` | `Double` | — |
| AW | Cube 2 light intensity | `Settings/Illumination/Cube2LightIntensity` | `Double` | % (0–100) |
| AX | Cube 2 LED intensity | `Settings/Illumination/Cube2LedIntensity` | `Double` | — |

### 7.6 Gates — columns AI–AT, AY–BP

Five instances of `GateDataType`. Note the **column order differs between the brightfield gates
(size → brightness → circularity) and the cube gates (brightness → size → circularity)** — an
easy way to write a silently wrong CSV parser.

| Gate | Browse path | SizeMin | SizeMax | BrightMin | BrightMax | CircMin | CircMax |
|---|---|---|---|---|---|---|---|
| Live | `Settings/LiveGate` | AI | AJ | AK | AL | AM | AN |
| Dead | `Settings/DeadGate` | AO | AP | AQ | AR | AS | AT |
| Brightfield | `Settings/BrightfieldGate` | AY | AZ | BA | BB | BC | BD |
| Cube 1 | `Settings/Cube1Gate` | BG | BH | BE | BF | BI | BJ |
| Cube 2 | `Settings/Cube2Gate` | BM | BN | BK | BL | BO | BP |

`SizeMin`/`SizeMax` are `Double` in µm (`EURange` 4–60). Brightness and circularity are UI slider
values; **Appendix E states no range or unit for them** — typed `Double`, no `EngineeringUnits`,
no `EURange`. Do not invent 0–100 or 0–1; if you need the real range, measure it against an
instrument export and record the finding here.

### 7.7 Protocol and trailer — columns BQ–BS

| Col | Appendix E name | Browse path | DataType | EU |
|---|---|---|---|---|
| BQ | Protocol name | `Protocol/ProtocolName` | `String` | — |
| BR | Software revision | `Protocol/SoftwareRevision` | `String` | — |
| BS | Aggregation(%) | `AggregationPercent` | `Double` | % (0–100) |

`BR` is the **as-run** firmware revision and belongs to the result. The device's *current*
revision lives in DI `Identification/SoftwareRevision`. They differ across an instrument update,
which is exactly when you need the result-level one.

**Coverage: 71 of 71 columns** — A–E (5), F–J (5), K–Q (7), R–AD (13), AE–AF (2), AG–AT (14),
AU–AX (4), AY–BD (6), BE–BP (12), BQ–BS (3).

---

## 8. Absent vs zero

Three distinct conditions that a naive mapping collapses into `0.0`:

| Condition | Model response |
|---|---|
| Cube slot physically empty | `LightCubeChannelType/Installed = false`; the corresponding optional `CubeN` object is **not instantiated** in results; structure field omitted |
| Cube installed, but count was BF-based (`E = BF-based`) | Nodes present, value `null`, StatusCode `Bad_NoData` |
| Cube used, genuinely found no fluorescing cells | Value `0`, StatusCode `Good` |

The difference between rows 2 and 3 is the difference between "we didn't look" and "we looked and
there was nothing," and a viability trend built on the wrong one is wrong in a way nobody catches.
OPC UA gives you `StatusCode` precisely so you don't have to encode this in the value; the CSV
does not, which is why the CSV is the weaker interface and worth saying so on stage.

---

## 9. Engineering units

`AnalogUnitType` variables carry `EUInformation`:

| Quantity | displayName | description | UNECE code | unitId | namespaceUri |
|---|---|---|---|---|---|
| Cell size | `µm` | micrometre | `4H` | `13384` | `http://www.opcfoundation.org/UA/units/un/cefact` |
| Percentage | `%` | percent | `P1` | `20529` | `http://www.opcfoundation.org/UA/units/un/cefact` |
| Concentration | `cells/mL` | cells per millilitre | — | `1` | `http://icc26.demo/UA/Countess3FL/` |

`unitId` for the UNECE units is the common code read as ASCII (`'4'<<8 | 'H'` = 13384). Cell
concentration has no UNECE common code, so it takes a locally assigned `unitId` under **this
model's** namespace URI — that is the mechanism the spec provides for non-UNECE units, and it is
correct to use it rather than pick an unrelated code.

---

## 10. MQTT projection (ICC-2026 pattern 03)

Topic: `icc26/site1/qc/analyzers/countess-01/result`, envelope per `docs/00-architecture.md`:

```json
{
  "ts": "2026-08-13T14:03:22.145Z",
  "seq": 1041,
  "source": { "id": "countess-01", "type": "analyzer" },
  "meta": { "mechanism": "opcua-event", "ingest_ts": "2026-08-13T14:03:22.190Z" },
  "values": {
    "count_id": 4711,
    "session_id": 88,
    "sample_name": "BR-201-D7",
    "count_mode": "FluorescenceBased",
    "sample": {
      "type": "Fluorescence",
      "total_concentration": 1.42e6,
      "total_cells_counted": 1832,
      "stain_correction_applied": true,
      "pre_dilution_correction_applied": false
    },
    "brightfield": {
      "live_concentration": 1.33e6, "live_cells_counted": 1714,
      "dead_concentration": 9.0e4,  "dead_cells_counted": 118,
      "viability_percent": 93.6,
      "live_average_size_um": 17.2, "dead_average_size_um": 15.8
    },
    "fluorescence": {
      "cube1": { "name": "GFP", "concentration": 1.11e6, "percent": 78.2,
                 "cells_counted": 1433, "average_size_um": 17.5 },
      "cube2": null,
      "combined": null
    },
    "aggregation_percent": 4.1,
    "protocol": { "name": "CHO viability", "software_revision": "1.4.212" }
  }
}
```

Rules for the projection:

- **One publish per `CountCompletedCounter` increment**, never per value change. Same rule as
  pattern 03's `SampleCompleteCounter`, and the same talk point: event-on-completion.
- `values` carries the **result**, not `Settings`. The gates and illumination are ~40 of the 71
  columns and are constant across a protocol; publishing them on every count is bytes nobody
  reads. Expose them on request (a `.../settings` topic, retained, republished on change) or omit.
  Say this deliberately — "we modeled all 71 and chose to publish 31" is an engineering decision,
  and if the audience is coming from the CSV they will notice.
- A field whose OPC UA StatusCode is not `Good` maps to JSON `null`, never `0`. §8.

Ignition side, per repo convention:

1. OPC UA client connection to `opc.tcp://opcua-countess:4840` (create in UI, commit what
   `git status` reveals — same as the `opcua-novaflex` connection).
2. One OPC tag on `CountCompletedCounter`; a tag-change gateway event script reads the
   `LastResult` branch and assembles the payload.
3. `system.cirruslink.transmission.publish("chariot_broker", topic, payload, 0, False)` —
   Transmission, not Engine, per the ACL split in `docs/plans/00-master-plan.md`.

---

## 11. Defects in the source table

Appendix E as published (pp. 91–94) contains copy-paste errors. Anyone implementing straight from
the PDF will reproduce them. Resolutions below are inference — flag them if you get access to a
real export.

| Col | Published description | Problem | Resolution used here |
|---|---|---|---|
| BI | `Cube 1 circularity min` → *"Template used for count"* | Description belongs to a protocol/template field | Treated as **Cube 1 circularity min**; name wins |
| BJ | `Cube 1 circularity max` → *"Current software version used for count"* | Description belongs to BR | Treated as **Cube 1 circularity max**; name wins |
| BG, BH | `Cube 1 size min/max` → *"Second (bottom) light cube … minimum brightness"* | Description names the **second** cube and says *brightness* | Treated as **Cube 1 (top) size min/max**; name wins |
| BE, BF | `Cube 1 brightness min/max` → *"First (top) … brightness"* | Consistent | Used as published |
| AN | `Live circularity max` → *"…value for minimum circularity"* | Says minimum on a max field | Treated as **maximum** |
| K, Z | *"Concetration"* | Typo | `Concentration` |
| M | *"Concentration of jus the 'dead' portion"* | Typo | `just` |
| AJ | *"LIve size max"* | Typo | `Live size max` |

Rule applied throughout: **the column name wins over the description** where they conflict, since
the name is what appears in the CSV header row and is what a parser keys on. Two of these (BI, BJ)
are the tail of a description column that slipped by one row; BG/BH is a cube-1/cube-2 mixup.

---

## 12. NodeSet2 fragment

Illustrative — one enum and one type, showing the intended encoding. Not the full model.

```xml
<UANodeSet xmlns="http://opcfoundation.org/UA/2011/03/UANodeSet.xsd">
  <NamespaceUris>
    <Uri>http://icc26.demo/UA/Countess3FL/</Uri>
  </NamespaceUris>

  <UADataType NodeId="ns=2;i=1" BrowseName="2:CountModeEnum">
    <DisplayName>CountModeEnum</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=29</Reference>
    </References>
    <Definition Name="2:CountModeEnum">
      <Field Name="BrightfieldBased" Value="0"/>
      <Field Name="FluorescenceBased" Value="1"/>
    </Definition>
  </UADataType>

  <UAObjectType NodeId="ns=2;i=1002" BrowseName="2:CountResultType">
    <DisplayName>CountResultType</DisplayName>
    <References>
      <Reference ReferenceType="HasSubtype" IsForward="false">i=58</Reference>
      <Reference ReferenceType="HasComponent">ns=2;i=2001</Reference>
      <Reference ReferenceType="HasComponent">ns=2;i=2002</Reference>
    </References>
  </UAObjectType>

  <UAVariable NodeId="ns=2;i=2001" BrowseName="2:CountId"
              ParentNodeId="ns=2;i=1002" DataType="UInt32">
    <DisplayName>CountId</DisplayName>
    <References>
      <Reference ReferenceType="HasTypeDefinition">i=63</Reference>
      <Reference ReferenceType="HasModellingRule">i=78</Reference>
      <Reference ReferenceType="HasComponent" IsForward="false">ns=2;i=1002</Reference>
    </References>
  </UAVariable>

  <UAObject NodeId="ns=2;i=2002" BrowseName="2:Sample" ParentNodeId="ns=2;i=1002">
    <DisplayName>Sample</DisplayName>
    <References>
      <Reference ReferenceType="HasTypeDefinition">ns=2;i=1003</Reference>
      <Reference ReferenceType="HasModellingRule">i=78</Reference>
      <Reference ReferenceType="HasComponent" IsForward="false">ns=2;i=1002</Reference>
    </References>
  </UAObject>
</UANodeSet>
```

`i=78` is `Mandatory`; `i=80` is `Optional`; `i=11508` is `OptionalPlaceholder`.

---

## 13. Simulator behavior (demo server)

**Built** — see [`services/opcua-countess/`](../../services/opcua-countess/README.md), which
implements this document and lists where it knowingly falls short of it. The table below is what
that server does:

| Behavior | Value |
|---|---|
| Count cycle | Operator-triggered via `StartCount`, plus a free-running mode every ~180 s (env-tunable) |
| Run duration | `State = Running` for a fixed 5 s. The instrument's own spec is < 30 s (Appendix B), but a demo waiting 30 s for a result is 30 s of dead air |
| Write order | All result nodes → `State = Completed` → **`CountCompletedCounter` last** |
| Concentration walk | Random walk within 1×10⁴–1×10⁷, biased to ~1×10⁶ for a CHO culture |
| Viability | Slow decline across a session, 96% → 88%, with noise — makes the trend chart worth looking at |
| Cube 2 | Unpopulated by default, so §8's absent-vs-zero case is live on stage, not hypothetical |
| Failure injection | `POST`-equivalent method or env flag to raise `CountFailedEventType` without incrementing the counter — proves the counter contract |

The last row is the one worth rehearsing: a client that treats "counter changed" as the trigger
sees nothing on a failed count, which is correct, and a client that polls `LastResult` on a timer
republishes a stale result as if it were new. That contrast is the pattern-03 argument in one
screen.
