# Clone test — executable brief

> **Written 2026-08-09**, immediately after Part 1 Phase D (first commits + push to
> <https://github.com/mpn1891/icc-2026>). Written to be handed to a fresh agent session with no
> other context.
>
> This is Part 1 **Phase E2 + E3** from [`00-master-plan.md`](00-master-plan.md), plus the part
> of E1 that can be proven without destroying anything. Read
> [`00-status.md`](00-status.md) for what happened in the sessions before this one.

## Objective

Prove that what is on GitHub is enough for a teammate to get a working stack, starting from
nothing but a clone and three licensed `.modl` files.

Three specific questions get answered, in this order of importance:

1. **Does `seed` take the clone-seed branch and leave the checkout untouched?** This path has
   never run against a real gateway — it has only ever been stub-tested. `00-status.md` calls
   it "the highest-risk unverified thing in Part 1." If it takes the wrong branch it overwrites
   committed config with a vanilla baseline.
2. **Do the Cirrus `Embedded` secrets decrypt on a gateway that never encrypted them?** Phase C
   items C1 and C2 were cut on the theory that a gateway with no `IGNITION_ROOT_KEY_PASSWORD`
   set uses a build-wide default encryption key, making committed ciphertext portable. That
   theory is unverified. This test is the verdict.
3. **Does the Ignition content travel?** Default-provider tags and the `icc-2026` project with
   its `pm-sensor-listener` event stream.

## Preconditions

- The main checkout at `C:\Users\matt\repos\icc-2026` exists and its stack has been up
  successfully at least once.
- Docker Desktop running.
- The three `.modl` files present in the main checkout's `compose/ignition/modules/`.

## Do not

- **Do not run `python tasks.py nuke` in the main checkout.** It is not needed for any step
  here. It destroys gateway state, Postgres data and Chariot users, and it removes the
  known-good stack you fall back to if this test fails. E1 is deferred deliberately — see the
  last section.
- **Do not `git commit` from inside the scratch clone.** It is a throwaway.

---

## Landmines

Every one of these was hit and diagnosed in an earlier session. They will cost you an hour each
if you rediscover them.

**Two checkouts cannot run at the same time.** `container_name` is pinned in
`docker-compose.yml` (`icc26-ignition`, `icc26-chariot`, `icc26-postgres`), the network is
pinned (`name: icc26`), and host ports come from `.env`. A second stack collides on all three
regardless of `COMPOSE_PROJECT_NAME`. Bring the main stack down first.

**`COMPOSE_PROJECT_NAME` separates volumes and nothing else.** That is still exactly why you
set it: it is what makes `down -v` in the scratch clone unable to reach the real gateway state.

**`tasks.py health` green does not mean MQTT works.** It checks Chariot's *listener*, not
Ignition's *client connection to it*. A broken Ignition MQTT credential shows green here. Check
the connection directly (checkpoint 5).

**Chariot currently accepts anonymous connections.** `allowAnonymous` is `true` for the initial
rollout. This does **not** invalidate checkpoint 5: MQTT Transmission supplies a username and
password, and Chariot still validates credentials that are supplied. A failed decrypt therefore
still surfaces as an auth failure rather than silently falling through to anonymous. But be
aware that a *credential-less* client connecting proves nothing about secrets.

**MQTT Engine's state is deliberately uncertain.** Its config has `username: admin` with no
password block at all, and `admin` is not in `compose/chariot/mqtt-users.json` — it is Chariot's
web-UI admin, a different thing. Whether Chariot treats username-without-password as anonymous
or as a failed auth is unknown. Engine's behaviour is **not** a pass/fail criterion for this
test. Transmission is.

**Git Bash mangles container paths in `docker exec`.** `/Chariot/...` becomes
`C:/Program Files/Git/Chariot/...`. Prefix with `MSYS_NO_PATHCONV=1` and use `//Chariot/...`,
or just use PowerShell.

**`docker exec <ctr> test -e <path>` gives false negatives** — there is no `test` binary in
these images. Use `sh -c '[ -e ... ]'`.

**Two independent 2-hour trial clocks**, Ignition's and Chariot's. A fresh clone gets fresh
ones, so this test is in *better* shape than the main stack, whose Ignition trial is expired.
`python tasks.py up` starts Chariot's for you.

---

## Step 1 — Stop the main stack

```powershell
cd C:\Users\matt\repos\icc-2026
python tasks.py down
```

`down` without `-v`. Volumes survive; this is reversible.

Confirm nothing is left holding the ports:

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}"
```

Expect no `icc26-*` containers.

## Step 2 — Clone

```powershell
cd C:\Users\matt\repos
git clone https://github.com/mpn1891/icc-2026.git icc26-clone
cd icc26-clone
```

## Step 3 — E3, the guardrail negatives

**Do this before copying the modules in.** A fresh clone has no `.modl` files, which is exactly
the state E3 needs, so it is free right now and expensive to reproduce later.

```powershell
python tasks.py init
python tasks.py seed
echo "seed exit code: $LASTEXITCODE"
python tasks.py up
echo "up exit code: $LASTEXITCODE"
```

**Pass:** `init` succeeds with a *warning* about the modules. Both `seed` and `up` **exit 1**
and print a pointer to `compose/ignition/MODULES.md`. Neither one invokes compose.

**Fail:** either command exits 0, or `seed` actually starts a container.

## Step 4 — Configure and seed

Copy the licensed binaries in. This is the one irreducible manual step:

```powershell
copy ..\icc-2026\compose\ignition\modules\*.modl compose\ignition\modules\
python tasks.py verify-modules
```

Expect all three green at `5.0.4`.

Now edit `.env` and set:

```
COMPOSE_PROJECT_NAME=icc26test
```

Then:

```powershell
python tasks.py seed
```

### Checkpoint 1 — the branch (this is the important one)

Watch the banner `seed` prints near the top.

**Pass** — it must say:

```
  Clone seed: ignition/config came from git, but this machine has no
  gateway volume yet. Booting once WITHOUT the bind mounts initializes
  the volume, then we copy out only the machine-local identity files.
  Your committed config and projects are never touched.
