# 01 — Native MQTT: wireless vibration gateway

> **Living document.** Updated as the pattern is built — see the [progress log](#progress-log) at
> the bottom. This file supersedes spec 01 in [`00-master-plan.md`](00-master-plan.md), which was
> written before the UDT modelling started; most of its assumptions have been replaced (see
> [Deviations](#deviations-from-the-earlier-docs)).

| | |
|---|---|
| **Pattern** | 1 of 7 — native MQTT pub/sub, fleet-addressed command, bulk response |
| **Mechanism tag** | `meta.mechanism = "native-mqtt"` |
| **New container** | **none** — the gateway is simulated inside Ignition |
| **Depends on** | nothing (Wave 1) |
| **Blocks** | nothing |

## Objective and talk point

A wireless vibration gateway is the most *honestly primitive* thing on the backbone: raw MQTT
3.1.1, a vendor JSON command blob rather than anything standardised, and a bulk response that
does not fit the shape of a tag. Two things it puts on stage:

**1. Fleet-addressed commands break a clean namespace.** Every other topic in this demo is
device-addressed. The vibration gateways are configured the way the real hardware is — every
gateway on site subscribes to *one shared control topic* and self-selects on the `gwSerial` in
the payload, then resolves the sensor from `channelIndex`. The device address moves out of the
topic and into the body. That a vendor's command protocol can force this on an otherwise
disciplined namespace is worth showing rather than hiding.

**2. The domain decides what "response" means.** There is no correlation id here. The PM system
does not care whether a waveform came from its request, a retry, or somebody else's — it needs
*a* waveform and the timestamp it was captured at, taken while the machine was in steady state.
Ignition enforces the steady-state precondition on the way out (`collect_trigger` is
`steady_state && collect_request`); the response carries the capture time on the way back. The
honest moment is not "look how we rebuilt request/response over 3.1.1" — it is "we checked
whether we needed to."

**Two ingest surfaces, chosen deliberately.** Telemetry is a value stream and belongs in tags,
so it arrives through an MQTT Engine custom namespace. A waveform is a document that arrives on
request, so it goes through an 8.3 event stream instead. Showing both — with the reason for
picking each — is a better five minutes than showing either alone.

### What this pattern deliberately does *not* do

**No birth/death, no Last Will.** A will is registered in the MQTT CONNECT packet, so only the
client that owns the session can set one; every publish API is "send this message now" on a
session that already exists. The simulated gateway runs inside Ignition, on Ignition's session,
so it has no will available to it. Rather than fake a lifecycle with startup/shutdown scripts
that only cover the graceful case, the pattern simply doesn't claim one — it is a live running
stream. Pattern 2's edge node *does* get a real will (Transmission owns that connection and
registers its NDEATH there), so the LWT still reaches the stage; it just isn't pattern 1's job.

---

## Physical model

One wireless gateway, serial `12345678`, in the USP suite, wired to four accelerometer
channels. **Only channel 0 is provisioned** — the agitator drive-end bearing on `BR-201`.
Channels 1–3 are physically present and unpopulated; a collect aimed at them is rejected to
the gateway log with nothing published, which is the pattern's negative path.

```
   UDT collect_request ──► collect_trigger  (expr: steady_state && collect_request)
            │
            │ transmission.publish  (plain MQTT, RPC into the module)
            ▼
   icc26/site1/upstream/vibration-gw/cmd/collect ──► Chariot ──┐
            (fleet topic: every gateway subscribes,            │
             each ignores payloads whose gwSerial              │
             isn't its own)                                    │
                                                               ▼
                                              Event Stream "vibration-gw-control"
                                                               │
                                              transform: build_collect_response()
                                                               │
                                              MQTT handler (Transmission)
                                                               │  waveform only — no ack
                                                               ▼
                       icc26/site1/upstream/vibration-gw/response/waveform ──► Chariot
                                                               │
                        ┌──────────────────────────────────────┴────────────┐
                        ▼                                                   ▼
          Engine custom namespace                             Event Stream
          (telemetry → pm-sensors tags,                       "pm-sensor-listener"
           per-cell topics)                                            │
                                                          vibsim.route_waveform()
                                                                       │
                                              SENSOR_TAGS[(gwSerial, channelIndex)]
                                                                       ▼
                                    [default]icc26/site1/upstream/bioreactors/br-201/
                                             asset_data/agitator_vibration/waveform/*
```

**The demux is the point.** Every gateway's waveforms land on one flat response topic, and
`route_waveform` turns `(gwSerial, channelIndex)` back into one sensor's tags. Adding a sensor
is a row in `SENSOR_TAGS`, not a new subscription — the topic tree stays flat while the tag
tree stays ISA-95.

Ignition is both the simulated device and the consumer, and the traffic makes a genuine round
trip through Chariot. That is a real cost of not shipping a separate client — noted in
[Deviations](#deviations-from-the-earlier-docs) — and it is why `services/sim-vibration/` was
kept on disk rather than deleted.

---

## Topic contract

Namespace rule from [`../00-architecture.md`](../00-architecture.md):
`icc26/{site}/{area}/{line-or-cell}/{device}/{message_type}`.

| Topic | Dir | QoS | Purpose |
|---|---|---|---|
| `icc26/site1/upstream/vibration-gw/cmd/collect` | in | 1 | Fleet-addressed collect command |
| `icc26/site1/upstream/vibration-gw/response/waveform` | out | 1 | Time waveform, on command |
| `icc26/site1/upstream/{cell}/{device}/telemetry` | out | 0 | RMS, peak, temp — every 5 s |

Nothing is retained. **There is no `ack` and no `state`** — the gateway publishes nothing about
itself, so a collect is answered by its waveform or by nothing at all, and a rejection is
visible only in the gateway log. See talk point 1.

Only the telemetry topic is device-addressed, and its `{cell}` comes from the channel map, not
from the gateway: one Erbessd gateway is a radio concentrator serving several skids, so
channel 0 can publish under `br-201` while channel 1 publishes under another cell entirely.

### The deliberate namespace exception — a pair, not one topic

**`vibration-gw` occupies the line-or-cell slot for both the command and its response.** It is
the class of gateways serving the area, not a place.

- On `cmd/collect` the device slot is elided: a broadcast has no single addressee, and the
  device is resolved in-payload from `gwSerial` + `channelIndex`.
- On `response/waveform` the device slot is elided for the mirror-image reason: one topic
  carries every gateway's responses, and `route_waveform` demuxes them to tags.

The `cmd/<verb>` message type is preserved. These are the only two topics in the demo that are
not device-addressed, and both are deliberate — see talk point 1.

---

## Payload contracts

The inbound collect command and its waveform response use the **vendor's own shape** — no
standard envelope. Telemetry (periodic) still uses the envelope from
[`../00-architecture.md § Payload envelope`](../00-architecture.md).

### Inbound — collect command

Published by the UDT's `collect_trigger` script (or `mosquitto_pub`). The vendor's own shape,
byte-for-byte, with **no envelope and no correlation id** — a real gateway rejects anything else.

```json
{
  "type": "observerrequest",
  "channelIndex": 0,
  "action": "wiredcollectnow",
  "settings": { "lor": 4096, "sendto": "cloud" },
  "gwSerial": "12345678"
}
```

Handling, in order. Failures **log and publish nothing** — there is no `ack` topic; all
rejection reasons look identical on the wire.

| Check | Failure (log only) |
|---|---|
| `gwSerial` == own serial | **silently ignored** — not this gateway's command |
| `type == "observerrequest"` | `malformed-request` |
| `action == "wiredcollectnow"` | `unsupported-action` |
| `channelIndex` in 0–3 | `unknown-channel` |
| channel provisioned (ch0 only) | `not-provisioned` |
| `settings.lor` power of two, 512 ≤ lor ≤ `MAX_LOR` | `invalid-lor` |

`gwSerial` **must be a JSON string.** The `gw_serial` UDT tag is explicitly typed `String`
because an undeclared Ignition tag defaults to numeric and publishes the serial unquoted, which
a real gateway drops on the floor. That comment already exists on the tag — keep it.

### Outbound — waveform (`wiredCollection`)

Same honesty as the command: vendor JSON on the fleet response topic, demuxed in
`route_waveform` on `(gwSerial, wiredChannel)`.

```json
{
  "datatype": "wiredCollection",
  "wiredChannel": 0,
  "gwSerial": "12345678",
  "timestamp": "2026-08-10T18:22:04.512Z",
  "sensorType": "accel",
  "sampleRate": 32000,
  "data": [0.0123, -0.0456]
}
```

| Field | Meaning |
|---|---|
| `wiredChannel` / `gwSerial` | Echo of the request — demux keys for `SENSOR_TAGS` |
| `timestamp` | Capture **start** (ISO-8601 UTC) — what the PM system keys on; no correlation id |
| `sampleRate` | Hz; fixed at 32000 for this gateway |
| `data` | Acceleration samples in g; length = requested `settings.lor` |

**Size.** ~8.5 bytes per sample once rounded to 5 dp — roughly **34 KB at `lor=4096`**,
scales with `lor`. `MAX_LOR` is 65536 as a guard; the UDT default is 4096.

### Outbound — telemetry (every 5 s)

```json
{
  "ts": "2026-08-10T18:22:00.004Z",
  "seq": 1041,
  "source": { "id": "agitator-vib", "type": "vibration-sensor" },
  "meta": {
    "mechanism": "native-mqtt",
    "ingest_ts": "2026-08-10T18:22:00.004Z",
    "gw_serial": "12345678",
    "channel_index": 0
  },
  "values": {
    "rms_velocity_mm_s": 2.99,
    "peak_accel_g": 1.32,
    "crest_factor": 5.62,
    "temperature_c": 47.4,
    "shaft_rpm": 1780.0
  }
}
```

Telemetry is **computed from the same synthesis model as the waveform** — a short block is
synthesised each tick and reduced to statistics — so when the simulated defect grows,
`rms_velocity_mm_s` climbs *and* the next waveform shows deeper impacts. One story, not two.

---

## Waveform synthesis

Acceleration in **g**, one model driving both waveform and telemetry.

```
f_shaft = shaft_rpm / 60                    # 1780 rpm → 29.67 Hz
BPFO    = 3.585 × f_shaft                   # 8-ball deep-groove, outer race → 106.4 Hz
f_res   = 1500 Hz                           # housing resonance rung by each impact
ζ       = 0.05                              # damping ratio

a(t) = A1·sin(2π·f_shaft·t + φ1)            # imbalance, 1×
     + A2·sin(2π·2f_shaft·t + φ2)           # misalignment, 2×
     + A3·sin(2π·3f_shaft·t + φ3)           # 3×
     + S · Σₖ g(t − tₖ)·(1 + m·sin(2π·f_shaft·tₖ))   # BPFO impulse train, shaft-modulated
     + 𝒩(0, σ²)                             # broadband noise

g(τ)    = exp(−ζ·2π·f_res·τ)·sin(2π·f_res·τ)   for τ ≥ 0, truncated at 5 time constants
tₖ      = k/BPFO · (1 + jitter),  jitter ~ U(−0.01, 0.01)   # rolling-element slip
```

`S` — **defect severity** — ramps from 0.05 to 0.9 over `DEFECT_RAMP_S` (default 30 min), then
holds, so a gateway left running through a rehearsal shows a visibly worse bearing than one just
started. Set `DEFECT_RAMP_S = 0` to pin it for a reproducible stage run.

The impulse train modulated at shaft frequency is the textbook outer-race signature: an FFT
shows a BPFO peak with ±1× shaft sidebands, and the housing resonance makes the envelope
spectrum look like something a vibration analyst would recognise. Slip jitter keeps it from
looking synthetic.

**Why 32000 Hz and 1500 Hz.** Vendor `sampleRate` is 32000. At `lor=4096` that is a 0.128 s
record. The housing resonance at 1500 Hz stays well below Nyquist (~21 samples/cycle), so
impacts still look like ringdowns rather than Nyquist garbage.

Velocity RMS is derived, not assumed: g → mm/s², cumulative-trapezoid integrate, remove the
linear drift the integration introduces, then RMS. That is what a real analyser does, and it
means `rms_velocity_mm_s` is consistent with the samples actually published.

**Validated offline** against a naive DFT before the Jython port
(`services/sim-vibration/app.py`, the CPython original): peak 1.32 g, RMS 0.235 g, crest 5.62,
velocity RMS 2.99 mm/s — ISO 10816 zone B/C for a medium machine. 1×, BPFO and the resonance all
stand ≥ 6× above the noise floor at 700 Hz; severity 0.9 gives 8× the peak of severity 0.05.

---

## Implementation — inside Ignition

**Partial today.** Landed on disk: `vibsim` script library (incl. box-whisker ingest), UDT
control/waveform/`box_whisker` tags, instance at `icc26/site1/upstream/bioreactors/br-201`,
event stream `vibration-gw-control`, and the `pm-sensor-listener` source topic/QoS. Still to
wire in Designer (then commit): Gateway Timer, `pm-sensor-listener` handler, Engine custom
namespace, and `readings/*` references. Exercise collect from Designer (`steady_state` +
`collect_request`); no Perspective view in v1. Verification below assumes the wiring is in
place.

### Script library `vibsim`

`ignition/projects/icc-2026/ignition/script-python/vibsim/code.py`. Jython 2.7 port of the
validated model. Configuration lives in constants at the top of the module — the counterpart of
the container build's environment variables.

| Function | Called by (once wired) |
|---|---|
| `telemetry_tick()` | Gateway Timer Script, 5000 ms — **still to create** |
| `build_collect_response(request)` | Event stream `vibration-gw-control` transform — **landed** |
| `handle_collect(request)` | Legacy build+publish helper (stream uses transform + MQTT handler) |
| `route_waveform(payload)` | Event stream `pm-sensor-listener` handler — **still to add** |
| `bearing_block(n)`, `velocity_rms_mm_s(block, fs)` | the three above |

Two lookup tables at the top of the module carry the routing, and they point in opposite
directions — worth reading together:

| Table | Key → value | Used by |
|---|---|---|
| `CHANNELS` | `index` → `(cell, device)` | `telemetry_tick`, to address the machine |
| `SENSOR_TAGS` | `(gw_serial, index)` → UDT instance path | `route_waveform`, to reach the tags |

> **Both guesses in this resource are now confirmed — 2026-08-11.** The on-disk path
> (`ignition/script-python/<library>/{code.py,resource.json}`) and the minimal `resource.json`
> shape both work: the library appeared in the Designer after `python tasks.py restart
> ignition`, and the gateway did **not** rewrite the file or add a
> `lastModificationSignature`. A hand-authored project script library is therefore a known
> format and can be authored as files, like tags and Perspective views. Event stream
> transforms are per-event (`return …`); script handlers are batch (`for event in events`).

### Gateway Timer Script — telemetry (**still to create**)

Gateway Events → Timer → new, **5000 ms, fixed delay, gateway scope**, enabled:

```python
vibsim.telemetry_tick()
```

### Event stream `vibration-gw-control` — the device side (**landed**)

On disk at
`ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/01_mqtt/vibration-gw-control/`.

Pipeline:

| Stage | Config |
|---|---|
| Source | Cirrus MQTT Engine, `icc26/site1/upstream/vibration-gw/cmd/collect`, QoS 1 |
| Encoder | `ignition.jsonObject` |
| Transform | `return vibsim.build_collect_response(event.data)` |
| Handler | MQTT Transmission → `icc26/site1/upstream/vibration-gw/response/waveform`, server `chariot_broker`, QoS 1 |

`build_collect_response` validates the fleet command and synthesizes a vendor
`wiredCollection` (no sleep — `SIMULATE_CAPTURE_TIME` is False). The MQTT handler publishes
that document. There is no ack — the waveform on the response topic *is* the response. Filter is
left disabled for now; rejects / wrong `gwSerial` return `None` from the transform.

### MQTT Engine custom namespace — telemetry ingest

**UI-then-commit.** The custom-namespace schema is not in the repo and must not be hand-authored.

1. Gateway UI → MQTT Engine → Settings → Custom Namespaces → new.
2. Name `icc26-native`; subscription `icc26/site1/upstream/+/+/telemetry`; payload JSON; tag
   provider **`pm-sensors`**. The wildcard is in the cell slot on purpose — one gateway serves
   several skids, so pinning it to `br-201` would silently drop channel 1's telemetry the day
   a second channel is provisioned.
3. `git status` → commit exactly what appears under
   `ignition/config/resources/core/com.cirruslink.mqtt.engine.gateway/`.
4. **Record the generated tag paths in this document** before writing the `readings/*` references.

Engine builds the folder tree from topic tokens, so the existing empty
`pm-sensors:process_area_1` folder becomes dead once the real tree lands — delete it then.

> **The response topic must NOT be in this subscription.** A 34 KB document per collect does
> not belong in the tag change pipeline — that is the whole reason for the split. It is also
> on a different branch (`vibration-gw/response/…`), so the wildcard above cannot reach it
> even by accident.

### Event stream `pm-sensor-listener` — waveform ingest

Already existed as a skeleton (Cirrus MQTT source, `EventStreams/#`, no handlers).

**Applied to `…/event-streams/01_mqtt/pm-sensor-listener/config.json`:**

| Field | Value |
|---|---|
| `source.config.topic` | `icc26/site1/upstream/vibration-gw/response/waveform` |
| `source.config.qos` | `0` → `1` |
| `sourceEncoder` | `ignition.jsonObject` (unchanged) |

**Still to do in the Designer** — handler schemas are not in the repo and must not be
hand-authored. Add one script handler:

```python
vibsim.route_waveform(event.data)
```

then commit whatever appears under
`ignition/projects/icc-2026/com.inductiveautomation.eventstream/event-streams/01_mqtt/pm-sensor-listener/`.

All the routing logic lives in `route_waveform`, so the handler stays a batch loop
(`for event in events: …`).

### Tag model — `default` provider

The `vibration_sensor` UDT already existed; its control half was correct and is unchanged
(`collect_request`, `steady_state`, `collect_trigger` and the `valueChanged` script — including
its one-shot semantics, `lor` power-of-two validation, the `String` `gw_serial` note, and the
honest comment that `publish()` reports handoff, not delivery).

**Applied:**

| Change | |
|---|---|
| `mqtt_topic` default | → `icc26/site1/upstream/vibration-gw/cmd/collect` |
| `resolution` default | 32768 → `4096` (32768 is a 267 KB publish) |
| `uns_path` parameter | **new** — locates this sensor's ingested tags in `pm-sensors` |
| `last_request_ts` tag | **new** DateTime, stamped by the collect script |
| `waveform/` folder | **new** — `latest` (Document), `captured_at` (DateTime), `sample_rate_hz`, `sample_count` (Int4) |
| instance `br-201` | at `icc26/site1/upstream/bioreactors/br-201`, `resolution=4096`, `uns_path=site1/upstream/br-201/agitator-vib` |
| `waveform/box_whisker/` | **new** — five-number summary, Tukey fences, `outlier_count`, `check_ok` (written by `route_waveform`) |

**Still to add:** a `readings/` folder of reference tags (`rms_velocity_mm_s`, `peak_accel_g`,
`crest_factor`, `temperature_c`) pointing into `pm-sensors`.

> **Ordering constraint.** Those reference paths cannot be authored until the Engine custom
> namespace exists and the paths it generates have been *observed*. Build the namespace first,
> read the real paths out of the provider, then add the references. Do not guess them.

`last_request_ts` and `waveform/captured_at` side by side *are* this pattern's request/response
story — the "we asked at" and the "captured at", with no correlation id between them.

### Box-and-whisker check

Computed in `route_waveform` from `data` on every successful ingest:

| Tag under `waveform/box_whisker/` | Meaning |
|---|---|
| `min` / `q1` / `median` / `q3` / `max` | Five-number summary of the capture (g) |
| `iqr` | `q3 - q1` |
| `fence_low` / `fence_high` | Tukey fences (`q1 - 1.5·iqr`, `q3 + 1.5·iqr`) |
| `outlier_count` | Samples outside the fences |
| `check_ok` | `outlier_count / n ≤ vibsim.MAX_OUTLIER_FRACTION` (default `0.05`) |

Healthy noise stays under the threshold; growing BPFO impacts drive the fraction up as
`DEFECT_SEVERITY` ramps — that is the demo hook. No Perspective chart; read the tags in
Designer (or bind later if a view is added).

### Perspective

**Not in scope for v1.** Exercise the pattern from Designer: set `steady_state = true`, then
`collect_request = true`. FFT / envelope-spectrum plotting remains a nice-to-have (open item 8).

---

## Infrastructure

**No compose service.** `docker-compose.yml` carries a comment where pattern 1's container would
have gone, pointing here.

**`services/sim-vibration/` is retained but not wired in.** A complete, tested standalone
container implementing the same contract (also no ack, no state, no Last Will). It is the
executable specification of the payloads and the origin of the validated model. Delete it if
it becomes a maintenance drag; it costs nothing sitting there.

**The `vib-gateway` MQTT user is now unused.** Ignition publishes as `ign-transmission`
(`icc26/#`) and subscribes through Engine. `compose/chariot/mqtt-users.json` still defines
`vib-gateway` with the corrected topic list — harmless, and it is what a real gateway would
need. Note that `MQTT_USERS` applies **on first run only**, so editing that file does nothing to
a Chariot that already has its user store.

**`compose/postgres/initdb/03-seed.sql`** — the four `vib-01…04` pumpskid rows were replaced with
`vib-gw-01` and `agitator-vib` under `usp`/`br-201`. `initdb` runs on an **empty volume only**;
for a live database apply the equivalent `DELETE` + `INSERT` by hand.

---

## Deviations from the earlier docs

Recorded so nobody "fixes" these back.

| Master plan / architecture doc said | Now | Why |
|---|---|---|
| 4 sensors `vib-01…04` on `pumpskid1` | 1 sensor on `br-201`, gateway with 3 idle channels | The UDT models a real multi-channel wireless gateway; the bioreactor framing ties pattern 1 to pattern 2's asset |
| Per-device `.../vib-01/cmd/collect` | Fleet topic `icc26/site1/upstream/vibration-gw/cmd/collect` | Real gateways share one control topic and self-select on `gwSerial` |
| Waveform carries the originating `correlation_id` | No correlation id; capture `timestamp` echoed on the vendor payload | The PM system needs a waveform and its capture time, not request lineage |
| Perspective fires the command **via Engine** | Via **Transmission** | Engine's ACL wildcard does not match a five-token control topic; Transmission holds `icc26/#`. Neither is Sparkplug — `transmission.publish` is a general MQTT publish reached by RPC |
| Engine custom namespace → Document tags for everything | Namespace for telemetry, event stream for waveform | Keeps a 34 KB document out of the tag change pipeline; demonstrates both 8.3 ingest surfaces |
| Standalone `services/sim-vibration` container | Simulated inside Ignition | One fewer container and image build; the code is kept on disk unwired |
| Retained LWT birth/death on `.../state` | **Dropped entirely** | A will can only be set by the client that owns the session. Faking it with startup/shutdown scripts covers only the graceful case, so the pattern claims no lifecycle at all |
| `ack` topic, accept/reject per collect | **Dropped entirely** | The waveform arriving *is* the acceptance. A rejection now logs and publishes nothing, so all four negative paths look identical on the wire — which is the honest shape of a fire-and-forget command protocol |
| Waveform on the sensor's own branch `br-201/agitator-vib/waveform` | One fleet topic `vibration-gw/response/waveform`, demuxed in `route_waveform` | A response belongs to the command channel, not the sensor. Keeps the topic tree flat while the tag tree stays ISA-95: a new sensor is a row in `SENSOR_TAGS`, not a new subscription |
| Gateway sits in cell `br-201` (topics and `plant.equipment`) | Gateway sits in `vibration-gw`; each *channel* carries its own cell | An Erbessd gateway is a radio concentrator serving several skids. Pinning it to a cell asserts the fleet cannot cross cells, which is false |

---

## Verification

Prerequisite: **the stale-image blocker in [`00-status.md`](00-status.md) is fixed** — a gateway
running Cirrus 4.0.8 has no working Engine or Transmission, so everything below fails for reasons
unrelated to this pattern. `tasks.py scan` also requires the one-time, per-machine HTTPS API key
setup described in the root README; until then, apply config changes with
`python tasks.py restart ignition`.

Watcher, in its own terminal:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

**1 — Telemetry.** Within 5 s of the timer script being enabled, a `telemetry` document every
5 s on `icc26/site1/upstream/br-201/agitator-vib/telemetry`.

**2 — Collect by hand.**

```powershell
docker run --rm --network icc26 eclipse-mosquitto:2 `
  mosquitto_pub -h chariot -u observer -P observer `
  -t 'icc26/site1/upstream/vibration-gw/cmd/collect' `
  -m '{\"type\":\"observerrequest\",\"channelIndex\":0,\"action\":\"wiredcollectnow\",\"settings\":{\"lor\":4096,\"sendto\":\"cloud\"},\"gwSerial\":\"12345678\"}'
```

Expect **only** a `wiredCollection` on `…/response/waveform` with `len(data) == 4096` and
`sampleRate: 32000`. `timestamp` is the capture start. Nothing is published immediately —
there is no `ack`. (`observer` has no publish rights in `mqtt-users.json`; this works only
because `allowAnonymous: true`. When that goes back off before the talk, publish as
`ign-transmission`.)

**3 — Negative paths.** `channelIndex: 2` → log `not-provisioned`, nothing on the wire.
`"lor": 1000` → log `invalid-lor`, nothing on the wire. `gwSerial: "99999999"` → **nothing at
all** (not even a log on this gateway), which is the point. All rejection cases look identical
to a subscriber. Offline harness covers the dispatch table; re-check at least these three live.

**4 — Ingest.** `pm-sensors` telemetry tags update every 5 s. A collect populates
`waveform/latest` and `waveform/captured_at` on the UDT instance.

**5 — Round trip from Ignition.** With `steady_state` true, press Collect in Perspective →
`wiredCollection` on the watcher, `last_request_ts` and `waveform/captured_at` populated, and the
chart redraws. With `steady_state` false the expression tag never fires and nothing is
published — verify by watching the topic, not by trusting the disabled button.

---

## Open items

| # | Item | Status |
|---|---|---|
| 1 | Script-library path + `resource.json` shape | **confirmed 2026-08-11** — hand-authored library loads; no rewrite on restart |
| 2 | UDT instance path vs topic tree | **done 2026-08-12** — instance is `icc26/site1/upstream/bioreactors/br-201` (MQTT topics stay `…/upstream/br-201/…`) |
| 3 | `steady_state` has no driver. Toggle in Designer for v1; may derive from `shaft_rpm` stability once pattern 2's process data exists | open (Designer toggle accepted) |
| 4 | Engine-generated tag paths under `pm-sensors` — observe and record here before authoring `readings/*` | blocked on the namespace being built |
| 5 | Jython synthesis timing for `lor=4096` is unmeasured (CPython was ~10 ms; Jython is slower and blocks the handler) | measure on first run |
| 6 | Chariot's max MQTT packet size unconfirmed. `MAX_LOR=65536` (~530 KB) is a guess at a safe ceiling | verify against Chariot 3.0.1 |
| 7 | Transmission logs `Failed to subscribe to TARGET elements` (see `00-status.md`) — may or may not affect publish | investigate at step 2 of verification |
| 8 | FFT / envelope-spectrum chart in Perspective | out of scope for v1 |
| 9 | Perspective view for pattern 1 | out of scope for v1 — Designer tag writes instead |
| 10 | Box-and-whisker tags + `check_ok` on the UDT | **done 2026-08-12** — computed in `route_waveform` |

---

## Progress log

| Date | Change |
|---|---|
| 2026-08-10 | Document created. Design settled: fleet control topic, one 4-channel gateway with ch0 provisioned, no correlation id, split namespace/event-stream ingest. Existing `vibration_sensor` + `bioreactor` UDTs reviewed and kept. |
| 2026-08-10 | Standalone container built and validated offline (physics + all 10 command-dispatch cases + envelope shape). |
| 2026-08-10 | **Reversed: gateway moves inside Ignition.** Container unwired from compose but kept on disk. Birth/death and LWT dropped entirely — a will belongs to whoever owns the session, and faking it would only cover graceful shutdown. `state` topics removed from the contract. Jython port written to `vibsim`. |
| 2026-08-10 | UDT edits applied: `mqtt_topic`, `resolution`, `uns_path`, `last_request_ts`, `waveform/` folder, instance overrides. Architecture doc topic table and LWT/Sparkplug paragraph corrected. |
| 2026-08-11 | Script-library path + `resource.json` confirmed after gateway restart. |
| 2026-08-11 | Doc scrub: removed leftover `ack` payload/verification; marked timer, `vibration-gw-control`, handlers, namespace, and Perspective as still to wire. |
| 2026-08-12 | UDT instance moved to `icc26/site1/upstream/bioreactors/br-201`. Perspective deferred — Designer toggles `steady_state`/`collect_request`. Added `waveform/box_whisker/*` tags + Tukey check in `route_waveform`; FFT chart stays out of scope. |
| 2026-08-12 | Event stream `vibration-gw-control` landed on disk (MQTT Engine source on `cmd/collect` → batch `vibsim.handle_collect`). Doc markers updated; JSON left as-is. |
| 2026-08-12 | `vibration-gw-control` reworked: transform `build_collect_response` + MQTT Transmission handler to `response/waveform` (no sleep, filter still off). |
| 2026-08-12 | Waveform response is vendor `wiredCollection` (`data`/`sampleRate`/`wiredChannel`/…); `route_waveform` demuxes on `gwSerial`+`wiredChannel`; `SAMPLE_RATE_HZ` → 32000. |
