# ICC Demo Through Line — "Becoming Event-Driven in Operational Technology with Ignition"

> Merged into [`plans/00-master-plan.md`](plans/00-master-plan.md) on 2026-08-23, reconciled
> against that day's pattern 5/6/7 re-source, and **reconciled again on 2026-08-25** against the
> master-plan revision that cut spec 08, took the Countess out of the demo, moved pattern 6 into
> `qc/analyzers` and gave pattern 7 a store requirement (see that file and
> [`00-architecture.md`](00-architecture.md) § *Cut on 2026-08-25*). Pattern numbering and
> ordering are fixed and referenced elsewhere — do not renumber, and note that **the numbering
> stops at 7**; there is no pattern 8.

## Purpose

This document defines the unifying demo narrative across all seven patterns.

## The Spine

All seven demos draw from **a single fed-batch bioprocess run**. Each pattern contributes
one signal from that same batch. Pattern 7 assembles those signals into a composite event
that no single source system could produce.

The audience watches events get assembled across seven demos, rather than seven unrelated
vignettes.

**The GxP through line:** contemporaneous record of a sampling **and analysis** event, and
whether that sample was pulled inside the qualified phase window.

**The composite event (Pattern 7 payoff):**
> Sample pulled outside qualified phase window with concurrent environmental excursion.

## Segment Structure

Each pattern segment runs: **intro (business context) → demo → risk/complexity in GxP →
then a positive closing message.** The upbeat beat after the risk speaker is deliberate — it prevents each segment, and the talk overall, from ending on risk.

**Running order, as of 2026-08-25 — the segments are grouped, not strictly one at a time.**
Patterns **1–4** are introduced as summaries, then demoed together; then the same again for
**5–7**. Two demo blocks rather than seven interruptions. The per-pattern beats below still
apply — they are what each summary and each demo covers — but plan for two switches to the
live stack, not seven, and for the risk beats to arrive in two batches.

**There is no dashboard.** Spec 08 (Perspective pages, the mechanism-coloured firehose, the
runbook) was cut on 2026-08-25. The demo surface is `mosquitto_sub` on `icc26/#`, the two valve
config pages on 8085/8086, and the LIMS approval screen on 8000. Every one of those is somebody's
real product screen rather than a dashboard about the demo, which is a better answer than the
firehose was — but it does mean no single view shows all seven mechanisms at once, and the
narration carries that claim instead.

---

## Pattern 1 — Native MQTT Source

**Demo asset:** Smart sample valve

- Valve publishes actuation directly: aliquot ID, timestamp, operator badge

**Signal contributed:** Sample actuation event

**GxP hook:** The record originates at the point of action. No transcription, no
intermediary.

---

## Pattern 2 — Sparkplug B

**Demo asset:** The same sample valve, now as a fleet

- Two valves across a suite; show onboarding the third as pure configuration
- Considering dropping the connection on one valve mid-sampling-window and let the death
  certificate fire

**Signal contributed:** Device liveness / session state — a stage argument, not a field in
pattern 7's document. This valve is `sample-valve-02` on `BR-202`; the aggregate reads pattern
1's `sample-valve-01` only.

**GxP hook:** You can prove the valve was alive when it said nothing — and where pattern 1
leaves you guessing, the spec-enforced sequence number tells you whether you *missed* messages
in between. Silence becomes evidence, and so does a gap.

---

## Pattern 3 — OPC UA → MQTT Bridge / Event Streams

**Demo asset:** Cell analyzer (VCD / viability) — the cell analyzer,
addressed as `cell-analyzer-01`

- Qualified instrument that cannot be touched; bridge sits at the gateway
- The Countess is **out of the demo** as of 2026-08-25 — the "model we would design versus the
  one a vendor actually ships" contrast is now a sentence, not a second running instrument
- Show the change-control boundary explicitly — instrument untouched, gateway does the work
- Ignition 8.3 Event Streams story

**Signal contributed:** Viable cell density / viability reading, timestamped as the sample
instant pattern 7 keys off (`meta.correlation_id` = `sample_id`)

**GxP hook:** Qualified system read by a platform, one-way.

---

## Pattern 4 — Webhook

**Demo asset:** LIMS analyst review of the pattern-3 result

- The analyst's approve/reject posts back to Ignition hours after the physical sampling
  event; both outcomes publish (`disposition` ∈ `pass | fail`)
