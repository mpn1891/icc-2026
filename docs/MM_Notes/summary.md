## Pattern 1

MQTT native valve published to an MQTT Topic and MQTT Engine is listening on a custom namespace. Each bioreactor (br-201 in this case) in the default tag provider has an instance mapped that is referencing those engine tags, and its also historizing it. I3X is directly grabbing that UDT and applying the history coalesce to unify an "event" from the time series DB — the window is `HistoryCoalesceMs = 2000`, declared on the nested `sample_valve` instance, not on the bioreactor. It is needed because a custom namespace has nowhere to carry a payload timestamp, so Engine stamps each key as it walks the document and one sample lands as three rows ~10 ms apart.

## Pattern 2

MQTT spB native smart valve publishing to a spB topic structure. Each bioreactor (br-202 in this case) in the default tag provider has an instance mapped that is referencing those engine tags, and its also historizing it. I3X is directly grabbing that UDT — but **no coalesce is applied here, and none is needed**: `HistoryCoalesceMs` stays at the type default of 0. Sparkplug carries a payload timestamp that Engine applies to every metric in the message, so all the members land on one timestamp and one sample is already one row. Same UDT as pattern 1, same historian; the difference is entirely the ingestion, which is the point of running them side by side.

## Pattern 3

A cell analyzer is making its data structure available via OPC UA. Ignition is connected to that OPC UA Server and mapping the tags from the OPC UA Server into tags under the cell analyzer UDT instance (`cell-analyzer-01`, 52 bound tags). When the sample time is detected to have changed — a tag-change script on `result/sample_time` — it calls an Event Stream that **writes to Postgres and then publishes to the MQTT broker, in that order**.

The write is the stream's transform (`qc.analyzer_result`); the publish is its MQTT handler (`icc26/site1/qc/analyzers/cell-analyzer-01/result`, QoS 1). Store first, publish second, and the store is wrapped so it cannot throw: the handler's `failureStrategy` is ABORT, so a Postgres outage must cost the row and never the publish.

The stored document is the whole instance value, in the same shape `/objects/value` returns, and it is what `POST /objects/history` serves over i3X. That is the point of the store — unlike patterns 1 and 2, a coalesce window cannot rescue this one. Asked to reassemble one analysis out of ~38 tag-historian series, the server invented states the object was never in, dropped `result_json` (not historised, so no window brings it back), and collided five leaf names once the folders flattened — `osmo` came back as the module flag rather than the reading.

## Pattern 4

A LIMS system has been listening to receive both the original sample and event and the analysis. This is viewable on a simulated webpage, when an operator/qa rep is ready they can approve the sample. On doing so this triggers a push to an Ignition webdev endpoint. This endpoint receives it and publishes it to MQTT as well as write it to tags


> **UPDATE ME** — close, but the push is not direct and the endpoint does three writes.
> **Approve does not POST.** It flips the row and writes a `lims.webhook_delivery` outbox row in one transaction; a separate drainer thread POSTs it over HTTPS :8043 with a shared secret and an idempotency key, with retries — that is the *Pause outbound* demo. **Reject takes the same path**, `disposition: "fail"`.
> **The endpoint checks before it acts:** wrong secret 401, replayed key 409 with no publish (in-memory, last ~500 keys), bad body 400. It publishes the document byte-for-byte — no `seq`, no `source`, no `meta.mechanism`; the topic is the provenance.
> **Then, in order:** publish to `icc26/site1/qc/lims/sample-result` → insert `lims.review_event` (ICC26 datasource) → write the tags at `…/bioreactors/<equipment_id>/qc_data/last_review`, wrapped so a failure cannot 500 and make the outbox republish. The tag write lives in the webhook rather than an Event Stream because 07's `lims-review` stream already holds that topic and a second registrant is silently starved.
> Also worth saying: the LIMS **has no publish rights at all** (`publishTopics: []`), which is why the webhook exists; it writes the analyzer's result into `lims.sample_result` on the way in; and pattern 7 consumes the release message on both dispositions.

## Pattern 5

A barebones simulated BES is writing information to a postgres database. A debezium instance is setup to watch the WAL (this includes both inserts and updates). When debezium sees the event, it writes to an ignition webdev endpoint which then publishes the changes into the MQTT broker 

The BES is not a container — it is `bes_batch`, an Ignition script fired by a `valueChanged` on the `manual_advance` tag. One click writes **two rows in one transaction** (the outgoing `operation_end` and the incoming `operation_start`) sharing one `occurred_at`, which is why pattern 7 tie-breaks on `ORDER BY occurred_at DESC, id DESC`. **The writer never publishes and holds no broker credentials** — that is the whole claim. Stop the Debezium container and clicking still writes rows and still advances the reactor, with a silent topic: the failure demo.

**Two topics, and the split is the point.** An INSERT is a batch event and goes to `icc26/site1/upstream/<equipment_id>/batch/event`, device-addressed, with nothing in the payload saying CDC. `bes.batch_event` is append-only, so an UPDATE or DELETE is not a batch event but somebody amending the record: it publishes on `icc26/site1/audit/bes/batch-event` carrying the `before` pre-image (`REPLICA IDENTITY FULL`), `op` and `lsn` — the one topic allowed to name a database operation. Deletes publish too; only truncate is skipped.

Debezium connects as its own `cdc` replication role against publication `icc26_cdc`, which names `bes.batch_event` **only**, with `autocreate.mode=disabled` — otherwise it would create a FOR ALL TABLES publication and put every LIMS write on the backbone. `snapshot.mode=no_data` stops a restart replaying a rehearsal's history in one burst, and offsets live on a volume so the stop-click-restart catch-up works. The sink authenticates with a **query-string token** (a header would be version-dependent), 401 on a wrong one, and posts to HTTPS **:8043** because :8088 302s and Debezium does not follow redirects.

**A second path leaves the same click, and it is not CDC.** After the commit `bes_batch` writes the `batch_data` tags, historised on change, and *that* is what i3X serves — so stopping Debezium silences the topic while `/objects/history` keeps answering. Measured 2026-09-20: one advance is one clean history row (no coalesce needed here), but a member whose last stored point predates the requested window comes back **null**, so the same HARVEST row read over ten minutes gave `batch_id: null` and over two days gave `B-20260918-03`. Fixed on the write side — all five members written every advance, with `historicalDeadbandMode` Off on the ones that rarely change. A window containing no advance still returns zero rows, which is why an event-store reader over `bes.batch_event` is still open.

## Pattern 6

The particle counter hosts its own local database with a graphql API. Ignition has a timer script that polls the source. In this case it also exposes a cursor/index field so the ignition polling side can know when it polls if it should continue polling to grab the next one (if more than one ran between polls)
## Pattern 7
