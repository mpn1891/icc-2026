> ## ✅ EXECUTED 2026-08-17 — every checkpoint passed
>
> This file is kept as the record of what was run, not as work to do. **Do not execute it
> again**: step 0 nukes volumes, and step 1's edits are already committed.
>
> | | |
> |---|---|
> | **CP1** | Cirrus **5.0.4** Engine, Transmission, Distributor loading — no compatibility warnings |
> | **CP2** | Transmission authenticated as `ign-transmission`, zero decrypt errors — **`Embedded` ciphertext is portable** |
> | **CP3** | Sparkplug `selftest.py` ran and passed inside `docker build` |
> | **CP4** | Both valves connected, both config pages answering, both topic trees live |
> | **CP5** | Pattern 1 tree auto-created — **a JSON `null` creates no tag at all** |
> | **CP6** | Pattern 2: **19 typed tags with engineering units, zero configuration** |
> | **CP7** | Engine requested Rebirth unprompted; device answered in **6 ms**; no loop |
> | **CP8** | Both death paths measured; pattern 1's will timestamp is the **connect** time, to within 7 ms |
> | **CP9** | Both auto-created trees correctly gitignored |
>
> Also proven: editing `services/sim-valve-mqtt/app.py` and re-running `tasks.py up` now reaches
> the container — the `--build` fix, tested directly.
>
> Findings moved to [`../00-architecture.md`](../00-architecture.md),
> [`01-native-mqtt.md`](01-native-mqtt.md) § *Ingest, as built* and
> [`02-sparkplug-b.md`](02-sparkplug-b.md) § *Ingest, as built*. Current state and what is next:
> [`00-status.md`](00-status.md).
>
> **Three things this brief got wrong**, worth knowing before trusting a cold-execution doc:
> the scratch clone was five commits stale and had none of the valve work (which was uncommitted
> in main, so no `git pull` could fix it); `docker image rm` needed `-f` because main's *exited*
> container held the tag; and a graceful `docker stop` does **not** mean nothing is published —
> the device publishes its own frozen `offline` document.

---

# The next build step — land patterns 1 & 2 in Ignition and run them for real

> **Written to be executed cold.** Everything needed is here or one link away; you should not
> have to reconstruct any decisions. When it is done, this file is replaced by whatever the
> next step turns out to be, and the durable findings move to
> [`../00-architecture.md`](../00-architecture.md) and the two pattern specs.
>
> Entry point: [`00-status.md`](00-status.md) → *Do this next* points here.

