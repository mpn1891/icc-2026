# sim-vibration — pattern 1, native MQTT

> **Not wired into `docker-compose.yml`.** Pattern 1 simulates the gateway *inside Ignition*
> instead — see [`docs/plans/01-native-mqtt.md`](../../docs/plans/01-native-mqtt.md). This
> directory is kept as the executable specification of the payload contract and the origin of
> the validated bearing model that `vibsim` is a Jython port of. It implements the same
> contract as the Ignition design: **no ack, no state, no Last Will.** Add it back to compose
> if you ever want a separate MQTT client on the wire.

A simulated four-channel wireless vibration gateway. Serial `12345678`, one MQTT connection.
**Channel 0 is the only provisioned channel** — the agitator drive-end bearing on `BR-201`.
Channels 1–3 exist and answer "not provisioned", which is the pattern's negative path rather
than a gap.

Full contract, payload shapes and the reasoning behind them:
[`docs/plans/01-native-mqtt.md`](../../docs/plans/01-native-mqtt.md).

## Topics

| Topic | Dir | Retained |
|---|---|---|
| `icc26/site1/upstream/vibration-gw/cmd/collect` | sub | — |
| `icc26/site1/upstream/vibration-gw/response/waveform` | pub | no |
| `icc26/site1/upstream/br-201/agitator-vib/telemetry` | pub | no |

Nothing is retained, and there is **no ack, no state and no Last Will** — the gateway
publishes nothing about itself. A collect is answered by its waveform arriving, or by nothing;
a rejected collect appears only in this container's log.

The control topic is **fleet-addressed**: every gateway subscribes to it and ignores payloads
whose `gwSerial` is not its own. The waveform comes back on a single response topic carrying
`gw_serial` + `channel_index` in `meta`, which is what the consumer demuxes on to reach one
sensor's tags. Only unsolicited telemetry is addressed at the machine the sensor is mounted
on — and that cell is per channel, since one gateway serves several skids.

## Trigger a collect by hand

```powershell
docker run --rm --network icc26 eclipse-mosquitto:2 `
  mosquitto_pub -h chariot -u observer -P observer `
  -t 'icc26/site1/upstream/vibration-gw/cmd/collect' `
  -m '{\"type\":\"observerrequest\",\"channelIndex\":0,\"action\":\"wiredcollectnow\",\"settings\":{\"lor\":4096,\"sendto\":\"cloud\"},\"gwSerial\":\"12345678\"}'
```

Watch it land:

```powershell
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer -t 'icc26/#' -v
```

Nothing comes back immediately — there is no ack. The `waveform` lands on the response topic
about 1.1 s later at `lor=4096`, because a gateway cannot return a record faster than the
record is long. Its `ts` is the **capture start**, not the publish time; the gap between `ts`
and `meta.ingest_ts` is the capture itself.

Rejections to try: `"channelIndex":2` → `not-provisioned`. `"lor":1000` → `invalid-lor`
(must be a power of two, 512…`MAX_LOR`). `"gwSerial":"99999999"` → silence, which is correct.
**All four look identical on the wire** — nothing is published — so `docker logs` is the only
place a rejection is visible. That is the point being made, not an omission.

## Death is silent

```powershell
docker stop icc26-sim-vibration   # graceful — publishes nothing
docker kill icc26-sim-vibration   # abrupt   — publishes nothing
```

Neither announces anything: this gateway has no Last Will and no state topic, so a subscriber
learns it is gone only by noticing the telemetry stopped. There is no way to distinguish a dead
gateway from a healthy one with nothing to say, and no way at all to tell which *sensor* died —
MQTT gives a client exactly one Last Will, so even adding one back could only cover the gateway.
Pattern 2 gets NDEATH *and* DDEATH from Sparkplug for free, and this is the contrast.

## Environment

| Var | Default |
|---|---|
| `BROKER_HOST` / `BROKER_PORT` | `chariot` / `1883` |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | `vib-gateway` / `vib-gateway` |
| `GW_SERIAL` | `12345678` — must match the `gw_serial` UDT parameter |
| `GW_ID` | `vib-gw-01` |
| `AREA_ROOT` | `icc26/site1/upstream` |
| `FLEET_ID` | `vibration-gw` — the line-or-cell token the command and response hang off |
| `CONTROL_TOPIC` | `icc26/site1/upstream/vibration-gw/cmd/collect` |
| `RESPONSE_WAVEFORM_TOPIC` | `icc26/site1/upstream/vibration-gw/response/waveform` |
| `CHANNELS` | `0:br-201:agitator-vib` — `index:cell:device-id`, comma separated; the cell is per channel because one gateway serves several skids, and anything absent is unprovisioned |
| `CHANNEL_COUNT` | `4` |
| `TELEMETRY_INTERVAL_S` | `5` |
| `SAMPLE_RATE_HZ` | `6400` (Fmax 2.5 kHz at the standard 2.56×) |
| `MAX_LOR` | `65536` — guard against a multi-MB publish |
| `CAPTURE_OVERHEAD_S` | `0.5` — radio/buffering time on top of the record length |
| `SHAFT_RPM` | `1780` |
| `DEFECT_SEVERITY_START` / `DEFECT_SEVERITY_END` | `0.05` / `0.9` |
| `DEFECT_RAMP_S` | `1800` — **set `0` to pin severity for a reproducible stage run** |
| `LOG_LEVEL` | `INFO` |

## What the waveform actually contains

An outer-race bearing defect, not noise dressed up as one: imbalance at 1×, misalignment at 2×
and 3×, and a BPFO impulse train (3.585 × shaft) where every impact rings a 1500 Hz housing
resonance. The impacts are modulated at shaft frequency — the defect passes through the load
zone once per revolution — and jittered ±1 % for rolling-element slip, which is what stops an
FFT of this looking synthetic. The spectrum shows a BPFO peak with ±1× sidebands.

Defect severity ramps over `DEFECT_RAMP_S`, so a gateway that has been up through a rehearsal
has a visibly worse bearing than one just started. **Telemetry is computed from the same model**
— a short block is synthesised each tick and reduced to statistics, and velocity RMS is obtained
by integrating and detrending the acceleration rather than being invented. When the defect
grows, RMS climbs *and* the next waveform shows deeper impacts. One story, not two.
