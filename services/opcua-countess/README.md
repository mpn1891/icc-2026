# opcua-countess — pattern 3 source

A simulated Thermo Fisher Countess 3 FL automated cell counter, served over OPC UA.

The address space implements
[`docs/reference/countess-3fl-opcua-model.md`](../../docs/reference/countess-3fl-opcua-model.md),
which maps all 71 columns of Appendix E of the instrument's user guide (MAN0019567) into an
information model. Read that first — it carries the reasoning and the provenance note. **The
real instrument has no OPC UA server**; it writes CSV to USB, SMB or Thermo Fisher Connect.
Nothing here is a vendor artifact.

```
opc.tcp://opcua-countess:4840/countess/     from inside the compose network (Ignition)
opc.tcp://localhost:4840/countess/          from the host (UaExpert, probe scripts)
```

Anonymous, no security policy. A certificate exchange before the gateway will browse is a
twenty-minute detour nobody watching a demo wants to sit through.

## The contract

Subscribe to **one** node:

```
ns=2;s=Countess-01/CellCounter/CountCompletedCounter        UInt32, monotonic
```

It is written **last**, after all 71 result fields and after `State`. When it changes, read the
`LastResult` branch — or just `LastResult/ResultJson` — and publish once. A client that
subscribes to the leaves instead has to guess when a count is finished.

```
ns=2;s=Countess-01/CellCounter/ResultSet/LastResult         the Appendix E row, as an object
ns=2;s=Countess-01/CellCounter/ResultSet/LastResult/ResultJson   the same row, one string
ns=2;s=Countess-01/CellCounter/State                        0 Idle 1 Running 2 Completed
                                                            3 Aborted 4 Error
ns=2;i=3000  CountCompletedEventType   ns=2;i=3001  CountFailedEventType
```

Every leaf of a result carries the **same SourceTimestamp** — the acquisition instant, not the
write instant — so a set of reads is provably one count rather than a plausible mixture of two.

A **failed count raises `CountFailedEventType` and does not increment the counter.** A
counter-driven client correctly sees nothing; a client polling `LastResult` on a timer
republishes the previous count as though it were new. `InjectFailure` produces that on cue.

## Triggering a new analysis

Two ways, on purpose.

**From a tag** — write `true` to the command bit. This is what the Ignition UDT uses:

```
nsu=http://icc26.demo/UA/Countess3FL/;s=Countess-01/CellCounter/Command/SampleName    write first
nsu=http://icc26.demo/UA/Countess3FL/;s=Countess-01/CellCounter/Command/StartRequest  then true
```

The server subscribes to its own `StartRequest` node and clears it back to `false` as soon as
it accepts the request — a one-shot, so a client that never resets the bit still gets exactly
one count per write. A write while `State = Running` is ignored and logged; nothing queues.
Empty `SampleName` auto-names the count `SAMPLE-nnnn`.

**From a method** — `StartCount` below, which returns the `CountId` and a real status code.

The method is better engineering and the bit is what ships: a SCADA tag write cannot invoke an
OPC UA method, so a tag-only client has no way to call `StartCount` without a script behind it.
§6.1 of the model doc has the comparison.

## Methods

On `ns=2;s=Countess-01`:

