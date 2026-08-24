# 01 — Native MQTT: smart sample valve assembly

> **Build spec.** What exists, what it publishes, and how it was measured. Talk material lives
> in [`../talk-tracks/01-native-mqtt.md`](../talk-tracks/01-native-mqtt.md). Progress log at the
> bottom. Supersedes spec 01 in [`00-master-plan.md`](00-master-plan.md).

| | |
|---|---|
| **Pattern** | 1 of 7 — native MQTT pub/sub, hand-rolled everything |
| **Mechanism tag** | `meta.mechanism = "native-mqtt"` |
| **Container** | `sim-valve-mqtt` — [`../../services/sim-valve-mqtt/`](../../services/sim-valve-mqtt/) |
| **Config page** | <http://localhost:8085> |
| **Pairs with** | [`02-sparkplug-b.md`](02-sparkplug-b.md) — same assembly, different firmware |
| **Depends on** | nothing (Wave 1) |
| **Blocks** | pattern 7 (reads `event/sample-complete` for `sample_start`); Wave-1 sample-id correlation |
| **Signal contributed** | Sample actuation event — the badge scan and the valve stroke |

## Build constraints that are not negotiable

**Publish-only. No command path.** No `cmd` topic, nothing subscribed, nothing on the backbone
can open this valve. Authorization is decided at the sample port against a local roster. Both
patterns 1 and 2 are pure publishers.

