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
| `sample-valve-02` | Pattern 2 the same assembly, Sparkplug B | `spBv1.0/#` both ways since 2026-08-25 — the widest grant of any device account. See below |
| `analyzer-bridge` | Pattern 3, reserved | Only if the Nova Flex demo ever publishes without routing through Ignition |
| `lims-bridge` | Pattern 4 LIMS | Subscribe-only, `icc26/site1/qc/analyzers/+/result`. Empty publish grant is the cycle-hazard lock |
| `observer` | Read-only | Firehose view, `mosquitto_sub`, MQTT Explorer |

**The two valve accounts are the ACL half of the pattern 1 / pattern 2 comparison, and it is
worth putting on the slide.**

`sample-valve-01`'s configuration page has a free-text topic box (see
[`services/sim-valve-mqtt/`](../../services/sim-valve-mqtt/)). Nothing in the device stops
somebody typing `icc26/site1/qc/lims/sample-result` into it. What stops them is this ACL —
`icc26/site1/upstream/#`, deliberately the *area* rather than the exact topic, so the valve
can legitimately be re-addressed to another cell on stage but cannot leave upstream. **In
pattern 1 the namespace discipline is enforced by an ACL somebody remembered to write. In
pattern 2 it is enforced by the protocol** — the Sparkplug topic is not the device's to
choose, so a tight ACL costs it nothing.

**That is no longer what this file does.** On 2026-08-25 `sample-valve-02` was widened to
`spBv1.0/#` on both the publish and subscribe side. It had been pinned to
`spBv1.0/ICC26-Site1-UPSTREAM/#` publish and its own two NCMD/DCMD topics, and the pin is what
broke: the Sparkplug identity is commissionable from the config page on 8086, somebody changed
the group to `smart_valves`, and **the edge node was then refused at CONNECT** — its NDEATH will
lands outside the grant, and Chariot validates the will topic before it accepts the session.
Widening the account is the fix that was chosen. What it costs, recorded rather than discovered
later:

- **The ACL half of the pattern 1 / pattern 2 slide is gone.** The two accounts no longer
  contrast; pattern 2's is now the *looser* of the two. `sample-valve-01` is still pinned to
  `icc26/site1/upstream/#`.
- **`sample-valve-02` can now publish commands.** `spBv1.0/#` includes `spBv1.0/+/NCMD/#` and
  `spBv1.0/+/DCMD/#`, so this account can issue Sparkplug commands to *any* edge node, itself
  included. Nothing in the demo does this, and the device has no code path that would — but
  the grant is there, and it is the one grant this file elsewhere argues no field device
  should have.
- **It can also see every other edge node's traffic**, subscribe side.

If the pin is wanted back, the fix is to constrain the *config page* rather than the ACL —
the identity fields are commissionable and nothing validates them against the grant.

`sample-valve-01` subscribes to nothing at all: both valves are publish-only, because
authorization is decided at the sample port against a local badge roster rather than over the
network.

No account other than `sample-valve-02` and `ign-transmission` may publish commands. A field
device that can issue commands to its peers is a lateral-movement path, and the ACL is where
you close it. This is also why `observer` has
an empty `publishTopics` array: it is safe to hand out and safe to leave connected during the
talk.

**`lims-bridge` publishes nothing.** Pattern 4's only output is an HTTP callback into
Ignition; Transmission publishes the released sample as `ign-transmission`. The empty
publish grant is load-bearing: this is the first component that both consumes the
backbone and causes publishes onto it, and the ACL is what stops that being a loop.
The analyzer-result subscribe grant is the other half — `mqtt-users.json` seeds on
first run only, so changing it against an existing Chariot store does nothing.

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
