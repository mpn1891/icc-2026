# Status

> **Updated 2026-08-23.** Conference is ~4 weeks out.
>
> **The stale-image blocker is CLEARED.** A gateway rebuilt from the repo loads Cirrus **5.0.4**
> Engine, Transmission and Distributor with no compatibility warnings. `tasks.py` now forces
> `--build` on both `up` and `seed`, so a stale tag cannot come back quietly.
>
> **Patterns 1 and 2 are built, run and verified end-to-end.** Both valve containers were built
> and run for the first time on 2026-08-17, both produced Ignition tags, and every checkpoint
> passed. Findings live in [`01-native-mqtt.md`](01-native-mqtt.md) and
> [`02-sparkplug-b.md`](02-sparkplug-b.md), each under *Ingest, as built*.
>
> **Pattern 3 is done on the Nova path** (broker-verified 2026-08-20): vendor `SampleTime` →
> Event Stream `03_opcua/novaflex-result` → Transmission →
> `icc26/site1/qc/analyzers/novaflex-01/result` with `meta.mechanism = "opcua-event"` and
> `meta.correlation_id` = `sample_id`. Countess is **not on the talk** — docs live in
> [`../extra/`](../extra/README.md); the container may still be in compose.
> **MQTT publish is intentionally not wired**.
>
> **Pattern 4 is rebuilt in code and is UNVERIFIED.** `services/webhook-novaflex/` (a new
> container, not an addition to `opcua-novaflex`) POSTs a vendor-shaped result over HTTPS to
> Event Stream `04_webhook/novaflex-result`, which publishes onto pattern 3's topic with
> `meta.mechanism = "webhook"`. The sender was exercised locally against a stub receiver;
> **nothing has met the gateway or the broker.** The Event Stream was authored blind — the
> HTTP source type, its config keys and the mount URL are all guesses. Run the runbook in
> [`../04-novaflex-webhook.md`](../04-novaflex-webhook.md) before demoing it, and read the
> deviations table first — same-sample correlation across patterns 3 and 4 is no longer
> automatic. The LIMS is **unwired**: commented out of compose, `lims-bridge` dropped, `lims.*`
> marked Extra but not dropped. It stays on disk as pattern 4's proven fallback
> ([`../extra/lims-webhook-spec.md`](../extra/lims-webhook-spec.md)).
>
> **Patterns 5 and 6 share a turbidity-meter local database** (CDC vs poll). Specs:
> [`05-cdc-turbidity.md`](05-cdc-turbidity.md), [`06-poll-turbidity.md`](06-poll-turbidity.md).
> **Vendor docs arrived 2026-08-23**: the instrument is an Anton Paar **Haze 3001** turbidity
> module and its data lands in **AP Connect** 4.0. Both specs were re-sourced against it the
> same day; see [`../reference/apconnect-haze3001-model.md`](../reference/apconnect-haze3001-model.md).
> The catalog is `apconnect`, not `turbidity`. AP Connect really runs on MS SQL Server; the demo
> substitutes Postgres deliberately, and both specs record why. Odoo is still out. The MET ONE
> particle counter is out.
>
> **Pattern 6's Ignition side is authored as files but has never been run** (2026-08-23). The
> `APCONNECT` datasource, the four memory tags, the `poll_turbidity` script module and the two
> gateway-event resources are all on disk; the stack was down throughout and the gateway UI was
> not touched. The two gateway events had **no committed example to copy**, so their on-disk
> schemas are inferred and are the pattern's main risk. Every guessed field, its failure mode, the
> deviations, and a one-pass runbook that settles them are in
> [`../06-poll-turbidity.md`](../06-poll-turbidity.md). Nothing can be checked until pattern 5's
> branch (database + `sim-apconnect`) is merged, and that needs a nuke.
>
> **Pattern 7 is TBD** and remains the designated cut. Vibration / AMS / DCS is not the plan.

What is true right now and what to do next. Durable knowledge does not live here — it lives in
[`../00-architecture.md`](../00-architecture.md). Work still to be built lives in
[`00-master-plan.md`](00-master-plan.md). If this file disagrees with either, this file is newer,
and the fix is to move the fact rather than keep two copies.

## Do this next

**Patterns 4–7 were re-sourced again on 2026-08-23.** Read
[`../00-architecture.md` § *Shared sources*](../00-architecture.md) before touching any of
them. In short: 4 is a NovaFlex HTTPS POST into an Event Stream (LIMS leaves the talk); 5 is
CDC of a turbidity meter's local database; 6 polls that same database on `id`; 7 is TBD.

