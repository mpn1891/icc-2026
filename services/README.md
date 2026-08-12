# Pattern services

Step 1 built only the infrastructure — Postgres, Ignition, Chariot — so there is a working
backbone to attach these to.

| Directory | Pattern | Status |
|---|---|---|
| `sim-vibration/` | 1 — native MQTT pub/sub | **not wired in** — pattern 1 simulates the gateway inside Ignition instead ([spec](../docs/plans/01-native-mqtt.md)). Kept as the payload-contract reference. Ignition side is partial: `vibsim` + UDTs + response-stream topic landed; timer/handlers/namespace/Perspective still TODO |
| `opcua-novaflex/` | 3 — OPC UA → MQTT | not started |
| `lims/` | 4, 6, 7 — webhook / poll / aggregation source | **implementation undecided** |

Patterns 1, 2 (Sparkplug B) and 5 (CDC) add no running service here: patterns 1 and 2 run
inside Ignition — pattern 1 as a Jython script library plus (planned) two event streams,
pattern 2 on the built-in Programmable Device Simulator — and pattern 5 is the
`quay.io/debezium/server` image configured from `compose/debezium/`.

## The LIMS contract

`lims/` is deliberately unresolved — it may become [SENAITE](https://www.senaite.com/) or
stay a small FastAPI stub. What matters is that whatever fills it exposes all four surfaces,
because four separate patterns read from it:

1. **Emits a webhook POST** on sample-complete → pattern 4
2. **`GET /results?since_id=N`** with a monotonic id → pattern 6
3. **Writes to `lims.sample_result`** in Postgres, which Debezium tails → pattern 5
4. **Answers a query** the aggregation script can join against → pattern 7

Build against the contract, not the implementation. SENAITE satisfies all four and carries
real weight with a life-sciences audience; a ~100-line stub satisfies them too and starts in
a second. Either way the swap is a `docker-compose.yml` change, not a redesign.

The Postgres schema those patterns depend on already exists — see
`compose/postgres/initdb/02-schema.sql`.
