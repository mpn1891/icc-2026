# Status

> **Updated 2026-08-11.** Conference is ~5 weeks out.
>
> **Blocker: the committed image is stale.** A clone gets Cirrus 4.0.8 and a gateway with no
> working MQTT, silently, behind a green `health`. Fix that before anything else.

What is true right now and what to do next. Durable knowledge does not live here — it lives in
[`../00-architecture.md`](../00-architecture.md). Work still to be built lives in
[`00-master-plan.md`](00-master-plan.md). If this file disagrees with either, this file is newer,
and the fix is to move the fact rather than keep two copies.

## Do this next

1. **Rebuild the image and prove 5.0.4 loads.** In the scratch clone at
   `C:\Users\matt\repos\icc26-clone`, never the main checkout:

   ```powershell
   docker image rm icc26/ignition:8.3.8
   python tasks.py nuke      # CLONE ONLY -- icc26test_* volumes
   python tasks.py seed      # rebuilds the image, re-accepts certs once
   python tasks.py up
   ```

   Mechanism and the general lesson: [*The stale-image trap*](../00-architecture.md#the-stale-image-trap).

2. **Then rerun the secrets check (CP5).** Confirm MQTT Transmission connects to
   `tcp://chariot:1883` as `ign-transmission`, from Chariot's client list as well as the gateway
   log. That proves ciphertext encrypted on Matt's gateway decrypts on a gateway that has never
   seen his machine — the last unproven assumption in Part 1. A failure means the committed
   `Embedded` secrets are not portable and a Secret Provider comes back onto the plan; watch for
   `Unable to decrypt ciphertext` and record the exact line.

3. **Then harden `verify-modules`** so a stale image fails loudly: land the module sha256s in
   `modules.manifest.json` and hash the `.modl` files *inside the image*, or force
   `docker compose build` on every `seed`.

4. **Only then touch the main checkout.** `icc26_ign-data` is currently the only place the
   working 5.0.4 Engine and Transmission exist — they were hand-installed through the gateway UI
   and are not in the image, so **do not `nuke` it** until a rebuilt image is proven in the clone.

## Also outstanding

- **Per-machine Ignition 8.3 API key** — `tasks.py scan` now uses verified HTTPS only. After
  seeding, each user must create a secure-channel key in Gateway UI → Platform → Security → API
  Keys, grant its security level Gateway read/write access, and put the complete `name:secret`
  value in `.env` as `IGNITION_API_TOKEN_HTTPS`. The first key cannot be automated because its
  creation API already requires authenticated write access.
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
- Ignition content travels: default tag provider, the `icc-2026` project, its
  `vibration-gw-listener` event stream, and a regenerated gitignored `.resources/` cache.
- Chariot config is fully reproducible from compose against a fresh volume.
- The guardrails hard-fail against a real missing-module clone, not just stubs.
- The A1–A4 state machine, via `tests/test_tasks.py` — 22 checks, no Docker needed. Re-run after
  touching `tasks.py`.

**Disproven, and worth remembering:**

- That `verify-modules` passing means the gateway will load those modules. It does not.
- That the repo alone reproduces the working stack. It does not, today.

**Still assumed:** that `Embedded` ciphertext is portable between gateways — step 2 above.

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
| 4 | Content travelled | tag provider, `icc-2026` project, `vibration-gw-listener` present. Tag *values* are empty and that is correct — `valueStore.idb` is gitignored |
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
  connects with its own credential.
