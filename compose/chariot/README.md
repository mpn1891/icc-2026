# Chariot MQTT Server configuration

`mqtt-users.json` is bind-mounted to `/config/mqtt-users.json` and referenced by the
`MQTT_USERS` environment variable. It is applied **on first run only** — Chariot persists
users into its own store, so editing this file against an existing volume changes nothing.
To re-apply: `python tasks.py nuke` (drops volumes) or edit users in the Chariot UI at
`http://localhost:8081`.

The file is deliberately comment-free — Chariot parses it strictly, and a stray `_comment`
key risks an unknown-property rejection. The rationale lives here instead.

## Why one user per pattern

Every pattern gets its own credential with an ACL scoped to its branch of the namespace.
The demo would work fine with one shared account; the split exists because it makes a good
slide and because the asymmetries are the interesting part:

| User | Role | Note |
|---|---|---|
| `ign-engine` | Ignition MQTT Engine — northbound consumer | Subscribes broadly; publishes only commands |
| `ign-transmission` | Ignition MQTT Transmission — Sparkplug edge node + publisher for patterns 3–7 | The only account with broad publish rights |
| `sample-valve-01` | Pattern 1 smart sample valve assembly, plain MQTT | Publish only, and only into `upstream` — see below |
| `sample-valve-02` | Pattern 2 the same assembly, Sparkplug B | Publishes its own group; subscribes only NCMD/DCMD |
| `analyzer-bridge` | Pattern 3, reserved | Only if the Nova Flex demo ever publishes without routing through Ignition |
| `lims-bridge` | Patterns 4, 5, 6 | Sample-result + batch-event topics — same ACL, two converging publishers |
| `observer` | Read-only | Firehose view, `mosquitto_sub`, MQTT Explorer |

**The two valve accounts are the ACL half of the pattern 1 / pattern 2 comparison, and it is
worth putting on the slide.**

`sample-valve-01`'s configuration page has a free-text topic box (see
[`services/sim-valve-mqtt/`](../../services/sim-valve-mqtt/)). Nothing in the device stops
somebody typing `icc26/site1/qc/lims/sample-result` into it. What stops them is this ACL —
`icc26/site1/upstream/#`, deliberately the *area* rather than the exact topic, so the valve
can legitimately be re-addressed to another cell on stage but cannot leave upstream. **In
pattern 1 the namespace discipline is enforced by an ACL somebody remembered to write. In
pattern 2 it is enforced by the protocol**, which is why `sample-valve-02` can be pinned to
one Sparkplug group and nothing is lost.

`sample-valve-02` subscribes to exactly two topics, both commands addressed to it, and the
only one it acts on is `Node Control/Rebirth`. `sample-valve-01` subscribes to nothing at
all: both valves are publish-only, because authorization is decided at the sample port
against a local badge roster rather than over the network.

Neither may publish commands to anything. A field device that can issue commands to its peers
is a lateral-movement path, and the ACL is where you close it. This is also why `observer` has
an empty `publishTopics` array: it is safe to hand out and safe to leave connected during the
talk.

**`lims-bridge` publishes to the sample-result topic and the bioreactor batch-event topic**,
which is what forces patterns 4, 5 and 6 to converge rather than drifting into three parallel
namespaces.

## Server settings

Broker listener configuration (ports, anonymous access, WebSocket) is set via the
`SERVER_CONFIG` environment variable in `docker-compose.yml` rather than a file here,
because those values need to agree with the published container ports and are easier to
keep consistent when they live next to them.

`allowAnonymous` is **true for the initial rollout**, and is meant to go back to `false`
before the talk.

The accounts in the table above are unaffected — they are still seeded on first run and
still authenticate normally. Anonymous only means a client is *also* allowed to connect
without credentials, so nobody writing their first pattern service loses an afternoon to a
silent ACL rejection. The ACL story is still on the table; it is just not yet enforced.

Reverting is one boolean in `SERVER_CONFIG` plus `python tasks.py restart chariot`.
Deleting the users instead would not have been reversible that cheaply — see the first-run
caveat at the top of this file.

**Before the talk:** set it back to `false`, restart, and confirm each pattern service
still connects with its own credential. Do this early enough that a missing ACL is a
Tuesday problem, not a stage problem.

## Trial timer

Chariot runs a 2-hour trial that is **independent of Ignition's**. Two clocks to watch on
stage — a dual-trial checklist will live in `docs/demo-runbook.md` when that file is written
(planned with presentation spec 08). A Chariot demo license key from Cirrus Link removes
this entirely and is worth requesting before the conference.

**The trial does not auto-start, and starting it is a manual step.** Until it is running,
Chariot serves its web UI while port 1883 refuses every connection. Start it at
`http://localhost:${CHARIOT_HTTP_PORT}` → **License** → start trial (or install the demo key
on that same page). `tasks.py up`, `trial` and `health` all check the listener and print that
URL when it is shut, but none of them change license state.
