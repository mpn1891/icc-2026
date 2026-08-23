# opcua-novaflex — pattern 3, the vendor half

A simulated **Nova Biomedical BioProfile FLEX2** bioanalyzer, served over OPC UA.

Unlike `opcua-countess`, this address space is **not ours**. The FLEX2 ships a real, licensed
OPC UA server on its Bridge PC, and this container reproduces what that server publishes, from
Section 9 of the vendor's OPC Server Instructions for Use Manual (LPN 60644B, 2024-03). Read
[`docs/reference/novaflex2-opcua-model.md`](../../docs/reference/novaflex2-opcua-model.md)
first — it carries the transcription, the provenance and the list of places the published table
contradicts itself.

```
opc.tcp://opcua-novaflex:4840/novaflex/     from inside the compose network (Ignition)
opc.tcp://localhost:4841/novaflex/          from the host (UaExpert, probe scripts)
```

4841 on the host because `opcua-countess` already holds 4840; both analyzers run at once.
Anonymous, no security policy — the real instrument offers Basic256Sha256 Sign & Encrypt and a
production integration should use it.

## Why this exists next to the Countess

| | `opcua-countess` | `opcua-novaflex` |
|---|---|---|
| Vendor OPC server | none — instrument writes CSV | **yes, this is its address space** |
| Shape | DI + LADS, as OPC UA intends | two flat trees of string-id tags |
| Completion signal | counter + events, designed in | **none. We had to add one** |
| Actions | method *and* command bit | **command bits only, no methods** |
| Refusal on a bad request | `Bad_InvalidState` from the method | nothing. The write returns Good either way |

The Countess is the model we wish vendors shipped. The FLEX2 is what they ship. Pattern 3 is
more convincing with both on stage than with either alone.

## The contract

Subscribe to **one** vendor node — `HistoricalSampleResults/SampleTime`. The simulator writes
it last, after every other historical leaf, so a tag-change is a settled result:

```
nsu=http://icc26.demo/UA/NovaflexII/;s=OPCSystemObjects->HistoricalSampleResults->SampleTime
```

Ignition: tag-change on `novaflex-01/result/sample_time` → Event Stream `03_opcua/novaflex-result`
→ Transmission to `icc26/site1/qc/analyzers/novaflex-01/result`.

**Pattern 4 (planned):** the same simulator will HTTPS POST that completed sample to an Ignition
Event Stream. Same topic, `mechanism=webhook`. See
[`docs/plans/04-novaflex-webhook.md`](../../docs/plans/04-novaflex-webhook.md). OPC UA is
pattern 3; the POST is pattern 4. They can run together.

`ICC26Extensions` is still in the address space (counter, state, ResultJson) so the missing
vendor trigger is visible on a browse. The MQTT path does not read it.

The vendor data itself lives under `OPCSystemObjects->HistoricalSampleResults->…` — the manual's
own recommendation over `SampleResults`, because the historical tree captures every analysis
regardless of how it was initiated.

Every leaf of one result carries the **same SourceTimestamp** — the acquisition instant, not the
write instant — so a set of reads is provably one sample.

## Triggering an analysis

**Nothing was invented for this.** The FLEX2's own command tags are the trigger. Write the
metadata, then write the bit:

```
OPCSystemCommands->ESMScheduleAnalysis->SampleInformation->SampleID     write first
OPCSystemCommands->ESMScheduleAnalysis->SampleInformation->BatchID      ...
OPCSystemCommands->ESMScheduleAnalysis->ESMScheduleAnalysis   := true   then this
```

`EXT_OLSScheduleAnalysis` and `AutosamplerScheduleAnalysis` work the same way and record a
different `SampleSource` on the result. `ESMTerminate` and friends abort a run in progress.
`ChemistryQcLevel1/2` and `GasQcLevel1/2` run an onboard control.

Three things about this that are the vendor's design, not ours, and are worth saying on stage:

- **Nothing is atomic.** The metadata is a series of separate writes. Two clients racing produce
  a spliced sample and neither is told.
- **There is no refusal.** Trigger during a run and the write still returns `Good`; all that
  happens is nothing. A method could have answered `Bad_InvalidState`.
- **`DueTime` blank or in the past means "now"** — and an unwritten `DateTime` tag is 1970.

