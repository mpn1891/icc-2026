# Nova Biomedical BioProfile FLEX2 — OPC UA information model

A transcription of the address space published by the **real vendor OPC UA server**, taken from
Section 9 ("BioProfile FLEX2 OPC Tag List", pp. 9-1 – 9-40) of
[LPN 60644B](LPN%2060644%20-BioProfile-FLEX2-IFU-EN-Manual-OPC.pdf), *BioProfile® FLEX2 OPC
Server Instructions for Use Manual*, Nova Biomedical, 2024-03.

## Provenance — read this first, it is the opposite of the Countess

| | [Countess 3 FL](countess-3fl-opcua-model.md) | **FLEX2 (this document)** |
|---|---|---|
| Vendor OPC server | **None.** CSV to USB/SMB/Connect | **Yes**, shipped on the Bridge PC, licensed per instrument |
| Whose address space | Ours. We designed it | **Nova's.** We are transcribing it |
| Shape | DI + LADS: `DeviceSet/…/FunctionalUnitSet/…/ResultSet` | Two flat trees of string-id tags, `OPCSystemObjects` and `OPCSystemCommands` |
| Completion signal | `CountCompletedCounter` + events, by design | **None. See [§6](#6-the-missing-trigger-and-icc26extensions)** |
| Actions | Methods *and* a command bit | **Command bits only. No methods at all** |
| That doc is | a proposal | a **report** |

The Countess model is what we think an analyzer *should* publish. This one is what a vendor
actually ships in 2024. Where they disagree, the disagreement is the interesting part — and it
is the reason pattern 3 runs two instruments rather than one.

**What is faithful here:** every browse name, every hierarchy level, every data type and the
NodeId scheme, exactly as section 9 prints them — including the redundancies (`DP_GasCal ->
GasCal -> GasCal`), the inconsistent spellings (`CdvDilutionRatio` vs
`CellDensityDilutionRatio`) and the near-duplicate result trees. None of it has been tidied.

**What is ours:** the `ICC26Extensions` object, and nothing else. It is a separate top-level
node with a `README` variable inside it saying so, because a tag export taken from this
simulator will outlive anyone's memory of which half was invented.

**What is simulated:** the values. This is a demo instrument. No real culture, no real sensors.

---

## 1. Namespace and NodeId conventions

| Item | Real FLEX2 (manual §1.2.2) | This simulator |
|---|---|---|
| Server description | Nova Biomedical OPC UA Server | ICC26 BioProfile FLEX2 (simulated) |
| Endpoint | `opc.tcp://<ip>:59888/NovaBiomedical` | `opc.tcp://opcua-novaflex:4840/novaflex/` |
| Namespace index | **`ns=3` for tags, `ns=2` for tag folders** | `ns=2` for everything |
| Namespace URI | **not published in the manual** | `http://icc26.demo/UA/NovaflexII/` |
| Identifier type | String, for every tag | String, for every tag |
| Identifier | `OPCSystemObjects->Example.Item.Name` | same, separator configurable |

Two consequences worth planning around before anyone points Ignition at a real instrument:

- **The manual never states the namespace URIs.** Ignition 8.3 addresses by URI (`nsu=…;s=…`),
  not index, so a real integration has to browse a live server once and read the URI off it.
  The index cannot be trusted — the manual itself shows it moving between server versions
  (`ns=2` on ≤ 1.2.19066, `ns=3` on ≥ 3.0), which is exactly the drift that makes index
  addressing a bad habit.
- **Folders and tags live in different namespaces on the real server.** Reproducing that split
  would require both URIs, which the manual does not give. This simulator uses one namespace.
  See [deviations](#12-deviations-in-this-simulator).

### 1.1 The separator, and why it is a knob

Section 1.2.2 prints the identifier as `s = OPCSystemObjects → Example.Item.Name`, and section 9
prints every path the same way — `<-OPCSystemObjects->ChemCard->LotNumber`. The arrow appears to
be *inside the identifier*, not typography around it. But this is a PDF, and the leading `<-` on
every row of section 9 is plainly a rendering artifact, so the arrow may be one too. The
underlying product is OPC Expert, whose DA item ids are conventionally dotted.

Unresolvable without a live instrument. So the simulator takes `NODE_SEPARATOR`, defaulting to
`->`, and every field table stores paths with `/`, translated at NodeId-construction time.
If a real FLEX2 turns out to use dots, one environment variable fixes it and no table changes.

```
nsu=http://icc26.demo/UA/NovaflexII/;s=OPCSystemObjects->HistoricalSampleResults->Chem->Gluc->Result
```

---

## 2. Address space

```
Objects
├── OPCSystemObjects                     read-only. 799 nodes
│   ├── ActiveTasks, CoreHeartbeat, DateTime, ScheduledTasks,
│   │   SampleTypes, SampleTypeNames, Settings, SoftwareVersion, TimeSync
│   ├── CDVPackStatus  ChemPackStatus  ChemQCPackStatus                 9 fields each
│   │   GasPackStatus  GasQCPackStatus  ESMPackStatus
│   ├── ChemCard  GasCard                                              7 fields each
│   ├── Modules->InstalledUnits                                        5 module presence tags
│   ├── OsmoState, Resources->Wells
│   ├── Parameters-><analyte>->{Alert,Warning}                         13 × 2
│   ├── DP_GasCal / DP_ChemCal / DP_OsmoCal / DP_CdvCal                 calibration status
│   ├── ParametersConfiguration-><analyte>->Units                      13
│   ├── SampleResults                    : 138 leaves      ← changes silently
│   ├── HistoricalSampleResults          : 141 leaves      ← the one to read
│   ├── QCResults                        : 102 leaves      ← NOT in either of the above
│   ├── AutomationEvents->Automation                                    4 timestamps
│   └── AutosamplerStatus                 2 banks + 10 RSM × 7 fields
├── OPCSystemCommands                    writable. 104 nodes, ALL Boolean bits + arguments
│   ├── ESMScheduleAnalysis->{DueTime, Operator, SampleType,
│   │                         SampleInformation->…, ESMScheduleAnalysis}   ← stage trigger
│   ├── EXT_OLSScheduleAnalysis->…   AutosamplerScheduleAnalysis->…
│   ├── ChemistryCalibration  GasCalibration  ChemistryQcLevel1/2  GasQcLevel1/2
│   ├── DeproWells  ClearWells  ClearScheduledTasks  AdjustIntensity  …
│   └── SetSyncEvent->{Event, SetSyncEvent}
└── ICC26Extensions                      NOT VENDOR. 8 nodes
    ├── README                : String   says exactly that
    ├── SampleCompleteCounter : UInt32   ← the trigger this demo subscribes to
    ├── QcCompleteCounter     : UInt32
    ├── State                 : FunctionalUnitStateEnum
    ├── ResultJson            : String   the whole sample result, one value
    ├── QcResultJson          : String
    ├── LastError             : String
    └── InjectFailure         : Boolean  demo-only
```

---

## 3. Analytes

The FLEX2 measures across four modules. Any of them may be absent from a given instrument, and
`StartTags->ModuleInformation->Modules->{CDV,Chemistry,Gas,Osmo}` says per-analysis which took
part — see [§8](#8-absent-vs-zero).

| Module | Browse branch | Analytes |
|---|---|---|
| pH/Gas | `Gas` | `pH`, `pCO2`, `pO2` |
| Chemistry | `Chem` | `Na`, `K`, `Ca`, `NH4`, `Gln`, `Glu`, `Gluc`, `Lac` |
| Osmometer | `Osmo` | single value, no sub-branch |
| Cell Density / Viability | `CellDensity` | density, viability, diameter, counts |

### 3.1 Units

The FLEX2 publishes units as **`String` tags, one per analyte, per result** — not as OPC UA
`EUInformation` properties. That is the vendor's answer to units and it is reproduced verbatim;
no `EngineeringUnits` or `EURange` property is attached anywhere in the vendor branch.

| Analyte | Units string | Notes |
|---|---|---|
| `pH` | *(empty)* | a logarithmic ratio — genuinely unitless |
| `pCO2`, `pO2` | `mmHg` | |
| `Na`, `K`, `Ca`, `NH4`, `Gln`, `Glu` | `mmol/L` | |
| `Gluc`, `Lac` | `g/L` | mass concentration, unlike the other chemistries |
| `Osmo` | `mOsm/kg` | |
| `Density` / `TotalDensity` | `10^6 cells/mL` | |

### 3.2 The three lists of thirteen

Section 9 contains three per-analyte lists of thirteen members that **disagree on the last one**:

| List | Members 1–12 | 13th |
|---|---|---|
| `StartTags->Ranges->…` | pH pCO2 pO2 Na K Ca NH4 Gln Glu Gluc Lac Osmo | **`TotalDensity`** |
| `Parameters->…->{Alert,Warning}` | *(same)* | **`CDV`** |
| `ParametersConfiguration->…->Units` | *(same)* | **`Density`** |

Three names for the cell-density channel, in three adjacent tables. Reproduced rather than
harmonised: a client browsing a real instrument meets all three, and quietly unifying them here
would hide a thing an integrator has to know.

---

## 4. The result trees

### 4.1 `SampleResults` vs `HistoricalSampleResults`

Two near-identical trees, 138 and 141 leaves. The manual opens section 9 with:

> **IMPORTANT:** Please be aware that the Historical Sample Result Object Tags retrieves all
> Sample Result Object Tag records, regardless of how the sample was initiated. For your
> specific goal of gathering data from sample analysis results, it is recommended to use the
> Historical Sample Result Object Tags.

So: **read `HistoricalSampleResults`.** It differs by three fields, all sample-retain related —
`StartTags->FollowWithRetain`, `StartTags->RetainVolume`, `RetainCount`.

Neither tree covers QC. Both are documented "Displays results for all sample analyses **except
quality control**." A client that watches only these silently misses every control run.

### 4.2 Structure, per tree

| Group | Leaves | Contents |
|---|---|---|
| `StartTags` | 6 | `AutosamplerPort`, `SampleSource`, `DispenseVolume`, `Operator`, `SampleType`, `TrayLocation` |
| `StartTags->ModuleInformation` | 7 | 3 dilution/inspection strings + `Modules->{CDV,Chemistry,Gas,Osmo}` Booleans |
| `StartTags->SampleInformation` | 8 | `BatchID`, `CellType`, `PreDilutionMultiplier`, `SampleID`, `SpargingO2`, `VesselID`, `VesselPressure`, `VesselTemperature` |
| `StartTags->Ranges` | 52 | 13 analytes × `LowerLimit`, `UpperLimit`, `OffsetIntercept`, `OffsetMultiplier` |
| `Gas`, `Chem`, `Osmo` | 36 | 12 analytes × `Result`, `Units`, `ErrorStatus` |
| `CalculatedResults` | 11 | 6 derived values + 5 unit strings |
| `CellDensity` | 10 | `TotalDensity`, `ViableDensity`, `Viability`, `AvgLiveDiameter`, `LiveStdDeviation`, counts, units |
| `*/FlowTimeData->FlowTime` | 3 | per module |
| timestamps | 4 | `SampleTime`, `TimeStamp`, `ModifiedTime`, `TimeInTray` |
| `Errors` | 1 | free text, empty when clean |
| retain *(historical only)* | 3 | |

`OffsetIntercept = 0` and `OffsetMultiplier = 1` are the vendor's documented "no correlation
applied" values, published per analysis so a downstream record can prove none was applied.

`ModifiedTime` is not `SampleTime`. The manual: a deferred modified time occurs "if CDV images
are reanalyzed or any changes are made after the analysis was completed." **A result can change
after you have already read it** — which, with no completion counter, is a second reason a
polling client cannot tell what it is looking at.

### 4.3 `QCResults`

102 leaves. Not a subset of the sample tree: its `StartTags` carry the control's
`Level`, `LotNumber` and `ExpirationDate` instead of sample metadata, there is no
`ModuleInformation` and no `CalculatedResults`, and the `CellDensity` block is reduced to
density and image count.

---

## 5. Commands — every one of them a bit

`OPCSystemCommands` holds 104 writable nodes. **There is not a single OPC UA Method on this
instrument.** Every action is "write a 1 to this tag," and arguments are sibling tags you are
expected to write first.

§6.1 of the Countess model doc argued that command bits are what actually ships, because a SCADA
tag write cannot invoke a method. This is that argument, from a vendor, in a shipping product.

| Command | Argument tags | Effect |
|---|---|---|
| `ESMScheduleAnalysis` | `DueTime`, `Operator`, `SampleType`, `SampleInformation->` ×11 | run a sample via the External Sampling Module |
| `EXT_OLSScheduleAnalysis` | same + `DispenseTimeout` | run via an external online sampler |
| `AutosamplerScheduleAnalysis` | same + `AutosamplerPort`, `RetainVolume`, `NumberOfRetains`, `FollowWithRetain` | run via the Nova OLS |
| `ESMTerminate` / `EXT_OLSTerminate` / `AutosamplerTerminate` | — | abort |
| `ChemistryCalibration`, `GasCalibration` | — | 2-point calibration |
| `ChemistryQcLevel1/2`, `GasQcLevel1/2` | — | onboard auto-QC |
| `DeproWells`, `ClearWells`, `ClearScheduledTasks`, `AdjustIntensity` | — | maintenance |
| `AutosamplerCleanup` / `PrimePack` / `PrimeReactor` | `AutosamplerPort` | per-RSM maintenance |
| `SetSyncEvent` | `Event` (one of four names) | stamp an automation event |

What the vendor's design costs you, and worth saying out loud:

- **Nothing is atomic.** Sample id, batch id, vessel and due time are separate writes. A client
  that flips the trigger bit while another is halfway through its metadata gets a spliced sample.
- **There is no refusal.** A tag write returns `Good` whether or not the instrument acted.
  Trigger during a run and all you learn is that nothing happened. The Countess method can
  answer `Bad_InvalidState`; a bit cannot.
- **`DueTime` in the past or blank means "now."** Documented, and a good trap: an unset
  `DateTime` tag is 1970, which is in the past, which means immediately.
- **Whether the server clears the bit is undocumented.** The manual says only "write a 1 to this
  tag." A bit that does not self-clear cannot fire twice until the client clears it — a real
  integration difference, so the simulator makes it `COMMAND_AUTO_CLEAR`, default true.

---

## 6. The missing trigger, and `ICC26Extensions`

**The FLEX2 OPC server publishes no completion counter, no state variable and no OPC UA events.**
Section 9 lists no monotonic sequence tag. There is no "sample ready" flag. `SampleResults`
simply changes underneath whatever is subscribed to it.

A client therefore cannot answer "is this a new sample?" from the address space. The best
available proxy is diffing `TimeStamp` — which fails when a result is amended in place
(`ModifiedTime`, §4.2), and gives no ordering across the QC tree.

This is not a criticism of Nova specifically; it is what most instrument OPC servers look like,
and it is precisely the gap pattern 3 exists to talk about. So this simulator adds a separate,
loudly-named object:

| Node | Type | Purpose |
|---|---|---|
| `ICC26Extensions/README` | `String` | states in the address space that this branch is not vendor |
| `SampleCompleteCounter` | `UInt32` | **written last**, after every result leaf and after `State`. The one node to subscribe to |
| `QcCompleteCounter` | `UInt32` | increments on QC only — never `SampleCompleteCounter` |
| `State` | `FunctionalUnitStateEnum` | `0` Idle `1` Running `2` Completed `3` Aborted `4` Error `5` Calibrating `6` QualityControl `7` Maintenance |
| `ResultJson` / `QcResultJson` | `String` | the whole result as one value — one read, one timestamp, cannot tear across 141 leaves |
| `LastError` | `String` | |
| `InjectFailure` | `Boolean` | demo-only. The real instrument has no such button — say so on stage |

Events, likewise ours: `SampleCompletedEventType` (`ns=2;i=3000`), `SampleFailedEventType`
(`i=3001`), `QcCompletedEventType` (`i=3002`), all emitted from the `OPCSystemObjects` node.

### 6.1 The contract

1. Every leaf of a result is written first, all carrying **one `SourceTimestamp`** — the
   acquisition instant, not the write instant. That is what proves a set of reads is one sample.
2. Then `State`.
3. Then `SampleCompleteCounter`, **last**.

A failed analysis raises `SampleFailedEventType` and does **not** increment. A QC run increments
only `QcCompleteCounter`. Both are the demo's argument in one screen: a counter-driven client
sees exactly the events it should, and a client polling `SampleResults` on a timer republishes a
stale result as though it were new.

---

## 7. Instrument state

Beyond results, the vendor publishes a lot of genuinely useful operational data — this is the
part of the FLEX2 model that is *better* than the Countess's, and worth saying so.

| Branch | Contents |
|---|---|
| `CoreHeartbeat->UpTime`, `DateTime->DateTime` | the manual's own liveness pair (§6): subscribe to these two to confirm the server is updating |
| six `*PackStatus` | `Empty`, `Expired`, `ExpirationDate`, `InstallationDate`, `Installed`, `LotNumber`, `FluidRemaining` %, `SamplesRemaining`, `SamplesRemainingPercent` |
| `ChemCard`, `GasCard` | as above minus fluid, plus `Hydrated` |
| `Parameters-><a>->{Alert,Warning}` | per-sensor health, `False` = available for analysis |
| `DP_*Cal->…->CalibrationStatus` | `Calibrated` / `Uncalibrated` per analyte |
| `Modules->InstalledUnits` | `Ready` per module |
| `Resources->Wells` | `WellState.Clear` per well |
| `AutosamplerStatus` | 2 banks × `{Initialized, Status}`, 10 RSM × 7 fields |

Consumable lot numbers and expiry dates against a result timestamp are exactly what a QC
investigation needs and exactly what the CSV export cannot give you. That is a real argument for
OPC UA over file drops, and it is the vendor's, not ours.

---

## 8. Absent vs zero

The FLEX2 gives this its own vocabulary, which the Countess had to invent:

| Condition | Model response |
|---|---|
| Module not fitted | `Modules-><m>` = `False` **Good**; every `Result` under it `null` / `Bad_NoData`; its `Units` and `ErrorStatus` absent too |
| Module fitted, not used this analysis | identical — the Boolean is per-analysis, not per-instrument |
| Sensor errored | `Result` `Bad_NoData`, `ErrorStatus` = text, **`Units` stays Good** — the unit belongs to the channel, not the reading |
| Measured, genuinely zero | `Result` = `0.0`, **Good** |

`Modules->Osmo = False` (Good) sitting beside `Osmo->Result = null` (`Bad_NoData`) at the same
`SourceTimestamp` is the whole absent-vs-zero argument in two adjacent nodes, in the vendor's own
vocabulary. The demo ships with the osmometer unfitted so it is live on stage rather than
hypothetical, and autosampler bank B likewise (ten RSM tags at `Bad_NoData`).

Verified at the wire — one result, 130 Good and 11 Bad leaves, one distinct `SourceTimestamp`.

---

## 9. Calculated results

Six derived values, five unit strings. `pHCorrected` has no unit tag, which is correct — pH is
unitless — though the vendor still ships a `Gas->pH->Units` tag beside it.

| Field | Basis used by this simulator |
|---|---|
| `HCO3` | Henderson–Hasselbalch, `0.0307 · pCO2 · 10^(pH − 6.105)` |
| `O2Saturation` | Severinghaus, `100 / (23400/(pO2³ + 150·pO2) + 1)` |
| `pHCorrected` | `pH − 0.0147·(T − 37)` |
| `pCO2Corrected` | `pCO2 · 10^(0.019·(T − 37))` |
| `pO2Corrected` | `pO2 · 10^(0.0244·(T − 37))` |
| `CO2Saturation` | **the manual does not define this.** Reported as bicarbonate as a fraction of total CO₂. Do not read clinical meaning into it |

`T` is `SampleInformation->VesselTemperature`, default 37 °C — at which the three corrections are
identities, which makes them easy to sanity-check live: change the vessel temperature and watch
them separate from their uncorrected twins.

---

## 10. MQTT projection (ICC-2026 pattern 03)

Topic `icc26/site1/qc/analyzers/novaflex-01/result`, envelope per `docs/00-architecture.md`.
The device id is `novaflex-01`. This document, the simulator directory, and the Event Stream
name keep `novaflex` because those name the manual and the service.



```json
{
  "ts": "2026-08-13T18:46:17.700Z",
  "seq": 412,
  "source": { "id": "novaflex-01", "type": "analyzer" },
  "meta": { "mechanism": "opcua-event", "ingest_ts": "2026-08-13T18:46:17.760Z" },
  "values": {
    "sample_id": "S-00042", "batch_id": "BR-2026-014", "vessel_id": "BRX-2000-A",
    "cell_type": "CHO-K1", "sample_source": "ESM", "operator": "Auto",
    "gas":  { "ph": 7.08, "pco2": 58.4, "po2": 96.2 },
    "chem": { "na": 149.3, "k": 5.1, "ca": 1.11, "nh4": 3.2,
              "gln": 1.8, "glu": 1.6, "gluc": 3.42, "lac": 1.61 },
    "osmo": null,
    "cell_density": { "total_density": 6.71, "viable_density": 6.24,
                      "viability_percent": 93.1, "avg_live_diameter_um": 15.4 },
    "calculated": { "hco3": 17.2, "o2_saturation": 96.6, "co2_saturation": 90.6 },
    "modules_used": { "cdv": true, "chemistry": true, "gas": true, "osmo": false }
  }
}
```

Rules for the projection:

- **One publish per `HistoricalSampleResults/SampleTime` change**, never per value change. That
  is a vendor field, not `ICC26Extensions/SampleCompleteCounter`. The simulator writes
  `SampleTime` last on the historical tree so the trigger cannot fire before the rest of the
  result is settled. QC does not touch this tree and is a separate event.
- Read the **historical UDT tags** already bound under `novaflex-01/result/…`. Do not read
  `ICC26Extensions/ResultJson` — that node is an extension, and the publish path stays on
  vendor tags. Bad quality maps to JSON `null`, never `0`.
- `values` carries the result, **not `StartTags->Ranges`**. That is 52 of the 141 leaves,
  constant across a sample type, and publishing them on every sample is bytes nobody reads.
  Expose them on a retained `…/ranges` topic if anyone asks. Say the choice out loud.
- **Publish `modules_used`.** Without it a consumer cannot distinguish "not measured" from
  "dropped in transit," and it is one Boolean per module.
- QC results go to a **separate topic** (`…/qc`) if/when that stream is built. They are not
  samples and must not land in a sample trend.

Ignition side: tag-change on `result/sample_time` → Event Stream `03_opcua/novaflex-result`
(`opcua_event.build_novaflex_result`) → MQTT Transmission handler → the topic above.
Transmission, not Engine, per the ACL split in `docs/plans/00-master-plan.md`.

---

## 11. Defects and ambiguities in the source

Section 9 as published contains errors and gaps. Anyone implementing straight from the PDF will
reproduce them. Resolutions are inference — flag them if you get access to a real instrument.

| Location | Problem | Resolution used here |
|---|---|---|
| `QCResults->*->Units` (p. 9-20) | Typed **`Single`** — a floating-point number — while the description says "unit of measurement" and the identical tags under `SampleResults` are `String` | Treated as **`String`**; the description and the sibling tables win |
| NodeId separator (§1.2.2) | The arrow is printed inside the identifier, but every section-9 row also has a spurious leading `<-`, so it may be typography | `NODE_SEPARATOR`, default `->`. See [§1.1](#11-the-separator-and-why-it-is-a-knob) |
| Namespace URIs | Never stated anywhere in the manual, though §1.2.2 gives the indices | Own URI. A real integration must browse a live server and read it |
| `HistoricalSampleResults` flowtimes (pp. 9-10, 9-11) | The three `FlowTime` tags are printed **twice**, once under "Sample Information" and again under a "Flowtime" heading | One set of three |
| `CalculatedResults` units | 6 values, 5 unit tags — no `pHCorrectedUnits` | Correct as published: pH is unitless |
| `CO2Saturation` | Named and typed, never defined | Simulated as bicarbonate ÷ total CO₂; documented as undefined in §9 |
| `TotalDensity` / `CDV` / `Density` | Three names for the cell-density channel in three adjacent tables | All three reproduced as published — see [§3.2](#32-the-three-lists-of-thirteen) |
| `CdvDilutionRatio` (command) vs `CellDensityDilutionRatio` (result) | Same quantity, two spellings | Both reproduced |
| `DP_GasCal->GasCal->GasCal->…` | Three levels of the same name | Reproduced |
| `QcResults` vs `QCResults` (pp. 9-18, 9-19) | Case flips between the offset tables and every other QC table | Treated as **`QCResults`** throughout; the majority spelling wins |
| Command bit clearing | Never stated | `COMMAND_AUTO_CLEAR`, default true |

Rule applied throughout, same as the Countess: **the browse name and description win over the
printed data type** where they conflict, since the name is what a client addresses.

---

## 12. Deviations in this simulator

Implemented in [`services/opcua-novaflex/`](../../services/opcua-novaflex/README.md).

| Real FLEX2 | Here | Why |
|---|---|---|
| `ns=3` tags, `ns=2` folders | one namespace | the manual publishes neither URI |
| Port 59888, path `/NovaBiomedical` | 4840 (host 4841), `/novaflex/` | compose convention; 4841 because the Countess already holds 4840 host-side |
| Basic256Sha256 Sign & Encrypt down to None | **None**, anonymous | a certificate exchange before the gateway will browse is a twenty-minute detour on stage. A production integration should use the vendor's strongest policy |
| Licensed per instrument; unlicensed = no tags | always on | |
| Bridge PC also serves OPC **DA** | UA only | DA is COM/DCOM on Windows and irrelevant to this demo |
| No counter, no state, no events, no result document | `ICC26Extensions` | [§6](#6-the-missing-trigger-and-icc26extensions). Separately named and self-documenting so it is never mistaken for vendor |
| Result values from real sensors | simulated fed-batch CHO culture | |
| Analysis takes minutes | 8 s (`RUN_DURATION_S`), QC 5 s | a demo waiting minutes for a result is minutes of dead air |
| `Units` as String tags only | unchanged — **no `EUInformation` added** | tempting, but adding engineering-unit properties to the vendor branch would make the simulator lie about the product. What a properly modelled server would do is [the Countess doc §9](countess-3fl-opcua-model.md#9-engineering-units) |

---

## 13. Simulator behavior

| Behavior | Value |
|---|---|
| Sample cycle | free-running every ~120 s, plus any vendor `*ScheduleAnalysis` bit |
| Run duration | `State = Running` for 8 s; QC 5 s |
| Write order | all result leaves → `State` → **`SampleCompleteCounter` last** |
| QC | every 6th free-running cycle, or any `*QcLevel*` bit. Writes `QCResults`, increments `QcCompleteCounter` **only** |
| Culture | fed-batch CHO: glucose 6.0 → 1.4 g/L, lactate 0.2 → 2.6 g/L, ammonia 0.9 → 5.8 mmol/L, viable density peaking ~12 ×10⁶/mL at 70 % of the run, viability 98 → 84 % |
| Osmometer | unfitted by default, so §8 is live on stage |
| Autosampler bank B | absent by default, ditto |
| Sensor errors | `SENSOR_ERROR_RATE` — one analyte goes `Bad` with `ErrorStatus` text and `Parameters-><a>->Alert` = `True`; **the sample still completes and still increments the counter**, because one bad sensor does not void a run |
| Whole-analysis failure | `FAILURE_RATE` or `InjectFailure` — "Dispense Timeout", `State = Error`, `SampleFailedEventType`, **counter untouched** |
| Consumables | `SamplesRemaining` / `FluidRemaining` decrement per analysis |

The two rows worth rehearsing are the last two but one: a *sensor* error still produces a sample,
a *dispense* failure produces none, and a counter-driven client gets both right without being
told which is which.
