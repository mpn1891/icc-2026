# sim-valve-mqtt — pattern 1, native MQTT

A smart sample valve assembly on the sample port of `BR-201`: a sanitary diaphragm valve, an
RFID badge reader, and position feedback. Badge in, the valve strokes open for a sampling
window, the valve closes. Every scan is published — granted or denied.

Its twin is [`../sim-valve-spb/`](../sim-valve-spb/), the identical assembly with Sparkplug B
firmware. `valve.py` and `webui.py` are byte-for-byte the same file in both directories, so
everything that differs between the two containers is a difference the protocol caused.

Full contract and reasoning: [`docs/plans/01-native-mqtt.md`](../../docs/plans/01-native-mqtt.md).

## The configuration page

<http://localhost:8085> — the device's own embedded commissioning UI, which is the point of
this service as much as the MQTT traffic is. Three fields:

| Field | |
|---|---|
| **Topic** | free text, unvalidated against any namespace |
| **QoS** | 0, 1 or 2 |
| **Retained** | on or off |

That is the entire contract with the outside world, and all three are somebody's opinion.
Put it next to <http://localhost:8086> and read the two pages side by side.

The same three settings apply to **all** message types, which is the flaw worth pointing at:
a badge scan is an audit record that must not be lost, and a line temperature is disposable,
and this page cannot tell them apart.

## Topics

Derived from whatever is in the Topic field. Shipped default:

| Topic | Dir | Default QoS | Default retained |
|---|---|---|---|
| `icc26/site1/upstream/br-201/sample-valve-01/event` | pub | 1 | yes |
| `icc26/site1/upstream/br-201/sample-valve-01/state` | pub | 1 | yes |
| `icc26/site1/upstream/br-201/sample-valve-01/telemetry` | pub | 1 | yes |

`state` also carries the **Last Will**, with `values.state = "offline"`.

Nothing is subscribed. The valve takes no commands: authorization is decided against the
local roster, because a sample port that stops working when the broker does is not one
anybody would install.

## Payloads

The envelope from [`docs/00-architecture.md`](../../docs/00-architecture.md), with
`meta.mechanism = "native-mqtt"` and `meta.event` naming what happened.

```json
{
  "ts": "2026-08-17T18:22:04.512Z",
  "seq": 41,
  "source": { "id": "sample-valve-01", "type": "sample-valve" },
  "meta": { "mechanism": "native-mqtt", "ingest_ts": "2026-08-17T18:22:04.512Z",
            "event": "badge-scan", "cell": "br-201",
            "assembly_serial": "SV-2000-0417" },
  "values": {
    "badge_id": "B-2087", "badge_holder": "Sam Okafor", "badge_role": "maintenance",
    "result": "denied", "deny_reason": "badge-not-authorized",
    "valve_state": "locked", "sample_id": null
  }
}
```

Events are `badge-scan` and `sample-complete`. Deny reasons: `badge-unknown`,
`badge-not-authorized`, `training-expired`, `interlock-open`, `valve-busy` — checked in that
order, so who you are is decided before what the valve happens to be doing.

## Badge roster

`BADGE_ROSTER` is `id:holder:role:status` entries, comma separated. The default carries one
authorized analyst, one badge refused for its role, and one whose training lapsed:

| Badge | Holder | Role | Status |
|---|---|---|---|
| `B-1042` | Jordan Reyes | qc-analyst | authorized |
| `B-2087` | Sam Okafor | maintenance | not-authorized |
| `B-3311` | Alex Chen | qc-analyst | training-expired |
| `B-9999` | — | — | not on the roster at all |

## Two things that look like bugs and are not

**The Last Will's timestamp is wrong.** It is the time the session *connected*, because a
will is registered in the CONNECT packet — before the death it describes. Nothing can be done
about that from inside a hand-rolled protocol, and it is one of the things the Sparkplug
variant answers.

**A retained message outlives the config that produced it.** Change the topic on the page and
the old retained state sits at the old topic until something clears it. The page says so when
it happens.

## Environment

| Variable | Default | |
|---|---|---|
| `BROKER_HOST` / `BROKER_PORT` | `chariot` / `1883` | |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | `sample-valve-01` | see `compose/chariot/mqtt-users.json` |
| `BASE_TOPIC` | `icc26/site1/upstream/br-201/sample-valve-01` | factory default; the page overrides it |
| `PUBLISH_QOS` / `PUBLISH_RETAIN` | `1` / `true` | ditto |
| `DEVICE_ID` / `CELL` | `sample-valve-01` / `br-201` | |
| `BADGE_ROSTER` | see above | |
| `SAMPLE_WINDOW_S` / `VALVE_STROKE_S` | `12` / `1.5` | |
| `TELEMETRY_INTERVAL_S` | `5` | |
| `SCAN_INTERVAL_S` | `90` | free-running background scans; **`0` disables them** for a scripted stage run |
| `UI_PORT` | `8080` | container-side; compose maps it to 8085 |
| `CONFIG_PATH` | `/data/config.json` | commissioned settings, on a named volume |

## Verify

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

Press a badge button on the page. One `event` message per scan; on a granted scan the `state`
topic moves `locked → unlocking → open → closing → locked` and a `sample-complete` event
follows.

`docker stop icc26-sim-valve-mqtt` disconnects cleanly, so the will is discarded and never
fires. `docker kill icc26-sim-valve-mqtt` is what proves it works.
