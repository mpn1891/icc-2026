# ICC Demo Through Line — "Becoming Event-Driven in Operational Technology with Ignition"

> Merged into [`plans/00-master-plan.md`](plans/00-master-plan.md) on 2026-08-23, reconciled
> against that day's pattern 5/6/7 re-source (see that file and
> [`00-architecture.md`](00-architecture.md)). Pattern numbering and ordering are fixed and
> referenced elsewhere — do not renumber. Open items now live in the master plan's own
> *Open items* section, not here, so there is one place to check them off.

## Purpose

This document defines the unifying demo narrative across all seven patterns.

## The Spine

All seven demos draw from **a single fed-batch bioprocess run**. Each pattern contributes
one signal from that same batch. Pattern 7 assembles those signals into a composite event
that no single source system could produce.

The audience watches one event get built across seven demos, rather than seven unrelated
vignettes.

**The GxP through line:** contemporaneous record of a sampling event, and whether that
sample was pulled inside the qualified phase window.

**The composite event (Pattern 7 payoff):**
> Sample pulled outside qualified phase window with concurrent environmental excursion.

## Segment Structure

Each pattern segment runs: **intro (business context) → demo → risk/complexity in GxP →
then a positive closing message.** The upbeat beat after the risk speaker is deliberate — it prevents each segment, and the talk overall, from ending on risk.

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

**Signal contributed:** Device liveness / session state

**GxP hook:** You can prove the valve was alive when it said nothing. Silence becomes
evidence.

---

## Pattern 3 — OPC UA → MQTT Bridge / Event Streams

**Demo asset:** Cell analyzer (VCD / viability) — the Nova Biomedical BioProfile FLEX2,
run alongside a second, unpublished analyzer (Countess) for contrast

- Qualified instrument that cannot be touched; bridge sits at the gateway
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

**Demo asset:** An Ignition timer auto-cycling `BR-201` through
`CIP → SIP → INOC → GROWTH → HARVEST`, standing in for a Batch Execution System — CDC's
honest use case is a system the demo does not control, and this timer plays that role: the
writer does not know MQTT exists, and the `cdc` database role has read-only access.

- Tail `mes.batch_event`; a phase change emits without the sequencer's cooperation
- Each row carries `qualified_window` — `true` only during `GROWTH`, the only phase the
  protocol qualifies for sampling

**Signal contributed:** Current batch phase and whether it falls inside the qualified
sampling window

**GxP hook:** Reading a validated system's internals without its owner in the loop. State it
plainly and hand it to the risk speaker.

---

## Pattern 6 — API Poll and Diff

**Demo asset:** Environmental monitoring system (Met One particle counter, simulated), with
an HTTP API

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
| Smart sample valve | 1 / 2 | Sample actuation + device liveness |
| Cell analyzer + LIMS review | 3 / 4 | VCD reading + assay disposition |
| Batch timer (BES stand-in) | 5 | Batch phase and qualified window |
| EM system | 6 | Environmental excursion status |

Triggered by pattern 4's review message; always publishes, whether or not either derived
flag (`outside_qualified_window`, `environmental_excursion`) is `true`.

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

Open items (asset availability, per-segment closing lines, presenter assignments, Event
Streams weighting) are tracked in
[`plans/00-master-plan.md` § Open items for the master plan](plans/00-master-plan.md#open-items-for-the-master-plan).