The server clears the bit as soon as it accepts, making it a one-shot. Whether a real FLEX2 does
that is undocumented; set `COMMAND_AUTO_CLEAR=false` to find out the hard way.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPCUA_BIND_HOST` / `OPCUA_PORT` | `0.0.0.0` / `4840` | |
| `OPCUA_ENDPOINT_PATH` | `/novaflex/` | |
| `NODE_SEPARATOR` | `->` | Separator inside every string NodeId. See model doc § 1.1 — the manual is genuinely ambiguous here |
| `ANALYZER_ID` / `LOCATION` | `FLEX2-01` / `Site 1 / QC Lab` | `Settings->AnalyzerID` and `->Location` |
| `SOFTWARE_VERSION` / `SERIAL_NUMBER` | `4.3.1` / `FX2-2026-0119` | |
| `GAS_INSTALLED` / `CHEM_INSTALLED` / `CDV_INSTALLED` | `true` | Absent module → its results stay `Bad_NoData` |
| `OSMO_INSTALLED` | **`false`** | Deliberate. Keeps absent-vs-zero live on stage |
| `ESM_INSTALLED` / `AUTOSAMPLER_INSTALLED` | `true` | |
| `AUTOSAMPLER_BANK_B` | `false` | Ten RSM status tags at `Bad_NoData` |
| `RETAIN_COLLECTOR_INSTALLED` | `false` | Gates the three retain fields |
| `SAMPLE_TYPES` | `Default,CHO Fed-Batch,HEK Perfusion,Spent Media` | |
| `FIRST_SAMPLE_DELAY_S` | `15` | So a fresh container has data before you finish browsing to it |
| `SAMPLE_INTERVAL_S` | `120` | Free-running cycle |
| `QC_EVERY_N` | `6` | Every Nth free-running cycle runs QC instead. `0` disables |
| `SENSOR_ERROR_RATE` | `0.0` | Per-analyte. Sample still completes and still counts |
| `FAILURE_RATE` | `0.0` | Whole-analysis dispense timeout. Counter does **not** move |
| `COMMAND_AUTO_CLEAR` | `true` | Whether command bits are one-shot |
| `CULTURE_SPAN_SAMPLES` | `60` | Samples across the whole fed-batch trajectory |
| `VIABILITY_START` / `_END` | `98` / `84` | |
| `DENSITY_START` / `_PEAK` | `0.4` / `12.0` | ×10⁶ cells/mL |
| `GLUCOSE_START` / `_END`, `LACTATE_START` / `_END` | `6.0`/`1.4`, `0.2`/`2.6` | g/L |
| `RANDOM_SEED` | `0` (unseeded) | Set for a reproducible rehearsal |
| `LOG_LEVEL` / `ASYNCUA_LOG_LEVEL` | `INFO` / `WARNING` | asyncua logs every publish request at INFO |

**Not configurable:** how long an analysis takes — `RUN_DURATION_S` in `app.py`, fixed at 8 s
(QC 5 s). A real FLEX2 running the full panel takes several minutes.

## Deviations from the vendor

§12 of the model doc has the full table. The four that change what a client sees:

| Real FLEX2 | Here | Why |
|---|---|---|
| `ns=3` tags, `ns=2` folders | one namespace | the manual publishes neither namespace URI |
| Port 59888, path `/NovaBiomedical` | 4840, `/novaflex/` | compose convention |
| No counter, no state, no events, no result document | `ICC26Extensions` | there is otherwise nothing to drive a publish from |
| `Units` as String tags, no `EUInformation` | **unchanged** | adding engineering-unit properties would make this simulator lie about the product |

`ICC26Extensions` is a separate top-level object carrying a `README` variable that says it is not
vendor. A tag export from this simulator will outlive anyone's memory of which half was invented.

Two asyncua notes, both load-bearing and both non-obvious — same as the Countess:

- **A typed null Variant is not constructible** for numeric types; asyncua permits `None` only
  for Null, String, DateTime, ExtensionObject and ByteString. An absent field is therefore an
  untyped Null variant carrying `Bad_NoData`, which is what OPC UA means by "no value" anyway.
- **Event types need an explicit NodeId.** `create_custom_event_type()` auto-assigns one, so a
  client addressing `ns=2;i=3000` subscribes to a node that does not exist. The generators are
  also created once in `build()`, before serving — creating one is what sets the emitting node's
  `EventNotifier` bit.

## Verifying it

```bash
docker compose up -d --build opcua-novaflex
docker compose logs -f opcua-novaflex
```

Within `FIRST_SAMPLE_DELAY_S`:

```
watching 27 command bits
serving opc.tcp://0.0.0.0:4840/novaflex/ (namespace http://icc26.demo/UA/NovaflexII/, ns=2, separator '->')
modules: gas yes, chem yes, cdv yes, osmo NO -- 141 sample fields, 102 QC fields
analysis 1 (Manual) running for 8.0s
analysis 1 complete: 0.4 x10^6/mL viable, all sensors good -- counter=1
```

Then point a client at `opc.tcp://localhost:4841/novaflex/`. What to look at, in order:

1. **The vendor's own acceptance test** (manual §6): subscribe to
   `OPCSystemObjects->CoreHeartbeat->UpTime` and `->DateTime->DateTime` and watch them tick.
2. `HistoricalSampleResults->Chem->Gluc->Result` reads **Good**, `->Osmo->Result` reads
   **Bad_NoData**, and `->StartTags->ModuleInformation->Modules->Osmo` reads **Good False** — one
   result, one timestamp, three different kinds of "nothing." That is §8 of the model doc, live.
