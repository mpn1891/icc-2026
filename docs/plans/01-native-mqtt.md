# 01 — Native MQTT: smart sample valve assembly

> **Build spec.** What exists, what it publishes, and how it was measured. Talk material lives
> in [`../talk-tracks/01-native-mqtt.md`](../talk-tracks/01-native-mqtt.md). Progress log at the
> bottom. Supersedes spec 01 in [`00-master-plan.md`](00-master-plan.md).

| | |
|---|---|
| **Pattern** | 1 of 7 — native MQTT pub/sub, hand-rolled everything |
| **Mechanism tag** | **none** — this pattern carries no `meta` at all (2026-08-25) |
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

**No `state` topic, no interlock** (2026-08-25). Valve position is not published at all, and
authorization is the roster and only the roster. Liveness is a retained `status` topic —
`online` on connect, `offline` by Last Will. `state` joins `cmd` and `response` as vocabulary
the architecture doc keeps and **no pattern uses.**

**`valve.py` and `webui.py` are byte-for-byte identical to the copies in
`services/sim-valve-spb/`.** Self-contained build contexts, no shared library. The two
containers must differ *only* in how they speak. Fix one, copy it across, `diff` before
committing.

---

## Physical model

A sanitary diaphragm sample valve with an integrated RFID reader, pneumatic actuator and
position feedback, on the sample port of `BR-201`. Serial `SV-2000-0417`.

An operator presents a badge. The assembly checks it against its own roster, strokes open for a
sampling window, and closes. **Every scan is published, granted or denied.**

```
   badge presented at the reader
            │
            ▼
   ┌──────────────────┐   on the roster, right role?
   │  LOCKED          │──────────── no ──────────► event/badge-scan, result=denied
   └────────┬─────────┘                            (nothing moves)
            │ yes
            ▼
      UNLOCKING ──► OPEN ──► CLOSING ──► LOCKED
      (1.5 s)      (12 s)    (1.5 s)        │
            │                               └──► event/sample-complete
            └──► event/badge-scan, result=granted, sample_id assigned

   on CONNECT ──► status: online   (retained, published by the device)
   on death   ──► status: offline  (retained, published by the broker — Last Will)
   every 5 s  ──► telemetry topic
```

**The stroke itself is not published.** With `state` cut, the four states above exist only on
the device's own page; outside the box a sample is two events fifteen seconds apart and nothing
in between. Pattern 2 declares `Valve/State` as a metric in DBIRTH.

Deny reasons, checked **in this order**:

| Reason | |
|---|---|
| `badge-unknown` | not on the roster |
| `badge-not-authorized` | on the roster, wrong role |
| `valve-busy` | mid-cycle for somebody else |

Roster ships two: `B-1042` authorized, `B-2087` wrong role. The page offers a third button for
`B-9999`, which is on no roster; `valve-busy` needs `B-1042` pressed twice in quick succession.

---

## Topic contract

Namespace from [`../00-architecture.md`](../00-architecture.md):
`icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}`. **This device does not know
that.** The base topic is a factory default in a text box.

| Topic | Dir | QoS | Retained | Purpose |
|---|---|---|---|---|
| `icc26/site1/upstream/br-201/sample-valve-01/event/badge-scan` | out | 1 | yes | One per badge presented, granted or denied |
| `icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete` | out | 1 | yes | One per sample that actually ran |
| `icc26/site1/upstream/br-201/sample-valve-01/status` | out | 1 | yes | `online` \| `offline`; **also the Last Will** |
| `icc26/site1/upstream/br-201/sample-valve-01/telemetry` | out | 1 | yes | Actuator air supply / enclosure temperature, every 5 s |

Nothing is subscribed.

### Why the two event subtypes are two topics

`event/<subtype>` is a **two-token message type**, same shape as `cmd/<verb>`.
`icc26/+/+/+/+/event/#` still catches every event — `#` matches zero levels. Recorded in
[`../00-architecture.md § Topic namespace`](../00-architecture.md); no other pattern is
obliged to grow a subtype.

