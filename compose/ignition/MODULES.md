# Cirrus Link modules — you must supply these

The `.modl` files are licensed binaries and are **gitignored**. A fresh clone will not have
them; drop them into `modules/`.

```bash
python tasks.py verify-modules
```

That reads the version out of each `.modl` (they are zip files containing `module.xml`) and
compares it against `modules.manifest.json`. It is not a filename check — see below for why
that matters.

## What to download

Ignition **8.3.8** requires the Cirrus Link **5.x** line. Version **5.0.4** is pinned in
`modules.manifest.json`.

| File | Required | Purpose |
|---|---|---|
| `MQTT-Engine-signed.modl` | yes | Northbound consumer — Sparkplug B and Custom Namespace ingest |
| `MQTT-Transmission-signed.modl` | yes | Sparkplug B edge node; publish path for patterns 3–7 |
| `MQTT-Distributor-signed.modl` | optional | In-gateway broker, break-glass fallback only |

**All three versions must match exactly.** Cirrus Link documents class-loading instability
and gateway crashes when they do not.

### The 4.x / 5.x trap

The 8.1-era **4.x** downloads have **identical filenames** to the 8.3-era 5.x ones. Nothing
about the file on disk tells you which you have, and Ignition will happily try to load the
wrong one. The two lines are even signed by different certificates — 4.0.8 by thawte, 5.0.4
by a DigiCert-issued Cirrus cert.

This is exactly why `verify-modules` cracks the zip open and reads `<version>` from
`module.xml` rather than trusting the filename:

```
  FAIL  MQTT Engine - WRONG VERSION
          manifest expects 5.0.4.x
          file actually is 4.0.8.2021071520
```

When you download, confirm you are on the **8.3** tab, not 8.1.

## Where to get them

Cirrus Link modules are not anonymously fetchable, which is why there is no download script:

- <https://inductiveautomation.com/downloads/third-party-modules> — pick the **8.3** tab
- <https://cirrus-link.com/mqtt-modules/>
- 8.3 release notes and compatibility matrix: <https://docs.chariot.io/display/CLD83/>

## Keeping the team on identical bits

`modules.manifest.json` carries a `sha256` per file. The values start empty. Once you have
the right files:

```bash
python tasks.py hash-modules      # prints sha256 for each .modl present
```

Paste them into `modules.manifest.json` and commit. From then on `verify-modules` fails on
any mismatch instead of letting a subtly different build reach the stage.

## How they reach the gateway

Baked into a derived image — see `Dockerfile`, which documents the reasoning in full. Short
version: Ignition 8.3 reads modules from `data/var/ignition/modl` and only discovers them on
the **first launch of a fresh data volume**. That folder is inside a named volume, and
bind-mounting into a subpath of a named volume stops Docker seeding it at all, which leaves
the gateway with no `data/config` and no `data/projects`.

The `/modules` bind-mount convention and `GATEWAY_MODULE_RELINK` are 8.1-era. The 8.3
entrypoint handles neither. Do not reintroduce them.

**Adding or upgrading a module therefore requires a volume rebuild:**

```bash
python tasks.py nuke      # drops volumes; an existing volume is never re-seeded
python tasks.py seed
```

## Commissioning

The presence of **any** third-party module makes the gateway stop in commissioning on first
launch and wait for a human to accept the module certificate in the browser. It still answers
`/StatusPing` with `{"state":"RUNNING","details":"COMMISSIONING"}`, so naive health checks
report it as healthy while it does nothing.

Pre-seeding `data/modules.json` with the correct certificate fingerprints **does not** avoid
this — it was implemented and tested, and the gateway still demanded commissioning. (The
fingerprint is the SHA-1 of the signing cert's DER; the method was validated by reproducing
Inductive Automation's own `88338069eb9c3f2d46a4baf701e4fa71bf073293`.) The approach was
removed rather than left in as dead complexity.

So `tasks.py seed` detects the state, prints the URL, and waits for you. It is a one-time
click per fresh volume, and `tasks.py health` reports COMMISSIONING as a failure so it can
never be mistaken for a working gateway.