| Method | Signature | Notes |
|---|---|---|
| `StartCount` | `(SampleName: String, ProtocolName: String) → CountId: UInt32` | Empty `ProtocolName` keeps the active one. `Bad_InvalidState` while running, `Bad_NotFound` for an unknown protocol |
| `AbortCount` | `() → ()` | `Bad_InvalidState` unless running. No counter increment |
| `InjectFailure` | `() → ()` | Demo-only. The real instrument has no such button — say so on stage |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OPCUA_BIND_HOST` / `OPCUA_PORT` | `0.0.0.0` / `4840` | |
| `OPCUA_ENDPOINT_PATH` | `/countess/` | |
| `DEVICE_ID` | `Countess-01` | Root of every string NodeId |
| `CUBE1_INSTALLED` / `CUBE2_INSTALLED` | `true` / `false` | Empty slot → that cube's result fields stay `Bad_NoData` |
| `CUBE1_NAME` / `CUBE2_NAME` | `GFP` / `RFP` | EVOS light cube in the slot |
| `PROTOCOLS` | `CHO viability,HEK GFP transfection,PBMC viability` | What `StartCount` accepts |
| `FIRST_COUNT_DELAY_S` | `15` | So a fresh container has data before you finish browsing to it |
| `COUNT_INTERVAL_S` | `180` | Free-running cycle |
| `BF_ONLY_EVERY_N` | `4` | Every Nth count is brightfield-only despite a fitted cube. `0` disables |
| `FAILURE_RATE` | `0.0` | Probability a count fails on its own |
| `RESULT_HISTORY` | `10` | `ResultSet` ring size. `0` keeps only `LastResult` |
| `VIABILITY_START` / `_END` / `_SPAN_COUNTS` | `96` / `88` / `40` | Session-long decline, so the trend is worth charting |
| `CONCENTRATION_START` | `1.2e6` | Random walk inside Appendix B's 1e4–1e7 cells/mL |
| `RANDOM_SEED` | `0` (unseeded) | Set for a reproducible rehearsal |
| `LOG_LEVEL` / `ASYNCUA_LOG_LEVEL` | `INFO` / `WARNING` | asyncua logs every publish request at INFO |

**Not configurable:** how long a count takes. That is `RUN_DURATION_S` in `app.py`, fixed at
**5 s**, and it is the only artificial delay in the server — trigger to result measures ~5.1 s,
the extra 100 ms being the command subscription's polling interval. The real instrument takes
under 30 s (Appendix B, p. 85); 5 keeps the demo moving.

## Deviations from the model doc

The spec is the target; these are where the implementation knowingly falls short of it, and why.
None of them change the contract above.

| Model doc | Here | Why |
|---|---|---|
| §3.2 `CountResultDataType` and the other structures, with Binary/JSON encodings | `LastResult/ResultJson`, a `String` | Ignition cannot decode a custom structure into tags, and asyncua's structure support wants a generated type dictionary neither side would read. The property that matters survives: one read, one timestamp, no tearing across 71 leaves |
| §4.3 `AsDataType` | renamed `ResultJson` | Same node, honest name |
| §5 event field `Result : CountResultDataType` | `ResultJson : String` | As above |
| §2 `DeviceSet` / `Identification` as DI (OPC 10000-100) nodes | Same browse names, this namespace | Loading `Opc.Ua.Di.NodeSet2.xml` would make them genuine ns=DI nodes — one `server.import_xml()` if it ever matters |
| §4 `AnalogUnitType` / `AnalogItemType` type definitions | `BaseDataVariableType` carrying `EngineeringUnits` and `EURange` properties | asyncua's `add_variable` takes no type definition. The properties are what a client reads either way |
| §4.2 `ProgramManager` with `ProgramTemplateSet` | `ProtocolSet` of name-only objects | Nothing in the demo reads a protocol's contents |
| §7.4 `Cube 1+2` present whenever cube 1 is | Absent unless **both** cubes are fitted | The union of one channel is that channel; reporting it under a "1+2" name invites the reader to think two channels were used |
| §6 methods marked Optional | Always present | They are the stage trigger |

Two further notes on asyncua itself, both load-bearing and both non-obvious:

- **A method that raises `UaStatusCodeError` returns `BadUnexpectedError` to the caller.**
  asyncua's `MethodService` catches every exception and flattens it. Returning a
  `ua.StatusCode` is the path it honours, so the methods here return rather than raise.
- **A typed null Variant is not constructible** for numeric types — asyncua permits `None` only
  for Null, String, DateTime, ExtensionObject and ByteString. An absent field is therefore an
  untyped Null variant carrying `Bad_NoData`, which is what OPC UA means by "no value" anyway;
  the variable's DataType attribute still says what would be there.

## Verifying it

```bash
docker compose up -d --build opcua-countess
docker compose logs -f opcua-countess
```

Within `FIRST_COUNT_DELAY_S` you should see:

```
serving opc.tcp://0.0.0.0:4840/countess/ (namespace http://icc26.demo/UA/Countess3FL/, ns=2)
cube 1 installed (GFP), cube 2 empty (RFP), 71 result fields
count 1 (SAMPLE-0001) running for 24.3s
count 1 complete: 1478 cells, 95.8% viable, FL -- counter=1
```

Then point any OPC UA client at `opc.tcp://localhost:4840/countess/` and browse
`Objects → DeviceSet → Countess-01 → FunctionalUnitSet → CellCounter → ResultSet → LastResult`.

What to look at, in order:

1. `Fluorescence/Cube1/*` reads Good; `Fluorescence/Cube2/*` and `Fluorescence/Combined/*` read
   **Bad_NoData**, in the same result, at the same timestamp. That is §8 of the model doc, live.
