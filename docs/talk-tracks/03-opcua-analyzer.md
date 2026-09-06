# 03 — OPC UA → MQTT: the instrument nobody may touch

> Talk track for pattern 3. The spec this was built from is
> [`plans/03-opcua-analyzer-playbook.md`](../plans/03-opcua-analyzer-playbook.md). Architecture
> decisions live in [`00-architecture.md`](../00-architecture.md); this file is what you speak.
>
> **Written 2026-09-06**, from a build finished on 2026-08-20 and rebuilt around a sample-login
> screen on 2026-08-26. Every number below comes out of the spec's own record of what was
> measured.

| | |
|---|---|
| **Pattern** | 3 of 7 — OPC UA read, Event Stream relay, MQTT publish |
| **Mechanism tag** | **none.** This pattern publishes `ts` and `values` and nothing else — talk point 5 |
| **Container** | `opcua-cell-analyzer` — [`services/opcua-cell-analyzer/`](../../services/opcua-cell-analyzer/) |
| **Sample login** | <http://localhost:8087> — the instrument's own screen |
| **Depends on** | pattern 1, for the sample id a person types in |
| **Blocks** | pattern 4 (the LIMS appends this result to the open entry), and pattern 7 through it |
| **Signal contributed** | the analytes — and the acquisition instant everything downstream is measured against |
| **GxP hook** | A qualified system read by a platform, one way. The instrument is not modified, not reconfigured, and not asked for permission |

## The segment

**Intro.** A cell analyzer on the bench beside `BR-201`, addressed as `cell-analyzer-01`. It is
qualified, it is somebody's validated instrument, and **nothing in this demo is allowed to change
it.** It ships a licensed OPC UA server with about 400 documented tags — and no methods at all.

**Demo.** Read the sample id off the valve's page, type it into the instrument's own screen at
:8087, press Run. Seconds later one message lands on
`icc26/site1/qc/analyzers/cell-analyzer-01/result`.

**Risk.** Nothing in the vendor's model says *"a run finished."* The bridge infers it from a
timestamp changing value, and everything downstream rests on that inference being right.

