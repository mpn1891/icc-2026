# 04 — NovaFlex HTTPS webhook

> **BUILT 2026-08-23, and this file is now the *plan*, not the record.** The as-built is
> [`../04-novaflex-webhook.md`](../04-novaflex-webhook.md) — read its deviations table before
> using anything below, because four things went differently:
>
> 1. **The sender is a new container, `services/webhook-novaflex/`**, not an addition to
>    `services/opcua-novaflex/app.py`. Every "Files to change" row naming `opcua-novaflex`
>    landed in the new directory instead; `opcua-novaflex` is untouched.
> 2. **Checkpoint 3 is not achievable as written.** Two containers means two independent
>    sample lifecycles, so one physical sample cannot appear on the topic twice. The id format
>    matches and `SAMPLE_ID_START` lines the counters up by hand; nothing automates it.
> 3. **The Event Stream was authored blind, from files** — no UI step, with the stack down. The
>    HTTP source type, its config keys and the mount URL below are all **guesses**. The
>    as-built lists every one and the runbook settles them in one pass.
> 4. **Nothing has been verified against a running gateway or broker.** The empirical
>    checkpoints below are superseded by the runbook in the as-built, which is written to be
>    executed.
>
> **Supersedes the pattern-4 entry in [`00-master-plan.md`](00-master-plan.md) entirely.**
> Rewritten **2026-08-23.** The LIMS approval webhook
> ([`../extra/lims-webhook-spec.md`](../extra/lims-webhook-spec.md)) is the previous design; it was built and
> broker-verified on 2026-08-20, then dropped from the talk. Do not "fix" this file back
> toward a LIMS.
>
> Talk track (draft, not yet rebuilt): [`../04-novaflex-webhook.md`](../04-novaflex-webhook.md).
>
> **This file is the build spec.** Pattern 3 is already live. This rebuild adds a second vendor
> surface on the same instrument and unwires the LIMS in the same pass.

| | |
|---|---|
| **Pattern** | 4 of 7 — webhook, because that is all the analyzer can emit |
| **Mechanism tag** | `meta.mechanism = "webhook"` |
| **Depends on** | NovaFlex simulator (already live). Does **not** consume pattern 3's MQTT |
| **Blocks** | nothing. Independent of 05/06 |
| **Pairs with** | [`03-opcua-analyzer-playbook.md`](03-opcua-analyzer-playbook.md) — same sample, same topic, `opcua-event` |
| **Nuke?** | not required for the HTTP path. Retiring `lims.*` tables can wait for the 05/06 volume drop |

## Objective

The BioProfile FLEX2 — the same instrument as pattern 3 — can output a completed sample only
by **HTTPS POST** to a URL the lab configured. That URL is Ignition. An Event Stream with an
HTTP source receives the POST and publishes onto the backbone with
`meta.mechanism = "webhook"`.

## Talk point

**A lot of lab instruments do not speak MQTT, OPC UA, or SQL. They POST JSON, and that is the
whole integration surface.** Pattern 3 is what this vendor actually shipped (a licensed OPC UA
server). Pattern 4 is the more common case: a callback URL in a config screen, and nothing else.

The Event Stream is the point of reuse. Pattern 3's tag-change and pattern 4's HTTP POST are
two sources on the same kind of pipeline, both ending at Transmission. The mechanism is not
the transport, and here you can see it — HTTP in, MQTT out, `mechanism=webhook`.

The failure is honest and short: **if Ignition is down, the POST fails.** Unless the analyzer
queued it, the result never reaches the backbone. That is why pattern 5 exists — the same
class of instrument, but it wrote the row locally and CDC can catch up.

## The chain

```
opcua-novaflex ──HTTPS POST──▶ Ignition Event Stream (HTTP source)
                                      │
                                      ▼
                               Transmission  (ign-transmission)
                                      │
            icc26/site1/qc/analyzers/novaflex-01/result   (mechanism: webhook)
```

Pattern 3 is a **parallel** output from the same simulator, same topic, `mechanism=opcua-event`.
`meta.correlation_id` is `sample_id` on both, so one sample is two colours on the firehose.
Running both at once is allowed and is the demonstration that the namespace does not leak
mechanism. For the talk, "imagine this POST is all it has."

The analyzer **does not subscribe to MQTT.** The LIMS cycle hazard is gone with the LIMS.

HTTP source URL, once the Event Stream exists (WebDev module is already in the gateway because
the retired LIMS used it):

```
https://ignition:8043/system/eventstream/icc-2026/04_webhook/novaflex-result
```

