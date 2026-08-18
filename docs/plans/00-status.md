# Status

> **Updated 2026-08-17.** Conference is ~4 weeks out.
>
> **The stale-image blocker is CLEARED.** A gateway rebuilt from the repo loads Cirrus **5.0.4**
> Engine, Transmission and Distributor with no compatibility warnings. `tasks.py` now forces
> `--build` on both `up` and `seed`, so a stale tag cannot come back quietly.
>
> **Patterns 1 and 2 are built, run and verified end-to-end.** Both valve containers were built
> and run for the first time on 2026-08-17, both produced Ignition tags, and every checkpoint
> passed. Findings live in [`01-native-mqtt.md`](01-native-mqtt.md) and
> [`02-sparkplug-b.md`](02-sparkplug-b.md), each under *Ingest, as built*.

What is true right now and what to do next. Durable knowledge does not live here — it lives in
[`../00-architecture.md`](../00-architecture.md). Work still to be built lives in
[`00-master-plan.md`](00-master-plan.md). If this file disagrees with either, this file is newer,
and the fix is to move the fact rather than keep two copies.

## Do this next

**→ Pattern 3, step 9** — [`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md) § 9,
the tag-change script that turns an analyzer result into an MQTT publish. Both OPC UA servers are
built and both address spaces are bound (the FLEX2 verified at 57/57 tags); the publish path is
what is missing. Pattern 4 is the alternative if you would rather widen than deepen.

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
