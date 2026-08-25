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
a badge scan is an audit record that must not be lost, an enclosure temperature is
disposable, and this page cannot tell them apart.

## Topics

Derived from whatever is in the Topic field. Shipped default:

| Topic | Dir | Default QoS | Default retained |
|---|---|---|---|
| `icc26/site1/upstream/br-201/sample-valve-01/event/badge-scan` | pub | 1 | yes |
| `icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete` | pub | 1 | yes |
| `icc26/site1/upstream/br-201/sample-valve-01/status` | pub | 1 | yes |
| `icc26/site1/upstream/br-201/sample-valve-01/telemetry` | pub | 1 | yes |

`event/<subtype>` is a two-token message type, the same shape as `cmd/<verb>`;
`icc26/+/+/+/+/event/#` still catches both, because `#` matches zero levels. They are two
topics because the two documents carry different field sets, and one `event/values/` folder
would hold the union of two schemas with half the tags stale.

`status` is the **birth/will pair** and nothing else: the device publishes
`values.state = "online"` as its first message after CONNACK, and the broker publishes
`values.state = "offline"` from the Last Will. Both retained — untick Retained and the pair is
worth nothing, because a subscriber that arrives tomorrow is told neither.

**Valve position is on no topic at all.** The `state` topic was cut on 2026-08-25; the four
states exist only on the config page, and outside the box a sample is two events fifteen
seconds apart with silence in between.

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
    "scan_time": "2026-08-17T18:22:04.512Z", "sample_id": null
  }
}
```

`event/badge-scan` carries `badge_id`, `badge_holder`, `badge_role`, `result`, `deny_reason`,
`scan_time` and `sample_id` — the last one `null` on a denial, because a denial belongs to no
sample. `event/sample-complete` carries `sample_id`, `badge_id`, `badge_holder`,
`sample_start`, `sample_completion`, `open_duration_s`, `cycle_result` and `cycle_count`.

Deny reasons: `badge-unknown`, `badge-not-authorized`, `valve-busy` — checked in that order,
so who you are is decided before what the valve happens to be doing. There is nothing else
that can refuse a sample: authorization is the roster and only the roster.

`cycle_result` is `normal`, `failed-to-seat` or `stroke-timeout`, and the cause is physical.
The actuator is pneumatic, so a supply that has sagged below 4.5 bar is a valve whose position
feedback does not come back to 0 % on close (`failed-to-seat`), and below 2.5 bar it cannot
finish the opening stroke at all (`stroke-timeout`). `telemetry.air_supply_bar` is the reading
that says so, minutes in advance, on a topic with no relationship to the event.

## Badge roster

`BADGE_ROSTER` is `id:holder:role:status` entries, comma separated. Two ship — one authorized
analyst and one badge refused for its role. The third denial needs no roster entry:

| Badge | Holder | Role | Status |
|---|---|---|---|
| `B-1042` | Jordan Reyes | qc-analyst | authorized |
| `B-2087` | Sam Okafor | maintenance | not-authorized |
| `B-9999` | — | — | not on the roster at all |

`valve-busy` is `B-1042` pressed twice in quick succession.

## Two things that look like bugs and are not

**The Last Will's timestamp is wrong.** It is the time the session *connected*, because a
will is registered in the CONNECT packet — before the death it describes. Nothing can be done
about that from inside a hand-rolled protocol, and it is one of the things the Sparkplug
variant answers.

**A retained message outlives the config that produced it.** Change the topic on the page and
the old retained documents sit at the old topics until something clears them — including a
`status` that says `online` and will never be corrected. The page says so when it happens.

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
| `AIR_SUPPLY_BAR` | `5.5` | nominal actuator supply |
| `AIR_SUPPLY_SAG_BAR` | `3.2` | what the page's sag button drops it to; between the seat threshold (4.5) and the stroke threshold (2.5), so it produces `failed-to-seat`. Below 2.5 the same button produces `stroke-timeout` |
| `ENCLOSURE_TEMPERATURE_C` | `31.5` | |
| `SCAN_INTERVAL_S` | `90` | free-running background scans; **`0` disables them** for a scripted stage run |
| `UI_PORT` | `8080` | container-side; compose maps it to 8085 |
| `CONFIG_PATH` | `/data/config.json` | commissioned settings, on a named volume |

## Verify

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

Press a badge button on the page. One `event/badge-scan` per scan; on a granted scan an
`event/sample-complete` follows about fifteen seconds later, and **nothing lands in between** —
the page walks `unlocking → open → closing → locked` while the wire stays silent.

Sag the air supply, then press `B-1042`: the scan is granted and the sample completes
`failed-to-seat`. Restore the supply and the next one is `normal`.

`docker kill icc26-sim-valve-mqtt` fires the will: `status` goes to `offline`, published by
the broker, with a `ts` that is when the session *connected*. `docker stop` disconnects
cleanly, so the broker discards the will — and `offline` lands anyway, published by the device
on its way out with the same frozen timestamp. Only `kill` proves the will works; both prove
the timestamp is wrong.