**No request/response.** Chariot is MQTT 3.1.1 — no response-topic or correlation-data. (`cmd`
and `response` remain in the architecture doc's message-type set; **no pattern uses them.**)

**`valve.py` and `webui.py` are byte-for-byte identical to the copies in
`services/sim-valve-spb/`.** Self-contained build contexts, no shared library. The two
containers must differ *only* in how they speak. Fix one, copy it across, `diff` before
committing.

---

## Physical model

A sanitary diaphragm sample valve with an integrated RFID reader, pneumatic actuator and
position feedback, on the sample port of `BR-201`. Serial `SV-2000-0417`.

An operator presents a badge. The assembly checks it against its own roster and process
interlock, strokes open for a sampling window, and closes. **Every scan is published, granted
or denied.**

```
   badge presented at the reader
            │
            ▼
   ┌──────────────────┐   authorized && interlock_ok?
   │  LOCKED          │──────────── no ──────────► event/badge-scan, result=denied
   └────────┬─────────┘                            (nothing moves)
            │ yes
            ▼
      UNLOCKING ──► OPEN ──► CLOSING ──► LOCKED
      (1.5 s)      (12 s)    (1.5 s)        │
            │                               └──► event/sample-complete
            └──► event/badge-scan, result=granted, sample_id assigned

   every transition ──► state topic (retained)
   every 5 s        ──► telemetry topic
```

Deny reasons, checked **in this order**:

| Reason | |
|---|---|
| `badge-unknown` | not on the roster |
| `badge-not-authorized` | on the roster, wrong role |
| `training-expired` | on the roster, right role, lapsed qualification |
| `valve-busy` | mid-cycle for somebody else |
| `interlock-open` | CIP in progress / vessel not at sampling conditions |

Roster ships with one of each of the first three (`B-1042` authorized, `B-2087` wrong role,
`B-3311` training expired). Interlock is toggled from the config page.

---

## Topic contract

Namespace from [`../00-architecture.md`](../00-architecture.md):
`icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}`. **This device does not know
that.** The base topic is a factory default in a text box.

| Topic | Dir | QoS | Retained | Purpose |
|---|---|---|---|---|
| `icc26/site1/upstream/br-201/sample-valve-01/event/badge-scan` | out | 1 | yes | One per badge presented, granted or denied |
| `icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete` | out | 1 | yes | One per sample that actually ran |
| `icc26/site1/upstream/br-201/sample-valve-01/state` | out | 1 | yes | Valve position; **also the Last Will** |
| `icc26/site1/upstream/br-201/sample-valve-01/telemetry` | out | 1 | yes | Actuator air supply / enclosure temperature, every 5 s |

Nothing is subscribed.

### Why the two event subtypes are two topics

`event/<subtype>` is a **two-token message type**, same shape as `cmd/<verb>`.
`icc26/+/+/+/+/event/#` still catches every event — `#` matches zero levels. Recorded in
[`../00-architecture.md § Topic namespace`](../00-architecture.md); no other pattern is
obliged to grow a subtype.

One topic for both does not work because of the tag tree, not the wire. The two documents
carry **different field sets**, Engine's custom namespace mirrors whatever document arrives,
and **a document only writes the keys it contains** — so one `event/values/` folder holds the
union of two schemas with half the tags stale. After a granted sample, `values/deny_reason`
still reads the last denial. Two topics, two branches, one schema each. It also stops
completion clobbering the scan in retained storage.

The page offers one QoS and one Retained flag for every derived topic. Honest settings would
be QoS 1 unretained for the events, QoS 1 retained for state, QoS 0 unretained for telemetry.
Shipping default is `1` / retained: right for `state`, wasteful for `telemetry`, and it
leaves stale events on the broker for the next subscriber to mistake for live ones.

**Not built yet.** `app.py` publishes both subtypes to a single `…/event` topic today; see
open item 2.

---

## Payload contracts

Envelope from [`../00-architecture.md § Payload envelope`](../00-architecture.md). No vendor
payload — everything on the wire is ours.

**`meta.event` is not a discriminator.** On the event topics it names what happened; on
`state` and `telemetry` it echoes the message type (`app.py` passes the message type where the
event name goes). Redundant with the topic's last level on all four; kept so a document read
alone says what it is.

**No `meta.correlation_id` in this pattern** (2026-08-23). The sample id travels as
`values.sample_id`. Patterns 3 and 4 keep the `meta.correlation_id` they already ship.

### `event/badge-scan` — one per badge presented

`values`: `badge_id`, `badge_holder`, `badge_role`, `result` (`granted` | `denied`),
`deny_reason` (`null` on a grant), `sample_id` (minted on a grant; **`null` on a denial** —
a denial belongs to no sample).

Published the instant the badge is read, before the valve has moved. No timing field: this is
the announcement; `sample-complete` is the record. Unknown badges report `badge_holder` and
`badge_role` as `"unknown"` (the page says `not on roster` — same fact).

`values.valve_state` was dropped 2026-08-23: it duplicated `state`, and on a grant it reported
`unlocking` rather than `locked` (`valve.py:243-251` builds the scan after the stroke is
commanded).

### `event/sample-complete` — one per sample that ran

`values`: `sample_id`, `badge_id`, `badge_holder`, `sample_start`, `sample_completion`,
`open_duration_s`, `cycle_result`, `cycle_count`.

Published ~15 s after the granted scan (1.5 + 12 + 1.5). Repeats badge fields so the record
is self-contained. `sample_start` / `sample_completion` are the old `opened_at` / `closed_at`.
`open_duration_s` is close-finish minus open-finish, so ~13.5 s against a 12 s window — the
valve is still passing material while it seats.

**`cycle_result`** — `normal` | `failed-to-seat` | `stroke-timeout` | `aborted-interlock`.
`failed-to-seat` is the demo path: position feedback does not return to 0 % on close.

**Not built yet.** `valve.py`'s state machine has no fault path; see open item 2.

### `state` — retained, and the death certificate

`values`: `state` (`locked` | `unlocking` | `open` | `closing` | `offline`), `is_open`,
`position_pct`, `interlock_ok`, `sample_id`, `badge_id`, `cycle_count`, `since`.

Published on every transition. **The Last Will is not this document.** What `app.py:272-277`
registers carries four keys — `state: "offline"`, `is_open: null`, `position_pct: null`, and
a `note` reading *"last will — ts is when this session connected, not when it died"*. Under
the JSON-null rule below, that contributes exactly two tags (`values/state`, `values/note`).
It lands retained on the same topic and only writes the keys it contains, so surviving tags
are expected to keep last-live values next to `state: offline`. **Inferred, not measured** —
open item 3.

Three things about the will are wrong on purpose and measured below: `ts` is connect time, not
death; `seq` is minted at CONNECT too, so death sorts to the *beginning* of the session; nothing
on the backbone knows the topic means death unless told separately.

**`seq` is process-local and shared across all four topics** (`app.py:197-206`). Every topic
sees gaps by design; it resets to 0 on restart. Looks like Sparkplug's `seq` and does none of
its work. Renaming to `seq_since_start` was deferred — the envelope is shared by four emitters.

### `telemetry` — every 5 s

`values`: `air_supply_bar`, `enclosure_temperature_c`, `valve_cycles_total`, `interlock_ok`,
`uptime_s`.

Re-pointed 2026-08-23 from line pressure/temperature: a shut sample valve has nothing moving
through it, so the old pair was a dead-leg reading or a restatement of BR-201's own
instruments. What the assembly measures all the time is **its own condition**. **Low air
supply is the physical cause of `failed-to-seat` and `stroke-timeout`** — sag it from the
config page to demonstrate the fault.

**Not built yet.** `valve.py` still simulates the line pair; see open item 2.

---

## The configuration page

<http://localhost:8085> — [`../../services/sim-valve-mqtt/webui.py`](../../services/sim-valve-mqtt/webui.py)
+ `page.html`. `http.server`, one HTML file, **no external assets** (demo runs with networking
disabled).

| Section | |
|---|---|
| **MQTT publishing** | Topic (free text), QoS (0/1/2), Retained. Save reconnects — a new topic means a new will, registered only at CONNECT |
| **What this produces** | The four derived topics, live |
| **Last will** | Topic, QoS, retained, and the frozen-timestamp sentence |
| **Simulator controls** | Fenced *not part of the device*: one badge button per roster entry, interlock toggle, air-supply sag, live valve state |

Saving a new topic: **a retained message outlives the config that produced it.** No cleanup
path, deliberate (2026-08-23) — a real device would not clean up either. The page's warning is
the whole remedy.

Settings persist to `/data/config.json` on `valve-mqtt-config`. `VALVE_BASE_TOPIC` /
`VALVE_PUBLISH_QOS` / `VALVE_PUBLISH_RETAIN` are **factory defaults only** — once saved, the
volume wins. `python tasks.py nuke` returns the device to factory.

---

## Implementation

`services/sim-valve-mqtt/`, `paho-mqtt` and nothing else.

| File | |
|---|---|
| `valve.py` | Roster, interlock, state machine, stroke faults, air-supply and enclosure simulation. Knows nothing about MQTT. **Identical to the spb copy** |
| `app.py` | Config, envelope, paho client, will, page's view of the device |
| `webui.py` | Stdlib config server. **Identical to the spb copy** |
| `page.html` | The page |

---

## Infrastructure

**MQTT user `sample-valve-01`**, publish `icc26/site1/upstream/#`, subscribe nothing. Area
wildcard on purpose — re-address to another cell on stage and it still works; cannot put
sample data in `qc`. See `compose/chariot/README.md`.

- **`MQTT_USERS` seeds Chariot on first run only.** On an already-running broker the new
  accounts do not exist. Both valves connect today because `allowAnonymous` is `true`; when
  that goes `false` before the talk, a `nuke` is what creates them.
- **`compose/postgres/initdb/` runs on an empty volume only.** `03-seed.sql` carries
  `BR-202`, `sample-valve-01` and `sample-valve-02`; against a live database, apply the
  equivalent `DELETE`/`INSERT` by hand.

---

## Ingest, as built

Verified 2026-08-17 against a gateway seeded from an empty volume.

`…/com.cirruslink.mqtt.engine.gateway/custom-namespace/icc26-native/config.json`

```json
"subscription": "icc26/site1/upstream/br-201/sample-valve-01/#"
```

`jsonPayload: true`, `qos1: true`, `writeableTags: false`, `numbersAsFloats: true`,
`rootFolder: ""`. No UDT, no event stream, no routing script — Engine auto-creates tags from
the JSON:

```
MQTT Engine/icc26/site1/upstream/br-201/sample-valve-01/
    {event,state,telemetry}/{ts,seq}
    {event,state,telemetry}/meta/{mechanism,event,cell,ingest_ts,assembly_serial}
    {event,state,telemetry}/source/{id,type}
    {event,state,telemetry}/values/…
```

> **Measured against the single-`event` design.** Under the split, `event` becomes a folder
> with `badge-scan` and `sample-complete` beneath it — five branches rather than three. **The
> subscription needs no edit** (`#` covers the extra level). Re-measure when the split lands.

`meta.mechanism` is duplicated into every branch because the tree mirrors the document. The
split makes that worse (five copies, not three). Pattern 2 has one node with 19 metrics.

**`event` is its own branch, and Engine decided that** — which settles whether it should
instead have been a Perspective-only audit table.

> The `icc26-native` Engine namespace is **this pattern's only ingest surface**. Deleting it
> breaks pattern 1.

### The subscription has to name one device

`icc26/site1/upstream/+/+/#` also swallows pattern 5's `…/br-201/batch/event` and pattern 7's
`…/br-201/sample-chain/event` — **`#` matches zero levels too.** So this subscription
enumerates a single device, and **adding a second plain-MQTT valve means editing this file.**
Pattern 2's `spBv1.0/#` already covers every edge node that will ever exist.

### What Engine does with the payload — measured

| Field | Tag datatype |
|---|---|
| `ts`, `ingest_ts`, `since`, `sample_start`, `sample_completion` | **String** — not DateTime. Pattern 2 sends real DateTime metrics |
| every number (`seq`, `cycle_count`, `valve_cycles_total`, `uptime_s`, `position_pct`, `open_duration_s`, `air_supply_bar`, `enclosure_temperature_c`) | **Float8** — `numbersAsFloats: true` turns every counter into a float |
| `is_open`, `interlock_ok` | Boolean |
| everything else | String |

**A JSON `null` produces no tag at all.** The key is skipped; the tag never exists.
`values.sample_id` was absent until the first *granted* scan. **The shape of the tag tree is a
function of which messages happened to arrive**, not of the payload contract. Pattern 2
declares `Badge/LastScanId` and `Sample/LastSampleId` as typed Strings in DBIRTH before
anybody has badged in.

Under the topic split this lands asymmetrically: `event/sample-complete` only publishes when a
sample ran, so its `values/sample_id` exists from the first completed sample.
`event/badge-scan` carries `null` on every denial, so **its** `values/sample_id` does not
appear until the first grant.

### Death, measured both ways

Both paths publish `state: "offline"`, and **both timestamps are the moment the session
connected** — within 7 ms. The will document is built before CONNECT and can never describe
the death it announces:

| | delivered | payload `ts` | stale by |
|---|---|---|---|
| `docker stop` (graceful) | 01:34:02.882 | 01:30:12.169 | **3 m 50 s** |
| `docker kill` (will fires) | 01:34:29.887 | 01:34:15.929 | **14 s** |

**Those two `stale by` figures are not a comparison.** Staleness equals session age in both
rows; rerun the test and they come out different. The invariant is `payload ts == connect
time`.

A graceful stop still publishes: the broker discards the will, but the device publishes the
same frozen document itself as part of clean shutdown — live `state: "locked"` first, then
stale `offline`. **The graceful path is therefore broken by a second route.**

### Three behaviours captured on purpose, and not fixed

- **Retained events replay on reconnect.** A brand-new subscriber immediately received a badge
  scan from 21 s earlier with nothing marking it historical. Engine gets this on every
  reconnect and presents it as current.
- **Each scan overwrites the last.** One message retained per topic; the tag model holds one
  set of badge tags. A granted `B-1042` scan was gone minutes later, replaced by a `B-2087`
  denial. Tag history is the remedy; it stays off. **The topic split does not fix this** — it
  stops a completion clobbering a scan, but scan-overwrites-scan is untouched.
- **Identity leaked into the state topic.** `state_snapshot()` carries `badge_id` and
  `sample_id` (`valve.py:178-179`), so retained `state` names whoever last held the valve —
  and if the device dies mid-sample it goes on naming them, forever, next to `state: offline`.
  Pattern 2 has the same fields, declared as metrics in DBIRTH rather than an accident of a
  snapshot function.

---

## Verification

Apply on-disk config with `python tasks.py scan`. Watcher, in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

> **Turn the auto-scanner off first.** `VALVE_SCAN_INTERVAL_S=0`, then `python tasks.py up`.
> The device badges **itself every 90 seconds** by default (`valve.py:346-359`,
> `docker-compose.yml:348-350`), one scan in five with the unknown badge. Left on, it
> invalidates every step below. **On stage, leave it on.**

**1 — Both pages answer.** <http://localhost:8085> and <http://localhost:8086>.

**2 — A granted scan.** Press `B-1042`. Expect, in order: `event/badge-scan` granted +
`sample_id`, `state` walking `unlocking → open → closing → locked`, then ~15 s later
`event/sample-complete` with the same `sample_id` and `cycle_result: normal`.

**3 — Every denial.** `B-2087` → `badge-not-authorized`. `B-3311` → `training-expired`.
`B-9999` → `badge-unknown`. Interlock off, then `B-1042` → `interlock-open`. `B-1042` twice
quickly → `valve-busy` on the second. Each: one `event/badge-scan`, **nothing on
`event/sample-complete`**, **no state change**.

**4 — Retain does the work.** Start a second `mosquitto_sub` → retained `state` arrives
immediately. `docker kill icc26-sim-valve-mqtt` → retained `state: "offline"` will; `ts` is
connect time. Restart, set Retained **off**, repeat — the new subscriber gets nothing.

**5 — The config page round trip.** Change topic to
`icc26/site1/upstream/br-202/sample-valve-01`, save, scan → traffic on the new topic; page
warns about the retained message left at the old one. Change to
`icc26/site1/qc/lims/sample-result` → ACL refuses (needs `allowAnonymous: false`).

**Then change it back.** Engine's subscription names `br-201` explicitly, so while
re-addressed the tag tree is frozen. Leave it and the rest of the demo is dead.

**6 — `docker stop` vs `docker kill`.** `stop` disconnects cleanly and the broker discards
the will. Only `kill` proves the will works.

**7 — `cycle_result`** (once open item 2 lands). Sag the air supply, press `B-1042`. Scan is
granted; sample completes `failed-to-seat`. Restore supply; next sample is `normal`.

**8 — The sample id reaches the Nova** (once open item 1's Wave-1 half lands). A granted
scan's `values.sample_id` matches what Ignition wrote into the Nova's
`SampleInformation/SampleID`.

---

## Open items

| # | Item | Status |
|---|---|---|
| 1 | **The valve's `sample_id` must reach the Nova.** Valve mints `S-YYYYMMDD-NNNN`, Nova mints `S-NNNNN`; the containers never talk. Ignition writes the valve's id into the Nova's writable `SampleInformation/SampleID` before the run. Touches 01 and 03 | open — **pattern 7 cannot be specified until it lands.** Unaffected by dropping `meta.correlation_id` |
| 2 | **The contract above is ahead of the build.** Four unwritten changes: `event/<subtype>` split; payload changes (`valve_state` out, `sample_start`/`sample_completion` renamed); `cycle_result` plus a fault path in `valve.py`; telemetry re-pointed to air supply / enclosure temperature. `valve.py` is shared, so the last two land in pattern 2 as well | open — one change, both containers, `diff` before committing |
| 3 | **The Last Will is not the `state` document.** Four keys including a free-text `note`. Knock-on (dead valve's tag tree keeps last live position/interlock/cycle count) is **inferred from the null rule, not measured** | open — measure it, then either fix the will to carry the full snapshot or keep the gap and say so on stage |
| 4 | **Pattern 7 cannot look up a past valve event.** One message retained per topic, tag history off. Either 7 subscribes live and holds its own state, or history goes on for the two event branches | open — decide in pattern 7's spec |
| 5 | `allowAnonymous` is still `true`, so the ACL talk point is not enforced and verification step 5's refusal cannot be shown | tracked in `compose/chariot/README.md`; must be `false` before the talk |
| 6 | Perspective page for pattern 1 | per master plan §08 — mostly a link out to 8085 and 8086 |

Closed 2026-08-23: event-branch-vs-audit-table (Engine's auto-created tree settled it);
retained-message-at-the-old-topic (kept deliberately, no cleanup); `meta.correlation_id` in
pattern 1 (dropped — id travels as `values.sample_id`).

---

## Progress log

| Date | Change |
|---|---|
| 2026-08-17 | Re-scoped vibration gateway → smart sample valve. `services/sim-valve-mqtt/` built. First gateway run: Engine namespace narrowed to one device, JSON-null behaviour and both death paths measured. |
| 2026-08-23 | Vibration work deleted. Talk material split to [`../talk-tracks/01-native-mqtt.md`](../talk-tracks/01-native-mqtt.md). Payload/topic redesign: `event` splits into two subtypes; `meta.correlation_id` dropped; `valve_state` dropped; telemetry re-pointed to air/enclosure; `cycle_result` added. Four findings recorded (will ≠ state document; `meta.event` echoes message type; `seq` is process-local; auto-scanner undocumented). |
