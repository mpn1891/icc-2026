# 01 — Native MQTT: smart sample valve assembly

> **Living document.** Updated as the pattern is built — see the [progress log](#progress-log) at
> the bottom. This file supersedes spec 01 in [`00-master-plan.md`](00-master-plan.md).
>
> **This pattern was a wireless vibration gateway until 2026-08-17.** It is now a badge-operated
> sample valve, and pattern 2 is the *same device* speaking Sparkplug B. See
> [Deviations](#deviations-from-the-earlier-docs) before assuming anything here is a typo for
> something older.

| | |
|---|---|
| **Pattern** | 1 of 7 — native MQTT pub/sub, hand-rolled everything |
| **Mechanism tag** | `meta.mechanism = "native-mqtt"` |
| **New container** | `sim-valve-mqtt` — [`services/sim-valve-mqtt/`](../../services/sim-valve-mqtt/) |
| **Config page** | <http://localhost:8085> |
| **Pairs with** | [`02-sparkplug-b.md`](02-sparkplug-b.md) — same assembly, different firmware |
| **Depends on** | nothing (Wave 1) |
| **Blocks** | nothing |

## Objective and talk points

A smart sample valve assembly is the most *ordinary* thing on the backbone, and that is why it
earns the slot: it is a field device somebody bought, commissioned through a web page, and
pointed at a broker. Three things it puts on stage.

**1. The commissioning page is the protocol.** Everything this device promises the outside
world is three form fields — a topic, a QoS, and a retained flag — typed in by whoever
installed it. Nothing validates the topic against the site namespace. Nothing knows a badge
scan is an audit record and a line temperature is not, so one QoS applies to both. Pattern 2's
page has the same three controls greyed out. **Put the two screenshots side by side and the
argument makes itself**, before a single message crosses the wire.

**2. The device knows nothing about itself, so everything else must be agreed.** The payload
shape is ours. The death certificate is a retained JSON document on a topic we picked, whose
timestamp is wrong by construction. Datatypes are whatever `json.dumps` produced. Every one of
those is a decision that has to be written down somewhere and kept in step forever — and the
place it gets written down is an Ignition tag configuration, by hand, which is exactly the work
pattern 2 does not do.

**3. The namespace holds because an ACL holds it.** `sample-valve-01` may publish to
`icc26/site1/upstream/#` and nothing else, so the free-text topic box cannot put sample data in
the QC area. That rule lives in `compose/chariot/mqtt-users.json`, not in the device and not in
the protocol. Delete it and the discipline goes with it.

### What this pattern deliberately does *not* do

**No command path. At all.** There is no `cmd` topic, nothing is subscribed, and nothing on the
backbone can open this valve. Authorization is decided at the sample port against a roster the
assembly holds locally, because a sample port that stops working when the broker does is not one
anybody would install. That is not a simplification for the demo — it is what makes the device
credible, and it means both patterns 1 and 2 are pure publishers.

**No request/response, and therefore no correlation id.** Nothing asks this device for
anything. Chariot is MQTT 3.1.1 and has no response-topic or correlation-data properties, and
this pattern never needs to find that out. (`cmd/<verb>` and `response/<what>` remain in the
architecture doc's message-type set as the names to reach for; **no pattern currently uses
them.**)

---

## Physical model

A sanitary diaphragm sample valve with an integrated RFID reader, a pneumatic actuator and
position feedback, mounted on the sample port of `BR-201`. Serial `SV-2000-0417`.

An operator presents a badge. The assembly checks it against its own roster and its process
interlock, strokes the valve open for a sampling window, and closes it again. **Every scan is
published, granted or denied** — a refused sample attempt is exactly as audit-relevant as a
successful one.

```
   badge presented at the reader
            │
            ▼
   ┌──────────────────┐   authorized && interlock_ok?
   │  LOCKED          │──────────── no ──────────► event: badge-scan, result=denied
   └────────┬─────────┘                            (nothing moves)
            │ yes
            ▼
      UNLOCKING ──► OPEN ──► CLOSING ──► LOCKED
      (1.5 s)      (12 s)    (1.5 s)        │
            │                               └──► event: sample-complete
            └──► event: badge-scan, result=granted, sample_id assigned

   every transition ──► state topic (retained)
   every 5 s        ──► telemetry topic
```

Deny reasons, checked **in this order** — who you are is decided before what the valve happens
to be doing, so the audit trail says the same thing every time:

| Reason | |
|---|---|
| `badge-unknown` | not on the roster — a contractor at the wrong skid |
| `badge-not-authorized` | on the roster, wrong role |
| `training-expired` | on the roster, right role, lapsed qualification |
| `valve-busy` | mid-cycle for somebody else |
| `interlock-open` | CIP in progress / vessel not at sampling conditions |

Roster ships with one of each of the first three (`B-1042` authorized, `B-2087` wrong role,
`B-3311` training expired) so all the denial paths are demonstrable without editing config on
stage. The interlock is toggled from the config page.

---

## Topic contract

Namespace rule from [`../00-architecture.md`](../00-architecture.md):
`icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}`.

**And note that this device does not know that.** The base topic below is a factory default in a
text box. It is device-addressed and namespace-conformant because somebody typed it that way.

| Topic | Dir | QoS | Retained | Purpose |
|---|---|---|---|---|
| `icc26/site1/upstream/br-201/sample-valve-01/event` | out | 1 | yes | One per badge scan and per completed sample |
| `icc26/site1/upstream/br-201/sample-valve-01/state` | out | 1 | yes | Valve position; **also the Last Will** |
| `icc26/site1/upstream/br-201/sample-valve-01/telemetry` | out | 1 | yes | Line pressure/temperature, every 5 s |

Nothing is subscribed.

**The QoS and Retained columns are identical down the table, and that is the finding.** The page
offers one of each, applied to all three message types. The honest settings would be QoS 1
unretained for events, QoS 1 retained for state, QoS 0 unretained for telemetry — three
different answers the device cannot express. Shipping default is `1` / retained, which is right
for `state`, wasteful for `telemetry`, and leaves a stale `event` sitting on the broker for the
next subscriber to mistake for a live one.

Demonstrate it: turn Retained off and the death certificate stops working for anyone who was not
already connected.

---

## Payload contracts

The envelope from [`../00-architecture.md § Payload envelope`](../00-architecture.md), with
`meta.event` naming what happened. Unlike the old vibration pattern there is no vendor payload
here — everything on the wire is ours, which is precisely the problem being illustrated.

### `event` — badge scan

```json
{
  "ts": "2026-08-17T18:22:04.512Z",
  "seq": 41,
  "source": { "id": "sample-valve-01", "type": "sample-valve" },
  "meta": {
    "mechanism": "native-mqtt",
    "ingest_ts": "2026-08-17T18:22:04.512Z",
    "event": "badge-scan",
    "cell": "br-201",
    "assembly_serial": "SV-2000-0417"
  },
  "values": {
    "badge_id": "B-2087",
    "badge_holder": "Sam Okafor",
    "badge_role": "maintenance",
    "result": "denied",
    "deny_reason": "badge-not-authorized",
    "valve_state": "locked",
    "sample_id": null
  }
}
```

`result` is `granted` or `denied`; `deny_reason` is `null` on a grant. A granted scan carries the
`sample_id` it authorized.

### `event` — sample complete

`values`: `sample_id`, `badge_id`, `badge_holder`, `opened_at`, `closed_at`,
`open_duration_s`, `cycle_count`.

### `state` — retained, and the death certificate

`values`: `state` (`locked` | `unlocking` | `open` | `closing` | `offline`), `is_open`,
`position_pct`, `interlock_ok`, `sample_id`, `badge_id`, `cycle_count`, `since`.

The **Last Will** is the same document with `state: "offline"`, and two things about it are
wrong on purpose:

- **Its timestamp is the moment the session connected**, not the moment the device died. A will
  is registered in the CONNECT packet, before the death it describes. Nothing can fix that from
  inside a hand-rolled protocol.
- **Nothing on the backbone knows this topic means death** unless it was told separately.

Sparkplug does not solve the first by magic — NDEATH has the same constraint — it solves it by
making every consumer apply the same rule, and by having the *consumer* stamp the time. The
difference is the agreement, not the plumbing.

### `telemetry` — every 5 s

`values`: `line_pressure_bar`, `line_temperature_c`, `valve_cycles_total`, `interlock_ok`,
`uptime_s`. Pressure drops and temperature rises while the valve is open, so the stream is
visibly coupled to the events rather than decorative.

---

## The configuration page

<http://localhost:8085>, served by the container itself
([`services/sim-valve-mqtt/webui.py`](../../services/sim-valve-mqtt/webui.py) +
`page.html`) — a device's embedded commissioning UI, which is what every smart field device
ships. `http.server` from the standard library, one HTML file with CSS and JS inline, **no
external assets**, because the demo has to run with networking disabled.

| Section | |
|---|---|
| **MQTT publishing** | Topic (free text), QoS (0/1/2), Retained (checkbox). Save reconnects, because a new topic means a new will and a will is only registered at CONNECT |
| **What this produces** | The three derived topics, live, with a note on each saying what it carries and why one QoS cannot be right for all three |
| **Last will** | Topic, QoS, retained, and the sentence about the frozen timestamp |
| **Simulator controls** | Fenced off, labelled *not part of the device*: a badge button per roster entry, an interlock toggle, and the live valve state |

The simulator block is the stage trigger. It exists because a deny path you cannot fire on
demand is a deny path you will not get to show.

Saving a new topic also surfaces a genuine gotcha: **a retained message outlives the config that
produced it.** The old retained state sits at the old topic until something clears it, and the
page says so when it happens.

Commissioned settings persist to `/data/config.json` on the `valve-mqtt-config` volume, so they
survive a restart. The `VALVE_BASE_TOPIC` / `VALVE_PUBLISH_QOS` / `VALVE_PUBLISH_RETAIN`
environment variables are **factory defaults only** — once the page has been saved, the volume
wins. `python tasks.py nuke` returns the device to factory.

---

## Implementation

`services/sim-valve-mqtt/`, `paho-mqtt` and nothing else.

| File | |
|---|---|
| `valve.py` | The assembly: roster, interlock, state machine, line simulation. Knows nothing about MQTT |
| `app.py` | Config, envelope, the paho client, the will, and the page's view of the device |
| `webui.py` | The stdlib config server |
| `page.html` | The page |

**`valve.py` and `webui.py` are byte-for-byte identical to the copies in
`services/sim-valve-spb/`.** That is the house convention (self-contained build contexts, no
shared library) and here it is load-bearing: the two containers must differ *only* in how they
speak, or the comparison is worthless. Fix one, copy it across, `diff` before committing.

---

## Infrastructure

**MQTT user `sample-valve-01`**, publish `icc26/site1/upstream/#`, subscribe nothing. The
wildcard is the *area* rather than the exact topic on purpose — so the valve can be re-addressed
to another cell live on stage and still work, while it cannot put sample data in `qc`. See
talk point 3 and `compose/chariot/README.md`.

Two caveats that will bite before they help:

- **`MQTT_USERS` seeds Chariot on first run only.** On an already-running broker the new
  accounts do not exist. Both valves connect anyway today because `allowAnonymous` is `true`;
  when that goes back to `false` before the talk, a `nuke` is what creates them.
- **`compose/postgres/initdb/` runs on an empty volume only.** `03-seed.sql` now carries
  `BR-202`, `sample-valve-01` and `sample-valve-02`; against a live database, apply the
  equivalent `DELETE`/`INSERT` by hand.

**`services/sim-vibration/` stays on disk, unwired**, along with the whole vibration
implementation inside Ignition — see [Deviations](#deviations-from-the-earlier-docs).

---

## Deviations from the earlier docs

Recorded so nobody "fixes" these back.

| Earlier docs said | Now | Why |
|---|---|---|
| Pattern 1 is a wireless vibration gateway; pattern 2 is a bioreactor UDT | Both are the **same smart sample valve assembly**, in two firmwares | Two unrelated subjects made the mechanism comparison harder than it needed to be. One device in two protocols is the argument, and the two config pages make it before any traffic does |
| The gateway is simulated **inside Ignition** (`vibsim` + two event streams) | A **standalone container** | The device needs its own commissioning webpage, and it needs to own its own MQTT session — which is also what gives pattern 1 a real Last Will |
| Pattern 1 has no Last Will and claims no lifecycle | Hand-rolled retained `state: "offline"` will | Owning the session makes a will available. Pattern 1 vs 2 is now *hand-rolled vs spec-mandated* rather than *none vs some*, which is the better comparison |
| Fleet-addressed `…/vibration-gw/cmd/collect` + `…/response/waveform` | **No command topics at all** | The valve is publish-only. The namespace's two deliberate non-device-addressed topics are gone with it |
| Adds no container | Two containers, `sim-valve-mqtt` and `sim-valve-spb` | See above |
| Pattern 2 runs on the Programmable Device Simulator via MQTT Transmission | Pattern 2 is its own Sparkplug edge node container | A device that configures itself through the SCADA system it publishes to cannot make the config-page point |
| `agitator-vib` / `vib-gw-01` in `plant.equipment` | `sample-valve-01` on `br-201`, `sample-valve-02` on `br-202` | Topic strings and equipment ids stay in step, as required |

**The vibration work is left in place inside Ignition and is not referenced by any doc.**
`vibsim`, the `vibration_sensor` UDT and its `br-201` instance, the `vibration-gw-control` and
`vibration-gw-listener` event streams and the `icc26-native` Engine namespace are all still on
disk under `ignition/`. Nothing publishes to them and nothing subscribes; they are inert.
`services/sim-vibration/` likewise. Delete them when it is clear nothing wants them back.

---

## Ingest, as built

Verified 2026-08-17 against a gateway seeded from an empty volume. **One file, one line.**

`…/com.cirruslink.mqtt.engine.gateway/custom-namespace/icc26-native/config.json`

```json
"subscription": "icc26/site1/upstream/br-201/sample-valve-01/#"
```

`jsonPayload: true`, `qos1: true`, `writeableTags: false`, `numbersAsFloats: true`,
`rootFolder: ""`. No UDT, no event stream, no routing script — Engine auto-creates tags from
the JSON and the tree comes out shaped like the payload rather than like the asset:

```
MQTT Engine/icc26/site1/upstream/br-201/sample-valve-01/
    {event,state,telemetry}/{ts,seq}
    {event,state,telemetry}/meta/{mechanism,event,cell,ingest_ts,assembly_serial}
    {event,state,telemetry}/source/{id,type}
    {event,state,telemetry}/values/…
```

**`meta.mechanism` is duplicated into all three branches** because the tree mirrors the
document, and every document carries the envelope. Pattern 2 has one node with 19 metrics under
it.

### The subscription has to name one device, and that is the finding

The obvious `icc26/site1/upstream/+/+/#` also swallows pattern 5's `…/br-201/batch/event` and
pattern 7's `…/br-201/batch-summary` — **`#` matches zero levels too.** So pattern 1's
subscription enumerates a single device by name, and **adding a second plain-MQTT valve means
editing this file.** Pattern 2's `spBv1.0/#` already covers every edge node that will ever
exist. That row is in [`02-sparkplug-b.md`](02-sparkplug-b.md)'s comparison table.

### What Engine does with the payload — measured, not guessed

| Field | Tag datatype |
|---|---|
| `ts`, `ingest_ts`, `since` | **String** — not DateTime. Pattern 2 sends real DateTime metrics |
| `seq`, `cycle_count`, `valve_cycles_total`, `uptime_s` | **Float8** — `numbersAsFloats: true` turns every counter into a float |
| `is_open`, `interlock_ok` | Boolean |
| everything else | String |

**A JSON `null` produces no tag at all.** Not a dropped value, not bad quality, not an empty
string — the key is skipped and the tag never exists. `values.sample_id` was absent from the
tag tree entirely until the first *granted* scan carried a non-null id, at which point it
appeared. So **the shape of the tag tree is a function of which messages happened to arrive**,
not of the payload contract: a consumer inspecting the tree before the first grant would
conclude `sample_id` does not exist.

That is the precise counterpart to pattern 2's typed nulls, which declare `Badge/LastScanId` as
a String in DBIRTH *before anybody has badged in*.

### Death, measured both ways

Both paths publish `state: "offline"`, and **both timestamps are the moment the session
connected** — within 7 ms, confirming the will document is built before CONNECT and can never
describe the death it announces:

| | delivered | payload `ts` | stale by |
|---|---|---|---|
| `docker stop` (graceful) | 01:34:02.882 | 01:30:12.169 | **3 m 50 s** |
| `docker kill` (will fires) | 01:34:29.887 | 01:34:15.929 | **14 s** |

**Correction to the earlier prediction:** a graceful stop does *not* mean nothing is published.
The broker discards the will, but the device publishes the same frozen document itself as part
of clean shutdown — a live `state: "locked"` first, then the stale `offline`. So a consumer sees
`offline` either way, and on the graceful path the timestamp is **staler**, not fresher.

### Two behaviours captured on purpose, and not fixed

- **Retained `event` replays on reconnect.** A brand-new subscriber immediately received a badge
  scan from 21 s earlier with nothing marking it historical. Engine gets this on every
  reconnect and presents it as current.
- **Each scan overwrites the last.** Exactly one `event` is retained, and the tag model holds
  one set of badge tags. A granted `B-1042` scan was gone minutes later, replaced by a `B-2087`
  denial. A GxP audit trail that silently keeps only the most recent record is invisible until
  somebody asks about last Tuesday. Tag history is the remedy; it stays off.

---

## Verification

Prerequisite: the stale-image blocker in [`00-status.md`](00-status.md). Apply on-disk config
with `python tasks.py scan`.

Watcher, in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

**1 — Both pages answer.** <http://localhost:8085> and <http://localhost:8086>. Open them side
by side; this is the pattern before any traffic exists.

**2 — A granted scan.** Press `B-1042`. Expect, in order: one `event` with `result: granted` and
a `sample_id`, `state` moving `unlocking → open → closing → locked`, then one `event` with
`sample-complete` carrying the same `sample_id` and the open duration.

**3 — Every denial.** `B-2087` → `badge-not-authorized`. `B-3311` → `training-expired`.
`B-9999` → `badge-unknown`. Toggle the interlock, then `B-1042` → `interlock-open`. Press
`B-1042` twice quickly → `valve-busy` on the second. Each produces exactly one `event` and **no
state change**.

**4 — Retain does the work.** Start a second `mosquitto_sub` → the retained `state` arrives
immediately, before anything happens. Then `docker kill icc26-sim-valve-mqtt` → the retained
`state: "offline"` will lands. Note its `ts`: it is the connect time. Now `docker start` it, set
Retained **off** on the page, and repeat — the new subscriber gets nothing, and the death
certificate is only seen by whoever was already listening.

**5 — The config page round trip.** Change the topic to
`icc26/site1/upstream/br-202/sample-valve-01`, save, scan → traffic appears on the new topic and
the page warns about the retained message left at the old one. Change it to
`icc26/site1/qc/lims/sample-result` → the ACL refuses it (with `allowAnonymous: false`), which
is talk point 3.

**6 — `docker stop` vs `docker kill`.** `stop` disconnects cleanly, the broker discards the
will, and nothing is published. Only `kill` proves the will works. Both are worth showing.

---

## Open items

| # | Item | Status |
|---|---|---|
| 1 | Ignition-side ingest | **done 2026-08-17.** One Engine custom-namespace subscription and nothing else — no UDT, no event stream, no routing script. See *Ingest, as built* above |
| 2 | Whether `event` should be its own tag branch or a Perspective-only audit table | open, decide with the Ignition wiring |
| 3 | The retained-message-at-the-old-topic gotcha has no cleanup path | open — a real device would not clean up either, so this may stay as-is deliberately |
| 4 | `allowAnonymous` is still `true`, so the ACL in talk point 3 is not actually enforced yet | tracked in `compose/chariot/README.md` |
| 5 | Perspective view for pattern 1 | out of scope for v1 — the device's own page is the UI |

---

## Progress log

| Date | Change |
|---|---|
| 2026-08-17 | **Pattern re-scoped: vibration gateway → smart sample valve assembly.** Publish-only, local badge authorization, two containers with device config webpages, pattern 2 becomes the same device in Sparkplug B. Vibration implementation left inert inside Ignition. |
| 2026-08-17 | `services/sim-valve-mqtt/` built: valve state machine, badge roster, envelope publishes, hand-rolled retained-LWT death certificate, stdlib config page on 8085. Compose service, ACL account, seed rows, `.env.example` block. |
| 2026-08-17 | **Ran against a real gateway for the first time.** Engine custom namespace narrowed to one device, tag tree auto-created and confirmed, JSON-null behaviour settled, both death paths measured. Auto-created tree gitignored. See *Ingest, as built*. |
