# Anton Paar AP Connect + Haze 3001 — the parts patterns 5 and 6 need

> Distilled **2026-08-23** from the vendor documentation set. This file exists so the build specs
> can cite one place instead of re-deriving the API from PDFs. Where this file and the vendor
> documents disagree, the vendor documents win — say so and fix this file.
>
> Source documents, as supplied:
>
> | Document | What it settles |
> |---|---|
> | `apconnect-openapi-4.0.json` (**in this folder**) | AP Connect REST API 1.4.0. Endpoints, parameters, schemas. Machine-readable, so prefer it over the PDFs |
> | `D96IE011EN-J_RESTinterface_APConnect_4.0.pdf` | Subscription semantics, retry rule, well-known tables, Variant representation |
> | `D96IE016EN-C_APConnect_OpenAPIdefinition_4.0.pdf` | The OpenAPI definition as a printed document. Same content as the JSON |
> | `D96IB001EN_X_AP_Connect_software_manual_3.3.pdf` | The MS SQL Server dependency and its connection string |
> | `C84IB005EN_E_IMSI_Haze_3001_web.pdf` | The turbidity module itself: ranges, units, output quantities |
> | `D98IB008EN_A_IMSI_DMA_4002_5002_6002.pdf`, `J63IB001EN_A_Xsample_3200_Imsi.pdf` | The host instruments a Haze 3001 plugs into |
>
> The PDFs are **not** in the repo. They live outside it; only the OpenAPI JSON was copied in,
> because it is the one a build actually reads.

## The topology

The **Haze 3001** is a measuring *module*, not a standalone networked instrument. It plugs into a
host — a DMA 4002/5002/6002 density meter or an Xsample 3200 sample changer — and the host is what
talks to the network. Completed measurements land in **AP Connect**, Anton Paar's lab data
management server, which is a Windows service with a REST API and a SQL database behind it.

```
Haze 3001 module ──▶ host instrument (DMA / Xsample) ──▶ AP Connect server
                                                              │
                                                    REST API :8393  +  MS SQL Server
```

Nothing in that chain speaks MQTT, OPC UA, or Sparkplug. **This is why the instrument is a good
pattern-5/6 source**: the only ways in are the store and the API.

## Haze 3001 — what it measures

Three-angle scattered light. Range **0–100 EBC (0–400 NTU)**, values up to 200 EBC displayable.

Units are selectable and convert exactly:

| | |
|---|---|
| 1 EBC | 4 NTU |
| 1 EBC | 40 Helms |
| 1 EBC | 69 ASBC |

Besides the primary turbidity value, the module can output the raw ratios, for comparison against
meters that read only one angle:

- `Haze value S25/S0` — 25° scattered over transmitted
- `Haze value S90/S0` — 90° scattered over transmitted
- `Haze absorbance S0` — equivalent to an absorbance measurement

`HAZE` is a well-known AP Connect quantity with canonical unit `EBC` (API 110).

## AP Connect REST API 1.4.0

Base URL `https://localhost:8393`. Auth is **HTTP Basic** or **OAuth2** (password flow against a
Keycloak realm `leo` on `:5278`). No top-level security requirement is declared in the OpenAPI
document, so per-endpoint behaviour has to be confirmed against a real server.

### Endpoints that matter here

| Endpoint | Use |
|---|---|
| `GET /api/v1/measurements/completed` | The list. Filtering + pagination, sorted **oldest to newest** |
| `GET /api/v1/measurements/completed/latest` | Cheap change detection. Returns the counters, not the data |
| `GET /api/v1/measurements/completed/{id}` | One measurement in full |
| `POST /api/v1/subscriptions/measurements` | Register a webhook. **Not used by this demo** — see below |
| `GET /api/v1/instruments` | Instrument inventory: type, serial, modules, firmware |

Also present and irrelevant to us: checks, adjustments, audit trail, tasklists, system info.

### The poll contract

`GET /api/v1/measurements/completed` takes, among others:

