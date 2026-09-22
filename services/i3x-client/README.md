# i3X consumer — the stack's only reader

A small application that does, from outside the gateway, exactly what pattern 7
does inside it:

1. **Finds** the LIMS review objects and the particle counter by browsing
   `GET /objecttypes` and `GET /objects?typeElementId=…`.
2. **Subscribes** to those review objects and holds open an SSE stream
   (`POST /subscriptions/stream`).
3. **Waits.** When an analyst approves or rejects a sample, the gateway pushes
   the whole review object — all fourteen members, including `document`, the
   pattern-4 envelope as it went onto the backbone.
4. **Asks** `POST /objects/history` for the particle counter's readings around
   *that sample's* instant, takes the nearest either side, and reads `status`.
5. **Says so**, if you let it — the finding goes back onto the backbone as
   `icc26/site1/qc/i3x-meta-review-completed`. Off by default; there is a button.

Its page (<http://localhost:8092>) shows every one of those requests and
responses verbatim, next to the verdict drawn from them.

## Why it exists

`sample_chain.CONTEXT_SOURCE` can already be flipped between `sql` and `i3x`,
and the composite comes out the same either way. That shows the facts are
*available* over the API. It does not show that they are available to somebody
who is not the gateway — the flip is still the gateway asking itself.

This is a different container, a different language, a different HTTP client,
reading on **one credential: an Ignition login**. No Postgres, no tag path, no
element id spelled out anywhere in the source. Four systems contributed to the
answer it reaches — a valve, an analyzer, a LIMS and a particle counter — and
this process has never heard of any of them.

It also computes nothing. `clean` and `dirty` are the counter's own `status`
member read back verbatim, set by `particle_counter_poll` against
`config/excursion_threshold`, which is the only copy of the cleanroom limit in
the stack. A threshold here would be a second copy, and two copies drift.

## Publishing what it found

Everything above is the read path, and the read path is the argument. Publishing
is the sentence after it: **Start publishing** on the page turns the reader into
a participant, and every finding from then on goes out as

```
icc26/site1/qc/i3x-meta-review-completed        QoS 1, not retained
{"ts": "<the sample instant>", "values": {...}}
```

`ts` and `values` and nothing else — the envelope every other pattern on this
bus uses, with no `seq`, no `source` and no block naming the mechanism. `ts` is
when material left the reactor, `values.assessed_at` is when this client reached
the verdict, and the gap between them is the document's whole provenance.

**It takes a second credential, and that is the interesting part.** The i3X
login let this container browse an address space nobody had told it about, find
two types by name, subscribe to them and query their history. It bought no right
whatsoever to put anything back. Publishing needs the `i3x-client` **broker**
account — a different system, a different grant, `publishTopics` of exactly this
one topic and an empty subscribe list. `lims-bridge` is the same argument from
the other end: it consumes the backbone and may not publish at all. Both live in
[`compose/chariot/mqtt-users.json`](../../compose/chariot/mqtt-users.json).

**What it says is what it contributed**, and no more:

| | |
|---|---|
| `verdict` | `clean`, `dirty` or `unverifiable` — the counter's own `status`, or a refusal |
| `reason` | why, on `unverifiable`. Never a bare null for somebody to interpret |
| `environment` | the reading the verdict came off, `age_s` included |
| `sample_instant_from`, `history_window_s`, `rows_returned` | how the verdict was reached, so it can be audited without re-running the query |
| `sample_id`, `equipment_id`, `disposition`, `analyst` | enough to correlate, and nothing more |

The analyzer's numbers are deliberately **not** in there. They are already on the
bus as pattern 3 and again inside pattern 4's review; a third copy arriving from
a reader would be this process putting its name on somebody else's measurement.

### Choices worth knowing about the publish

**Off by default.** `PUBLISH_ENABLED=false` in compose and in `.env.example`, and
it should stay that way. The closing argument is that this container reads the
whole model on one API login, and a stack that comes up already publishing has
spent that argument before anybody is watching. Turning it on live, on stage, is
when it is worth seeing.

**The button stops the publish, not the connection.** The broker session is held
from startup either way — the same shape as pattern 4's drainer, which keeps
filling its outbox while delivery is paused. The toggle governs what this client
*says*, not what it is holding, and doing it the other way would put a reconnect
between the button and the first message in front of an audience. So the page
has two lights and not one: `withholding` is a choice, `broker unreachable` is a
fault, and a failed publish has to be attributable to one of them.

**A withheld finding still shows its document.** The card carries the exact bytes
that were not sent. "We chose not to say this" and "we had nothing to say" must
not look the same, and an audience reading the payload beats an audience trusting
a summary of it — which is this page's whole position.

**Enabling replays nothing.** The findings already on the page were withheld at
the moment they were assessed and they stay withheld; re-announcing them on a
button press would date every one of them to the button press. Approve another
sample.

**Startup reads are never published.** Those episodes are this client asking
rather than the server telling, and each one would go out again on every
container restart — a subscriber would watch the same finding announced as new
each time the container was rebuilt. The loop this topic exists for starts at an
analyst signing something off.

**Every verdict publishes, `clean` included.** 07's `icc26/site1/qc/deviation` is
a gate — it fires only when something is wrong, which is right for a topic called
deviation. This one is a review log, and a consumer of it wants to know a sample
was looked at and found clean. A topic that only ever carries bad news cannot
answer that.

**The topic names a mechanism, which the namespace rule forbids.** Taken
knowingly, and it is the second exception on this bus after
`icc26/site1/audit/bes/batch-event`. The message is not a fact about a sample —
`qc/deviation` and `qc/lims/sample-results-released` already carry those. It is the record
that somebody *outside the gateway* reached the same verdict, and a subscriber
who cannot see that in the address cannot see it at all. Nothing in the payload
repeats it. Written up in docs/00-architecture.md § Topic namespace.

**Nothing in the gateway consumes it**, and that is the correct asymmetry rather
than an omission. No Engine custom namespace matches it — checked against the
committed config, which subscribes `icc26/site1/upstream/#`,
`icc26/site1/qc/analyzers/#`, `icc26/site1/env_monitoring/#`,
`icc26/site1/qc/deviation` and `icc26/site1/qc/lims/sample-results-released` — so this
topic creates no tags and adds no second registrant to anybody's Event Stream
source. Watch it with `observer`, or in MQTT Explorer.

⚠ **The `i3x-client` broker account was added on 2026-09-21 and
`mqtt-users.json` seeds on first run only.** On an older Chariot volume it does
not exist — and `allowAnonymous: true` is currently hiding that, because the
client connects anonymously and publishes fine. It will start failing the moment
anonymous goes back off before the talk. Add the account by hand in the Chariot
UI at <http://localhost:8081>, or `python tasks.py nuke`.

## Run it

```powershell
docker compose up -d --build i3x-client
```

Then open <http://localhost:8092>. On startup it reads each review object's
current value so the page is populated before anybody touches the LIMS; those
episodes are tagged `startup` rather than `push`, because the difference between
this client asking and the server telling is most of what the page is for.

To see a push: approve or reject a sample at <http://localhost:8000>. To see a
`dirty` verdict, press **Dirty** on the particle counter panel at
<http://localhost:8089> first, wait for a reading, then draw and approve.

To see it publish, press **Start publishing** and approve another sample. What
went out is under the card, and on the wire:

```powershell
docker compose exec chariot mosquitto_sub -u observer -P observer `
  -t 'icc26/site1/qc/i3x-meta-review-completed' -v
```

## What it depends on

| | |
|---|---|
| The `i3x` project | `ignition/projects/i3x/` — the vendored CESMII server. Disable it in the Designer and this page says so rather than going blank |
| `lims_review` objects | Written by `model_feed.write_review`, called from `lims_webhook.handle` |
| `particle_counter` history | `em.reading.document`, served by the server's `_counterHistory` fork — migrate-13 |
| The `i3x-client` broker account | [`compose/chariot/mqtt-users.json`](../../compose/chariot/mqtt-users.json) — publish only, and only for the button. Seeded on first run only; see the warning above |

**br-202 is subscribed and will never fire.** It is a real `lims_review` object
on a real vessel, but `lims-bridge`'s ACL does not subscribe to br-202's sample
topic, so its samples never reach the LIMS and no review is ever written there.
The client finds it by type and watches it, which is honest: a consumer browsing
this model has no way to know that, and the empty object is what the model
actually says.

## Choices worth knowing

**The history window is ±300 s, not 07's ±3600 s.** `/objects/history` is a
range query and must be given bounds. At the counter's ten-second cadence an
hour either side is about 720 readings — correct, and unreadable as raw JSON on
a page whose entire purpose is the raw JSON. The nearest-either-side rule is
identical; only how far it may reach differs, and every episode names its
window. `I3X_CLIENT_WINDOW_S` changes it.

**SSE, not sync polling.** `POST /subscriptions/sync` is the acknowledged mode
and the right one if a missed update would matter; SSE is at-most-once, so an
update staged while nothing is connected is dropped rather than replayed. For
watching a demo that is the correct trade, and it is the mode that reads well on
stage. Note that SSE is also the mode the i3X plan's phase 3b had to *park* for
an in-gateway client: Jython's `system.net.httpClient` buffers a whole response
before returning, and an SSE stream never ends. Out here it is four lines of
`requests` — the constraint was the gateway's scripting environment, not the API.

**Reconnecting starts over.** Subscriptions live in the gateway's script globals
and die with it, so after a gateway restart the old `subscriptionId` resolves to
nothing. Discovery re-runs too: element ids are the server's to change, and a
client that cached them across a restart would be asserting something the spec
does not promise.

**TLS validation is off**, exactly as `i3x_client` turns it off inside the
gateway: the gateway's certificate is self-signed and names `localhost`, and
this container reaches it as `ignition`. It is logged as a warning on every
start rather than hidden in a constant.

**It keeps nothing.** Episodes live in a bounded deque in memory. Restart the
container and the page is empty, which is right — this is a consumer, and a
consumer that quietly became a store would be a worse citizen and a worse demo.
The publish toggle is memory too: a restart comes back at whatever
`PUBLISH_ENABLED` says, which is off. A control that silently survived a rebuild
would be the one piece of hidden state on a page built against hidden state.