- Contrast the payload against the tag change from Pattern 3 — the semantic gap is visible
  in one screenshot

**Signal contributed:** Assay disposition, already carrying business meaning — and the
trigger pattern 7 listens for

**GxP hook:** Authenticated inbound connection, and the result arrives out of sequence with
the physical event it describes.

---

## Pattern 5 — Change Data Capture

**Demo asset:** A batch engine stepping `BR-201` through
`CIP → SIP → INOC → GROWTH → HARVEST`, standing in for a Batch Execution System — CDC's
honest use case is a system the demo does not control, and this engine plays that role: the
writer does not know MQTT exists, and the `cdc` database role is not the application's.
**It advances on a click, not on a dwell** (2026-08-26) — a boolean tag in Tag Explorer, so the
reactor can be parked in `GROWTH` before the valve is badged rather than waited on.

- Tail `bes.batch_event`; an operation change emits without the engine's cooperation
- Each row carries `qualified_window` — `true` only on the `operation_start` of `GROWTH`, the
  only operation the protocol qualifies for sampling
- These are ISA-88 **operations**, not phases. A phase is the smallest process action

**Signal contributed:** Current batch operation and whether it falls inside the qualified
sampling window

**GxP hook:** Reading a validated system's internals without its owner in the loop. State it
plainly and hand it to the risk speaker.

---

## Pattern 6 — API Poll and Diff

**Demo asset:** Environmental monitoring system (Met One particle counter, simulated), with
an HTTP API. It lives in the analyzer path — `icc26/site1/qc/analyzers/particle-counter-01/result`
as of 2026-08-25, beside the analyzer rather than beside the reactor

- Poll on an interval, diff by record id or analysis time, emit
- Each analysis carries `status` ∈ `normal | excursion` against a configured cleanroom limit
- Show the polling interval, then ask what happened between polls

**Signal contributed:** Environmental excursion status

**GxP hook:** A characterized detection gap. Not fatal, but it goes in the assessment.

---

## Pattern 7 — Custom Scripted Complex Event

**Demo asset:** The assembly itself

Combines:

| Source | Pattern | Fragment |
|---|---|---|
| Smart sample valve (`sample-valve-01`) | 1 | Sample actuation |
| Cell analyzer + LIMS review | 3 / 4 | VCD reading + assay disposition |
| Batch timer (BES stand-in) | 5 | Batch phase and qualified window |
| EM system | 6 | Environmental excursion status |

Triggered by pattern 4's review message, and **published only when the sample violated
something** — an environmental excursion or a failed review. A clean sample produces no message
at all, so silence on `icc26/site1/qc/deviation` is the compliant case and every message on it
is a finding. Changed 2026-09-06; until then it published one composite per review.

**It needs a store, and that store does not exist yet.** Every one of those four fragments is a
question about the past — when the valve opened, when the analyzer ran, what phase was live at the
sample-open instant, which particle count was nearest. A subscriber holding only the message
that woke it up can answer none of them, so patterns 1, 3, 5 and 6 have to be *persisted*, not
just published. Today only pattern 5's events land in a table, and that table is the CDC source
rather than a queryable history. This is the largest unspecified piece of the demo.

**Composite event emitted:** Sample pulled outside qualified phase window with concurrent
environmental excursion.

**Framing:** No single source knows this. Four systems each hold a fragment that means
nothing alone. Patterns 1–6 are about acquisition; Pattern 7 is about meaning.

**GxP hook:** Derived data with disposition consequence — the heaviest item on the list, and
the right one to end on.

---

## Closing Beat

After the final risk segment:

> That composite event took days to surface in a paper-and-review world. The room just
> watched it surface in seconds, from systems nobody had to requalify.

**Still open**, and no longer tracked in the master plan (that section was removed on
2026-08-25) — they live here now, with what is true today:

- **Per-segment closing lines.** Only the overall closing beat above is written. Seven still needed.
- **Presenter assignments** — who owns intro / demo / risk per pattern, and per demo *block* now
  that 1–4 and 5–7 are grouped.
- **Demo asset availability** — pattern 6's MET ONE simulator is the long pole; vendor API notes
  are still TBD, and the excursion flag and its limit config are ours to build regardless.
- **Pattern 7's event store** — see above. Unspecified, and 07 cannot be specified without it.
- **Event Streams weighting** — patterns 3, 6 and 7 all use the Event Stream → Transmission relay
  shape. Name it explicitly on stage once, in one of them, rather than three times.
