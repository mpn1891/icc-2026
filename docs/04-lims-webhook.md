# 04 — LIMS approval webhook

> Talk track for pattern 4. The spec this was built from is
> [`plans/04-lims-webhook.md`](plans/04-lims-webhook.md). Architecture decisions live in
> [`00-architecture.md`](00-architecture.md); this file is what you speak.

| | |
|---|---|
| **Pattern** | 4 of 7 — webhook, because a person has to sign it off |
| **Mechanism tag** | `meta.mechanism = "webhook"` |
| **New container** | `lims` — [`services/lims/`](../services/lims/) |
| **Approval screen** | <http://localhost:8000> |
| **Depends on** | pattern 3's analyzer topic (already live). `POST /trigger` unblocks a broken analyzer |
| **Blocks** | nothing |

## Talk points

**A webhook exists because the answer is not ready when you ask.** If the LIMS could answer
synchronously, the correct design is an HTTP response, not a callback. The callback is what you
build when the work is asynchronous, and here it is asynchronous for the most ordinary reason in
a regulated plant: a person has to sign it off.

**A naive webhook loses data, permanently.** One delivery attempt, over a network the sender
does not control, against a receiver that may be restarting. When the retries exhaust there is
nothing to replay from, and the sender has already forgotten. There is no other mechanism in
this demo covering the same data, so nothing rescues it.

The fix is the one an audience can actually apply on Monday: **a transactional outbox.** Commit
the result and the intent-to-deliver in the same transaction, then let a separate worker drain
the outbox with retries and at-least-once delivery. That is what `lims.webhook_delivery` is for.

Then the line worth saving for last: **an outbox is change data capture that you wrote by
hand.** You end up rebuilding, badly and inside your application, the thing Postgres already
does in the WAL — which is pattern 5's argument arriving from the opposite direction.

## The chain

```
opcua-novaflex ──OPC UA──▶ Ignition ──Event Stream──▶ Transmission
                                                          │
                          icc26/site1/qc/analyzers/novaflex-01/result   (mechanism: opcua-event)
                                                          │
                                              subscribe (QoS 1, lims-bridge)
                                                          ▼
                                                   ┌──────────────┐
                                                   │  lims :8000  │  INSERT status='received'
                                                   │              │
                                                   │  approval UI │◀── a human clicks Approve
                                                   └──────┬───────┘    UPDATE + outbox row,
                                                          │            one transaction
                                        POST + shared secret + idempotency key
                                                          ▼
                                              Ignition WebDev  ──▶ Transmission
                                                          │
                                 icc26/site1/qc/lims/sample-result   (mechanism: webhook)
```

`meta.correlation_id` is `sample_id`, stamped once in pattern 3, and survives the whole chain
so one sample is two colours on the firehose.

The LIMS publishes **nothing**. `lims-bridge` has `publishTopics: []`. Widen its subscribe
grant to `icc26/#` and you have an infinite loop; the same ACL file that stops the valve
leaving upstream stops this service talking to itself.

## Envelope

One message per sample, all analytes that were present. `ts` is `collected_at` (the vendor's
`SampleTime`); `meta.ingest_ts` is the approval instant. The gap is the point.

```json
{
  "ts": "2026-08-20T14:03:22.145Z",
  "seq": 1,
  "source": { "id": "lims", "type": "lims" },
  "meta": {
    "mechanism": "webhook",
    "ingest_ts": "2026-08-20T14:07:01.002Z",
    "correlation_id": "S-00014"
  },
  "values": {
    "sample_id": "S-00014",
    "batch_id": "B-2026-0142",
    "collected_at": "2026-08-20T14:03:22.145Z",
    "analyst": "mnorris",
    "results": [
      { "analyte": "glucose", "value": 4.21, "uom": "g/L" },
      { "analyte": "lactate", "value": 1.08, "uom": "g/L" }
    ]
  }
}
```

Osmolality is often absent: the Nova osmometer is unfitted on purpose, and a null analyte
produces **no row**, not a zero. `/trigger` synthesises all three so the fallback path is
readable on a projector.

## The failure demo

Rehearse this. It is now the only durability argument this pattern makes.

1. Approve a sample. One message, `mechanism: webhook`. Normal.
2. **Naive delivery, broken.** Disable the drainer, approve. The result is verified inside the
   LIMS and the backbone never hears about it. Show the row, then show the silent topic.
3. **The outbox, working.** Re-enable the drainer. Restart this container if you like — the
   queue is in Postgres. The queued delivery lands, late, with `attempts > 1`.
