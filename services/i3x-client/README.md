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

Its page (<http://localhost:8092>) shows every one of those requests and
responses verbatim, next to the verdict drawn from them.

## Why it exists

`sample_chain.CONTEXT_SOURCE` can already be flipped between `sql` and `i3x`,
and the composite comes out the same either way. That shows the facts are
*available* over the API. It does not show that they are available to somebody
who is not the gateway — the flip is still the gateway asking itself.

This is a different container, a different language, a different HTTP client,
holding **one credential: an Ignition login**. No Postgres, no broker, no tag
path, no element id spelled out anywhere in the source. Four systems contributed
to the answer it reaches — a valve, an analyzer, a LIMS and a particle counter —
and this process has never heard of any of them.

It also computes nothing. `clean` and `dirty` are the counter's own `status`
member read back verbatim, set by `particle_counter_poll` against
`config/excursion_threshold`, which is the only copy of the cleanroom limit in
the stack. A threshold here would be a second copy, and two copies drift.

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

## What it depends on

| | |
|---|---|
| The `i3x` project | `ignition/projects/i3x/` — the vendored CESMII server. Disable it in the Designer and this page says so rather than going blank |
| `lims_review` objects | Written by `model_feed.write_review`, called from `lims_webhook.handle` |
| `particle_counter` history | `em.reading.document`, served by the server's `_counterHistory` fork — migrate-13 |

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
