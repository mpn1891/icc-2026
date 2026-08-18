# 03 — OPC UA analyzer: how the Countess was built, and how to build the next one

> Supersedes the pattern-3 sketch in [`00-master-plan.md`](00-master-plan.md). Written after
> building `services/opcua-countess` end to end, so it is a record of what actually worked
> rather than a plan. The gotcha tables at the bottom are the point of this document — they
> cost most of the build time and every one of them will repeat verbatim on the next analyzer.

**Built:** simulated Thermo Fisher Countess 3 FL cell counter → OPC UA → Ignition UDT, and then
a simulated Nova Biomedical BioProfile FLEX2 the same way.
**Not built yet:** the tag-change script that publishes to MQTT (step 9), for either analyzer.

| Artifact | Countess | Novaflex |
|---|---|---|
| Information model spec | [`countess-3fl-opcua-model.md`](../reference/countess-3fl-opcua-model.md) | [`novaflex2-opcua-model.md`](../reference/novaflex2-opcua-model.md) |
| Server | [`services/opcua-countess/`](../../services/opcua-countess/README.md) | [`services/opcua-novaflex/`](../../services/opcua-novaflex/README.md) |
| Compose service | `opcua-countess` | `opcua-novaflex` |
| Ignition connection | `opc-connection/cell_analyzer/` (UI) | `opc-connection/bioanalyzer/` (**copied as files**, see step 6) |
| UDT type | `tag-type-definition/default/udts.json` → `cell_analyzer` | → `bioanalyzer` |
| UDT instance | `tag-definition/default/icc26/site1/qc/analyzers/udts.json` → `countess-01` | → `novaflex-01` |

