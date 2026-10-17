# 01 — Native MQTT: the smart sample valve

> Pattern 1 of 7. Built from [`plans/01-native-mqtt.md`](../plans/01-native-mqtt.md);
> architecture decisions are in [`00-architecture.md`](../00-architecture.md).
>
> **Read it with [`02-sparkplug-b.md`](02-sparkplug-b.md).** The two are one device in two
> firmwares, and each makes the most sense beside the other.

| | |
|---|---|
| **Pattern** | 1 of 7: native MQTT pub/sub, hand-rolled everything |
| **Container** | `sim-valve-mqtt`, [`services/sim-valve-mqtt/`](../../services/sim-valve-mqtt/) |
| **Config page** | <http://localhost:8085>. The page is as much the pattern as the traffic |
| **Depends on** | nothing |
| **Feeds** | pattern 4 (the LIMS opens a sample entry from this valve's event) and pattern 7 |
| **Signal contributed** | **Sample actuation event**: the badge scan and the valve stroke |
| **GxP relevance** | The record originates at the point of action. No transcription, no intermediary |

## Overview

A sanitary diaphragm sample valve with an RFID reader on the sample port of `BR-201`, serial
`SV-2000-0417`. An operator badges in, the valve strokes open for a sampling window, and closes.
It is the most *ordinary* thing on the backbone: somebody bought it, commissioned it through a
web page, and pointed it at a broker.

Everything the record depends on was typed into that web page, and two of its defaults quietly
undermine the audit trail.

## Key ideas

**1. The commissioning page is the protocol.** Everything this device promises the outside
world is three form fields (a topic, a QoS and a retained flag) typed in by whoever installed
it. Nothing validates the topic against the site namespace. Nothing knows a badge scan is an
audit record and a line temperature is not, so one QoS applies to both. Pattern 2's page has the
same three controls greyed out, and comparing the two pages makes the argument before a single
message crosses the wire.

**2. The device knows nothing about itself, so everything else must be agreed.** The payload
shape is ours. The death certificate is a retained JSON document on a `status` topic we picked,
paired with an `online` message on connect by convention alone, and its timestamp is wrong by
construction. Datatypes are whatever `json.dumps` produced. Every one of those has to be written
down somewhere and kept in step forever, and the place it gets written down is an Ignition tag
configuration, by hand, which is exactly the work pattern 2 does not do.

**3. The namespace holds because an ACL holds it.** `sample-valve-01` may publish to
`icc26/site1/upstream/#` and nothing else, so the free-text topic box cannot put sample data in
the QC area. That rule lives in `compose/chariot/mqtt-users.json`: not in the device, not in
the protocol. Delete it and the discipline goes with it.

> The valve authenticates as `sample-valve-01`, so this ACL applies to it. Chariot also has
> `allowAnonymous: true` in this repo, though, so a client that sends no credentials is let in
> and the ACLs are not a security boundary. See
> [`compose/chariot/README.md`](../../compose/chariot/README.md) § *Anonymous access is on,
> deliberately*.

## The wire

Four topics, all outbound, nothing subscribed:

| Topic | QoS | Retained | Purpose |
|---|---|---|---|
| `…/sample-valve-01/event/vlv-badge-scanned` | 1 | yes | One per badge presented, granted or denied |
| `…/sample-valve-01/event/sample-acq-completed` | 1 | yes | One per sample that actually ran |
| `…/sample-valve-01/status` | 1 | yes | `online` \| `offline`; **also the Last Will** |
| `…/sample-valve-01/telemetry` | 1 | yes | Actuator air supply, enclosure temperature, every 5 s |

**The QoS and Retained columns are identical down the table, and that is the finding.** The page
offers one of each, applied to every topic it derives. The honest settings would be QoS 1
unretained for the two event subtypes, QoS 1 retained for `status`, QoS 0 unretained for
telemetry: three different answers the device cannot express, spread across four topics.

**Why the events are two topics.** The two documents carry different fields, and Ignition's tag
tree mirrors whatever document arrives, writing only the keys that are present. Put both on one
topic and you get one folder holding the union of two schemas, where `deny_reason` still reads
whatever the last *denial* said long after a sample succeeded. Nothing in the tree tells you
which message a tag came from. Pattern 2 cannot make this mistake; its metric list is declared
once, in the birth certificate.

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

**That is the whole document.** A timestamp and a bag of values. It does not say what it is,
where it came from, what device sent it, how it arrived, or whether you missed the one before.
Every one of those answers lives in the topic string somebody typed into the config page.
Pattern 2's equivalent carries datatypes, engineering units, aliases and a spec-mandated
sequence number, and nobody had to agree any of it.

**This valve mints the sample id**, because the sample begins when material leaves the reactor.
It travels as `values.sample_id` and everything downstream carries it unchanged: the analyzer
result, the LIMS review, and pattern 7's deviation all carry the same string. A denial carries
`null`, because a denial belongs to no sample.

## Risks, measured

**1. The death certificate cannot say when the device died.** The Last Will goes out on the
`status` topic carrying `state: "offline"`, and its timestamp is **the moment the session
connected**: a will is registered in the CONNECT packet, before the death it describes. So the
payload `ts` is stale by exactly the length of the session. Measured both ways:

| | payload `ts` stale by | because |
|---|---|---|
| `docker kill` (will fires) | **14 s** | the session had been up 14 s |
| `docker stop` (graceful) | **3 m 50 s** | the session had been up 3 m 50 s |

The two numbers are not a comparison; they differ only because one session ran longer. The
finding is that the graceful path is broken *as well*, by a different route: a clean DISCONNECT
makes the broker discard the will, so the device publishes the same frozen document itself. Same
wrong timestamp, none of the will machinery involved.

Sparkplug does not fix this by magic: NDEATH *is* a Last Will and inherits the identical
constraint. It fixes it by making every consumer apply the same rule and having the *consumer*
stamp the time. Almost all of the difference is the agreement, not the plumbing; see
[`02-sparkplug-b.md`](02-sparkplug-b.md) for the one exception.

**2. Nothing on the backbone knows that topic means death** unless it was told separately. The
device does try: the will carries a free-text `note` field reading *"last will — ts is when this
session connected, not when it died"*. It says so in English, in a field nothing parses. Pattern
2 puts the same fact in `bdSeq`, and every Sparkplug consumer already knows what to do with it.

**3. The retained events replay on reconnect, presented as current.** A brand-new subscriber
immediately received a badge scan from 21 s earlier with nothing marking it historical. Ignition
gets this on every reconnect.

**4. Each scan overwrites the last.** One message is retained per topic, and the tag model holds
one set of badge tags. A granted `B-1042` scan was gone minutes later, replaced by a `B-2087`
denial. An audit trail that silently keeps only the most recent record is invisible until
somebody asks about last Tuesday. Tag history is the remedy; it stays off, on purpose.

**And from the Ignition side: a JSON `null` produces no tag at all.** Not a dropped value, not
bad quality: the key is skipped and the tag never exists. So the shape of the tag tree depends
on **which messages happened to arrive**, not on the payload contract.
`event/sample-acq-completed` only publishes when a sample ran, so its `sample_id` tag exists from
the first completed sample. `event/vlv-badge-scanned` carries `null` on every denial, so its
`sample_id` does not appear until the first grant. Start with denials and half the schema is
missing. Pattern 2 declares both `Badge/LastScanId` and `Sample/LastSampleId` as typed Strings
in DBIRTH before anybody has badged in.

## What this pattern deliberately does not do

**No command path. At all.** There is no `cmd` topic, nothing is subscribed, and nothing on the
backbone can open this valve. Authorization is decided at the sample port against a roster the
assembly holds locally, because a sample port that stops working when the broker does is not one
anybody would install. That is what makes the device credible, and it means patterns 1 and 2
are both pure publishers.

**No request/response.** Nothing asks this device for anything. Chariot is MQTT 3.1.1 and has no
response-topic property, and this pattern never needs one.

## Try it

Watch the backbone in its own terminal:

```bash
docker run --rm -it --network icc26 eclipse-mosquitto:2 \
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

| Step | Do | What you see |
|---|---|---|
| Compare the pages | Open :8085 and :8086 side by side | The pattern, before any traffic exists |
| A granted sample | Press `B-1042` | `event/vlv-badge-scanned` granted + `sample_id`, then ~15 s later `event/sample-acq-completed`, **and nothing in between**, though the page shows the valve stroking |
| Every denial | `B-2087`, `B-9999`, `B-1042` twice quickly | One `event/vlv-badge-scanned` each, **nothing on `sample-acq-completed`** |
| The valve misbehaves | Sag the air supply, press `B-1042` | Granted (authorization knows nothing about air pressure), then `cycle_result: failed-to-seat`. The telemetry had been saying so for minutes |
| Retain does the work | Start a second `mosquitto_sub` | Retained `status: online` arrives before anything happens |
| The death certificate | `docker kill icc26-sim-valve-mqtt` | Retained `offline` will lands. Compare its `ts` with the time, and read its `note` |
| Turn Retain off | Uncheck it on the page, repeat | A new subscriber gets **nothing** |
| The ACL | Set the topic to `icc26/site1/qc/lims/sample-results-released` | Refused by the `sample-valve-01` ACL |

Roster: `B-1042` is authorized, `B-2087` has the wrong role, and anything else is unknown (the
page offers `B-9999`). `valve-busy` is the third denial: press `B-1042` twice in quick
succession.

**`docker stop` disconnects cleanly and the broker discards the will**; only `kill` proves the
will works. The stop path is the one that surprises people: the device publishes the same
frozen `offline` document itself, so it *looks* like the will fired.

**If you re-address the valve, change it back.** Engine's subscription names `br-201` by hand, so
a re-addressed valve publishes happily to the broker while Ignition's tag tree sits frozen. The
text box and the subscription are kept in step by a human and nothing else.
