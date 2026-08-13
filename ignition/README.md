# Ignition config as code

`config/` and `projects/` are bind-mounted into the gateway at
`/usr/local/bin/ignition/data/{config,projects}`. Ignition 8.3 keeps **all** gateway
configuration — database connections, device connections, tag providers, UDTs, alarm
pipelines, MQTT module settings — in human-readable files here rather than in an internal
database. That is what makes this repo possible.

**Both directories are committed** — a clone arrives with the shared gateway configuration
already in place. They were empty exactly once, before the very first `seed` on the machine
that built this repo. See `docs/00-architecture.md` § *Seeding* for why the first boot on any
machine still has to happen without these mounts in place.

## The workflow

It runs in both directions.

**You change something in the Designer or Gateway UI** → the gateway writes files here →
`git status` shows the diff → review and commit it like any other change.

**A teammate's changes arrive via `git pull`, or you edit these files on disk** → the gateway
does not notice on its own. It does **not** watch this directory. Apply with
**`python tasks.py scan`**.

Scan POSTs to `/data/api/v1/scan/config` and `/data/api/v1/scan/projects`. Ignition 8.3 guards
those routes with an API key rather than the admin password. Each machine needs a one-time
manual key created at Gateway UI → Platform → Security → API Keys, with a security level
granted Gateway read/write access. Put the complete `name:secret` value in the gitignored
`.env` as `IGNITION_API_TOKEN_HTTPS`. The first key cannot bootstrap itself through the API
because key creation already requires authenticated write access. Until yours is configured,
`scan` fails with a 401 that explains itself — then fall back to
`python tasks.py restart ignition`.

That second step is the one people forget. If a pulled change "didn't take", `python tasks.py
scan` before debugging anything else.

## Commit what you meant to change

Every gateway write stamps `lastModification` / `lastModificationSignature` into the
neighbouring `resource.json`, so `git status` will show resources you never touched. Stage the
files you actually changed, then `git restore .` to drop the rest. A `resource.json` whose only
diff is a timestamp is churn — restoring it costs nothing and saves everyone else a pointless
merge conflict.

Container-consumed secrets are the exception to "scan is enough": those live in `.env` and
reach the gateway as environment variables, which are read once at process start. After
changing one, run `python tasks.py restart ignition`. `IGNITION_API_TOKEN_HTTPS` is consumed
by `tasks.py` itself and is re-read on every invocation, so changing that token needs no restart.

## What is and is not tracked

Only `config/` and `projects/` are source. Everything else the gateway writes under `data/`
— its internal db, `logs/`, `var/`, `valueStore.idb`, `jar-cache/` — is runtime state living
in the `ign-data` named volume and is gitignored.

Two things inside these directories are also excluded, because they are machine-specific
rather than shared: `config/local/` and `config/resources/local/`.

`valueStore.idb` holds persisted Memory tag values. It is deliberately untracked — committing
it would mean every gateway restart produced a diff.

Those three paths are gateway *identity*, not configuration: each machine generates its own
during `seed`, which copies them out of the seed container so a clone has them before the real
stack starts. Because they are gitignored, that copy leaves `git status` clean. If you ever see
them in a diff, the gitignore is wrong — do not commit them.