The two are not alternatives. The Countess has no vendor OPC server, so its address space is the
one we would design; the FLEX2 has one, so its address space is the one a vendor actually ships.
See [What actually happened with the Novaflex](#what-actually-happened-with-the-novaflex--built).

---

## The order that worked

The single most useful sequencing decision: **prove the server with a Python client before
letting Ignition anywhere near it.** Every failure then has exactly one candidate cause. Going
the other way means debugging your address space and your gateway config simultaneously.

### 1 — Source the data model

For the Countess: Appendix E of the vendor user guide, 71 CSV columns. The PDF needed
extracting (`pip install pypdf`; the Read tool's PDF path wants poppler, which is not installed
here). Do not skip reading the whole appendix — the source table had **eight copy-paste errors**
(a description column slipped one row), all documented in §11 of the model doc.

Rule applied where a column name and its description disagreed: **the name wins**, because the
name is what appears in the CSV header and is what a parser keys on.

### 2 — Write the information model spec first

Before any code. `docs/reference/<device>-opcua-model.md` carries:

- **Provenance** — say plainly that the vendor ships no OPC UA server and this is a demo model.
- Namespace URI, NodeId allocation, address space tree.
- DataTypes (enums, structures), ObjectTypes with modelling rules.
- The **trigger contract** (counter + event), engineering units, absent-vs-zero semantics.
- The **MQTT projection** — including which fields you deliberately do *not* publish.
- A **deviations table** for where the implementation knowingly falls short.

Writing the spec first is what made the 71-field mapping tractable; the code then became a
transcription rather than a design exercise.

### 3 — Build the server

`services/opcua-<device>/{app.py,Dockerfile,requirements.txt,README.md}`. Only dependency is
`asyncua>=1.1,<2`. Copy the house style from `sim-valve-mqtt`: `_env`/`_env_float`/`_env_bool`
helpers, a `Config` class, docstrings that explain *why*.

**The one structural idea worth copying:** put the field mapping in a module-level table and
build both the address space and every write from it.

```python
# (browse path, variant type, EU key, source column)
RESULT_FIELDS = [
    ("Sample/TotalConcentration", V.Double, CONC, "I"),
    ...
]
```

The address space becomes a loop, a whole result becomes `dict[path] -> value`, and the table
diffs against the spec line for line. Paths absent from the dict are cleared to `Bad_NoData`,
which is how absent-vs-zero stays correct by construction instead of by remembering.

Also worth copying:

- **Counter written last**, after every result field and after `State`.
- **One `SourceTimestamp` for the whole result** — the acquisition instant, not write time. This
  is what proves a set of reads is one sample.
- **A `ResultJson` String node** carrying the whole result. Ignition cannot decode custom OPC UA
  structures into tags, so this is the practical substitute for a structured DataType — and it
  is the node the publish script should read, because it cannot tear across two samples.
- **A `Command/StartRequest` Boolean** beside the `StartCount` method (see step 8).

### 4 — Compose

`build: ./services/<name>`, `container_name: icc26-<name>`, `restart: unless-stopped`, a TCP
healthcheck, and an `ASYNCUA_LOG_LEVEL` knob defaulting to `WARNING` (step 7 explains why).

### 5 — Verify the server with a Python probe

Before Ignition. A throwaway `asyncua.Client` script that browses the result branch, reads
statuses, calls the methods, subscribes to the counter and events. This caught four real bugs
(all in the gotcha table) in one pass.

### 6 — Ignition OPC UA connection

Gateway UI → OPC UA → Connections, `opc.tcp://<service>:4840/<path>/`, security **None**,
**Anonymous**. Then commit whatever `git status` reveals under
`ignition/config/resources/core/ignition/opc-connection/<name>/` — the UI-then-commit rule from
the master plan.

**For the second and later analyzers, skip the UI — copy the first connection's directory.**
Proven on `bioanalyzer`: copy `cell_analyzer/`, change the two endpoint URLs in `config.json`,
give `resource.json` a fresh `uuid`, and **delete `lastModificationSignature`**. `tasks.py scan`
picks it up with no restart. The signature cannot be recomputed outside the gateway, and
removing it is honest — the resource really was modified externally. The only per-connection
secret, `keyStoreAliasPassword`, is Embedded ciphertext valid gateway-wide, so it copies.

### 7 — UDT type + instance, as files

Tags are a known file format, so author them directly and `python tasks.py scan`. Type goes in
`tag-type-definition/default/udts.json`, instance in
`tag-definition/default/<uns path>/udts.json`, and **every folder in the path needs its own
`unary-resource.json`**.

Parameterize everything — `device_id`, `namespace_uri`, `opc_server`, `uns_path` — so the type
names no gateway, server or namespace literally:

```json
"opcItemPath": {
  "bindType": "parameter",
  "binding": "nsu={namespace_uri};s={device_id}/CellCounter/State"
},
"opcServer": { "bindType": "parameter", "binding": "{opc_server}" }
```

Then verify binding **server-side**, because the gateway will not tell you:

```bash
COUNTESS_ASYNCUA_LOG_LEVEL=INFO docker compose up -d opcua-countess
python tasks.py scan
docker logs icc26-opcua-countess 2>&1 | grep -c "subscribe to datachange"
```

A tag that bound appears as a `create monitored items` request naming its node; a tag that
faulted is simply absent. Diff that set against the paths in the UDT JSON — expected vs
monitored, both counts equal and no missing entries, or it is not done.

### 8 — The command trigger

A SCADA tag write **cannot invoke an OPC UA method**, so `StartCount(...)` is unreachable from a
plain tag. Add a writable Boolean the tag can drive, and have the server subscribe to its own
node:

```python
self.command_sub = await self.server.create_subscription(200, _CommandHandler(self))
await self.command_sub.subscribe_data_change(self.unit_nodes["StartRequest"].node)
```

The handler dispatches to `asyncio.create_task(...)` rather than awaiting inline — writing to a
node from inside a subscription callback re-enters the service currently delivering the
notification. Clear the bit *before* starting the work, so it is a genuine one-shot and a client
that never resets it still gets exactly one run per write.

Keep the method too. The method is better engineering — atomic, typed, returns an id and a real
status code — and the contrast between the two is a talk point, not an embarrassment.

### 9 — Still to do: publish to MQTT

Tag-change gateway event script on `count_completed_counter` → read `result_json` → one
`system.cirruslink.transmission.publish(...)` per increment, `meta.mechanism = "opcua-event"`.
Transmission, not Engine, per the ACL split.

---

## asyncua gotchas

All of these are silent or misleading failures.

| Symptom | Cause | Fix |
|---|---|---|
| `UaError: Non array Variant of type 7 cannot have value None` | asyncua allows a null value only for Null, String, DateTime, ExtensionObject, ByteString variants | Use an **untyped** `ua.Variant(None, V.Null)` for absent values; the node's DataType attribute still says what belongs there |
| `FrozenInstanceError` setting `dv.SourceTimestamp` | `ua.DataValue` is a frozen dataclass | Construct it fully: `ua.DataValue(Value=..., StatusCode_=..., SourceTimestamp=...)`. Note the field is `StatusCode_`, with the underscore |
| Method returns `BadUnexpectedError` instead of your status | `MethodService._call` catches **every** exception and flattens it | **Return** `ua.StatusCode(...)` — do not `raise UaStatusCodeError` |
| Client subscribes to your event type and receives nothing | `create_custom_event_type` auto-assigns the NodeId, so a client addressing a guessed id subscribes to a node that does not exist | Build the type with an explicit NodeId (`base.add_object_type(ua.NodeId(3000, idx), ...)` plus `add_property` per field) |
| Events still not delivered | Creating the `EventGenerator` is what sets the emitting node's `EventNotifier` bit; a client that subscribed earlier matched nothing | Create generators in `build()`, once, before serving — not per trigger |
| `ServerDiagnosticsSummary` all `None` | asyncua does not populate it | Use the logs as the oracle, not diagnostics nodes |

## Ignition 8.3 gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Every tag reads `Error_Configuration`, **nothing in the gateway log** | Address used `ns=<index>` or a parameter did not substitute | See the two rows below. There is no log line — the server is the only witness |
| — | 8.3 addresses by namespace **URI** | `nsu=<uri>;s=<node>`, which is what the gateway's own drag-and-drop emits. The index is assigned at server startup by registration order and can drift |
| — | A plain string containing `{param}` is taken **literally** | Wrap it: `{"bindType": "parameter", "binding": "..."}`. Applies to `opcItemPath`, `opcServer`, and memory-tag `value` alike — it is the file format's rule, not an OPC quirk |
| `tasks.py scan` → 401, but the API token is correct | The key authenticates but lacks **write** permission; Ignition answers 401, not 403 | Platform → Security → API Keys → grant Gateway read/write. Test with a GET route (`/data/api/v1/trial`) to confirm the token itself is fine |
| **Tags missing in Designer, or present but never updating; the OPC server logs `Session timed out after 120s of inactivity`** | **The two-hour trial lapsed.** An expired gateway stops executing: subscriptions are torn down, tags stop, and the session goes idle. It looks exactly like "the tag definitions never loaded," and it will mislead you into rebuilding config that was fine | `GET /data/api/v1/trial` → `trialSecondsLeft`. **Check this first, before diagnosing anything tag-related.** Reset the trial in the gateway UI. An idle-timing-out OPC session is the tell: a session with live monitored items sends Publish requests continuously and can never idle out |
| Tag values lag the server by ~1 s | The tag group's subscription rate, default 1000 ms | Gateway-side setting; nothing to do with the server |

## Verification gotchas on Windows

Cost real time, produced confidently wrong answers twice:

- **`docker logs` writes to stderr.** `capture_output=True` then reading only `stdout` reports
  zero matches. Combine both streams.
- **CRLF.** `docker logs` output and Python-on-Windows stdout both carry `\r`; `comm` then treats
  two identical lists as completely disjoint. Do set comparisons inside one language.

---

## What actually happened with the Novaflex — **built**

This section was a forecast. It is now a record, and the forecast was wrong in the one way that
mattered: **the Novaflex has a real vendor OPC UA server, and its tag list is published.** The
BioProfile FLEX2 OPC Server Instructions for Use manual (LPN 60644B) documents ~400 tags in
Section 9. So steps 1 and 2 did not collapse — they got bigger, and the model doc got longer
than the Countess's rather than shorter.

| | Countess | Novaflex (built) | Forecast said |
|---|---|---|---|
| Directory | `services/opcua-countess` | `services/opcua-novaflex` | ✓ |
| Endpoint | `…/countess/` | `…/novaflex/` | ✓ |
| Host port | 4840 | **4841** | ✓ |
| Namespace URI | `…/UA/Countess3FL/` | `…/UA/NovaflexII/` | ✓ |
| Cycle | 5 s run, 180 s | 8 s run, 120 s | ✓ |
| Topic | `…/countess-01/result` | `…/novaflex-01/result` | ✓ |
| **Data model source** | vendor Appendix E, 71 columns | **vendor OPC manual §9, ~400 tags** | ✗ "none — the analyte list *is* the spec" |
| **Address space** | ours, DI + LADS | **Nova's**, flat `OPCSystemObjects` / `OPCSystemCommands` | ✗ assumed same shape |
| **Trigger node** | `CountCompletedCounter` | `ICC26Extensions->SampleCompleteCounter` | ✗ vendor has **no counter at all** |
| **Command bit** | ours, added in step 8 | **the vendor's own** `ESMScheduleAnalysis` | ✗ assumed we'd add one |
| Ignition UDT | `cell_analyzer`, 44 tags | `bioanalyzer`, 57 tags | ✓ two types |
| Size | 71 fields | 141 sample + 102 QC leaves, 911 nodes | ✗ "fewer fields" |

The three decisions, resolved:

1. **Join, not replace.** Two OPC UA analyzers is not two patterns, but this pair is one
   argument: the Countess is the model we wish vendors shipped, the FLEX2 is what they ship.
   The second earns its stage time by having no completion counter.
2. **Two UDTs.** As predicted — `cell_analyzer` and `bioanalyzer`.
3. **Command bit: didn't need one.** The FLEX2 has 104 writable bits and **zero methods**, so
   the stage trigger is the instrument's own. §6.1 of the Countess model doc argued command bits
   are what ships because a SCADA tag cannot invoke a method; this is that argument confirmed by
   a 2024 vendor product.

### What generalises to the next analyzer

- **Look for a vendor OPC manual before assuming there isn't one.** The Countess experience made
  "no vendor server, so we model it" feel like the default. It is not. One PDF changed the
  entire shape of this build, and it was already sitting in `docs/reference/`.
- **When the vendor's model exists, reproduce it — warts and all.** `DP_GasCal->GasCal->GasCal`,
  three different names for the cell-density channel, a unit-of-measure typed `Single`. Tidying
  these would produce a simulator that lies about the product.
- **Put your additions in a separate, self-documenting branch.** `ICC26Extensions` has a
  `README` variable inside the address space. A tag export outlives the memory of who added what.
- **Make the one genuine unknown a knob.** The manual is ambiguous about whether the NodeId
  separator is `->` or `.`; that is one env var and one UDT parameter, not a field-table rewrite.
- **The absent-vs-zero case may already be the vendor's.** The FLEX2 publishes
  `Modules->{CDV,Chemistry,Gas,Osmo}` Booleans per analysis. No invention needed.

Reused as-is from the Countess, exactly as predicted: the `Leaf`/`_good`/`_absent` write helpers,
the field-table pattern (generalised into `_add_branch`), `_CommandHandler`, `_nest`, the
Dockerfile, and the compose block. Every gotcha table below applied unchanged and cost no time
the second run.

Still TODO on the Novaflex, and identical to the Countess's step 9: the OPC connection must be
created in the gateway UI (`bioanalyzer` → `opc.tcp://opcua-novaflex:4840/novaflex/`, no
security, anonymous), and the tag-change publish script does not exist yet.
