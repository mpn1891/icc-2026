# webhook-novaflex — pattern 4, HTTPS webhook

The **same** Nova Biomedical BioProfile FLEX2 as pattern 3, imagined with the integration
surface most of the room has actually met: a callback URL in a config screen, and nothing
else. No browse, no subscribe, no query, no queue. One HTTPS POST per completed sample.

Its twin is [`../opcua-novaflex/`](../opcua-novaflex/), the same instrument as Nova really
ships it — a licensed OPC UA server with ~400 tags. Both land on the same topic. Only
`meta.mechanism` differs.

Talk track: [`docs/04-novaflex-webhook.md`](../../docs/04-novaflex-webhook.md).
Build spec: [`docs/plans/04-novaflex-webhook.md`](../../docs/plans/04-novaflex-webhook.md).

> **Not verified against a running stack.** Everything below was written and exercised with
> the stack down. The sender was run locally against a stub HTTP receiver — the POST, the
> header, the omitted-osmo body, the QC-is-silent rule, the failure path and the page's own
> API all behave — but nothing here has met the Ignition gateway or Chariot. The receiving
> Event Stream in particular was authored blind. See *Unproven* at the bottom.

## The configuration page

<http://localhost:8084> — the instrument's own embedded page. Three controls:

| Field | |
|---|---|
| **Callback URL** | free text |
| **Shared secret** | sent as `X-Webhook-Secret` |
| **Send results** | on / off |

That is the entire contract with the outside world. Put it next to <http://localhost:8085>
(pattern 1: topic, QoS, retained) and <http://localhost:8086> (pattern 2: three names inside
a namespace somebody else fixed) and read all three side by side — the shrinking of that
list, pattern by pattern, is as much of the talk as the traffic is.

The page also shows the last delivery (outcome, HTTP status, detail), the last sample, and
**the exact vendor body that was posted**, pretty-printed. That last panel is the one to have
on screen when you say "this is not our envelope."

Three simulator controls, which are not part of the device: run a sample, run an onboard QC
(completes, POSTs nothing), and make the next run fail with a dispense timeout (POSTs
nothing).

**The page's overrides are not persisted.** A restart returns the instrument to its `.env`
factory settings. This is the opposite of the valves, whose commissioned topic survives in a
named volume, and the difference is deliberate: a commissioned topic is a device setting, and
an enable switch you flip mid-talk is a stage control.

## What it sends

One POST per **completed sample**. Nothing else produces a request:

| Event | POST? |
|---|---|
| sample completes | yes — the vendor result body |
| onboard QC completes | **no.** QC is excluded from the sample stream, exactly as pattern 3 is silent on it |
| dispense timeout / failed run | **no.** There is no result |
| callback disabled on the page | no |

```
POST https://ignition:8043/system/eventstream/icc-2026/04_webhook/novaflex-result
Content-Type: application/json
X-Webhook-Secret: icc26-webhook-secret
```

The body is **the vendor's shape, not our envelope** — no `meta`, no `seq`, no topic. The
instrument does not know those things exist, and pretending otherwise would be the single
most dishonest thing this simulator could do. Wrapping it is Ignition's job
(`webhook_event.build_novaflex_result`), and that split is the pattern.

```json
{
  "SampleID": "S-00014",
  "BatchID": "BR-2026-014",
  "VesselID": "BRX-2000-A",
  "CellType": "CHO-K1",
  "SampleTime": "2026-08-23T14:03:22.145Z",
  "SampleSource": "ESM",
  "Operator": "Auto",
  "Gas": { "pH": 7.11, "pCO2": 52.0, "pO2": 110.4 },
  "Chem": { "Na": 148.2, "K": 4.9, "Ca": 1.11, "NH4": 2.1,
            "Gln": 2.9, "Glu": 1.2, "Gluc": 4.21, "Lac": 1.08 },
  "CellDensity": { "TotalDensity": 6.4, "ViableDensity": 6.1,
                   "Viability": 95.3, "AvgLiveDiameter": 15.8 },
  "Calculated": { "HCO3": 18.1, "O2Saturation": 97.4, "CO2Saturation": 91.9 },
  "Modules": { "CDV": true, "Chemistry": true, "Gas": true, "Osmo": false }
}
```

**Note what is missing.** There is no `Osmo` key at all, because the osmometer is not fitted.
Beside it, `Modules.Osmo` is present and `false`. Absent and "deliberately not measured" are
different statements, and both are on the wire. Nothing is ever sent as `0` to mean "did not
measure" — `_drop_nones()` in `app.py` enforces that once, at the edge, instead of it having
to be remembered at every assignment.

## One attempt. No outbox.