3. Every leaf of that tree shares one `SourceTimestamp`. 130 Good, 11 Bad, 1 distinct timestamp.
4. Write `OPCSystemCommands->ESMScheduleAnalysis->SampleInformation->SampleID`, then set
   `ESMScheduleAnalysis` true. The bit clears itself, `State` goes to 1 for 8 s, then
   `SampleCompleteCounter` steps once and your `SampleID` appears on the result.
5. Set `GasQcLevel1` true. `QcCompleteCounter` steps; **`SampleCompleteCounter` does not.**
   A client watching only sample results never sees the control run.
6. Set `ICC26Extensions->InjectFailure` true. `State` goes to 4, `SampleFailedEventType` fires,
   and the **counter does not move**.

Steps 2–6 are exactly what the throwaway `asyncua` probe checked before Ignition was pointed at
this: 26 assertions, all green, 911 nodes across the three trees.

## Ignition side

1. **OPC UA client connection** `bioanalyzer` → `opc.tcp://opcua-novaflex:4840/novaflex/`, no
   security, anonymous. **Done**, and authored as files rather than through the UI —
   `opc-connection/bioanalyzer/` is a copy of `cell_analyzer/` with the two endpoint URLs
   changed, a fresh `uuid`, and `lastModificationSignature` **removed** from `resource.json`.
   The gateway accepts it and picks it up on `python tasks.py scan`; no restart needed.

   That is a useful exception to the master plan's UI-then-commit rule: an OPC connection is
   copyable because the only per-connection secret, `keyStoreAliasPassword`, is Embedded
   ciphertext valid for the whole gateway. Drop the signature rather than trying to forge it —
   the resource genuinely was modified externally, and the gateway is fine being told so.
2. **`bioanalyzer` UDT + `novaflex-01` instance** — written as files, see below.
3. **MQTT publish** — tag-change on `result/sample_time` (vendor `HistoricalSampleResults/SampleTime`)
   hands the result folder to Event Stream `03_opcua/novaflex-result`. Transform
   `opcua_event.build_novaflex_result` reads the historical UDT siblings and Transmission
   publishes to `icc26/site1/qc/analyzers/novaflex-01/result`, `meta.mechanism = "opcua-event"`.
   Does not use `ICC26Extensions`.

### The UDT

`bioanalyzer` (in `tag-type-definition/default/udts.json`), instantiated as `novaflex-01` at
`[default]icc26/site1/qc/analyzers/novaflex-01`.

A **second type**, not a widened `cell_analyzer`: the frame is identical but the measurements are
completely different, and two honest types beat one type with half its members permanently Bad.
Factor only if a third analyzer appears.

| Parameter | Default | Purpose |
|---|---|---|
| `device_id` | `FLEX2-01` | **Not used in a single binding.** The FLEX2 puts no device identity in its NodeIds — every path starts at `OPCSystemObjects`, so one server serves exactly one analyzer. Kept because the publish script needs a `source.id` |
| `namespace_uri` | `http://icc26.demo/UA/NovaflexII/` | Declared once on the type |
| `node_sep` | `->` | Matches `NODE_SEPARATOR` on the container. If a real FLEX2 uses dots, change it in **two** places and nothing else |
| `opc_server` | `bioanalyzer` | The gateway's OPC UA connection name |
| `uns_path` | *(none — set per instance)* | Where this instance publishes |

`node_sep` as a parameter is the point: the one genuine unknown in the whole model is confined to
a single UDT parameter and a single environment variable.

57 OPC tags — the 12 analyte results, cell density, calculated results, sample metadata, the
module-used flags, the extension counters and state, and nine ESM command tags. All 57 verified
to resolve against a running server before Ignition was pointed at it; 56 read Good and the one
Bad is `result/osmo`, which is the point.

Per-analyte `ErrorStatus` is deliberately **not** tagged: a failed sensor already shows as **Bad
quality on the result tag itself**, and `result/errors` carries the text. The quality *is* the
error signal — duplicating it into a String tag would teach the opposite lesson.

The 52 `Ranges` leaves are also **not** tags: constant across a sample type, and 52
subscriptions that never change is what §10 of the model doc argues against. They remain
browsable on the server.

To run an analysis from the tag browser: set `command/sample_id` and `command/batch_id`, then
write `true` to `command/esm_schedule_analysis`.

### Two things Ignition 8.3 requires, learned on the Countess

Both produce `Error_Configuration` on every tag and **neither logs anything** gateway-side:

- **Address by namespace URI, not index** — `nsu={namespace_uri};s=<node>`. Doubly true here: the
  FLEX2 manual itself shows the index moving between server versions.
- **A property containing a `{parameter}` must be an object, not a string:**
  `{"bindType": "parameter", "binding": "..."}`. A plain string is taken literally.

Diagnose by setting `NOVAFLEX_ASYNCUA_LOG_LEVEL=INFO` and grepping the container log for
`create monitored items` — a tag that bound names its node there, a tag that faulted is absent.