```

**Fail** — if it instead says `Ignition 8.3 seeds data/ from the image on first launch`, it took
the **full-seed** branch and is about to overwrite the checkout with a vanilla baseline. Stop,
record it, and read `task_seed()` in `tasks.py` (~line 595). The decision is a 2x2 on whether
the `icc26test_ign-data` volume exists and whether `ignition/config` is populated.

`seed` will then **block, possibly for a long time**, and print a URL. Ignition parks in
commissioning until someone accepts the Cirrus module certificates in a browser. Open the URL,
accept, and it continues on its own. Timeout is 30 minutes.

### Checkpoint 2 — the checkout is untouched

```powershell
git status --short
```

**Pass:** empty. Clone-seed writes only gitignored identity paths.

**Fail:** any modified or deleted file under `ignition/`. Capture the full output — that is the
bug, and it is the single most valuable finding this test can produce.

## Step 5 — Bring it up

```powershell
python tasks.py up
python tasks.py health
```

### Checkpoint 3 — health

**Pass:** all green, exit 0. Note that `up` deliberately exits non-zero if health fails.

## Step 6 — Verify what actually travelled

### Checkpoint 4 — Ignition content

Open <http://localhost:8088> (`admin` / `password`).

- **Tags:** the default provider's tag tree is present. **Tag values will be empty, and that is
  correct** — `valueStore.idb` is gitignored, so definitions travel and values do not. Do not
  read an empty tag as a failed clone.
- **Project:** `icc-2026` exists, and under it the `pm-sensor-listener` event stream. Its
  presence also proves the `.resources/` cache regenerates from committed sources, since that
  directory is gitignored.

### Checkpoint 5 — the secrets verdict

This is question 2, and it is the reason C1 and C2 were cut.

```powershell
docker logs icc26-ignition 2>&1 | Select-String -Pattern "Transmission|decrypt|Unable" | Select-Object -Last 40
```

**Pass:** MQTT Transmission connects to `tcp://chariot:1883` as `ign-transmission`. Confirm from
the other side too — Chariot's UI at <http://localhost:8081> (`admin` / `password`) should list
the client. That proves ciphertext encrypted on Matt's gateway decrypted on a gateway that has
never seen his machine, and cutting C1/C2 was correct.

**Fail:** an auth failure, or `Unable to decrypt ciphertext` in the log. That means the
encryption key is *not* build-wide, committed `Embedded` secrets are not portable, and **C1 and
C2 need to come back onto the plan.** Record the exact log line.

Remember the trial: if the Cirrus modules log `Trial license is expired`, that is a lapsed clock,
not a secrets failure. A fresh clone should have ~2 hours.

## Step 7 — Clean up

```powershell
cd C:\Users\matt\repos\icc26-clone
python tasks.py down -v
```

Safe: with `COMPOSE_PROJECT_NAME=icc26test` this only removes `icc26test_*` volumes.

```powershell
cd C:\Users\matt\repos
Remove-Item -Recurse -Force icc26-clone
cd icc-2026
python tasks.py up
```

Confirm the main stack is back and healthy before declaring done.

## Step 8 — Record the results

Update [`00-status.md`](00-status.md) with:

- Pass/fail for each of the five checkpoints, with exact output for anything that failed.
- **The verdict on checkpoint 5**, called out explicitly. If it failed, C1 and C2 go back on the
  plan and `00-status.md` needs to say so loudly — a teammate's clone would then have MQTT auth
  failing silently while `health` prints green.
- Whether the clone-seed path can now be moved from "assumed" to "proven" in the verification
  section.
- Anything in the Landmines section above that turned out to be wrong.

---

## Deferred: E1, the author round-trip

E1 is the same proof on Matt's own machine: `nuke`, delete the untracked `local/` and
`valueStore.idb`, then `seed` should take the clone-seed path against his existing checkout.

**It is deliberately not in this brief.** It destroys the known-good stack, and the clone test
above answers the same questions more realistically. Run it only after the clone test passes,
and only when there is time to rebuild if it does not.

## Still open after this test

Independent of the result, these remain from Phase C:

- **C5** — Ignition 8.3 API key. Unblocks `tasks.py scan` and, once the path below is fixed,
  `reset-trial`.
- **`tasks.py trial` / `reset-trial` use the wrong path.** It is `/data/api/v1/trial`, not
  `/data/api/v1/license-status` (404 on this build). `GET` works on plain basic auth — verified.
  `POST` resets the trial but needs WRITE, hence C5.
- **C3** — Postgres JDBC datasource `ICC26`.
- **C4** — module sha256s into `modules.manifest.json`. Note `verify-modules` skips the hash
  check when the field is blank (`tasks.py:439`), which is why it does not block this test.
- **`00-status.md` still describes the pre-C1/C2-cut plan** and needs rewriting.
