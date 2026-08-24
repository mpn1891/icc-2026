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
> **Both were split into two documents on 2026-08-23**, matching pattern 4's convention: the
> `plans/` files are the build specs, and the new [`../talk-tracks/01-native-mqtt.md`](../talk-tracks/01-native-mqtt.md)
> and [`../talk-tracks/02-sparkplug-b.md`](../talk-tracks/02-sparkplug-b.md) are the talk tracks, carrying the
> through-line signal and GxP hook. The vibration deviations tables are gone — that code no
> longer exists. **One new work item fell out:** pattern 1 does not stamp `meta.correlation_id`,
> which the spine needs. It is now open item 1 in its build spec and pairs with work item 2
> below.
>
> **Pattern 3 is done on the Nova path** (broker-verified 2026-08-20): vendor `SampleTime` →
> Event Stream `03_opcua/novaflex-result` → Transmission →
> `icc26/site1/qc/analyzers/novaflex-01/result` with `meta.mechanism = "opcua-event"` and
> `meta.correlation_id` = `sample_id` (broker-watched 2026-08-20, e.g. `S-00140`). Countess
> stays in compose as a second designed OPC UA analyzer for the talk contrast; **MQTT publish
> is intentionally not wired** — not an open Pattern 3 work item.
>
> **Pattern 4 is verified end-to-end** on this checkout (2026-08-20). Ingest, reject, atomic
> approve, webhook publish (`mechanism: webhook`), 409 replay, 401 wrong secret,
> and outbox survival across `docker restart icc26-lims` all held. Talk track:
> [`../talk-tracks/04-lims-webhook.md`](../talk-tracks/04-lims-webhook.md). **Remaining:** both review outcomes
> publish `analyst` + `disposition` pass/fail — reject is no longer silent.

What is true right now and what to do next. Durable knowledge does not live here — it lives in
[`../00-architecture.md`](../00-architecture.md). Work still to be built lives in
[`00-master-plan.md`](00-master-plan.md). If this file disagrees with either, this file is newer,
and the fix is to move the fact rather than keep two copies.

## Do this next

**Patterns 5, 6 and 7 were re-sourced again on 2026-08-23.** The 2026-08-19 Odoo / Modbus
MET ONE / vibration-AMS-DCS plan is withdrawn. Read
[`../00-architecture.md` § *Sources as of 2026-08-23*](../00-architecture.md) before
touching any of them. In short:

- **4** stays the LIMS webhook; add `disposition` pass/fail and publish on reject too.
- **5** is an Ignition timer that auto-cycles `BR-201` through CIP/SIP/INOC/GROWTH/HARVEST,
  writes the bioreactor UDT and `mes.batch_event`, Debezium CDC off that table.
- **6** is a MET ONE HTTP API, polled from Ignition; new analyses go out through an Event Stream.
- **7** listens for the LIMS review and publishes one sample-chain aggregate (valve open →
  Nova complete, batch operation at sample time, nearest MET ONE to the Nova timestamp).

**Joseph's demo through line was merged in on 2026-08-23** (PR #4, folded into this branch the
same day). It adds a single fed-batch spine, a per-pattern GxP hook, and three booleans the
payoff depends on — `qualified_window` on 5, `status` on 6, and the two derived flags on 7. See
[`../demo-through-line.md`](../demo-through-line.md) and
[`../00-architecture.md` § *Derived flags travel with the fact that produced them*](../00-architecture.md).

**Work items, in the order they unblock each other:**

1. **04 pass/fail.** Small, and pattern 7 listens for that message. Confirmed unbuilt:
   `services/lims/app.py` `reject()` writes no outbox row, and the payload builder has no
   `disposition` key at all.
2. **The sample id correlation.** The valve mints `S-YYYYMMDD-NNNN`, the Nova mints `S-NNNNN`,
   and nothing joins them — so pattern 7's valve-open → analysis leg does not correlate today.
   Ignition writes the valve's id into the Nova's writable `SampleInformation/SampleID`, **and
   the valve stamps that id into `meta.correlation_id`** — it does not today, which is open item
   1 in [`01-native-mqtt.md`](01-native-mqtt.md).
   **07 cannot be specified until this lands**, because both derived flags are evaluated at the
   sample-open instant.
3. **08's fallback firehose.** No dependencies, and the only deliverable the audience sees.
   **There are no Perspective views in the repo at all** — `com.inductiveautomation.perspective/`
   holds only `page-config`. This is greenfield, not an edit, and it is the largest gap between
   what the plan assumes and what exists.
4. **05** (timer + JDBC + Debezium) and **06** (MET ONE simulator + poll) in parallel.
   06 waits on vendor API notes; stub the routes if they have not arrived — the excursion flag
   and its limit config are ours, not the vendor's, so they need not wait.
5. **07** last of the seven — it is the join.

Countess MQTT publish on `count_completed_counter` is optional polish if wanted later; nothing
depends on it, and Pattern 3 does not wait on it.

`mes.batch_event` **has a consumer again** (pattern 5). `04-cdc.sql` still publishes both
that table and `lims.sample_result`; drop the LIMS table from the publication with pattern
5's spec, do not tail both. Its `payload` jsonb column already exists, so `qualified_window`
needs **no schema change**.

**The retired vibration gateway was deleted on 2026-08-23** — `services/sim-vibration/`, the
`vibsim` script module and both `vibration-gw-*` event streams. One of those event streams was
still `enabled: true`, subscribed to a topic no service published. Two related things were
deliberately *not* deleted: the `vibration_sensor` UDT and `br-201`'s `agitator_vibration`
member (remove them with pattern 5's spec, which has to open `udts.json` anyway), and the
`icc26-native` Engine namespace — **that one is not retired at all**, it is the sample valve's
ingest surface and deleting it breaks pattern 1.

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
  Pattern 5's timer writes `mes.batch_event` through it. **Not created — and there is a
  look-alike.** `database-connection/pg_db` *is* in the repo, pointing at the **`postgres`
  database as user `ignition`**: wrong database, wrong user. It will pass a glance in the
  datasource dropdown and write nowhere useful. Create `ICC26` (UI first, then commit) and
  decide whether `pg_db` is deleted rather than left to be picked by mistake.
  (An earlier revision of this line said no `database-connection` resource existed at all.)
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