**Work items, independent except 05/06 sharing a database.** Specs 04/05/06 now have file
sketches, Ignition paths, MQTT users, empirical checkpoints, and copy-paste verification.

1. **Prove pattern 4.** The code is written and the stack has never seen it. One pass through
   the runbook in [`../04-novaflex-webhook.md`](../04-novaflex-webhook.md) settles the
   Event Stream HTTP source type, its config keys, the mount URL, the shape of `event`, and
   whether a bad secret returns 401 or a dropped 200. Everything else about pattern 4 is
   downstream of that one pass. **This is the highest-value hour in the tree right now** —
   it is the only unbuilt-knowledge item, and the fallback if it fails is already in place.
2. **Build 08's fallback firehose.** Depends on no pattern; only deliverable the audience sees.
3. **Turbidity simulator + database** (foundation for 05 and 06). Then Debezium, then the poll.

Countess MQTT publish is Extra polish; nothing depends on it. See [`../extra/`](../extra/README.md).

Newly dead: the LIMS as pattern 4 — **unwired 2026-08-23**, commented out of compose, no
Chariot account, still on disk as the fallback. `mes.batch_event` still has no consumer;
`04-cdc.sql`'s publication still names two unread tables — retire that with pattern 5, along
with the `lims.*` tables, in the same volume drop. The MET ONE particle counter and the
vibration/AMS/DCS aggregation were never built and are not the plan.

`00-next-step.md` is **done** and kept only as the record of what was run. Everything durable
from it has moved into [`../00-architecture.md`](../00-architecture.md) and the two pattern specs.

Rules that are still live:

- **`icc26_ign-data` is no longer precious.** It was the only place a working 5.0.4 Engine and
  Transmission existed; the image carries them now, so the main checkout can be nuked and
  reseeded like any other. That still costs one commissioning wizard and one API key.
- **Two checkouts still cannot run at once**, and stopping the other is not enough — its
  containers must be *removed*, because a name is claimed by an existing container whether it is
  running or not. `python tasks.py down`.
- **Do not `git commit` from inside the scratch clone.** Sync it the other way:
  `git -C ...icc26-clone fetch C:/Users/matt/repos/icc-2026 main && git reset --hard FETCH_HEAD`.

## Also outstanding

- **MQTT Engine connects to Chariot with no username at all.** Its server config
  (`com.cirruslink.mqtt.engine.gateway/server/Chariot SCADA/config.json`) has `"username": ""`,
  and it only works because `allowAnonymous` is still `true` — Chariot's client list shows it
  connected with `username: None` beside `ign-transmission`, which is authenticated properly.
  **Turning `allowAnonymous` off before the talk will break Engine**, and with it both patterns 1
  and 2, unless the `ign-engine` credential is set first. Found 2026-08-17; deliberately not
  fixed in the same pass as the pattern work.
- **`lims-bridge` is still in a running Chariot's user store.** It was removed from
  `compose/chariot/mqtt-users.json` on 2026-08-23, but `MQTT_USERS` seeds on first run only, so
  the account survives on any existing volume. Nothing connects with it — the `lims` container
  is commented out — but delete it in the UI at `:8081` → Users before the talk, or let the
  next `nuke` do it. Same class of thing as `allowAnonymous`.
- **Per-machine Ignition 8.3 API key** — `tasks.py scan` uses verified HTTPS only. After seeding,
  each user must create a secure-channel key in Gateway UI → Platform → Security → API Keys,
  grant its security level Gateway read/write access, and put the complete `name:secret` value in
  `.env` as `IGNITION_API_TOKEN_HTTPS`. The first key cannot be automated because its creation
  API already requires authenticated write access. Two traps met on 2026-08-17: the variable was
  once called `IGNITION_API_TOKEN`, and an older `.env` still carrying that name reads as "no
  token" with no error; and a key is bound to the gateway that minted it, so a key from another
  checkout returns 401 and looks identical to no key at all.
- **Postgres JDBC datasource `APCONNECT`** → `jdbc:postgresql://postgres:5432/apconnect`.
  Pattern 6 needs it. Pattern 5 (MQTT sink) does not. Spec 06 authors it as files by copying
  `pg_db`'s Embedded password blob rather than retyping the password in the UI. `ICC26` on `icc26`
  is leftover from the LIMS era; recreate only if something still queries it. **`APCONNECT` now
  exists on disk** (authored 2026-08-23, never connected — whether the copied blob decrypts is
  checkpoint 1); `ICC26` still does not. (Earlier notes called this one `TURBIDITY`, against a
  catalog `turbidity`; both names changed when the vendor docs landed.)