4. Say the line: what we just built to make a webhook trustworthy is a worse copy of the
   write-ahead log Postgres has had the whole time. Which is pattern 5.

## Verification

Schema and the `lims-bridge` subscribe grant take effect on an empty volume. On this checkout they
were applied live (`migrate-04-lims.sql` as `postgres`, Chariot `PUT /mqttusers/lims-bridge`)
without a nuke. `python tasks.py nuke` then `seed` is still the clean-room path — and do it
before Odoo exists, because the same `nuke` destroys Odoo's database.

Terminal 1, both topics:

```
docker run --rm -it --network icc26 eclipse-mosquitto:2 `
  mosquitto_sub -h chariot -u observer -P observer `
  -t 'icc26/site1/qc/analyzers/+/result' -t 'icc26/site1/qc/lims/sample-result' -v
```

Terminal 2:

```powershell
curl.exe -X POST http://localhost:8000/trigger
curl.exe -X POST http://localhost:8000/samples/<id>/approve -d "analyst=mnorris"
```

Expected: rows appear, **nothing** on `qc/lims/sample-result` until Approve, then exactly one
message. `/trigger` does not publish onto the analyzer topic — this service has no publish
grant. To see the analyzer half of the chain, trigger Nova.

```powershell
docker exec -it icc26-postgres psql -U icc26 -d icc26 -c `
  "SELECT sample_id, analyte, value, status, verified_at FROM lims.sample_result ORDER BY id DESC LIMIT 10;"
```

Reject publishes nothing. Replay a delivered idempotency key by hand → `409`, no second
message. Wrong secret → `401`.

## Topic wart, kept

`qc/lims/sample-result` puts a software system in the line-or-cell slot. The better address is
under BR-201. Revisited at spec time and kept: the conference is four weeks out, and the
reversal made this *less* defensible than when three patterns keyed off the topic. It is a
spoken aside — a violation of our own rule that we found, could justify fixing, and chose not
to fix this close to a deadline.

## Deviations

| Shortfall | Why it is acceptable here |
|---|---|
| Bare shared secret, not an HMAC over the body | Demo-grade committed credentials are an accepted trade |
| `UNIQUE (sample_id, analyte)` | A real LIMS repeats tests. Simplifies ingest dedupe to one constraint |
| Ignition-side dedupe is in-memory and lost on restart | Avoids depending on the `ICC26` datasource, which does not exist yet |
| Instrument operator dropped | No column; `analyst` is the approver |
| `qc/lims/sample-result` names a system in the line-or-cell slot | Kept for schedule. See above |
| WebDev resource was file-authored | Mount path is `/system/webdev/icc-2026/lims/sample-result`. The 8.3 discriminator is `resource-type: python-resource` in `config.json` — `resourceType: python` yields `500 Unknown resource factory:` with an empty name |
| Gateway cert SAN is `localhost` only | LIMS verifies the mounted public cert and skips hostname matching. Ignition 302s `:8088` → `:8043`, so the default URL is HTTPS |
| LoggerEx is not Python logging | `logger.info("… %s", x)` throws. Use `infof` / `warnf`, matching `vibsim` |
| Live Nova sample is two rows | Osmometer unfitted → `osmo` is null → no row, not a zero. `/trigger` still synthesises all three |

## Progress log

| Date | Change |
|---|---|
| 2026-08-20 | Service, schema, ACL, `correlation_id` on pattern 3, WebDev + `lims_webhook` script, approval screen on :8000. |
| 2026-08-20 | **Ingest verified without a nuke.** Schema applied live (`migrate-04-lims.sql`); `lims-bridge` ACL updated via `PUT /mqttusers/lims-bridge`. MQTT connect as `lims-bridge`, `/trigger` → 3 rows, QoS-1 redelivery is a no-op, Reject writes `rejected` and no outbox row, Approve is one transaction (rows + outbox). |
| 2026-08-20 | **Publish checkpoints after a trial reset.** Trial lapse returned WebDev `402` — check `GET /data/api/v1/trial` before anything else. File-authored WebDev needed `resource-type: python-resource` or GET/POST is `500 Unknown resource factory`. LoggerEx needs `infof`/`warnf`. Then: Approve → one message on `qc/lims/sample-result` with `mechanism: webhook`; replay of the same idempotency key → `409` and no second message; wrong secret → `401`; disable drainer, approve two, `docker restart icc26-lims` → both deliver from Postgres. Pattern 3 `correlation_id` watched live on `qc/analyzers/novaflex-01/result`. |