Exact path is confirmed in the UI — unknown gateway schemas are UI-first, then commit. If the
mounted path differs, write the real URL into this file as-built.

## What changed from the LIMS design

| | LIMS (built 2026-08-20) | This spec | Why |
|---|---|---|---|
| Source | FastAPI LIMS, human Approve | NovaFlex HTTPS POST on sample complete | The webhook is the instrument's only output, not a person signing |
| Ignition ingest | WebDev + script → Transmission | **Event Stream HTTP source** → Transmission | Same pipeline shape as pattern 3 |
| Topic | `qc/lims/sample-result` (system in the cell slot) | **same topic as pattern 3** | Namespace must not leak mechanism; the `qc/lims/` wart goes away |
| MQTT subscribe | `lims-bridge` on the analyzer topic | **none** | Analyzer is not a backbone consumer; cycle hazard retired |
| Outbox | `lims.webhook_delivery` | not in this pattern | Instrument-side retry is vendor behaviour, not ours to invent. The failed POST *is* the demo |
| Depends on pattern 3 MQTT | yes | **no** | Parallel vendor surface, not a subscriber |

The LIMS container, WebDev resource, outbox table, and `lims-bridge` user stay in the tree
until this spec is built. They are not the talk. Unwire them in the same pass as the HTTP
Event Stream lands.

## Decisions

**Same device, same topic, same envelope shape as pattern 3.** A subscriber reading
`icc26/site1/qc/analyzers/novaflex-01/result` cannot tell whether the document arrived by OPC UA
or by HTTP. Copy the as-built `values` tree from
`ignition/projects/icc-2026/ignition/script-python/opcua_event/code.py` (`build_novaflex_result`).
Do not invent a smaller gluc/lac payload. `source.id = "novaflex-01"`,
`source.type = "analyzer"`. `meta.correlation_id` is `sample_id`, same field as pattern 3.

**The POST body is result-shaped, not our envelope.** The instrument does not know our envelope.
The Event Stream transform wraps it. A null analyte produces no field, not a zero — same
absent-vs-zero rule as pattern 3. Osmo stays absent by default (`OSMO_INSTALLED=false`).

**HTTPS into Ignition.** Compose binds HTTP to loopback, so the simulator POSTs at
`https://ignition:8043/...` with the mounted gateway cert. Copy the LIMS TLS pattern exactly:
verify the signature, `check_hostname = False`, because the restored `ssl.pfx` SAN is
`localhost`, not `ignition`. Do not disable certificate verification.

**Auth is a shared secret header**, not an Ignition user. Event Stream HTTP source authentication
is user-source based and is the wrong flavour for "callback URL in a config screen." Check
`X-Webhook-Secret` in the filter. If a dropped event still returns HTTP 200, record that as
as-built and say it on stage — many real webhook endpoints do the same. If the UI offers a
clean 401 path without standing up a user source, take it.

**POST only on a completed sample.** Same rule as pattern 3: abort, fail, and QC do not POST.
The simulator already distinguishes those paths; hook the POST next to the historical
`SampleTime` write, after the result has settled.

**No outbox on this pattern.** Building another handmade WAL would steal pattern 5's line.

**`lims-bridge` is retired with the rebuild.** Pattern 4 publishes through Transmission as
`ign-transmission`, like 3 and 6.

**Config-page toggle**, so the talk can run OPC-only, HTTP-only, or both. Pattern 3's Event
Stream can be disabled from the gateway; the simulator's POST is switched from the device
page. Env `WEBHOOK_ENABLED` is the factory default; the page can override it.

## Build order

1. Event Stream HTTP source in the UI. Commit whatever `git status` reveals. Confirm the URL
   with `curl` from the host before teaching the simulator to POST.
2. Transform script that wraps the vendor JSON into the pattern-3 envelope with
   `mechanism=webhook`.
3. POST from `opcua-novaflex` on sample complete, TLS as above.
4. Toggle on a small stdlib config page (host port **8084** — 8085/8086 are the valves, 8000
   is the leftover LIMS).
5. Unwire LIMS: compose service, `lims-bridge` user, stop talking about `:8000`. Leave the
   WebDev resource and `lims` tree on disk, like the retired vibration gateway — Extra docs
   still describe them. Dropping `lims.*` tables waits for the 05/06 nuke.
6. Talk track + status + `services/README.md`.

## Files to change

