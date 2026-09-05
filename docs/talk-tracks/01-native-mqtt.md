# 01 — Native MQTT: the smart sample valve

> Talk track for pattern 1. The spec this was built from is
> [`plans/01-native-mqtt.md`](../plans/01-native-mqtt.md). Architecture decisions live in
> [`00-architecture.md`](../00-architecture.md); this file is what you speak.
>
> **Read it with [`02-sparkplug-b.md`](02-sparkplug-b.md).** They are one device in two
> firmwares, and neither segment lands alone.

| | |
|---|---|
| **Pattern** | 1 of 7 — native MQTT pub/sub, hand-rolled everything |
| **Mechanism tag** | **none** — this device carries no `meta` at all |
| **Container** | `sim-valve-mqtt` — [`services/sim-valve-mqtt/`](../../services/sim-valve-mqtt/) |
| **Config page** | <http://localhost:8085> — this *is* the demo, as much as the traffic |
| **Depends on** | nothing |
| **Blocks** | pattern 7 reads this valve's event for when material left the reactor |
| **Signal contributed** | **Sample actuation event** — the badge scan and the valve stroke |
| **GxP hook** | The record originates at the point of action. No transcription, no intermediary |

## The segment

**Intro.** A sanitary diaphragm sample valve with an RFID reader on the sample port of
`BR-201`. Serial `SV-2000-0417`. An operator badges in, the valve strokes open for a sampling
window, and closes. It is the most *ordinary* thing on the backbone, and that is why it earns
the slot — somebody bought it, commissioned it through a web page, and pointed it at a broker.

**Demo.** Open the config page. Badge `B-1042` and watch a sample happen. Then badge the three
that are refused.

**Risk.** Everything the record depends on was typed into a text box, and two of the defaults
quietly destroy the audit trail.