If the POST fails it is logged, counted on the page, and forgotten. The sample is gone from
the backbone's point of view.

That is deliberate and it is the pattern's honest ending: **an instrument that can only POST
gives you no second chance, and most of them do not queue.** The durable version of the same
problem is pattern 5 — the instrument wrote a row in a database you do not own, and you tail
the WAL. Building a retry queue in this container would be building pattern 5 in the wrong
place, and it would steal pattern 5's line.

## TLS

The gateway certificate is mounted read-only at `/certs/icc26-ignition.crt` and loaded as an
**additional trust anchor** on a context that still has the system store. `check_hostname` is
`False` and only that: `seed` restores the machine-local `ssl.pfx`, whose SAN is `localhost`,
not `ignition`, which is the name this container has to dial over the compose network. The
signature is still verified. **Verification is never disabled**, and the same concession is
what `services/lims/` made.

`ignition/certificates/icc26-ignition.crt` is generated by `python tasks.py seed` (or
`enable-ssl`) and is gitignored. **If the file does not exist when compose starts, Docker
creates a directory at that path** and the mount is useless — the container logs
`WEBHOOK_CA_FILE ... is missing` at startup and the page's *Gateway certificate* row turns
red. Run `seed` before `up` on a fresh clone.

## Configuration

Everything is an environment variable; see `.env.example` under *Pattern 4*. The ones that
matter:

| Variable | Default | |
|---|---|---|
| `WEBHOOK_ENABLED` | `true` | factory default for the switch on the page |
| `WEBHOOK_URL` | the Event Stream mount | **the path is a guess** until confirmed on a live gateway |
| `WEBHOOK_SECRET` | `icc26-webhook-secret` | must match `SECRET` in the `webhook_event` script module |
| `SAMPLE_ID_PREFIX` / `SAMPLE_ID_START` | `S-` / `1` | how you line this instrument's ids up with `opcua-novaflex`'s |
| `OSMO_INSTALLED` | `false` | keeps absent-vs-zero live |
| `QC_EVERY_N` | `6` | free-running cycles that run a QC instead, which POST nothing |
| `SAMPLE_INTERVAL_S` | `120` | free-running cycle |

## Why this is a separate container

`opcua-novaflex` is untouched by this pattern. The build spec sketched the POST as an addition
to that file; a separate service was chosen instead, and the trade is worth stating because it
is visible on stage:

- **Bought:** pattern 4 is a self-contained instrument. Stopping it, restarting it, toggling
  it or misconfiguring its secret cannot disturb pattern 3, which is live and broker-verified.
  Two containers is also the truer picture — a webhook-only instrument and an OPC UA
  instrument are two different products, not one product with a checkbox.
- **Paid:** two independent sample lifecycles, so **one physical sample cannot appear on the
  topic twice**. `meta.correlation_id` still carries `sample_id` on both paths and the id
  format is identical, but making a specific id appear under both mechanisms is a manual act —
  set `SAMPLE_ID_START` on one of them, or restart one, to line the counters up.

House style is duplication over a shared library — the same convention that makes
`sim-valve-mqtt` and `sim-valve-spb` comparable. `_culture()` and the result synthesis in
`app.py` are **copied** from `../opcua-novaflex/app.py`, not imported. Diff them when either
changes. Only what the vendor body carries is reproduced; the OPC-only material (ranges, flow
times, consumables, RSM status, the QC tag tree, the 911-node address space) has no analogue
in an HTTP callback and is deliberately absent.

`webui.py` is **not** byte-identical to the valves' copy, and should not be made so. Those two
share a file because they are the same physical device in two firmwares, so any difference
between their pages is evidence about the protocol. This is a different device.

## Dependencies

**None.** Not even `paho`. `urllib`, `ssl` and `http.server` are all standard library, so
there is no `requirements.txt` and no `pip install` layer in the Dockerfile — the only service
in the tree that can say that outright, which is itself a small argument about how little an
HTTP integration costs.

## Unproven

Written with the stack down. What has *not* been checked:

- **The Event Stream HTTP source URL.** `/system/eventstream/icc-2026/04_webhook/novaflex-result`
  came from the spec and is unconfirmed. The stream's own config panel prints the real one.
- **Whether the receiver returns 401 or a dropped 200** on a bad secret. The sender records
  and displays whichever it gets; the page's *HTTP status* row is the answer.
- **The TLS handshake against the real gateway certificate.** The context construction matches
  `services/lims/`, which was broker-verified, but this container has never made the call.
- **Anything on the Ignition side at all** — see the deviations table in
  [`docs/04-novaflex-webhook.md`](../../docs/04-novaflex-webhook.md).

The runbook in that document is the one pass that settles all of the above.