| | |
|---|---|
| **Goal** | The two sample valve services build, run, and produce Ignition tags — plus the blocker that gates them |
| **Touches** | `tasks.py`, `.gitignore`, one Engine namespace resource, `compose/ignition/modules.manifest.json`, docs |
| **Does not touch** | Anything under `services/sim-valve-*` — those are written and self-tested |
| **Blocked by** | Nothing. Step 0 *is* the blocker |
| **Unblocks** | Pattern 3 step 9 ([`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md) § 9) and pattern 4 |

## Why this, and why now

The two smart sample valve services are written but **have never been built or run.** Docker was
unavailable in the session that produced them, so the images do not exist, neither valve has
connected to Chariot, and no Ignition tag has ever been created from either. Open item 1 in both
[`01-native-mqtt.md`](01-native-mqtt.md) and [`02-sparkplug-b.md`](02-sparkplug-b.md) says the
same thing: *Ignition-side ingest — not built.*

This step closes that, and closing it is the whole point rather than a formality. **The pattern
1 / pattern 2 argument is only proven when the tags appear**, because the comparison is *how
much work each side took*. Pattern 1 needs a subscription written by hand and produces a tag
tree shaped like a JSON document. Pattern 2 needs nothing at all and produces nineteen typed,
unit-carrying tags. That asymmetry is currently asserted in two documents and demonstrated
nowhere.

It cannot start until the stale-image blocker is cleared — and that blocker now has a second
instance, because `docker compose up` builds a `build:` service only when its image tag is
missing. The two new valve images have exactly the same *"your edit never reached the
container"* failure mode that
[the stale-image trap](../00-architecture.md#the-stale-image-trap) describes for the gateway.

## Decisions already taken

Do not re-litigate these; they were settled when patterns 1 and 2 were designed.

- **Pattern 1's ingest is the Engine custom namespace, and nothing else.** No hand-authored UDT,
  no event stream, no routing script. Engine auto-creates tags from the JSON and the resulting
  tree is shaped like the payload rather than like the asset. That is a *better* contrast with
  pattern 2 than a laborious-but-correct UDT would have been, because it is visibly worse rather
  than merely more work.
- **The auto-created tag trees are gitignored**, matching what `.gitignore` already does for
  `MQTT Engine/Edge Nodes/` and for the reason that block already states: the module rebuilds
  them at runtime and they are keyed to whatever traffic that machine happened to see.
- **The audit-trail gap is documented, not fixed.** Engine maps a topic to tags, so each badge
  scan overwrites the last and the tag model holds no scan history. Tag history is the available
  remedy; it stays off for now.

---

## Step 0 — clear the blocker, and kill its second instance

Work in the scratch clone `C:\Users\matt\repos\icc26-clone`, **never the main checkout.** Per
[`00-status.md`](00-status.md), `icc26_ign-data` is currently the only place a working 5.0.4
Engine and Transmission exist — they were hand-installed through the gateway UI and are not in
the image. Do not `nuke` the main checkout until a rebuilt image is proven over there.

```powershell
docker image rm icc26/ignition:8.3.8
python tasks.py nuke      # CLONE ONLY -- icc26test_* volumes
python tasks.py seed      # rebuilds the image, re-accepts certs once
python tasks.py up
```

**CP1** — the gateway log shows Cirrus **5.0.4** Engine and Transmission loading, not 4.0.8.

**CP2** — MQTT Transmission connects to `tcp://chariot:1883` as `ign-transmission`, confirmed
from Chariot's client list *as well as* the gateway log. This settles the last unproven Part 1
assumption: that committed `Embedded` ciphertext decrypts on a gateway that has never seen
Matt's machine. On failure, watch for `Unable to decrypt ciphertext` and record the exact line —
a Secret Provider comes back onto the plan.

Then make a stale image fail loudly instead of passing green:

| File | Change |
|---|---|
| `tasks.py` → `task_up()` | `docker compose up -d` → **`up -d --build`**. One move fixes the gateway image *and* both valve images: a source edit now actually reaches the container. The layer cache keeps an unchanged build to a couple of seconds |
| `tasks.py` → new `task_build()` | `docker compose build` on demand, wired into `main()` and the `HELP` string |
| `compose/ignition/modules.manifest.json` | Fill the three empty `sha256` fields. `python tasks.py hash-modules` prints them; `verify-modules` already checks them when non-empty |

> Filling the manifest hashes only validates **host** files, and
> [the architecture doc already says](../00-architecture.md#the-stale-image-trap) that is
> measuring the wrong thing. `up --build` is what closes the real gap. Do both — the hashes
> catch a corrupt or swapped download, the build flag catches the stale tag.

## Step 1 — pattern 1: one subscription, and nothing else

Two files, both known formats, so edit on disk and apply with `python tasks.py scan`.

`ignition/config/resources/core/com.cirruslink.mqtt.engine.gateway/custom-namespace/icc26-native/`

- **`config.json`** — change `subscription` from `icc26/site1/upstream/+/+/telemetry` to
  **`icc26/site1/upstream/br-201/sample-valve-01/#`**. Everything else stays:
  `jsonPayload: true`, `qos1: true`, `writeableTags: false`, `numbersAsFloats: true`,
  `rootFolder: ""` (so tags land in the `MQTT Engine` provider).
- **`resource.json`** — the `description` still reads *"Pattern 1 native MQTT telemetry ingest
  into pm-sensors"*, which was never true even for the vibration gateway: `rootFolder` is empty,
  so nothing has ever gone to `pm-sensors`. Correct it.

**The wildcard has to be this narrow, and that is a finding to keep, not a wart to fix.** The
obvious `icc26/site1/upstream/+/+/#` also swallows pattern 5's `…/br-201/batch/event` and
pattern 7's `…/br-201/batch-summary` — `#` matches zero levels too. So pattern 1's subscription
enumerates one device by name, and **adding a second plain-MQTT valve means editing this file**,
while pattern 2's `spBv1.0/#` already covers every edge node that will ever exist. Add that as a
row to the comparison table in [`02-sparkplug-b.md`](02-sparkplug-b.md).

**`.gitignore`** — add one anchored path beside the existing `MQTT Engine/Edge Nodes/` entry and
widen that block's comment to cover both trees:

```
ignition/config/resources/core/ignition/tag-definition/MQTT Engine/icc26/
```

Then untrack what is already committed there:

```bash
git rm -r --cached "ignition/config/resources/core/ignition/tag-definition/MQTT Engine/icc26"
```

13 files, all of them the retired vibration gateway's auto-created telemetry tree. Nothing
publishes to those topics any more, so they will not regenerate; the valve's tree appears in
their place and stays untracked.

## Step 2 — pattern 2: verify that there is nothing to do

`default-namespace/Sparkplug B/config.json` already reads `subscription: "spBv1.0/#"` with
`defaultTagsEnabled: true`, and `namespace-server-set/Sparkplug B-Default Set` already binds it
to the Default Set. **Expect zero file changes.** If any turn out to be necessary, that *is* the
finding and it belongs in [`02-sparkplug-b.md`](02-sparkplug-b.md) — quietly fixing it and
saying nothing would erase the point of the pattern.

Its tags land under `MQTT Engine/Edge Nodes/…`, which `.gitignore` already covers. After step 1
both patterns are consistent and neither churns tag files into anyone's diff.

## Step 3 — run both valves and record what actually happens

`python tasks.py up` in the clone, then a watcher in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -t 'spBv1.0/#' -v
```

| CP | Check |
|---|---|
| **3** | Both images build. `selftest.py` runs inside the `sim-valve-spb` build and passes — a wire-format regression must fail `docker build`, not turn into a device Ignition silently refuses to birth |
| **4** | Both valves connect; config pages answer on <http://localhost:8085> and <http://localhost:8086>; badge buttons produce traffic on both topic trees |
| **5** | **Pattern 1**: Engine auto-creates `MQTT Engine/icc26/site1/upstream/br-201/sample-valve-01/{event,state,telemetry}/{meta,source,values}/…`. Record the datatypes it picks, and specifically **what it does with a JSON `null`** — `values.deny_reason` on a grant, `values.sample_id` on a denial. Dropped tag, bad quality, or empty string? This is currently a guess, and it is the direct counterpart to pattern 2's typed nulls |
| **6** | **Pattern 2**: the tag tree appears with **no configuration at all** — 19 metrics, correct datatypes, engineering units on the three analogs. The single most important checkpoint in this step: it is simultaneously the proof the hand-written protobuf encoder is correct *and* the half of pattern 2 that pattern 1 cannot do |
| **7** | Rebirth: watch the gateway log for Engine issuing `Node Control/Rebirth` unprompted, and confirm the device answers (the page counts them). **If CP6 fails, check for a Rebirth loop before suspecting the encoder** |
| **8** | Death, both ways, both patterns. `docker stop` is graceful — pattern 1 publishes a final state and the broker discards the will; pattern 2 publishes DDEATH then an explicit NDEATH. `docker kill` is where the wills actually fire. Note that pattern 1's will carries the *connect* time |
| **9** | `git status` in the clone is clean apart from intended files — proves the new ignore path is right and that nothing under `ignition/` was disturbed |

Two behaviours will look like bugs. **Capture them; do not fix them.**

- **Retained `event` messages replay on reconnect.** The valve ships with retain on for all
  three message types, so Engine receives a stale badge scan on every reconnect and presents it
  as current. Already foreshadowed in [`01-native-mqtt.md`](01-native-mqtt.md); now visible in
  the tag tree.
- **Each scan overwrites the last.** No history in the tag model, so a GxP audit trail silently
  keeps only the most recent record — invisible until somebody asks about last Tuesday. Document
  it; note tag history as the remedy; leave it off.

## Step 4 — fold the findings back

| File | |
|---|---|
| [`01-native-mqtt.md`](01-native-mqtt.md) | Replace open item 1 with what was built. Add the narrow-subscription finding, the CP5 JSON-null result, and the two captured behaviours. Progress log |
| [`02-sparkplug-b.md`](02-sparkplug-b.md) | Open items 1 and 2 resolved (or not). Add the subscription-breadth row to the comparison table. Progress log |
| [`../00-architecture.md`](../00-architecture.md) | A short subsection on the two Engine ingest surfaces and what each produces; the gitignore rationale now covering both auto-created trees; the `up --build` note in the stale-image section |
| [`00-status.md`](00-status.md) | **Needs the most rewriting.** The blocker is cleared or it is not; CP2 settles the `Embedded`-ciphertext question either way; and *Do this next* should come out of this step pointing at pattern 3 step 9 or pattern 4 |
| [`../../services/README.md`](../../services/README.md) | Flip both valve rows off "Ignition-side ingest still TODO" |

---

## Done when

In the scratch clone, from a cold `nuke` + `seed` + `up`:

1. `python tasks.py health` is green and the gateway is running Cirrus 5.0.4.
2. `python tasks.py verify-modules` passes with the manifest hashes filled in.
3. A badge press on <http://localhost:8085> produces tags under
   `MQTT Engine/icc26/site1/upstream/br-201/sample-valve-01/…` in the Designer.
4. A badge press on <http://localhost:8086> produces 19 typed tags under
   `MQTT Engine/Edge Nodes/ICC26-Site1-UPSTREAM/SAMPLE-VALVE-02/SV-202/…`, **without anyone
   having configured anything** — and both trees are absent from `git status`.
5. `docker kill` on each container produces its death certificate on the watcher: a retained
   `state: "offline"` JSON document for pattern 1, an NDEATH with matching `bdSeq` for pattern 2.
6. Editing a line in `services/sim-valve-mqtt/app.py` and re-running `python tasks.py up`
   actually changes the container's behaviour.

Then, in the **main** checkout, apply the same `tasks.py`, `.gitignore`, namespace and manifest
changes and commit. `git add` the files you meant to change and `git restore .` for the
`lastModification` churn — *Working rules* in [`00-status.md`](00-status.md).

Do not `git commit` from inside the scratch clone, and do not `nuke` the main checkout.