**Close.** *(unassigned — see the master plan's open items)*

## Talk points

**1. The address space is the vendor's, warts and all.** `DP_GasCal->GasCal->GasCal`. Three
different names for the cell-density channel. A unit-of-measure typed `Single`. Two flat trees —
`OPCSystemObjects` and `OPCSystemCommands` — of string-id nodes, where the Countess model we
designed is a tidy DI/LADS hierarchy. **Tidying any of it would produce a simulator that lies
about the product.** 911 nodes in the server, 141 sample leaves and 102 QC leaves; the Ignition
UDT binds 57 tags out of it, and every one was verified monitored server-side — because the
gateway will not tell you which ones bound.

**2. There is no completion signal, and that is the finding.** No counter. No event. No method.
The only thing that says a run finished is `HistoricalSampleResults/SampleTime` **changing
value**, so the trigger is a tag-change script on a timestamp — an inference, not a
notification. The simulator writes `SampleTime` last on the historical tree for exactly that
reason, which is the ordering guarantee a real instrument does not owe you.

> `ICC26Extensions->SampleCompleteCounter` **is** in the address space, and the publish path
> deliberately does not read it. It is our addition, in a branch that documents itself, kept
> visible so that the absence is visible. Point at it and say: this is the node the vendor did
> not ship, and this is what we would have used.

**3. The Countess is the contrast, and it is now a sentence rather than a second instrument.**
The Countess has no vendor OPC UA server, so its address space is the one *we* would design:
a completion counter, real events, and a `StartCount` method that can return `Bad_InvalidState`
when it is busy. The analyzer is what vendors actually ship — **104 writable command bits and
zero methods.** §6.1 of the Countess model doc argued that command bits are what ships, because
a SCADA tag write cannot invoke a method; a 2024 vendor product confirmed it. The Countess
server stays in compose as the worked example. It has been out of the demo since 2026-08-25, so
do not go looking for a second stream.

**4. A command bit cannot refuse.** The instrument's own screen can answer *"an analysis is
already running"* and decline. A client writing the same `ESMScheduleAnalysis` bit **cannot be
told anything at all** — a tag write has no return value, and the vendor documents no rejection
path. All the client learns is that nothing happened. The page knows because it is inside the
instrument; everybody else is inferring from outside.

**5. What goes on the wire is the instrument's document, not the site's.** `ts` and `values`.
No `seq`, no `source`, no `meta` — so this pattern carries **no `meta.mechanism` at all**, and a
consumer learns where the message came from the way it learns everything else here: from the
topic it arrived on. The LIMS is what re-stamps the sample id as `meta.correlation_id` on the way
back out.

> **The consequence, owned rather than hidden.** "Seven mechanisms, seven colours on one
> firehose" is not true and never was. Pattern 1 carries no `meta`, pattern 2 has no envelope of
> ours at all, and this one publishes the instrument's own document — so one `mosquitto_sub`
> shows **four** `meta.mechanism` values, from patterns 4, 5, 6 and 7. Say four, or do not say a
> number. [`plans/06-poll-particle-counter.md`](../plans/06-poll-particle-counter.md) open item 5.

**6. Bad quality becomes `null`, never zero.** The osmometer module is unfitted on the shipped
defaults, so `Osmo/Result` sits at `Bad_NoData`, the transform's `_value()` returns `None`, and
the LIMS writes **no row** — not a zero. **A released sample carries two analytes, not three**,
deterministically. That is the absent-versus-zero discipline arriving where it matters: a zero
osmolality is a number an analyst could have signed for.

## The chain

```
        a person types the valve's sample id into :8087 and presses Run
                                  │
                   the vendor's own ESMScheduleAnalysis bit
                                  ▼
                       opcua-cell-analyzer  :4841
                    (writes SampleTime LAST, on purpose)
                                  │
                     OPC UA subscription, nsu=…;s=…
                                  ▼
              Ignition — cell_analyzer UDT, 57 tags monitored
                                  │
        tag-change script on result/sample_time  (skips initialChange and Bad)
                                  ▼
      system.eventstream.publishEvent("icc-2026", "03_opcua/cell-analyzer-result", …)
                                  │
              transform: opcua_event.build_cell_analyzer_result
                    reads the historical siblings, Bad → null
                                  ▼
                            Transmission
                                  ▼
          icc26/site1/qc/analyzers/cell-analyzer-01/result
```

**Say the Event Stream → Transmission relay out loud once — here or in pattern 6, not both.**
Patterns 3, 6 and 7 all use this shape, and naming it three times spends the room's attention on
plumbing. [`demo-through-line.md`](../demo-through-line.md) § *Still open*.

## The wire

One message per completed sample. Never per value, never on a QC run, never on a failed or
aborted one. **This is the document's shape rather than a capture** — the branches and the nulls
are `opcua_event.build_cell_analyzer_result`'s, the two analyte values are the ones
`S-20260831-0103` carried through to the LIMS, and the timestamp is illustrative:

```json
{
  "ts": "2026-08-31T15:41:04.000Z",
  "values": {
    "sample_id": "S-20260831-0103",
    "batch_id": "B-20260831-01",
    "vessel_id": "BR-201",
    "operator": "Admin User",
    "chem": { "gluc": 5.92, "lac": 0.14, "na": "…", "k": "…" },
    "gas":  { "ph": "…", "pco2": "…", "po2": "…" },
    "osmo": null,
    "cell_density": { "viable_density": "…", "viability_percent": "…" },
    "modules_used": { "cdv": true, "chemistry": true, "gas": true, "osmo": false }
  }
}
```

**Read `osmo: null` and `modules_used.osmo: false` together.** The instrument said which modules
ran, and the analyte that did not run is null rather than absent and rather than zero. That pair
is the whole absent-versus-zero argument in two lines, on a projector, in the vendor's own
vocabulary.

**`ts` is the vendor's `SampleTime`** — the acquisition instant, not the publish instant. It is
what the LIMS stores as `collected_at`, what pattern 4 puts on the released record, and what
every timing gap on stage is measured against.

## The risk beat — four things measured, not asserted

**1. The trigger is an inference, and the simulator is what makes it safe.** `SampleTime` written
last is a convention we control here and would not control at a customer site. If a real
instrument wrote its timestamp first, this bridge would publish half a result — and nothing in
OPC UA would report an error, because every read would return Good on a value that was simply the
previous run's.

**2. Every tag reads `Error_Configuration` and there is nothing in the gateway log.** Two causes,
both silent: an address written `ns=<index>` instead of `nsu=<uri>` — the index is assigned at
server startup by registration order and can drift — or a `{parameter}` left as a plain string
instead of a `bindType: parameter` binding. **The server is the only witness.** The way to check
binding is to turn the simulator's log level up and count `create monitored items` requests
against the paths in the UDT: both counts equal, no missing entries, or it is not done.

**3. A lapsed trial looks exactly like a broken tag configuration.** When the two-hour Ignition
trial expires the gateway stops executing: subscriptions are torn down, tags stop updating, and
the OPC server logs `Session timed out after 120s of inactivity`. It reads as *"the tag
definitions never loaded"*, and it will send somebody rebuilding configuration that was fine.
**Check `GET /data/api/v1/trial` first.** The idle session is the tell — a session with live
monitored items sends Publish requests continuously and can never idle out.

**4. The instrument stopped free-running on purpose.** `CELL_ANALYZER_SAMPLE_INTERVAL_S` is `0`,
so nothing samples unless a person presses Run. It cannot be otherwise: a self-driving analyzer
invents sample ids nobody transcribed, and every result it produces parks unmatched in the LIMS.
That is not a demo convenience — it is what makes the transcription in pattern 4 real.

## What this pattern deliberately does not do

**It does not write to the instrument to run the demo.** The sample-login page on :8087 writes
the vendor's own `SampleInformation/*` nodes and then sets the vendor's own bit — the same nodes,
in the same order, that Ignition's `cell_analyzer` UDT drives from `command/*`. It does not reach
into the simulator's internals. **A page that shortcuts the vendor contract stops demonstrating
it.**

**It does not read `ICC26Extensions` on the publish path.** See talk point 2.

**It does not publish QC runs**, and it does not publish a failed or aborted analysis. One
message per completed sample is the contract, and the negative cases are worth showing.

## On stage

Watcher, in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/site1/qc/analyzers/+/result' -v
```

| Beat | Trigger | What lands |
|---|---|---|
| The instrument's own screen | Open <http://localhost:8087> | A sample-login field, not a dashboard. The vendor's surface, not ours |
| The transcription | Badge `B-1042` at :8085, copy the id, type it in, press Run | **One** message on `…/cell-analyzer-01/result` when the run completes |
| Absent is not zero | Read the payload | `osmo: null` beside `modules_used.osmo: false` — and two analytes downstream, not three |
| It refuses, and a bit cannot | Press Run again while one is running | The page says an analysis is already running. Then say what an OPC UA client would have been told: nothing |
| The document has no mechanism | Scroll the watcher | `ts` and `values`. Where it came from is the topic, and only the topic |

**The particle counter's topic shares this wildcard.** `qc/analyzers/+/result` catches
`particle-counter-01` too, which is useful in pattern 6 and noise here — narrow it to the one
device if the burst of three every 30 s is landing on top of the beat.

**Check the trial before diagnosing anything.** Risk beat 3 is the single most expensive wrong
turn available in this segment.

## Progress log

| Date | Change |
|---|---|
| 2026-09-06 | Written from [`plans/03-opcua-analyzer-playbook.md`](../plans/03-opcua-analyzer-playbook.md) as the closing step, per the two-document convention — late, because pattern 3 was built on 2026-08-20 and its talk track was the piece left over. Carries the 2026-08-26 sample-login rebuild (§ 8b of the spec) rather than the free-running instrument that preceded it, and states outright that this pattern publishes no `meta.mechanism`, which corrects the "seven colours on one firehose" line wherever it is still spoken. |
