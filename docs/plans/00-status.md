# Status

> **Updated 2026-08-19.** Conference is ~4 weeks out.
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
> **Pattern 3 Nova MQTT publish is built** (not yet run against a live gateway in this pass):
> vendor `SampleTime` → Event Stream `03_opcua/novaflex-result` → Transmission. Countess publish
> is still open.

What is true right now and what to do next. Durable knowledge does not live here — it lives in
[`../00-architecture.md`](../00-architecture.md). Work still to be built lives in
[`00-master-plan.md`](00-master-plan.md). If this file disagrees with either, this file is newer,
and the fix is to move the fact rather than keep two copies.

## Do this next

**Patterns 5, 6 and 7 were re-sourced on 2026-08-19, and the shared-topic set-piece was dropped.**
This is the largest design change since pattern 2 stopped being a bioreactor, so read
[`../00-architecture.md` § *Patterns 4, 5 and 6 used to share one
topic*](../00-architecture.md) before touching any of them. In short: 5 becomes CDC on **Odoo**,
6 becomes a Modbus poll of a **MET ONE particle counter**, 7 is leaning toward a **vibration
waveform gated on DCS steady-state and requested by an asset management system**. Each mechanism
now gets the source that genuinely forces it, and no two patterns carry the same data.

**Four work items, and they are genuinely independent** — the old plan funnelled everything
through pattern 4 because 5/6/7 read the LIMS. They no longer do.

1. **Watch Nova's publish on the broker.** Authored, never seen: tag-change on
   `result/sample_time` → Event Stream `03_opcua/novaflex-result` → Transmission.
   `python tasks.py scan`, then the mosquitto check in
   [`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md) § 9. Pattern 4 consumes this
   topic, so it is still the first thing to do — but it is no longer four patterns' critical path.
2. **Build 08's fallback firehose.** Moved forward from last. It depends on no pattern, and it is
   the only deliverable whose absence is visible from the audience.
3. **Pattern 4**, per [`04-lims-webhook.md`](04-lims-webhook.md) — now a single-purpose webhook
   source that blocks nothing.
4. **Pattern 6's Modbus simulator.** The longest new build of the four; start it earliest.

**One `nuke` + `seed`** covers both of pattern 4's config changes — the `lims.sample_result`
columns in `02-schema.sql` and the `lims-bridge` subscribe grant in `mqtt-users.json`. Neither
takes effect against an existing volume. **Do it before Odoo is initialized**, because the same
`nuke` destroys Odoo's database.

Countess still needs its own publish on `count_completed_counter`; nothing depends on it.

Newly dead, and worth deleting rather than maintaining: `mes.batch_event` has no consumer at all
(Odoo replaces the hand-made MES table), and `04-cdc.sql`'s publication points at two tables
nothing reads. Retire both with pattern 5's spec.

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
- **Per-machine Ignition 8.3 API key** — `tasks.py scan` uses verified HTTPS only. After seeding,
  each user must create a secure-channel key in Gateway UI → Platform → Security → API Keys,
  grant its security level Gateway read/write access, and put the complete `name:secret` value in
  `.env` as `IGNITION_API_TOKEN_HTTPS`. The first key cannot be automated because its creation
  API already requires authenticated write access. Two traps met on 2026-08-17: the variable was
  once called `IGNITION_API_TOKEN`, and an older `.env` still carrying that name reads as "no
  token" with no error; and a key is bound to the gateway that minted it, so a key from another
  checkout returns 401 and looks identical to no key at all.
- **Postgres JDBC datasource `ICC26`** → `jdbc:postgresql://postgres:5432/icc26`, user `icc26`.
  Patterns 6 and 7 need it; not created yet (no `database-connection` resource in the repo).
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
  (tags, project scripts, WebDev, Perspective views) can be authored as files.
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
