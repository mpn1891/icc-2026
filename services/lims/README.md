# lims — pattern 4, approval webhook

A FastAPI LIMS that **subscribes** to analyzer results, holds them unapproved, and
POSTs a released sample to Ignition over HTTP. It publishes nothing onto the
backbone; Transmission does that. See
[`docs/plans/04-lims-webhook.md`](../../docs/plans/04-lims-webhook.md).

```
http://localhost:8000/     approval screen
```

## The one contract surface

A sample result, received off `icc26/site1/qc/analyzers/+/result`, released by a
human, pushed to Ignition. That is the whole remaining LIMS contract. The three
surfaces that used to live here — `GET /results?since_id=N`, a Debezium-tailed
insert, a query for the aggregation script — were retired on 2026-08-19 when
patterns 5, 6 and 7 got their own sources.

## Why it has no publish rights

This is the first component in the stack that both consumes the backbone and
causes publishes onto it. That is not a feedback loop **only** because
`lims-bridge` has `publishTopics: []`. Its sole output is the HTTP callback;
Ignition's Transmission session (`ign-transmission`) is the publisher.

Widen the subscribe grant to `icc26/#` and you have an infinite loop. Give this
account publish rights and it can build one by itself. The ACL is the
enforcement, and it is the same file that stops pattern 1's free-text topic box
leaving the upstream area.

## Two dedupe points

QoS 1 is at-least-once. A redelivery must not create a second row, so ingest is
`INSERT … ON CONFLICT (sample_id, analyte) DO NOTHING`. That uniqueness is a
demo simplification — a real LIMS repeats tests — and it is why the constraint
exists.

The outbox is also at-least-once. A POST whose 200 is lost in flight is retried,
and the receiver is where exactly-once is manufactured: Ignition answers `409`
for an idempotency key it has already seen and does **not** publish a second
message. That window is a module-level bounded dict (~500 keys), lost on a
gateway restart, which is honest and acceptable until the `ICC26` JDBC
datasource exists.

## The outbox

`lims.webhook_delivery` is not part of `lims.sample_result`. Approve flips the
result rows **and** inserts the outbox row in one transaction, so a crash cannot
leave a verified sample with nobody obliged to deliver it. Disable the drainer
(`POST /webhook/disable`, or the button on the screen) and Approve still
enqueues; nothing is POSTed; the backbone stays silent. Re-enable, or restart
the container — the queue survives in Postgres, which is the argument.

A webhook you can trust is a worse copy of the write-ahead log Postgres has had
the whole time. That is pattern 5, arriving from the opposite direction.

## Endpoints

| Method + path | For |
|---|---|
| `GET /` | approval screen: pending queue, Approve / Reject, outbox |
| `POST /samples/{sample_id}/approve` | release. Form-posted; also curl-able |
| `POST /samples/{sample_id}/reject` | publishes nothing, ever |
| `POST /webhook/disable` · `/enable` | failure demo. Disable stops the drainer, not the enqueue |
| `POST /trigger` | fallback generator. Invents one sample without an analyzer |
| `GET /healthz` | broker connected + DB reachable |

```powershell
curl.exe -X POST http://localhost:8000/trigger
curl.exe -X POST http://localhost:8000/samples/S-20260820-141530/approve -d "analyst=mnorris"
```

`/trigger` inserts rows. It does **not** publish onto the analyzer topic —
`lims-bridge` has no publish grant, and inventing a backbone message would hide
a broken pattern 3. To see the chain, trigger the analyzer.

## TLS

Compose mounts `ignition/certificates/icc26-ignition.crt` and this service loads
it as an extra trust anchor. Ignition redirects container HTTP `:8088` to HTTPS
`:8043`, so the default `WEBHOOK_URL` is HTTPS. Hostname matching is off because
seed restores the existing `ssl.pfx`, whose SAN is `localhost` not `ignition`;
the signature is still verified against the mounted cert. Remint the keystore to
tighten that (new SAN names are already in `tasks.py`).