| Parameter | Meaning |
|---|---|
| `apc_FromMeasurementCompletionNo` | **The watermark.** Start from this completion number |
| `FromMeasurementNo` | Start from an AP Connect item number |
| `FromTimestamp` / `apc_ToTimestamp` | Time window |
| `Limit` / `Offset` | Pagination |
| `apc_SampleName`, `apc_Product`, `apc_Method`, `apc_Status` | Content filters |
| `apc_InstrumentSerialNumber`, `apc_InstrumentAlias`, `apc_InstrumentType` | *"In practice, it would mean to return measurements from single device"* |

Returns `PageOfMeasurement` — `{limit, offset, total, count, items[]}`. Note `count` may be smaller
than `limit` at the server's discretion, so **never infer "no more rows" from a short page**.

`GET /api/v1/measurements/completed/latest` returns `MeasurementLatest`:

| Field | Meaning |
|---|---|
| `dataRevision` (int64, **required**) | Incremented on every modification of the measurements database. *"It's possible that the value is reset to 0 on the next instrument startup."* |
| `measurementNo` (int64, nullable) | Item number of the newest measurement; 0 if none. Strictly consecutive from 1 |
| `apc_measurementCompletionNo` (int32, nullable) | Completion number of the newest completed measurement |

The vendor's own guidance, quoted: *"It's acceptable to run this query every few seconds. It's
recommended to compare dataRevision for **inequality** when determining whether changes have
occurred between two calls."*

Inequality, not greater-than — because the counter can reset. That detail is worth having.

### The subscription, and why it is out of scope

`POST /api/v1/subscriptions/measurements` with `{"webhookUri": "...", "measurementType": "..."}`
(`Measurement` | `Check` | `Adjustment` | `all`). AP Connect then POSTs to that URI on completion.

Two properties make it interesting, and both are documented:

1. **The callback carries no data.** `SubscriptionCallback` is `{id, href, measurementType}`. You
   receive an identifier and a link, and must GET the link to learn what was measured.
2. **Delivery gives up.** *"If the server is unavailable when AP Connect attempts to deliver a REST
   notification, AP Connect will retry to send the notification three additional times (after 5, 10
   and 15 seconds). If these attempts fail, AP Connect stops sending notifications for that
   particular job. Notifications will resume as normal for subsequent completed jobs."*

So a receiver down for ~30 s loses those measurements permanently from the push channel, and the
only recovery is to poll. **Decided 2026-08-23: not built, not presented.** Recorded here so the
detail is not lost. Pattern 7 remains TBD and is expected to be the vibration sensor.

Also worth knowing: in **AP Connect Pharma Edition**, notification and GET are both withheld until
an electronic-signature review completes, and every REST query for a measurement writes an audit
trail entry recording it as an export.

## The data model

Three levels, and the value level is generic key/value rather than columns.

```
Measurement
├── metadata          MeasurementMetadata   ── id, measurementNo, status, timestamp, …
├── specification     MeasurementSpecification
├── environment       MeasurementEnvironment
└── results[]         MeasurementResult
                      ├── metadata          MeasurementResultMetadata
                      ├── values[]          Variant       ◀── the numbers live here
                      └── results[]         MeasurementResult   (sub-results, recursive)
```

### `MeasurementMetadata`, the fields worth persisting

| Field | Notes |
|---|---|
| `id` | GUID. Globally unique, stable. **The natural correlation id** |
| `measurementNo` | *"strictly, consecutively increasing number starting by 1"*. **The natural watermark** |
| `measurementName` | Operator-entered or auto-generated |
| `status` | See `WellKnownMeasurementStatus` below |
| `assessment` | Whether results sat within constraints (API 130) |
| `timestamp` | Usually the **start** date/time |
| `flags` | `{exported, reliable}` |
| `typeId` / `typeName` | e.g. single measurement |
| `startedBy`, `operatedBy`, `operators[]` | `User{userId, login, name}` |
| `product` | `{id, href, name}` |
| `customFields[]`, `additional[]` | Key/value extras |
| `href` | Self link |

