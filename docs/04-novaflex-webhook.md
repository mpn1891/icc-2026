# 04 — NovaFlex HTTPS webhook

> Talk track for pattern 4. Build spec: [`plans/04-novaflex-webhook.md`](plans/04-novaflex-webhook.md).
> Architecture decisions live in [`00-architecture.md`](00-architecture.md); this file is what
> you speak, plus the runbook that has to be executed before you can speak it.
>
> **Built 2026-08-23. Not verified against a running stack.** Every file in the table below
> exists and is internally consistent. The sender was exercised locally against a stub HTTP
> receiver. **Nothing has met the Ignition gateway, Transmission, or Chariot** — the receiving
> Event Stream was authored blind, from files, with the stack down and no access to the
> gateway UI. Read [§ Deviations](#deviations) before demoing anything here, and run
> [§ The runbook](#the-runbook) once, in order. Until that runbook passes, pattern 4 is code,
> not a demo.
>
> The retired LIMS talk track is Extra: [`extra/lims-webhook.md`](extra/lims-webhook.md).
> Do not demo `:8000`.

| | |
|---|---|
| **Pattern** | 4 of 7 — webhook, because that is all the instrument can emit |
| **Mechanism tag** | `meta.mechanism = "webhook"` |
| **Source** | `services/webhook-novaflex/` — the same FLEX2, HTTPS POST on sample complete |
| **Device page** | <http://localhost:8084> |
| **Ignition** | Event Stream `04_webhook/novaflex-result`, HTTP source → Transmission |
| **Topic** | `icc26/site1/qc/analyzers/novaflex-01/result` — **the same as pattern 3** |
| **MQTT user** | none of its own. Publishes as `ign-transmission`, like 3 and 6 |
| **Depends on** | nothing. Does not consume pattern 3's MQTT and does not touch `opcua-novaflex` |

---

## Talk points

**A lot of lab instruments do not speak MQTT, OPC UA, or SQL. They POST JSON, and that is the
whole integration surface.** Pattern 3 is the FLEX2 as Nova actually ships it — a licensed OPC
UA server with about four hundred tags. Pattern 4 is the same instrument imagined with a
callback URL in a config screen and nothing else, which is what most of the room has actually
integrated.

**Put the three device pages side by side.** Pattern 1 on `:8085` gives you a topic, a QoS and
a retained flag — three fields, all of them somebody's opinion. Pattern 2 on `:8086` has taken
all three away and left three *names* inside a namespace the specification fixed. Pattern 4 on
`:8084` has a URL, a shared secret and an on switch. The list shrinks pattern by pattern, and
what it is shrinking towards is somebody else having already decided.

**The Event Stream is the point of reuse.** Pattern 3's tag-change and pattern 4's HTTP POST
are two sources on the same kind of pipeline, both ending at the same Transmission handler,
both landing on the same topic. The mechanism is not the transport — here you can watch HTTP
go in and MQTT come out, and the only thing that records what happened is `meta.mechanism`.

**One topic, two colours, and a subscriber cannot tell them apart.** The two documents carry
identical `values` keys. That is the namespace claim, demonstrated rather than asserted: if
you sorted these topics by how the data arrived, you would have had to split one analyzer
across two branches of the tree, and every consumer would care.

**If Ignition is down, the POST fails, and the result never reaches the backbone.** There is
no outbox on this pattern, deliberately. Most instruments that can only POST do not queue, and
the ones that do, queue badly. The durable version of the same problem is pattern 5: the
instrument wrote a row in a database you do not own, and you tail the WAL. Say the line, then
go to pattern 5.

---

## The chain

```
services/webhook-novaflex  (its own container, its own sample lifecycle)
        │
        │  HTTPS POST, one per completed sample
        │  X-Webhook-Secret: icc26-webhook-secret
        ▼
Ignition Event Stream  04_webhook/novaflex-result
        │   filter     webhook_event.accept(event)            ← secret + SampleID + SampleTime
        │   transform  webhook_event.build_novaflex_result()   ← vendor body → the envelope
        ▼
MQTT Transmission  (chariot_broker, as ign-transmission)
        │
        ▼
icc26/site1/qc/analyzers/novaflex-01/result       meta.mechanism = "webhook"
                    ▲
                    │
Event Stream 03_opcua/novaflex-result             meta.mechanism = "opcua-event"
        ▲
services/opcua-novaflex  ── vendor HistoricalSampleResults/SampleTime tag change
```

The instrument does not subscribe to MQTT. It has no broker credential and there is nothing
to grant it — see [`compose/chariot/README.md`](../compose/chariot/README.md). The LIMS cycle
hazard went away with the LIMS.

---

## What shipped

| Path | What |
|---|---|
| `services/webhook-novaflex/app.py` | **new.** Sample lifecycle, vendor result body, the one HTTPS POST. Stdlib only |
| `services/webhook-novaflex/webui.py` | **new.** `http.server` config page, house style |
| `services/webhook-novaflex/page.html` | **new.** Inline CSS/JS, no CDN, same stylesheet as the valves' pages |
| `services/webhook-novaflex/Dockerfile` | **new.** No dependencies, no `pip` layer |
| `services/webhook-novaflex/README.md` | **new.** Deviations, TLS notes, what is unproven |
| `ignition/.../event-streams/04_webhook/novaflex-result/` | **new, authored blind.** HTTP source, filter, transform, Transmission handler |
| `ignition/.../script-python/webhook_event/code.py` | **new.** Jython 2.7 envelope wrapper + filter |
| `docker-compose.yml` | `webhook-novaflex` on 8084, cert mount; `lims` commented out |
| `.env.example` | `WEBHOOK_NOVAFLEX_*` block; LIMS block kept as the documented fallback |
| `compose/chariot/mqtt-users.json` | `lims-bridge` removed |
| `compose/chariot/README.md` | row removed, and why pattern 4 needs no account of its own |
| `compose/postgres/initdb/02-schema.sql` | `lims.*` marked Extra. **Not dropped** — that waits for the 05/06 nuke |
| `services/opcua-novaflex/` | **untouched.** Pattern 3 is live; this pass does not go near it |

Left on disk, wired to nothing, on purpose: `services/lims/`, the WebDev resource at
`com.inductiveautomation.webdev/resources/lims/sample-result/`, the `lims_webhook` script
module, and the `lims.*` tables. They are the fallback — see the last row of the deviations
table.

---

## The envelope

Same topic, same `values` keys, same `correlation_id` field as pattern 3. `ts` is the vendor
`SampleTime`; `meta.ingest_ts` is when Ignition received the POST.

```json
{
  "ts": "2026-08-23T14:03:22.145Z",
  "seq": 1,
  "source": { "id": "novaflex-01", "type": "analyzer" },
  "meta": {
    "mechanism": "webhook",
    "ingest_ts": "2026-08-23T14:03:22.401Z",
    "correlation_id": "S-00014"
  },
  "values": {
    "sample_id": "S-00014", "batch_id": "BR-2026-014", "vessel_id": "BRX-2000-A",
    "cell_type": "CHO-K1", "sample_source": "ESM", "operator": "Auto",
    "gas": { "ph": 7.11, "pco2": 52.0, "po2": 110.4 },
    "chem": { "na": 148.2, "k": 4.9, "ca": 1.11, "nh4": 2.1,
              "gln": 2.9, "glu": 1.2, "gluc": 4.21, "lac": 1.08 },
    "cell_density": { "total_density": 6.4, "viable_density": 6.1,
                      "viability_percent": 95.3, "avg_live_diameter_um": 15.8 },
    "calculated": { "hco3": 18.1, "o2_saturation": 97.4, "co2_saturation": 91.9 },
    "modules_used": { "cdv": true, "chemistry": true, "gas": true, "osmo": false }
  }
}
```

**There is no `osmo` key**, because the osmometer is not fitted, and `modules_used.osmo` is
`false` right beside it. Absent and "deliberately not measured" are different statements and
both are on the wire. Nothing is ever `0` to mean "did not measure".

The **POST body is not this**. It is the vendor's own shape — `SampleID`, `Gas.pH`,
`Modules.Osmo` — with no `meta`, no `seq` and no topic, because the instrument does not know
those things exist. The device page shows the exact body it posted; have that panel on screen
when you say so. Wrapping it is Ignition's job, and that split is the pattern.

---

## Deviations

Everything here departs from [`plans/04-novaflex-webhook.md`](plans/04-novaflex-webhook.md),
or is a claim the spec makes that the as-built cannot support. **Nothing in this table has
been checked against a running gateway.**

| # | Spec said | As built | Why, and what it costs |
|---|---|---|---|
| 1 | POST added to `services/opcua-novaflex/app.py` | **A new container, `services/webhook-novaflex/`.** `opcua-novaflex` is byte-unchanged | Pattern 3 is live and broker-verified; a second output bolted into it puts that at risk every time pattern 4 is touched. Two containers is also the truer picture — a webhook-only instrument and an OPC UA instrument are two products. **Cost:** see row 2 |
| 2 | Checkpoint 3: one sample, two mechanisms, identical `correlation_id` | **Not achievable by construction.** Two containers run two independent sample lifecycles | Same-sample correlation across 3 and 4 is no longer automatic. The id *format* is identical (`S-#####`) and both stamp `sample_id` into `meta.correlation_id`, so the field still works and the firehose still colours by mechanism. **To line them up by hand:** set `WEBHOOK_NOVAFLEX_SAMPLE_ID_START` so the webhook device's next id equals the OPC device's next id (read both from `:8084` and the `sample_id` tag), `docker compose up -d webhook-novaflex`, then trigger one sample on each. **On stage, do not promise a matching pair** — say "the same field, on both paths, and here is what a consumer does with it" |
| 3 | Event Stream built in the UI, then committed | **Authored blind as files.** Never opened in a gateway | The stack was down and no UI step was wanted. Every guessed key is listed in [§ Guessed fields](#guessed-fields) below. If the resource fails to load, the fallback is row 9 |
| 4 | "If the UI offers a clean 401 path, take it" | **Unknown, and untestable from here.** The transform has no servlet response to set, unlike the WebDev handler in `lims_webhook`, so rejection means "no MQTT message" and the HTTP status is whatever the source decided before user code ran | **Do not claim a status on stage until runbook step 6 has been run.** The device page at `:8084` records and displays whatever comes back, which is how you find out. Many real webhook endpoints do return 200 and drop; if that is what happens, it is a better talk point than a 401 |
| 5 | Secret checked "in the filter" | **Checked in the filter *and* re-checked in the transform** | A filter returning `False` is Event Streams' own documented drop. Relying on the transform returning `None` would depend on how the Transmission handler treats a null payload, and a handler that published the string `null` onto the result topic is a bad thing to find out about on stage |
| 6 | — | **Fails closed if request headers cannot be found on the event object.** `ALLOW_WHEN_NO_HEADERS = False` in `webhook_event` | If the HTTP source turns out not to expose headers to user code, every POST is rejected and pattern 4 goes silent with a log line naming the remedy. The alternative — accept everything when the header is unreadable — would make the secret checkpoint pass by accident, which is worse. Runbook step 4 is what tells you the real shape |
| 7 | Vendor body per the sketch | **`SampleSource` added** to the POST body and `sample_source` to the envelope | Pattern 3 publishes `values.sample_source`; without it the two `values` trees differ, and "a subscriber cannot tell them apart" is only as good as the narrower tree |
| 8 | — | **Absent analytes are omitted; pattern 3 publishes explicit `null`** | `opcua_event.build_novaflex_result` emits `"osmo": null` for a Bad-quality tag — the OPC path knows the node exists and read Bad. Pattern 4 never had the key. Both obey "never `0`", but the two documents **are** distinguishable by key presence. Either accept it and say so, or make `opcua_event` drop nulls too — a one-line change, deliberately not made in this pass because pattern 3 is verified and this is not |
| 9 | — | **The WebDev fallback stays on disk.** `com.inductiveautomation.webdev/resources/lims/sample-result/` and the `lims_webhook` module are unwired but present | If the blind Event Stream will not load, this is the one-step recovery: a WebDev `doPost` was hand-authored as plain files and **broker-verified on 2026-08-20**, publishing with `system.cirruslink.transmission.publish("chariot_broker", …)`. See [§ If the Event Stream will not load](#if-the-event-stream-will-not-load) |
| 10 | Drop `lims-bridge` | Done in `mqtt-users.json` — **but `MQTT_USERS` seeds on first run only** | The account still exists in a running Chariot's own store. Delete it in the UI at `:8081` → Users, or wait for the next `nuke`. Nothing connects with it either way |
| 11 | Config-page toggle | Done, and **not persisted** across a restart | The valves persist their commissioned topic to a named volume; this does not. A commissioned topic is a device setting, an enable switch you flip mid-talk is a stage control, and a restart should return the instrument to its `.env` factory state |
| 12 | — | **No `requirements.txt` and no `pip` layer** | `urllib`, `ssl` and `http.server` are all standard library. It is the only service in the tree with no dependency at all, which is a small argument in itself about what an HTTP integration costs |

### Guessed fields

Every key in
`ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/04_webhook/novaflex-result/config.json`
that was inferred rather than observed. JSON holds no comments, so the list lives here.

| Key | Value written | Confidence | If it is wrong |
|---|---|---|---|
| `source.type` | `"ignition.http"` | **Guess.** Inferred from the sibling naming (`ignition.gatewayEvent`, `ignition.string`, `ignition.jsonObject`) | The resource fails to load, most likely with a deserialization error naming an unknown source type in the gateway log. Fix the string and `python tasks.py scan`; there is nothing else to change |
| `source.config` | `{}` | **Deliberately empty.** "Require HTTPS on" was specified, but the key name for it is unknown and a wrong key risks the whole resource failing to load | An empty config means the source's own defaults apply. **Require HTTPS is therefore NOT set by this file** — tick it once in the Event Stream's config panel after the resource loads, or add the real key here once you have seen it |
| `sourceEncoder.type` | `"ignition.jsonObject"` | **Guess**, chosen because the POST body is JSON. The 01_mqtt stream uses it for a JSON payload; the 03_opcua stream uses `ignition.string` because its source hands over a tag path | Either way `webhook_event._body()` accepts a dict *or* a string, so this one should be survivable. If the encoder rejects the body outright, switch to `ignition.string` |
| `transformEncoder.type` | `"ignition.string"` | **Copied** from `03_opcua/novaflex-result`, which is verified. The transform returns `system.util.jsonEncode(...)`, a string, exactly as pattern 3's does | — |
| `handlers[0]` (whole block) | copied verbatim from `03_opcua/novaflex-result`, topic unchanged | **Verified in pattern 3**, not in this stream | — |
| `batch`, `onError`, `enabled` | copied from `03_opcua/novaflex-result` | **Verified in pattern 3** | — |
| The HTTP source **URL** | `https://ignition:8043/system/eventstream/icc-2026/04_webhook/novaflex-result` | **Guess**, taken from the spec. The gateway's own Event Stream module mounts its REST API at `/data/event-stream/`, which is a hyphen and a different prefix — so this path is genuinely uncertain | Runbook step 2 probes the candidates. Whatever answers is the real one; put it in `.env` as `WEBHOOK_NOVAFLEX_URL`, in this file, and in the spec |
| `event.data` shape | assumed dict-or-string | **Guess** | `_body()` handles both. Step 4 confirms |
| `event.metadata.headers` | assumed to exist | **Guess.** `_headers()` tries `event.metadata.headers`, `event.metadata` as a map, `event.meta.*`, `event.headers`, and a callable `getHeaders()` | If none of those is right, pattern 4 goes silent with a warning naming the remedy. Step 4's shape dump tells you the true attribute name; fix `_headers()`, `scan`, done |

`webhook_event.code.py` logs the **real** shape of the first event it sees, once, at INFO —
`type`, `dir(event)`, the type of `event.data`, and `dir(event.metadata)`. That single log line
converts most of this table into facts. Runbook step 4 is reading it.

---

## The runbook

**Unverified by the agent that wrote it.** None of these commands has been executed. They are
written to be run once, in order, in one pass, and each says what to expect. Windows
PowerShell; `curl.exe` is spelled out because bare `curl` is an alias for `Invoke-WebRequest`
in Windows PowerShell 5.1 and will not take these flags.

### 0 — preconditions

```powershell
python tasks.py up
python tasks.py health
python tasks.py trial
```

Expect: all green, and **both** trials with time left. Chariot's does not auto-start — if the
listener is shut, open `http://localhost:8081` → License → start trial. A lapsed Ignition
trial tears down subscriptions and makes everything below look broken for the wrong reason.

Confirm the certificate the new container mounts actually exists as a *file*:

```powershell
Get-Item ignition\certificates\icc26-ignition.crt
```

Expect: a file, not a directory, not an error. If it is missing, run `python tasks.py seed`
(fresh machine) or `python tasks.py enable-ssl` (existing gateway) before `up`, or Docker will
create a directory at that path and the mount will be useless.

### 1 — load the new Ignition resources

```powershell
python tasks.py scan
docker logs icc26-ignition --since 3m 2>&1 | Out-String -Stream |
  Select-String -Pattern "webhook_event|04_webhook|event.?stream"
```

Expect: **nothing that says the resource failed to load.** A wrong `source.type` shows up here
as a deserialization or "unknown type" error naming
`04_webhook/novaflex-result`. If it does, go to
[§ If the Event Stream will not load](#if-the-event-stream-will-not-load).

Then ask the module itself what streams it has:

```powershell
curl.exe -s --cacert ignition\certificates\icc26-ignition.crt `
  -H "X-Ignition-API-Token: $token" `
  "https://localhost:8043/data/event-stream/api/v1/streams/status"
```

`.env` is not loaded into the shell, so read the token out of it first:

```powershell
$token = (Select-String -Path .env -Pattern '^IGNITION_API_TOKEN_HTTPS=(.+)$').Matches[0].Groups[1].Value
```

Expect: JSON listing **both** `03_opcua/novaflex-result` and `04_webhook/novaflex-result`. If
only the first appears, the new resource did not load. A 401 here means the key lacks *write*
permission, not that it is wrong — Ignition answers 401 rather than 403, which is the trap
recorded in `plans/03-opcua-analyzer-playbook.md`.

### 2 — find the real HTTP source URL

The path in `.env.example` is a guess. Probe the candidates; anything that is **not 404** is
the mount.

```powershell
$candidates = @(
  "/system/eventstream/icc-2026/04_webhook/novaflex-result",
  "/system/event-stream/icc-2026/04_webhook/novaflex-result",
  "/data/event-stream/icc-2026/04_webhook/novaflex-result",
  "/system/eventstream/icc-2026/04_webhook%2Fnovaflex-result"
)
foreach ($p in $candidates) {
  $code = curl.exe -s -o NUL -w "%{http_code}" `
    --cacert ignition\certificates\icc26-ignition.crt `
    -X POST -H "Content-Type: application/json" `
    -H "X-Webhook-Secret: icc26-webhook-secret" `
    --data-binary '{\"SampleID\":\"probe\",\"SampleTime\":\"2026-08-23T00:00:00.000Z\"}' `
    "https://localhost:8043$p"
  "{0,-6} {1}" -f $code, $p
}
```

Expect: exactly one non-404 (200, 202 or 204 are all plausible). Note it.

If **every** candidate is 404, the stream loaded but the HTTP source did not mount, which
almost certainly means `source.type` is wrong even though the resource parsed — go to the
fallback section.

`localhost` is used rather than `ignition` because it is the SAN on the seed-generated
certificate, so `--cacert` alone verifies cleanly with no `-k` and no hostname override. The
container has to dial `ignition` and therefore does turn the hostname check off; the host does
not have to.

Two shell traps in that snippet, both of which cost twenty minutes if you meet them cold.
`curl` is an alias for `Invoke-WebRequest` in Windows PowerShell 5.1 — it must be `curl.exe`.
And the `\"` inside a single-quoted string is correct and not a typo: PowerShell passes the
backslashes through literally and the C runtime argument parser turns `\"` back into `"`. If
the quoting still fights you, put the body in a file and use `--data-binary "@probe.json"`.

**Record the winner** in three places: `.env` as `WEBHOOK_NOVAFLEX_URL`, the table at the top
of this file, and `plans/04-novaflex-webhook.md`. Then:

```powershell
python tasks.py restart webhook-novaflex
```

(`WEBHOOK_URL` is container environment, so `scan` will not pick it up — see
`00-architecture.md`.) Or set it live on the device page at <http://localhost:8084>, which is
faster while you are still hunting.

### 3 — the probe reached user code

```powershell
docker logs icc26-ignition --since 2m 2>&1 | Out-String -Stream | Select-String "webhook_event"
```

Expect the probe to have been **rejected** — it has a valid secret but no real body — with
one of:

```
novaflex webhook rejected: no SampleID in the body        ← should not happen, the probe has one
novaflex webhook rejected: no request headers reachable   ← go to step 4, this is the interesting case
```

and, either way, **the shape dump**, once:

```
webhook event shape: type=... attributes=[...]
webhook event.data: type=...
webhook event.metadata: type=... attributes=[...]
```

If there is no `webhook_event` line at all, the request never reached the filter: the URL is
wrong (step 2) or the stream is disabled.

### 4 — read the shape dump and fix the guesses

This is the step everything else was written around. From those three lines:

- **Where the headers are.** If `event.metadata` has no `headers` attribute, edit `_headers()`
  in `ignition/projects/icc-2026/ignition/script-python/webhook_event/code.py` to use whatever
  it does have, then `python tasks.py scan`.
- **Whether `event.data` is a dict or a string.** `_body()` takes both, so nothing needs
  changing — but note which, and delete the dead branch afterwards.
- Update the *Guessed fields* table above, turning each row into a fact or a fix.

Re-run step 2's probe until the log says `rejected: no SampleID` or the request is accepted.
The gateway is now doing what the file intended.

### 5 — happy path

Subscribe first, in its own window:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/qc/analyzers/novaflex-01/result' -v
```

Trigger one sample on the webhook instrument — the **Run a sample** button at
<http://localhost:8084>, or:

```powershell
curl.exe -s -X POST -H "Content-Type: application/json" `
  --data-binary '{\"kind\":\"sample\"}' http://localhost:8084/api/trigger
```

Expect, after about 8 seconds: **one** JSON document on the topic, with
`"mechanism":"webhook"`, `"correlation_id"` equal to `values.sample_id`, no `osmo` key, and
`modules_used.osmo` false. On the device page, *Last delivery* reads **delivered** with a 2xx
status and the counter increments.

If the page says delivered and the topic is silent, the filter or the transform rejected it —
`docker logs icc26-ignition 2>&1 | Out-String -Stream | Select-String webhook_event` says which. If the page says
FAILED, it is TLS or the URL, and the *Detail* row names it.

### 6 — the secret (records an answer, does not assert one)

On the device page, change **Shared secret** to `wrong`, save, and run a sample.

Expect: **no MQTT message.** Then read two things and write both down here:

- The device page's *HTTP status* row — **this is the answer to "401 or dropped 200?"** and
  nobody knows it yet.
- `docker logs icc26-ignition --since 1m 2>&1 | Out-String -Stream | Select-String webhook_event` — expect
  `novaflex webhook rejected: missing or wrong X-Webhook-Secret`.

Put the secret back afterwards.

### 7 — QC and a failed run are silent

With the subscriber still attached, press **Run onboard QC**. Expect: nothing on the topic,
and `qc_completed` increments on the page. Then press **Make the next run fail** and **Run a
sample**. Expect: nothing on the topic, *Last sample* outcome `failed`, and *Last delivery*
unchanged.

This is the same rule pattern 3 already follows, and it has to hold on both paths or the two
mechanisms stop being comparable.

### 8 — both mechanisms on one topic

Leave the subscriber attached. Trigger pattern 3 by writing its `ESMScheduleAnalysis` command
bit (Designer, or the same tag write the pattern-3 runbook uses), and trigger pattern 4 from
`:8084`.

Expect: two documents on the same topic, one `"mechanism":"opcua-event"` and one
`"mechanism":"webhook"`, with **the same `values` keys**.

They will carry **different** `sample_id`s unless the two counters were lined up by hand —
that is deviation 2, and it is the sentence to have ready.

### 9 — the failure demo

```powershell
docker stop icc26-ignition
```

Run a sample on `:8084`. Expect: *Last delivery* **FAILED**, *Detail* naming a connection
error, `posts_failed` at 1, and the container log carrying `webhook POST for S-000NN failed`.

```powershell
docker start icc26-ignition
```

Expect: **nothing appears for that sample.** The result is on the instrument and the backbone
never hears about it. That is the line, and then you go to pattern 5.

(`docker stop` rather than `python tasks.py down` — `restart: unless-stopped` keeps a manually
stopped container stopped, and `tasks.py` has no per-service `stop`.)

### 10 — the stage controls

| Do this | Expect |
|---|---|
| Uncheck **Send results** on `:8084` | Only `opcua-event` on the topic |
| Re-check it, disable Event Stream `03_opcua/novaflex-result` | Only `webhook` |
| Both off | Silence |
| Both on | Two documents per pair of triggers |

Disabling either Event Stream needs no UI: set `"enabled": false` in its `config.json` under
`ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/…` and run
`python tasks.py scan`. On stage the toggle you want is the device page — it is instant, it is
a *device* setting rather than a gateway one, and that is the more honest control anyway.

---

## If the Event Stream will not load

The blind-authored resource is the one genuinely risky thing in this pattern. There is a
proven, file-authorable alternative already sitting in the tree, and switching to it is one
step.

**The fallback is WebDev.** `ignition/projects/icc-2026/com.inductiveautomation.webdev/resources/lims/sample-result/`
was hand-authored as plain files — no UI — and was **broker-verified on 2026-08-20** publishing
with `system.cirruslink.transmission.publish("chariot_broker", topic, payload, 1, False)`. Its
`config.json` is a known-good WebDev shape, including the `"resource-type": "python-resource"`
discriminator that everything else 500s without.

To use it:

1. Copy the directory to `.../webdev/resources/novaflex/result/`, new `uuid` in
   `resource.json`, and delete `lastModificationSignature` if one is present.
2. Point `doPost.py` at the module — `handle_webdev` is **already written** in
   `webhook_event`, precisely so this is one step and not an afternoon:
   ```python
   def doPost(request, session):
       return webhook_event.handle_webdev(request)
   ```
   It adapts WebDev's `request` dict onto the same `accept` / `build_novaflex_result` pair,
   then publishes with `system.cirruslink.transmission.publish` — the one call in this chain
   that is already proven. It also answers a real **401** on a bad secret, which the Event
   Stream transform may not be able to do (deviation 4), because WebDev hands you a
   `servletResponse` and the stream does not.
3. `WEBHOOK_NOVAFLEX_URL=https://ignition:8043/system/webdev/icc-2026/novaflex/result`
4. `python tasks.py scan`, then `python tasks.py restart webhook-novaflex`.

What that costs on stage: the "two sources, one pipeline" line gets weaker, because pattern 4
would no longer be an Event Stream. It is still HTTP in and MQTT out with
`mechanism = "webhook"`, which is the actual pattern; the Event Stream was the elegance, not
the substance.

The simplest version of all is to keep `services/lims/` — uncomment it in `docker-compose.yml`
and re-add `lims-bridge` to `mqtt-users.json` (which needs an empty Chariot volume, so a
`nuke`, or a hand-added user in the UI). But that is the *old* pattern 4, and it publishes to
`icc26/site1/qc/lims/sample-result`, which reintroduces the namespace wart. Use it only to get
a demo out the door.