- **Transmission logs `Failed to subscribe to TARGET elements`** immediately after connecting.
  Unexplained — possibly the `ign-transmission` ACL, possibly transmitter config. It connects, so
  it may block nothing; decide how hard to chase it before the pattern work starts.
- **The OPC UA connection** (`ignition/opc-connection/Ignition OPC UA Server/config.json`) holds
  two `Embedded` secrets, one paired with a keystore in gitignored `local/`. Fine as-is unless
  the loopback connection faults on a clone. Pattern 3 uses OPC UA.

## Where Part 1 stands

`tasks.py` lifecycle and guardrails, the config/compose edits, and the first commit are all
done — 5 commits on `main` at <https://github.com/mpn1891/icc-2026>, 946 tracked files. The
clone test ran on 2026-08-09 against a real clone and the two highest-risk unknowns came out
clean.

**Proven, not assumed:**

- The clone-seed path takes the right branch against a real gateway and leaves `git status`
  completely clean. This was the highest-risk unverified thing in Part 1.
- Ignition 8.3 regenerates all three identity paths when they are absent.
- Ignition content travels: default tag provider, the `icc-2026` project, its event streams,
  and a regenerated gitignored `.resources/` cache.
- Chariot config is fully reproducible from compose against a fresh volume.
- The guardrails hard-fail against a real missing-module clone, not just stubs.
- The A1–A4 state machine, via `tests/test_tasks.py` — 22 checks, no Docker needed. Re-run after
  touching `tasks.py`.

**Disproven, and worth remembering:**

- That `verify-modules` passing means the gateway will load those modules. It does not.
- ~~That the repo alone reproduces the working stack.~~ **It does, as of 2026-08-17**: a cold
  `nuke` + `seed` + `up` in a clean clone produced a 5.0.4 gateway, both valves, and green
  health. The only item on this list that has flipped back.

**Settled 2026-08-17:** `Embedded` ciphertext **is** portable between gateways. A gateway seeded
from an empty volume connected Transmission to Chariot as `ign-transmission` — confirmed in
Chariot's client list as well as the gateway log, with zero decrypt errors. No Secret Provider.

## Re-running the clone test

The checkpoints are still the right ones. Bring the main stack down first (two checkouts cannot
run at once), then in a scratch clone with `COMPOSE_PROJECT_NAME=icc26test` in `.env` and the
`.modl` files copied in:

| # | Check | Pass |
|---|---|---|
| 0 | `seed` and `up` with no `.modl` files | both exit non-zero, neither invokes compose |
| 1 | `seed` prints the **clone-seed** banner | not the full-seed one — otherwise it is about to overwrite the checkout |
| 2 | `git status --short` after seed | completely empty |
| 3 | `tasks.py health` | all green (start Chariot's trial by hand first) |
| 4 | Content travelled | tag provider and the `icc-2026` project present, with at least one event stream under it. Tag *values* are empty and that is correct — `valueStore.idb` is gitignored |
| 5 | Secrets verdict | Transmission connected as `ign-transmission`, no decrypt errors |

Do not `git commit` from inside the scratch clone, and do not `nuke` the main checkout.

## Working rules

- **Commit only what you meant to change.** Every gateway write stamps `lastModification*` into
  neighbouring `resource.json` files. `git add` your files, then `git restore .` for the rest.
- **Never commit** `ignition/config/local/`, `ignition/config/resources/local/`,
  `valueStore.idb`, `.modl` files, or `.env`.
- **Unknown gateway schemas: UI first, then read `git status`, then commit.** Known formats
  (tags, project scripts, WebDev, Perspective views) can be authored as files. WebDev python
  resources need `"resource-type": "python-resource"` in `config.json` — any other discriminator
  mounts the URL and then 500s.
- **On-disk config/project changes: `python tasks.py scan`**, not a container restart. Restart
  only if scan is unavailable (no API key) or you changed a container-consumed `.env` secret.
- **Changed container-consumed `.env` secrets need `python tasks.py restart ignition`**, not
  `scan`; the API token is read directly by `tasks.py` and needs no restart.
- **Before the talk:** set `allowAnonymous` back to `false` and confirm every client still
  connects with its own credential. **Start with MQTT Engine** — it is the one that is currently
  anonymous, see *Also outstanding*.
- **The scratch clone is downstream of main, always.** It has no unique work in it; bring it
  current with `git -C ...icc26-clone fetch C:/Users/matt/repos/icc-2026 main` then
  `git reset --hard FETCH_HEAD`. Its `.env` is gitignored and survives, which is the point.
