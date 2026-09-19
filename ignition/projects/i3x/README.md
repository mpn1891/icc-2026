# I3X API for Ignition

An implementation of the [CESMII i3X API](https://api.i3x.dev/v1/docs) (spec v1.0)
on top of Ignition, exposing UDT definitions and instances, their hierarchy,
live values, history, and alarm references through a single HTTP API.

The API is served by Ignition's **WebDev** module. Each endpoint resource is a
thin `doGet`/`doPost` that delegates to the project scripting library under
`i3x.*`, so the logic lives in one place.

## Requirements

- **Ignition 8.3+** (developed and tested on 8.3.2). The history endpoints use
  the `system.historian` scripting API introduced in 8.3.
- **WebDev module** — serves the HTTP endpoints.
- **Tag Historian** (the 8.3 Historian) — only for tag-value history; tags must
  be configured to store history.
- **Alarm Journal profile** — only for alarm history (see step 3 below).

## Installing into a Gateway

This repository *is* an Ignition project folder — its top level contains
`project.json`, the `ignition/script-python/i3x` library, and the
`com.inductiveautomation.webdev` resources.

### 1. Import the project

The project **must be named `i3x`** — the WebDev base URL is derived from the
project name, and the scripting library, endpoints, and docs all assume
`/system/webdev/i3x`. Import it one of two ways:

- **File-based (git workflow):** copy or clone this folder into
  `<IgnitionInstall>/data/projects/i3x` on the gateway (the folder name is the
  project name, so it must be `i3x`). The gateway detects it and loads it
  automatically (you'll see `Restarting gateway scripts … project=i3x` in
  `logs/wrapper.log`). This also lets you edit resources on disk and have the
  gateway pick up changes.
- **Gateway/Designer import:** on the gateway web UI, **Config → Projects →
  Import**, or in the Designer **File → Import**, selecting a project export —
  and set the project name to `i3x`.

Make sure the project is **enabled**. The API is then served at:

```
http(s)://<gateway>/system/webdev/i3x        e.g. …/system/webdev/i3x/info
```

Confirm it loaded: `GET /system/webdev/i3x/info` should return `200` with the
capability matrix (this endpoint is public and needs no credentials).

### 2. Configure authentication

Every endpoint except `/info` requires authentication (`require-auth = true`).
Choose the user source and roles that gate the API — see
[Authentication](#authentication). At minimum the gateway needs a user source
and a user your clients can authenticate as.

### 3. Configure the alarm journal (for alarm history)

Alarm history — `POST /objects/history` on an alarm object — reads from an
**alarm journal profile**. Configure one under **Config → Alarming → Journal**.
Its name must match `i3x.ignition.ALARM_JOURNAL` (default `"Journal"`): either
name the journal `Journal`, or change that constant. Without a matching journal,
alarm history returns empty and logs a warning under the `i3x.objects` logger
(it does not error the request).

### 4. Historize tags (for value history)

`POST /objects/history` on a UDT returns stored historian data for that UDT's
tags — only tags actually configured to store history return values.
**Composition history** (nested UDT children under `components`) appears only
when those child tags are historized; enable history on the relevant
UDT-definition member tags. See [History](#history).

### 5. (Optional) Namespaces

A UDT definition can declare a `NamespaceUri` parameter to group its instances
into an i3X namespace. UDTs without it fall under the default Ignition UDT
namespace (`https://inductiveautomation.com/UDT`).

## Layout

| Area | Path |
| --- | --- |
| Request handlers / response envelope | `i3x.handlers` |
| Ignition model (UDTs, alarms, schema) | `i3x.ignition` |
| Tag-change subscriptions | `i3x.tag` |
| Shared helpers (timestamps, cache, errors) | `i3x.utils` |
| HTTP endpoints | `com.inductiveautomation.webdev/resources/*` |

## Endpoints

`GET /info`, `GET /namespaces`, `GET|POST /objecttypes`,
`GET|POST /relationshiptypes`, `GET /objects`, `POST /objects/list`,
`POST /objects/related`, `POST /objects/value`, `POST /objects/history`,
`POST /subscriptions`, `POST /subscriptions/{register,unregister,sync,stream,delete,list}`.

## Authentication

`/info` is intentionally public (it doubles as a health check, per the spec).
**Every other endpoint requires authentication** — `require-auth` is enabled in
each endpoint's `config.json`.

To configure who may call the API, edit the enabled handler blocks in the
WebDev resource `config.json` files (or the WebDev resource UI):

- `user-source` — the Ignition user source to authenticate against. Empty = the
  gateway default user source.
- `required-roles` — comma-separated roles. Empty = any authenticated user.
  Set this (e.g. `i3x`) to restrict access to a dedicated role.

> **TLS:** `require-https` is left `false` so the API works behind a
> TLS-terminating proxy and in local testing. For any internet-facing
> deployment, either set `require-https` to `true` on each handler or ensure TLS
> is terminated in front of the gateway — credentials are sent on every request.

## Capabilities / known limitations

`GET /info` advertises the server capabilities, which reflect what is
implemented:

- `query.history` = **true**
- `update.current` / `update.history` = **false** — write endpoints
  (`PUT /objects/value`, `PUT /objects/history`) are intentionally not
  implemented; the corresponding WebDev methods are disabled.
- `subscribe.stream` = **true** — Server-Sent Events are supported (see below).

## Subscriptions

Register objects once, then consume value changes either by **polling** (`sync`,
high QoS with acknowledgement) or by **streaming** (`stream`, SSE, at-most-once).
The two are mutually exclusive per subscription.

1. `POST /subscriptions` with a `clientId` → returns a `subscriptionId`.
2. `POST /subscriptions/register` with `elementIds` and optional `maxDepth`
   (1 = the element only, 0 = all composition descendants). The server begins
   queuing value changes for those objects.

### Sync (polling)

Poll `POST /subscriptions/sync`. Each call returns all pending `SyncBatch`es.
Pass `lastSequenceNumber` to acknowledge (and drop) batches up to that point;
`lastSequenceNumber = -1` clears the whole queue. Staged updates and pending
batches are each bounded (`MAX_QUEUE_SIZE` in `i3x.tag`, default 1000); on
overflow the oldest data is dropped and `sync` responds **`206 Partial
Content`** so the client knows it missed updates.

### Stream (SSE)

`POST /subscriptions/stream` opens a `text/event-stream`. Each event is a JSON
array of updates: `data: [{"elementId":…,"value":…,"quality":…,"timestamp":…}]`.
Queued changes are delivered first, then changes as they occur. There are no
sequence numbers and no acknowledgement (at-most-once).

- **Single stream per subscription**: opening a new stream closes any existing
  one for the same subscription.
- **Mutually exclusive with sync**: while a stream is open, `sync` returns
  `409`. Close the stream (disconnect) before polling.
- A `: keep-alive` comment is sent during idle periods to hold the connection
  open and detect disconnects.

> **Scaling note:** each open stream holds one gateway web-server thread for its
> lifetime (WebDev exposes no async-servlet API). This is fine for a modest
> number of concurrent streams; clients needing to scale to very many consumers
> should prefer `sync` polling.

## History

`POST /objects/history` returns **raw stored points** — no resampling or
aggregation — forward-filled into composite rows:

- Each tag is queried individually (`system.historian.queryRawPoints`) and
  merged on timestamp. At every change timestamp each tag shows its last
  recorded value (last-observation-carried-forward), so a slow-changing tag
  keeps its value across the rows where a fast tag moves. A tag is `null` only
  before its first recorded value in the range.
- `startTime` and `endTime` are **required** RFC 3339 timestamps (a missing or
  malformed value returns `400`).
- `maxDepth` controls composition recursion (1 = this object, 0 = all
  descendants); child history appears under `components`.
- For an **alarm** object, history is the alarm-journal events over the range
  instead (see install step 3).

## Timestamps

All timestamps in and out of the API are **RFC 3339 UTC**
(`yyyy-MM-dd'T'HH:mm:ss.SSS'Z'`). Parsing and formatting go through
`i3x.utils.parseUtc` / `formatUtc`, which treat values as absolute instants —
there is no gateway-timezone assumption.

## Caching

The structural model (`i3x.ignition.getUdtInstances`) browses every tag
provider, every UDT instance, and queries alarm status — too expensive to
rebuild on every request. It is cached in gateway globals for
`i3x.utils.CACHE_TTL_MS` (default **5000 ms**). Live values and history are
always read fresh; only the structure map (and last-known alarm status) is
cached. Call `i3x.utils.cacheClear()` to invalidate immediately (e.g. after
bulk UDT changes).

## Errors

Errors follow the spec `ErrorResponse` shape:

```json
{ "success": false, "responseDetail": { "title": "Not Found", "status": 404, "detail": "..." } }
```

Bulk endpoints return per-item results with the same `responseDetail` on
failures. Unexpected server errors are logged (loggers named `i3x.*`) and
returned as a generic `500` — internal details are never sent to clients.