Two topics because of the tag tree, not the wire: the two documents carry **different field
sets**, Engine's custom namespace mirrors whatever document arrives, and a document writes only
the keys it contains — so one `event/values/` folder would hold the union of two schemas with
half the tags stale (`deny_reason` still reading the last denial after a granted sample). The
long form of this argument is in
[`../00-architecture.md § Topic namespace`](../00-architecture.md).

The page offers one QoS and one Retained flag for every derived topic. Honest settings would
be QoS 1 unretained for the events, QoS 1 retained for `status`, QoS 0 unretained for telemetry.
Shipping default is `1` / retained: right for `status`, wasteful for `telemetry`, and it
leaves stale events on the broker for the next subscriber to mistake for live ones.

**Built 2026-08-25.** `app.py:68-76` maps `valve.py`'s event name onto the two topics.

---

## Payload contracts

**This pattern does not use the shared envelope** (2026-08-25). Every document is exactly two
keys:

```json
{ "ts": "2026-08-25T20:32:00.836Z", "values": { } }
```

No `seq`, no `source`, no `meta` — so no `mechanism`, no `ingest_ts`, no `event`, no `cell`, no
`assembly_serial`, and no `correlation_id`. The
[shared envelope](../00-architecture.md) is the house standard for **the patterns we write**;
this one is a device somebody bought, and a bought device ships what its firmware author
decided.

What that costs, said plainly rather than discovered on stage:

| Gone | What it means now |
|---|---|
| `meta.mechanism` | Nothing in a pattern-1 document says how it arrived. The topic is the only clue, and the topic is a text box |
| `source.id` / `source.type` | The device does not name itself. **Identity is the topic string somebody typed** — re-address it and the same device is a different device |
| `seq` | No loss detection of any kind. There is no longer even the *appearance* of one |
| `meta.ingest_ts` | Nothing reveals the gap between when a thing happened and when the document was built — which is the entire Last Will problem, now invisible from inside the payload |
| `meta.event` | It only ever echoed the topic's last level. Deleting it is the fix for the redundancy, not a loss |

The sample id travels as `values.sample_id`, as it has since 2026-08-23.

### `event/badge-scan` — one per badge presented

`values`: `badge_id`, `badge_holder`, `badge_role`, `result` (`granted` | `denied`),
`deny_reason` (`null` on a grant), `scan_time`, `sample_id` (minted on a grant; **`null` on a
denial** — a denial belongs to no sample).

Published the instant the badge is read, before the valve has moved; `scan_time` is that
instant, and `sample-complete` is the record of what followed. Unknown badges report
`badge_holder` and `badge_role` as `"unknown"` (the page says `not on roster` — same fact).


### `event/sample-complete` — one per sample that ran

`values`: `sample_id`, `badge_id`, `badge_holder`, `sample_start`, `sample_completion`,
`open_duration_s`, `cycle_result`, `cycle_count`.

Published ~15 s after the granted scan (1.5 + 12 + 1.5). Repeats badge fields so the record
is self-contained. `sample_start` / `sample_completion` are the old `opened_at` / `closed_at`.
`open_duration_s` is close-finish minus open-finish, so ~13.5 s against a 12 s window — the
valve is still passing material while it seats.

**`cycle_result`** — `normal` | `failed-to-seat` | `stroke-timeout`.
`failed-to-seat` is the demo path: position feedback does not return to 0 % on close.