| Path | What |
|---|---|
| `services/opcua-novaflex/app.py` | POST on completed sample; TLS context; secret header |
| `services/opcua-novaflex/webui.py`, `page.html` | **new.** Enable/disable POST, last status, last error. Stdlib `http.server`, inline CSS/JS, no CDN |
| `services/opcua-novaflex/Dockerfile` | copy the page files; `EXPOSE 8080` in addition to 4840 |
| `docker-compose.yml` | webhook env + cert mount + port 8084 on `opcua-novaflex`; comment `lims` out of the talk (leave the service block until Extra is deleted) |
| `ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/04_webhook/novaflex-result/` | **UI first.** HTTP source, filter, transform, Transmission handler |
| `ignition/projects/icc-2026/ignition/script-python/webhook_event/code.py` | **new.** Envelope wrapper, Jython 2.7, copy helpers from `opcua_event` |
| `compose/chariot/mqtt-users.json` | drop `lims-bridge` |
| `compose/chariot/README.md` | drop the leftover row |
| `compose/postgres/initdb/02-schema.sql` | comment `lims.*` as Extra; do not DROP until the 05/06 nuke |

### Simulator POST (sketch)

Hook after the historical tree has been written and `SampleTime` has been deferred-applied —
the same moment pattern 3's tag-change fires. Independent of the OPC UA write: if the POST
fails, the address space is still correct.

```python
# services/opcua-novaflex/app.py — additions, not a rewrite

# Config
self.webhook_enabled = _env_bool("WEBHOOK_ENABLED", True)
self.webhook_url = _env("WEBHOOK_URL", "")
self.webhook_secret = _env("WEBHOOK_SECRET", "icc26-webhook-secret")
self.webhook_ca_file = _env("WEBHOOK_CA_FILE", "/certs/icc26-ignition.crt")

def _ssl_context(self) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if os.path.isfile(self.webhook_ca_file):
        ctx.load_verify_locations(self.webhook_ca_file)
        ctx.check_hostname = False  # SAN is localhost; signature still verified
    return ctx

def _vendor_payload(self, historical: dict) -> dict:
    """What a FLEX2 with only a callback URL would POST. Not our envelope."""
    def g(*keys, default=None):
        for k in keys:
            if historical.get(k) is not None:
                return historical[k]
        return default
    body = {
        "SampleID": g("StartTags/SampleInformation/SampleID"),
        "BatchID": g("StartTags/SampleInformation/BatchID"),
        "VesselID": g("StartTags/SampleInformation/VesselID"),
        "CellType": g("StartTags/SampleInformation/CellType"),
        "SampleTime": _iso(historical.get("SampleTime")),
        "Operator": g("StartTags/Operator"),
        "Gas": {
            "pH": historical.get("Gas/pH/Result"),
            "pCO2": historical.get("Gas/pCO2/Result"),
            "pO2": historical.get("Gas/pO2/Result"),
        },
        "Chem": {
            "Na": historical.get("Chem/Na/Result"),
            "K": historical.get("Chem/K/Result"),
            "Ca": historical.get("Chem/Ca/Result"),
            "NH4": historical.get("Chem/NH4/Result"),
            "Gln": historical.get("Chem/Gln/Result"),
            "Glu": historical.get("Chem/Glu/Result"),
            "Gluc": historical.get("Chem/Gluc/Result"),
            "Lac": historical.get("Chem/Lac/Result"),
        },
        "Osmo": historical.get("Osmo/Result"),  # None when module absent
        "CellDensity": {
            "TotalDensity": historical.get("CellDensity/TotalDensity"),
            "ViableDensity": historical.get("CellDensity/ViableDensity"),
            "Viability": historical.get("CellDensity/Viability"),
            "AvgLiveDiameter": historical.get("CellDensity/AvgLiveDiameter"),
        },
        "Calculated": {
            "HCO3": historical.get("CalculatedResults/HCO3"),
            "O2Saturation": historical.get("CalculatedResults/O2Saturation"),
            "CO2Saturation": historical.get("CalculatedResults/CO2Saturation"),
        },
        "Modules": {
            "CDV": bool(historical.get("StartTags/ModuleInformation/Modules/CDV")),
            "Chemistry": bool(historical.get("StartTags/ModuleInformation/Modules/Chemistry")),
            "Gas": bool(historical.get("StartTags/ModuleInformation/Modules/Gas")),
            "Osmo": bool(historical.get("StartTags/ModuleInformation/Modules/Osmo")),
        },
    }
    # Absent analyte: omit the key. Do not send 0.
    return _drop_nones(body)

def _post_result(self, historical: dict) -> None:
    if not self.webhook_enabled or not self.webhook_url:
        return
    body = json.dumps(self._vendor_payload(historical)).encode("utf-8")
    req = urllib.request.Request(
        self.webhook_url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Secret": self.webhook_secret,
        },
    )
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=self._ssl_context())
    )
    try:
        with opener.open(req, timeout=10) as resp:
            self.last_webhook = {"ok": True, "status": resp.status}
    except Exception as exc:
        # One attempt. The failed POST is the demo. Log and move on.
        LOG.warning("webhook POST failed: %s", exc)
        self.last_webhook = {"ok": False, "error": str(exc)}
```

