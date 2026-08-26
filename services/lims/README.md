# lims — pattern 4, approval webhook

A FastAPI LIMS that **opens a sample record when the valve draws the sample** and
**appends the analyzer result to it** when the analysis finishes. A human releases
the finished record and it is POSTed to Ignition over HTTP. It publishes nothing
onto the backbone; Transmission does that. See
[`docs/plans/04-lims-webhook.md`](../../docs/plans/04-lims-webhook.md).

```
http://localhost:8000/     review screen
```

## Two subscriptions, and the order they arrive in is the pattern

| | Topic | What it does |
|---|---|---|
| 1 | `…/br-201/sample-valve-01/event/sample-complete` (pattern 1) | **Opens the entry** — one `lims.sample` row with the badge holder, `sample_start`, `open_duration_s`, `cycle_result` |
| 2 | `icc26/site1/qc/analyzers/+/result` (pattern 3) | **Appends the analytes** to that entry, minutes later |

The sample begins when material leaves the reactor, so that is when the record
exists. An analyzer result is not the birth of a sample; it is something that
happens to one.

Routing is on the **topic**, never on the payload's shape. Pattern 1 is a bought
device: no `meta`, no `source`, nothing inside the document says what it is. The
topic string is the only thing that tells the two apart, which is pattern 1's
whole argument arriving as a code constraint.

## The two ids, and why there are two

A person reads the sample id off the valve's page and types it into the
analyzer's sample-login screen on :8087. It can be typed wrong. So:

- **`sample_result.reported_sample_id`** is what the instrument said, verbatim,
  and is never rewritten. The dedupe constraint lives on it.
- **`sample_result.sample_id`** is the entry it was attached to, and is **null**
  when nothing carries that id.

A result matching no entry is **parked**, shown on the screen under *Unmatched
results*, and reattached by an analyst — recorded with `attached_by` and
`attached_at`. You do not correct a record by overwriting what the instrument
reported; you record the correction beside it.

## Entry lifecycle

```
awaiting-analysis ──(analytes attach)──▶ received ──(e-sign)──▶ verified | rejected
        │
        └─ cycle_result ≠ normal opens straight into `received`: no material
           reached the analyzer, nothing is coming, and it still needs a signature
```

`awaiting-analysis` is the one state a signature cannot be applied to — there is
nothing yet to have reviewed. Both the screen and `Store._not_reviewable` refuse
it. `batch_id` arrives with the analysis: the valve opens on a badge, not a work
order, and its event carries no batch at all.

## Why it has no publish rights

This is the first component in the stack that both consumes the backbone and
causes publishes onto it. That is not a feedback loop **only** because
`lims-bridge` has `publishTopics: []`. Its sole output is the HTTP callback;
Ignition's Transmission session (`ign-transmission`) is the publisher.

Widen the subscribe grant to `icc26/#` and you have an infinite loop. Give this
account publish rights and it can build one by itself. The ACL is the
enforcement, and it is the same file that stops pattern 1's free-text topic box
leaving the upstream area.

## Three redelivery sources, not two

**QoS 1 is at-least-once.** A redelivered result must not create a second row, so
ingest is `INSERT … ON CONFLICT (reported_sample_id, analyte) DO NOTHING`. That
uniqueness is a demo simplification — a real LIMS repeats tests — and it is why
the constraint exists.

**`sample-complete` is retained and this client uses a clean session.** The broker
replays the last one on every reconnect, so `docker restart icc26-lims` delivers
the most recent valve event again. The entry insert is
`ON CONFLICT (sample_id) DO NOTHING` for exactly that reason: without it, a
restart resurrects the last sample — already approved, already released — back
into the review queue. Retained plus clean session is a redelivery source people
forget they signed up for.

The outbox is also at-least-once. A POST whose 200 is lost in flight is retried,
and the receiver is where exactly-once is manufactured: Ignition answers `409`
for an idempotency key it has already seen and does **not** publish a second
message. That window is a module-level bounded dict (~500 keys), lost on a
gateway restart, which is honest and acceptable until the `ICC26` JDBC
datasource exists.

## The outbox

`lims.webhook_delivery` is not part of `lims.sample`. Approve flips the entry
**and** inserts the outbox row in one transaction, so a crash cannot
leave a verified sample with nobody obliged to deliver it. Disable the drainer
(`POST /webhook/disable`, or the button on the screen) and Approve still
enqueues; nothing is POSTed; the backbone stays silent. Re-enable, or restart
the container — the queue survives in Postgres, which is the argument.

A webhook you can trust is a worse copy of the write-ahead log Postgres has had
the whole time. That is pattern 5, arriving from the opposite direction.

## Endpoints

| Method + path | For |
|---|---|
| `GET /` | review screen: open samples, unmatched results, outbox |
| `POST /samples/{sample_id}/approve` | release. Form-posted; also curl-able |
| `POST /samples/{sample_id}/reject` | publishes nothing, ever |
| `POST /attach` | attach a parked result to an entry. `reported_sample_id` + `sample_id` |
| `POST /webhook/disable` · `/enable` | failure demo. Disable stops the drainer, not the enqueue |
| `POST /trigger` | fallback generator. Invents one whole sample — both halves |
| `GET /healthz` | broker connected + DB reachable |

```powershell
curl.exe -X POST http://localhost:8000/trigger
curl.exe -X POST http://localhost:8000/samples/S-20260820-141530/approve -d "analyst=mnorris"
```

`/trigger` inserts rows. It does **not** publish onto the analyzer topic —
`lims-bridge` has no publish grant, and inventing a backbone message would hide
a broken pattern 3. It has to fake **both** halves now (open the entry as the
valve would, then append results as the analyzer would), or it would only ever
produce unmatched rows. To see the real chain: badge the valve on :8085, then
type its sample id into the analyzer on :8087.

## TLS

Compose mounts `ignition/certificates/icc26-ignition.crt` and this service loads
it as an extra trust anchor. Ignition redirects container HTTP `:8088` to HTTPS
`:8043`, so the default `WEBHOOK_URL` is HTTPS. Hostname matching is off because
seed restores the existing `ssl.pfx`, whose SAN is `localhost` not `ignition`;
the signature is still verified against the mounted cert. Remint the keystore to
tighten that (new SAN names are already in `tasks.py`).