**Built 2026-08-25.** The fault is decided from air supply at the start of the close stroke
(`valve.py:89-95, 381-400`): below `AIR_SUPPLY_SEAT_BAR` (4.5) position feedback rests at a
12 % residual instead of returning to 0 — `failed-to-seat`. `stroke-timeout` fires at the open
stroke off a lower threshold, `AIR_SUPPLY_STROKE_BAR` (2.5). First fault of a cycle wins.
**The shipped sag (3.2 bar) sits between the two thresholds**, so the page's one button always
yields `failed-to-seat`; `stroke-timeout` needs `AIR_SUPPLY_SAG_BAR` set below 2.5. All five
numbers were invented at build time — no vendor data behind them.

### `status` — the birth/will pair, and the death certificate

`values`: `state` (`online` | `offline`), `note`.

Two publishes, one topic, both retained, following the pattern in
[HiveMQ MQTT Essentials part 9](https://www.hivemq.com/blog/mqtt-essentials-part-9-last-will-and-testament/):

| | published by | when |
|---|---|---|
| `state: "online"` | the device, first publish after CONNACK | every successful connect |
| `state: "offline"` | **the broker**, from the will registered in CONNECT | I/O error, keep-alive expiry, or a connection closed without DISCONNECT |

Retained is what makes the pair worth anything: a subscriber that connects tomorrow is handed
the last one immediately and knows whether the valve is up without waiting for it to say
something. **Untick Retained on the config page and the pair becomes worthless** — the will
still fires, but only to whoever already happened to be subscribed.

**The will is registered in the CONNECT packet, so its payload is built before the connection
it will one day announce the end of.** Two consequences, both deliberate, both demonstrated:

- **`ts` is connect time, not death time.** The document cannot describe its own death;
  measured below.
- **Nothing on the backbone knows this topic means death** unless it was told separately. The
  device does try — `note` reads *"last will — ts is when this session connected, not when it
  died"*. It says so in English, in a field nothing parses.

**A graceful DISCONNECT makes the broker discard the will** — the article is explicit about
this — so the device publishes the same frozen `offline` document itself as part of clean
shutdown. Same wrong timestamp, none of the will machinery involved. **The graceful path is
broken by a second route.**

**Built 2026-08-25.** `app.py:320-326` registers the will, `:368-371` publishes the birth, and
`:391-409` republishes **the same bytes** on graceful shutdown — the document is kept on the
sink rather than rebuilt, so the frozen `ts` survives both routes.

### `telemetry` — every 5 s

`values`: `air_supply_bar`, `enclosure_temperature_c`, `valve_cycles_total`, `uptime_s`.

Re-pointed 2026-08-23 from line pressure/temperature: a shut sample valve has nothing moving
through it, so the old pair was a dead-leg reading or a restatement of BR-201's own
instruments. What the assembly measures all the time is **its own condition**. **Low air supply
is the physical cause of `failed-to-seat` and `stroke-timeout`** — sag it from the config page
to demonstrate the fault.

**Built 2026-08-25.** `valve.py`'s drift simulates the assembly's own condition, with the
supply dipping while the actuator strokes.

---

## The configuration page

<http://localhost:8085> — [`../../services/sim-valve-mqtt/webui.py`](../../services/sim-valve-mqtt/webui.py)
+ `page.html`. `http.server`, one HTML file, **no external assets** (demo runs with networking
disabled).

| Section | |
|---|---|
| **MQTT publishing** | Topic (free text), QoS (0/1/2), Retained. Save reconnects — a new topic means a new will, registered only at CONNECT |
| **What this produces** | The four derived topics, live |
| **Last will** | Topic, QoS, retained, both halves of the birth/will pair, and the frozen-timestamp sentence |
| **Simulator controls** | Fenced *not part of the device*: one badge button per roster entry plus off-roster `B-9999`, air-supply sag, live valve state (page-only — it is on no topic) |

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

> **The envelope is gone as of 2026-08-25**, so three of those four lines no longer exist. The
> tree Engine will build now is `{event/badge-scan, event/sample-complete, status,
> telemetry}/ts` plus `…/values/…` and nothing else. Every complaint below about `meta` being
> duplicated into every branch is resolved by deletion rather than by design.

> **Measured 2026-08-17, against the single-`event` design and the old `state` topic.** Under
> the current contract `event` becomes a folder with `badge-scan` and `sample-complete` beneath
> it, and `state` is renamed `status` — the tree reads `{event/badge-scan,
> event/sample-complete, status, telemetry}`. **The subscription needs no edit** (`#` covers the
> extra level). Re-measure when the build catches up.

`meta.mechanism` was duplicated into every branch because the tree mirrors the document — four
copies, one per document published. Pattern 2 has one node with 19 metrics. **Moot since the
envelope was cut**, and worth saying on stage as the shape of the problem rather than a live
defect: a tree that mirrors documents repeats whatever the documents repeat.

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
| `ts`, `scan_time`, `sample_start`, `sample_completion` | **String** — not DateTime. Pattern 2 sends real DateTime metrics |
| every number (`cycle_count`, `valve_cycles_total`, `uptime_s`, `open_duration_s`, `air_supply_bar`, `enclosure_temperature_c`) | **Float8** — `numbersAsFloats: true` turns every counter into a float |
| everything else | String |

> The rules are the measurement; the field lists have moved since. Booleans were measured on
> `is_open` and `interlock_ok`, and **no field in the current contract is a boolean**;
> `scan_time` is new and unmeasured (expect String, like every other timestamp).

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

Measured on the old `state` topic; the rename to `status` does not touch any of it. Both paths
publish `offline`, and **both timestamps are the moment the session connected** — within 7 ms.
The will document is built before CONNECT and can never describe the death it announces:

| | delivered | payload `ts` | stale by |
|---|---|---|---|
| `docker stop` (graceful) | 01:34:02.882 | 01:30:12.169 | **3 m 50 s** |
| `docker kill` (will fires) | 01:34:29.887 | 01:34:15.929 | **14 s** |

**Those two `stale by` figures are not a comparison.** Staleness equals session age in both
rows; rerun the test and they come out different. The invariant is `payload ts == connect
time`.

A graceful stop still publishes: the broker discards the will, but the device publishes the
same frozen document itself as part of clean shutdown. **The graceful path is therefore broken
by a second route.**

### Notes

- **Retained events replay on reconnect.** A brand-new subscriber immediately received a badge
  scan from 21 s earlier with nothing marking it historical. Engine gets this on every
  reconnect and presents it as current.
- **Each scan overwrites the last.** One message retained per topic; the tag model holds one
  set of badge tags. A granted `B-1042` scan was gone minutes later, replaced by a `B-2087`
  denial. Tag history is the remedy; it stays off. **The topic split does not fix this** — it
  stops a completion clobbering a scan, but scan-overwrites-scan is untouched.


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

**2 — A granted scan.** Press `B-1042`. Expect: `event/badge-scan` granted + `sample_id`, then
~15 s later `event/sample-complete` with the same `sample_id` and `cycle_result: normal`.
**Nothing lands in between** — the page walks `unlocking → open → closing → locked` while the
wire stays silent, which is the point of cutting `state`.

**3 — Every denial.** `B-2087` → `badge-not-authorized`. `B-9999` → `badge-unknown`. `B-1042`
twice quickly → `valve-busy` on the second. Each: one `event/badge-scan`, **nothing on
`event/sample-complete`**.

**4 — The birth/will pair.** On start, `status: "online"` retained. Start a second
`mosquitto_sub` → it arrives immediately, before the valve does anything.
`docker kill icc26-sim-valve-mqtt` → `status: "offline"` from the broker; **read its `ts` out
loud** — it is connect time. Restart, set Retained **off**, repeat: the will still fires to
whoever is already subscribed, and a late subscriber gets nothing at all.

**5 — The config page round trip.** Change topic to
`icc26/site1/upstream/br-202/sample-valve-01`, save, scan → traffic on the new topic; page
warns about the retained message left at the old one. Change to
`icc26/site1/qc/lims/sample-result` → ACL refuses (needs `allowAnonymous: false`).

**Then change it back.** Engine's subscription names `br-201` explicitly, so while
re-addressed the tag tree is frozen. Leave it and the rest of the demo is dead.

**6 — `docker stop` vs `docker kill`.** `stop` disconnects cleanly and the broker discards the
will — but `status: "offline"` lands anyway, published by the device on its way out with the
same frozen `ts`. Only `kill` proves the *will* works; both prove the timestamp is wrong.

**7 — `cycle_result`.** Sag the air supply, wait one telemetry tick, press `B-1042`. Scan is
granted — **authorization knows nothing about air pressure** — and the sample completes
`failed-to-seat` with the page's position readout resting at 12 %. Restore supply; the next
sample is `normal`. The telemetry had been reporting the sag for minutes.

**8 — The sample id reaches the analyzer, by hand.** Read `values.sample_id` off the granted scan,
type it into the analyzer's sample-login screen on :8087, press Run. The analyzer's result echoes
the same string on `HistoricalSampleResults/StartTags/SampleInformation/SampleID`, and the LIMS
on :8000 appends the analytes to the entry that scan already opened.

Then do it wrong on purpose. One transposed character and the entry stays *awaiting analysis*
while the result parks under **Unmatched results** — the whole cost of a transcription step,
on one screen, in about fifteen seconds.

---

## Open items

| # | Item | Status |
|---|---|---|
| 1 | **The valve's `sample_id` must reach the analyzer.** | **closed 2026-08-26 — and not the way this row predicted.** No Ignition tag write. The analyzer got its own sample-login screen (`services/opcua-cell-analyzer/webui.py`, port 8087) and **a person types the valve's id into it**, which is what a plant does and what makes the id fallible. Nothing in this pattern changed: the valve still mints on the grant and still publishes `values.sample_id` on `event/sample-complete`. The transcription is now this pattern's sharpest risk beat — *no transcription, no intermediary*, said in front of the intermediary. See [`../00-architecture.md` § *The sample id, and pattern 1 mints it*](../00-architecture.md) |
| 2 | **The contract above is ahead of the build** | **closed 2026-08-25 — landed.** All seven changes are in: the `event/<subtype>` split, `scan_time`, the `sample_start`/`sample_completion` renames, `cycle_result` with an air-supply fault path, telemetry re-pointed, `state` → `status` as a birth/will pair, and interlock + `training-expired` removed. `valve.py` and `webui.py` re-verified byte-for-byte identical across both containers. **Not re-measured against a gateway** — see item 8 |
| 3 | **The Last Will is not the `state` document** | **closed 2026-08-25 by the `status` redesign.** The mismatch existed because `state` carried nine fields and the will carried four; `status` carries `state` + `note`, and the will carries exactly that. The dead-valve tag tree can no longer keep stale position/cycle-count next to `offline`, because that topic no longer has them. The frozen `ts` and the unparsed `note` stay — deliberate, demonstrated in verification 4 and 6 |
| 4 | **Pattern 7 cannot look up a past valve event.** One message retained per topic, tag history off. Either 7 subscribes live and holds its own state, or history goes on for the two event branches | **narrowed 2026-08-26.** Pattern 4 now subscribes to `event/sample-complete`, stores it as a `lims.sample` row, and republishes `sample_start` / `cycle_result` / the badge holder on the released review message — so *this* pattern's contribution is persisted and reaches 07 without 07 storing anything. Still open for 5 and 6. Decide it once for those two before 07's spec — see [`../00-architecture.md`](../00-architecture.md) |
| 5 | `allowAnonymous` is still `true`, so the ACL talk point is not enforced and verification step 5's refusal cannot be shown | tracked in `compose/chariot/README.md`; must be `false` before the talk |
| 6 | Perspective page for pattern 1 | **closed 2026-08-25 — cut.** Spec 08 is gone; there are no Perspective views. The pattern 1 / pattern 2 comparison is the two device config pages on 8085 and 8086 themselves, which is what §08 had reduced it to anyway |
| 7 | **Nothing on the wire says where the valve is.** With `state` cut, `is_open` / `position_pct` exist only on the device's own page. Pattern 7 does not need them (it reads `event/sample-complete`), and no other consumer has asked — but it is a live gap against pattern 2, which declares `Valve/State` in DBIRTH | open — leave cut unless a consumer needs it; the asymmetry with pattern 2 is a talk point, not a defect |
| 8 | **The ingest section below is measured against the old payloads.** Engine built that tree on 2026-08-17 from a single `event` topic, a `state` topic and a line-pressure telemetry pair. The build has since changed all three. Nothing in the *rules* is expected to move — a JSON null still produces no tag, numbers are still Float8 — but the branch names and the tag list will | open — re-measure against a gateway seeded from an empty volume, then restate § *Ingest, as built* |

Closed 2026-08-23: event-branch-vs-audit-table (Engine's auto-created tree settled it);
retained-message-at-the-old-topic (kept deliberately, no cleanup); `meta.correlation_id` in
pattern 1 (dropped — id travels as `values.sample_id`).

Closed 2026-08-25: the `state` topic (cut — valve position is not published); the interlock and
`training-expired` (cut — authorization is the roster and only the roster); the will-vs-state
mismatch (item 3, dissolved by the `status` redesign); the pattern 1 Perspective page (item 6); the whole
spec-ahead-of-build backlog (item 2, landed).

---

## Progress log

| Date | Change |
|---|---|
| 2026-08-17 | Re-scoped vibration gateway → smart sample valve. `services/sim-valve-mqtt/` built. First gateway run: Engine namespace narrowed to one device, JSON-null behaviour and both death paths measured. |
| 2026-08-25 | **Envelope cut.** Every document is now `ts` + `values` and nothing else — no `seq`, no `source`, no `meta`. Pattern 1 no longer stamps `meta.mechanism`, no longer names itself in `source`, and no longer carries even the appearance of loss detection. Verified on the wire across all four topics. The `meta.event`-is-not-a-discriminator finding and the `seq`-is-process-local finding are both **deleted rather than resolved** — the fields are gone. |
| 2026-08-25 | **`state` → `status`.** Valve position dropped from the wire entirely; `status` is the birth/will pair only (`online` on CONNACK, `offline` by Last Will), retained, per [HiveMQ MQTT Essentials part 9](https://www.hivemq.com/blog/mqtt-essentials-part-9-last-will-and-testament/). Interlock and `training-expired` cut — authorization is the roster alone, so `aborted-interlock` leaves `cycle_result` and `interlock_ok` leaves telemetry. `scan_time` added to `event/badge-scan`. Items 3 and 6 closed; item 7 opened. |
| 2026-08-25 | **Spec landed in both containers.** `event` split; `scan_time` added; `sample_start`/`sample_completion` renamed; `cycle_result` driven by a real air-supply fault path; telemetry re-pointed; `state` → `status` as a retained birth/will pair; interlock and `training-expired` removed. Knock-on in pattern 2: `Interlock/Ok` dropped, `Sample/LastCycleResult` added, `Line/*` renamed — nineteen metrics, ten typed nulls. Item 2 closed, item 8 opened (ingest needs re-measuring). |
| 2026-08-23 | Vibration work deleted. Talk material split to [`../talk-tracks/01-native-mqtt.md`](../talk-tracks/01-native-mqtt.md). Payload/topic redesign: `event` splits into two subtypes; `meta.correlation_id` dropped; `valve_state` dropped; telemetry re-pointed to air/enclosure; `cycle_result` added. Four findings recorded (will ≠ state document; `meta.event` echoes message type; `seq` is process-local; auto-scanner undocumented). |