Call `_post_result(historical)` from the success path only. Do not call it from `_run_qc`,
the abort branch, or the dispense-timeout branch.

### Compose (sketch)

```yaml
  opcua-novaflex:
    # ... existing OPC UA block unchanged ...
    environment:
      # existing vars, plus:
      WEBHOOK_ENABLED: ${NOVAFLEX_WEBHOOK_ENABLED:-true}
      WEBHOOK_URL: ${NOVAFLEX_WEBHOOK_URL:-https://ignition:8043/system/eventstream/icc-2026/04_webhook/novaflex-result}
      WEBHOOK_SECRET: ${NOVAFLEX_WEBHOOK_SECRET:-icc26-webhook-secret}
      WEBHOOK_CA_FILE: /certs/icc26-ignition.crt
    ports:
      - "${OPCUA_NOVAFLEX_PORT:-4841}:4840"
      - "${NOVAFLEX_UI_PORT:-8084}:8080"
    volumes:
      - ./ignition/certificates/icc26-ignition.crt:/certs/icc26-ignition.crt:ro
```

No `depends_on: ignition`. If Ignition is down the POST fails, which is checkpoint 4.

Comment the `lims` service with a pointer to Extra; do not delete the directory in this pass.

### Envelope wrapper (sketch)

Jython 2.7. Copy `_iso` / `_next_seq` from `opcua_event`. Map the vendor POST onto the **same
`values` keys** `build_novaflex_result` already publishes, so a subscriber cannot tell the
documents apart except by `meta.mechanism`.

```python
# ignition/projects/icc-2026/ignition/script-python/webhook_event/code.py

SOURCE_ID = "novaflex-01"
SOURCE_TYPE = "analyzer"
MECHANISM = "webhook"
SECRET = "icc26-webhook-secret"

def build_novaflex_result(event):
    """Wrap a vendor POST body in the pattern-3 envelope. Return None to drop."""
    headers = (event.metadata.headers if hasattr(event, "metadata") else {}) or {}
    secret = _header(headers, "X-Webhook-Secret")
    if secret != SECRET:
        logger.warn("novaflex webhook rejected: missing or wrong secret")
        return None

    body = event.data
    if isinstance(body, (str, unicode)):
        body = system.util.jsonDecode(body)
    if not body or not body.get("SampleID"):
        return None

    sample_time = body.get("SampleTime")
    gas, chem, cd, calc, mods = (
        body.get("Gas") or {}, body.get("Chem") or {},
        body.get("CellDensity") or {}, body.get("Calculated") or {},
        body.get("Modules") or {},
    )
    envelope = {
        "ts": sample_time,
        "seq": _next_seq(),
        "source": {"id": SOURCE_ID, "type": SOURCE_TYPE},
        "meta": {
            "mechanism": MECHANISM,
            "ingest_ts": _iso(),
            "correlation_id": body.get("SampleID"),
        },
        "values": {
            "sample_id": body.get("SampleID"),
            "batch_id": body.get("BatchID"),
            "vessel_id": body.get("VesselID"),
            "cell_type": body.get("CellType"),
            "operator": body.get("Operator"),
            "gas": {"ph": gas.get("pH"), "pco2": gas.get("pCO2"), "po2": gas.get("pO2")},
            "chem": {
                "na": chem.get("Na"), "k": chem.get("K"), "ca": chem.get("Ca"),
                "nh4": chem.get("NH4"), "gln": chem.get("Gln"), "glu": chem.get("Glu"),
                "gluc": chem.get("Gluc"), "lac": chem.get("Lac"),
            },
            "osmo": body.get("Osmo"),
            "cell_density": {
                "total_density": cd.get("TotalDensity"),
                "viable_density": cd.get("ViableDensity"),
                "viability_percent": cd.get("Viability"),
                "avg_live_diameter_um": cd.get("AvgLiveDiameter"),
            },
            "calculated": {
                "hco3": calc.get("HCO3"),
                "o2_saturation": calc.get("O2Saturation"),
                "co2_saturation": calc.get("CO2Saturation"),
            },
            "modules_used": {
                "cdv": mods.get("CDV"), "chemistry": mods.get("Chemistry"),
                "gas": mods.get("Gas"), "osmo": mods.get("Osmo"),
            },
        },
    }
    return system.util.jsonEncode(_drop_nones(envelope))
```