2. Every leaf shares one SourceTimestamp.
3. Call `StartCount("DEMO-1", "")` on `Countess-01` and watch `CountCompletedCounter` step once.
4. Call `InjectFailure`, then `StartCount` — `State` goes to `4` (Error), an event fires, and the
   **counter does not move**.
5. Write `FunctionSet/Cube1/LightIntensity`, run a count, and read
   `LastResult/Settings/Illumination/Cube1LightIntensity` — the result froze the new value.

## Ignition side

1. **OPC UA client connection** `cell_analyzer` → `opc.tcp://opcua-countess:4840/countess/`,
   no security, anonymous. Created in the UI, committed under
   `ignition/config/resources/core/ignition/opc-connection/cell_analyzer/`.
2. **`cell_analyzer` UDT + `countess-01` instance** — done, see below.
3. **Tag-change gateway event script** on `count_completed_counter` — not built yet. It reads
   the `result` branch (or `result_json`) and publishes once, via Transmission, not Engine, per
   the ACL split in the master plan:
   `system.cirruslink.transmission.publish("chariot_broker", topic, payload, 0, False)` to
   `icc26/site1/qc/analyzers/countess-01/result`, `meta.mechanism = "opcua-event"`.

The payload projection — including which of the 71 fields to publish and why the 38 settings
fields are not among them — is §10 of the model doc.

### The UDT

`cell_analyzer` (in `tag-type-definition/default/udts.json`), instantiated as `countess-01` at
`[default]icc26/site1/qc/analyzers/countess-01`.

| Parameter | Default | Purpose |
|---|---|---|
| `device_id` | `Countess-01` | First segment of every NodeId — matches `DEVICE_ID` on the container |
| `namespace_uri` | `http://icc26.demo/UA/Countess3FL/` | The information model's namespace. Declared once on the type instead of repeated in 42 bindings |
| `opc_server` | `cell_analyzer` | The gateway's OPC UA connection name |
| `uns_path` | *(none — set per instance)* | Where this instance publishes; used by the tag-change script |

Every address is built from parameters — nothing in the type names a gateway, a server or a
namespace URI literally:

```
nsu={namespace_uri};s={device_id}/CellCounter/ResultSet/LastResult/Sample/TotalConcentration
```

`namespace_uri` carries its default on the **type**, not the instance: the URI identifies the
information model, which is identical for every Countess. An instance can still override it if
a server ever publishes the model under a different URI.

44 OPC tags: the 33 non-settings columns under `result/`, plus `count_completed_counter`,
`state`, `session_id`, `protocol_name`, `result_json`, five writable live settings under
`optics/`, and two under `command/`. The 38 as-run gate and illumination columns are
deliberately **not** tags — they are constant across a protocol, and 38 subscriptions that
never change is exactly what §10 of the model doc argues against. They remain browsable on the
server.

To run a fresh analysis from the tag browser: set `command/sample_name`, then write `true` to
`command/start_request`. It flips back to `false` on its own, `state` goes to 1 (Running) for
5 s, then `count_completed_counter` steps and the whole `result/` branch updates at once.

`protocol_name` (live, writable) and `result/protocol/name` (as-run) are both present on
purpose; they diverge the moment somebody changes protocol between counts.

### Two things Ignition 8.3 requires, learned the hard way

Both produce `Error_Configuration` on every tag, and **neither logs anything** — the gateway
sets the quality and stays silent. Diagnosed by turning up `ASYNCUA_LOG_LEVEL=INFO` on this
container and watching which nodes appeared in `create monitored items` requests: a tag that
binds shows up there, a tag that does not is simply absent.

- **Address by namespace URI, not index.** `nsu={namespace_uri};s=<node>`. This is what the
  gateway's own drag-and-drop emits. `ns=2;s=<node>` also resolves today, but the index is
  assigned at server startup by registration order — the URI cannot drift.
- **A property containing a `{parameter}` reference must be an object, not a string:**

  ```json
  "opcItemPath": {
    "bindType": "parameter",
    "binding": "nsu={namespace_uri};s={device_id}/CellCounter/State"
  }
  ```

  A plain `"opcItemPath": "...{device_id}..."` is taken **literally** — the braces are not
  substituted, the node is not found, and the tag faults. This applies to `opcServer` too. The
  same shape already appears in the `vibration_sensor` UDT for parameter-bound memory values;
  it is the general rule for this file format, not an OPC quirk.