**Close.** *(unassigned — see the master plan's open items)*

## Talk points

**1. The commissioning page is the protocol.** Everything this device promises the outside
world is three form fields — a topic, a QoS, and a retained flag — typed in by whoever
installed it. Nothing validates the topic against the site namespace. Nothing knows a badge
scan is an audit record and a line temperature is not, so one QoS applies to both. Pattern 2's
page has the same three controls greyed out. **Put the two screenshots side by side and the
argument makes itself**, before a single message crosses the wire.

**2. The device knows nothing about itself, so everything else must be agreed.** The payload
shape is ours. The death certificate is a retained JSON document on a `status` topic we picked,
paired with an `online` message on connect by convention alone, and its timestamp is wrong by
construction. Datatypes are whatever `json.dumps` produced. Every one of
those has to be written down somewhere and kept in step forever — and the place it gets written
down is an Ignition tag configuration, by hand, which is exactly the work pattern 2 does not do.

**3. The namespace holds because an ACL holds it.** `sample-valve-01` may publish to
`icc26/site1/upstream/#` and nothing else, so the free-text topic box cannot put sample data in
the QC area. That rule lives in `compose/chariot/mqtt-users.json` — not in the device, not in
the protocol. Delete it and the discipline goes with it.

> **Before the talk:** `allowAnonymous` is still `true` on Chariot, so the ACL is not actually
> enforced yet and talk point 3 cannot be demonstrated live. Flip it to `false` and `nuke` to
> seed the accounts. Tracked in `compose/chariot/README.md`.

### What this pattern deliberately does *not* do

**No command path. At all.** There is no `cmd` topic, nothing is subscribed, and nothing on the
backbone can open this valve. Authorization is decided at the sample port against a roster the
assembly holds locally, because a sample port that stops working when the broker does is not
one anybody would install. That is not a simplification for the demo — it is what makes the
device credible, and it means both patterns 1 and 2 are pure publishers.

**No request/response.** Nothing asks this device for anything. Chariot is MQTT 3.1.1 and has no
response-topic property, and this pattern never needs to find that out.

## The wire

Four topics, all outbound, nothing subscribed:

| Topic | QoS | Retained | Purpose |
|---|---|---|---|
| `…/sample-valve-01/event/badge-scan` | 1 | yes | One per badge presented, granted or denied |
| `…/sample-valve-01/event/sample-complete` | 1 | yes | One per sample that actually ran |
| `…/sample-valve-01/status` | 1 | yes | `online` \| `offline`; **also the Last Will** |
| `…/sample-valve-01/telemetry` | 1 | yes | Actuator air supply, enclosure temperature, every 5 s |

**The QoS and Retained columns are identical down the table, and that is the finding.** The page
offers one of each, applied to every topic it derives. The honest settings would be QoS 1
unretained for the two event subtypes, QoS 1 retained for `status`, QoS 0 unretained for telemetry
— three different answers the device cannot express, now spread across four topics.

**Why the events are two topics** is worth thirty seconds, because it is a decision somebody had
to make and get right. The two documents carry different fields, and Ignition's tag tree mirrors
whatever document arrives — writing only the keys that are present. Put both on one topic and
you get one folder holding the union of two schemas, where `deny_reason` still reads whatever
the last *denial* said long after a sample succeeded. Nothing in the tree tells you which
message a tag came from. Pattern 2 cannot make this mistake; the metric list is declared once,
in the birth certificate.

A badge scan on the wire:

```json
{
  "ts": "2026-08-25T20:30:47.041Z",
  "values": {
    "badge_id": "B-2087",
    "badge_holder": "Sam Okafor",
    "badge_role": "maintenance",
    "result": "denied",
    "deny_reason": "badge-not-authorized",
    "scan_time": "2026-08-25T20:30:47.041Z",
    "sample_id": null
  }
}
```

**Read the whole document out loud — that is all of it.** A timestamp and a bag of values. It
does not say what it is, where it came from, what device sent it, how it arrived, or whether
you missed the one before. Every one of those answers lives in the topic string somebody typed
into a text box on the config page, which is the argument of this segment in a single payload.
Pattern 2's equivalent carries datatypes, engineering units, aliases and a spec-mandated
sequence number, and nobody had to agree any of it.

**This valve mints the sample id**, because the sample begins when material leaves the reactor.
It travels as `values.sample_id` and everything downstream carries it unchanged — the analyzer
analyzer, the LIMS review, and pattern 7's composite document are all the same string. A denial
carries `null`, because a denial belongs to no sample.

## The risk beat — four things measured, not asserted

**1. The death certificate cannot say when the device died.** The Last Will goes out on the
`status` topic carrying `state: "offline"`, and its timestamp is **the moment the session
connected** — a will is registered in the CONNECT packet, before the death it describes. So the
payload `ts` is stale by exactly the length of the session, whatever that happened to be.
Measured both ways:

| | payload `ts` stale by | because |
|---|---|---|
| `docker kill` (will fires) | **14 s** | the session had been up 14 s |
| `docker stop` (graceful) | **3 m 50 s** | the session had been up 3 m 50 s |

**Those two numbers are not a comparison** — they differ only because one session ran longer, and
on stage the second one will read differently again. The finding is that the graceful path is
broken *as well*, by a different route: a clean DISCONNECT makes the broker discard the will, so
the device publishes the same frozen document itself. Same wrong timestamp, none of the will
machinery involved.

Sparkplug does not fix this by magic — NDEATH *is* a Last Will and inherits the identical
constraint. It fixes it by making every consumer apply the same rule and having the *consumer*
stamp the time. **Almost all of the difference is the agreement, not the plumbing** — see
[`02-sparkplug-b.md`](02-sparkplug-b.md) for the one exception.

**2. Nothing on the backbone knows that topic means death** unless it was told separately. The
device does try: the will carries a free-text `note` field reading *"last will — ts is when this
session connected, not when it died"*. **It told you, in English, in a field nothing parses.**
That is the whole pattern in one key — the knowledge exists, it just isn't anywhere a machine
can act on it. Pattern 2 puts the same fact in `bdSeq`, and every Sparkplug consumer already
knows what to do with it.

**3. The retained events replay on reconnect, presented as current.** A brand-new subscriber
immediately received a badge scan from 21 s earlier with nothing marking it historical. Ignition
gets this on every reconnect.

**4. Each scan overwrites the last.** One message is retained per topic, and the tag model holds
one set of badge tags. A granted `B-1042` scan was gone minutes later, replaced by a `B-2087`
denial. **A GxP audit trail that silently keeps only the most recent record is invisible until
somebody asks about last Tuesday.** Tag history is the remedy; it stays off, on purpose.

And one more, from the Ignition side: **a JSON `null` produces no tag at all.** Not a dropped
value, not bad quality — the key is skipped and the tag never exists. `values.sample_id` was
absent from the tag tree entirely until the first *granted* scan carried a non-null id. So the
shape of the tag tree is a function of **which messages happened to arrive**, not of the payload
contract.

Say the sharp version: the *same field*, on the *same device*, appears at two different moments
depending on which badge somebody pressed first. `event/sample-complete` only publishes when a
sample ran, so its `sample_id` tag exists from the first completed sample. `event/badge-scan`
carries `null` on every denial, so its `sample_id` does not appear until the first grant. Open
with denials and half the schema is missing. Pattern 2 declares both `Badge/LastScanId` and
`Sample/LastSampleId` as typed Strings in DBIRTH before anybody has badged in.

## On stage

Watcher, in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

| Beat | Trigger | What lands |
|---|---|---|
| Both pages, side by side | 8085 and 8086 | The pattern, before any traffic exists |
| A granted sample | Press `B-1042` | `event/badge-scan` granted + `sample_id`, then 15 s later `event/sample-complete` — **and nothing in between**, though the page shows the valve stroking |
| Every denial | `B-2087`, `B-9999`, `B-1042` twice | One `event/badge-scan` each, **nothing on `sample-complete`** |
| The valve misbehaves | Sag the air supply, press `B-1042` | Granted — authorization knows nothing about air pressure — then `cycle_result: failed-to-seat`. **The telemetry had been saying so for minutes** |
| Retain does the work | Second `mosquitto_sub` | Retained `status: online` arrives before anything happens |
| The death certificate | `docker kill icc26-sim-valve-mqtt` | Retained `offline` will lands — read its `ts` out loud, then read the `note` field out loud |
| …and turn Retain off | Uncheck on the page, repeat | The new subscriber gets **nothing** |
| The ACL | Set topic to `icc26/site1/qc/lims/sample-result` | Refused (needs `allowAnonymous: false`) |

**If you re-address the valve on stage, change it back.** Engine's subscription names `br-201`
by hand, so a re-addressed valve publishes happily to the broker while Ignition's tag tree sits
frozen. That gap *is* a talk point — the text box and the subscription are kept in step by a
human and nothing else — but leave it and the rest of the demo is dead.

Roster: `B-1042` authorized, `B-2087` wrong role, anything else unknown (the page offers
`B-9999`). `valve-busy` is the third denial — press `B-1042` twice in quick succession. All
three paths are demonstrable without editing config on stage.

**`docker stop` disconnects cleanly and the broker discards the will** — only `kill` proves the
will works. Both are worth showing, because the stop path is the one that surprises people: the
device publishes the same frozen `offline` document itself, so it *looks* like the will fired.

## Progress log

| Date | Change |
|---|---|
| 2026-08-23 | Talk track split out of [`plans/01-native-mqtt.md`](../plans/01-native-mqtt.md), which stays the build spec. Through-line signal and GxP hook folded in; `meta.correlation_id` added to the envelope. |
| 2026-08-23 | Moved to `docs/talk-tracks/`; links repointed here and in every inbound file. Risk beat 1 rewritten — the two staleness numbers were being read as a comparison when they only reflect session length, and the real finding is that the graceful path fails by a second route. |
| 2026-08-25 | **Follows the `state` → `status` redesign in the spec.** The valve no longer publishes position at all, so the granted-sample beat is two events fifteen seconds apart with silence in between — the page strokes, the wire does not. `status` is the birth/will pair only (`online` on connect, `offline` by will), which sharpens risk beat 1: the topic now means exactly one thing and *still* cannot say when the device died. Interlock and `training-expired` cut; the third denial is `valve-busy`. |
| 2026-08-23 | **Follows the payload redesign in the spec.** Four topics, not three: `event` splits into `event/badge-scan` and `event/sample-complete`, with a thirty-second aside on why. `meta.correlation_id` dropped — the sample id travels as `values.sample_id`. Telemetry is now actuator air supply and enclosure temperature, which buys a new stage beat: sag the air, the scan is still granted, and the sample comes back `failed-to-seat` with the warning sitting in the telemetry nobody was reading. Risk beat 2 gains the will's `note` field — *it told you, in English, in a field nothing parses*. The null-tag finding gains the two-branch asymmetry. |