`event.data` / `event.metadata` shapes are unknown until the HTTP source exists. Adjust to
whatever the encoder actually hands you; that is the first empirical checkpoint.

## Ignition resources

Unknown schemas: **UI first**, then commit whatever `git status` reveals. Known: the script
module above, authored as files.

| Resource | How |
|---|---|
| Event Stream `04_webhook/novaflex-result` | Gateway UI → Event Streams. HTTP source, Require HTTPS **on**. Encoder JSON. Filter: secret present (and `SampleID` present). Transform: `return webhook_event.build_novaflex_result(event)`. Handler: MQTT Transmission, server `chariot_broker`, topic `icc26/site1/qc/analyzers/novaflex-01/result`, QoS 1, not retained. Copy the handler block from `03_opcua/novaflex-result/config.json` |
| Script `webhook_event` | files, then `python tasks.py scan` |
| WebDev `lims/sample-result` | leave on disk, unwired. Do not delete in this pass |
| `lims_webhook` script | leave on disk |

On-disk Event Stream path once the UI has written it:

```
ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/04_webhook/novaflex-result/config.json
```

Transmission server name is `chariot_broker` — already proven on pattern 3. Do not invent a
second MQTT server.

## MQTT user + topics

No new MQTT user. Publish is `ign-transmission` on

```
icc26/site1/qc/analyzers/novaflex-01/result
```

Drop `lims-bridge` from `compose/chariot/mqtt-users.json`. That file seeds **on first run
only**; against a running Chariot, delete the user in the UI at `:8081` as well, or nuke.

## Envelope

One message per completed sample. `ts` is the vendor `SampleTime`. `meta.ingest_ts` is when
Ignition received the POST. `values` matches pattern 3. Absent osmo is omitted, not `0`.

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
    "sample_id": "S-00014",
    "batch_id": "B-2026-0142",
    "gluc": 4.21,
    "lac": 1.08
  }
}
```

The gluc/lac excerpt is for reading; the on-wire document carries the full `gas` / `chem` /
`cell_density` / `calculated` / `modules_used` tree from the sketch above.

## Empirical checkpoints

**1 — HTTP source answers.** From the host, with the gateway cert:

```
curl -sk -o /dev/null -w "%{http_code}" ^
  --data-binary "{\"SampleID\":\"probe\"}" ^
  -H "Content-Type: application/json" ^
  -H "X-Webhook-Secret: icc26-webhook-secret" ^
  https://localhost:8043/system/eventstream/icc-2026/04_webhook/novaflex-result
```

Not 404. Record the real path if it differs.

**2 — Transform sees a dict.** One POST, gateway logs, no MQTT yet if you have not attached
the handler. Confirm `event.data` is already parsed JSON (or a string — then jsonDecode).

**3 — Happy path, both mechanisms.** Trigger `ESMScheduleAnalysis` with the webhook enabled
and pattern 3's Event Stream still on. Two messages on the result topic, same
`correlation_id`, mechanisms `opcua-event` and `webhook`. `values.sample_id` equal.

**4 — Secret.** Wrong or missing `X-Webhook-Secret` → no MQTT message. Record whether the
HTTP status is 401 or 200-dropped.

**5 — QC and abort do not POST.** `ChemistryQcLevel1` and `ESMTerminate` mid-run: pattern 3
already stays quiet; pattern 4 must too.

**6 — Failure demo.** `python tasks.py stop ignition` (or disable the Event Stream). Complete
a sample. Simulator log shows the POST failed. Start Ignition. **Nothing** appears for that
sample under `mechanism=webhook`. Say the line, then go to pattern 5.

**7 — OPC-only / HTTP-only.** Toggle the device page off: only `opcua-event`. Disable Event
Stream `03_opcua/novaflex-result`: only `webhook`. Both off: silence. That is the talk
control.

## Verification (copy-paste)

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/qc/analyzers/novaflex-01/result' -v
```

Trigger one ESM sample. Expect two JSON documents, same `values.sample_id`, different
`meta.mechanism`.

## Closing step

Rewrite [`../04-novaflex-webhook.md`](../04-novaflex-webhook.md) from this draft into the
as-built talk track. Then update `00-status.md`, `services/README.md`, and
`../00-architecture.md` for what actually shipped (the HTTP path, the dropped `lims-bridge`
user, the real Event Stream URL). Add a deviations table if the HTTP source could not return
401, if the URL path differed, or if the POST had to go through WebDev after all.