`MeasurementResultMetadata` repeats much of this and adds `measurementFullNo`; its `timestamp` is
usually the **end** date/time. So start and end are both available, on different objects.

### `WellKnownMeasurementStatus`

`SUCCESS` · `SUCCESS_WITH_WARNING` · `SUCCESS_WITH_ERROR` · `CANCELED` · `FAILURE`

`WellKnownMeasurementAssessment` uses the same five strings.

`CANCELED` and `FAILURE` are the "do not publish a reading" cases — the analogue of pattern 3's
abort branch.

### `Variant` — how a number is expressed

Every measured value is a `Variant`: `{id, name, names, type, value, href, items, series,
columnDefinitions}`. `id` and `type` are mandatory; `type` defaults to `STRING` if omitted.

For a number with a unit, `type` is `QUANTITY` and `value` expands into an object:

```json
{
  "id": "Haze/Haze",
  "name": "Haze",
  "type": "QUANTITY",
  "value": { "numeric": 4.12, "unit": "EBC", "quantity": "HAZE" }
}
```

`numeric` is always a 64-bit float. `unit` may be `""` and `quantity` may be `"-"` for
dimensionless values. Other types in use: `COLLECTION` (nested `items[]`), `ENUM` (`value` is
`{key, name, names}`), `PERCENTAGE`, `INT32`, `STRING`, `IMAGE`, `EMBEDDED_IMAGE`, `DATASERIES`,
`TABLE`.

Unit strings legitimately contain non-ASCII: `²` `³` `·` `°`. Anything reading them must be UTF-8
clean end to end.

### Well-known value ids

The vendor publishes ids as `Module/Quantity`. The REST manual's table documents the density
module:

| Id | Type |
|---|---|
| `Density/SetTemperature` | Quantity / Temperature |
| `Density/CellTemperature` | Quantity / Temperature |
| `Density/Density` | Quantity / Density |

**The Haze module's ids are not in the documented table.** The `Haze/...` ids used by this demo are
*modelled on that convention*, not transcribed from the vendor. Flagged everywhere they appear.
Confirm against a real AP Connect before claiming them on stage.

## The store

AP Connect persists to **Microsoft SQL Server** — a bundled SQL Express 2019, or an existing
instance. Supported: MS SQL 2016 (extended support, not recommended), 2017, 2019, 2022. The
connection string lives in
`%PROGRAMDATA%\Anton Paar\APConnect\configuration\database.config`:

```xml
<database connectionString="Data Source=hostname\<INSTANCENAME>,port;Initial Catalog=<MyDatabase>;User Id=<USER>;Password=<PASSWORD>;MultipleActiveResultSets=True"></database>
```

**The table schema is not published.** The manual documents how to point AP Connect at a server; it
does not document what AP Connect puts there. So a faithful simulation of the *schema* is not
available at any level of effort — which is exactly why this demo models the **API's** data model
instead, and says so.

**Decided 2026-08-23: the demo simulates the store on Postgres, not MS SQL Server.** The mechanism
argument — an application you do not own, whose database is the product — is identical on either
engine, and Postgres keeps the existing `pgoutput` path, the existing Ignition driver, and roughly
a gigabyte and a half of laptop. What is given up is the ability to say "this is the engine the
vendor ships", and one true aside: on SQL Server, CDC is not log streaming — the engine's capture
job writes rows into change tables and Debezium **polls those**, so SQL Server CDC is itself a poll.
Both losses are recorded in the deviations tables of specs 05 and 06.

## Site facts

A `notes` file supplied with the documents carries a real AP Connect address, a real instrument
address, a jump-box hostname and an account reference. **That is site information and it is
deliberately not reproduced here or anywhere else in this repository.** The demo runs entirely
against a simulator on the `icc26` network. Do not point any part of this stack at a real
instrument.
