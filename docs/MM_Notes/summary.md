## Pattern 1

MQTT native valve published to an MQTT Topic and MQTT Engine is listening on a custom namespace. Each bioreactor (br-201 in this case) in the default tag provider has an instance mapped that is referencing those engine tags, and its also historizing it. I3X is directly grabbing that UDT and applying the history coalesce to unify an "event" from the time series DB — the window is `HistoryCoalesceMs = 2000`, declared on the nested `sample_valve` instance, not on the bioreactor. It is needed because a custom namespace has nowhere to carry a payload timestamp, so Engine stamps each key as it walks the document and one sample lands as three rows ~10 ms apart.

## Pattern 2

MQTT spB native smart valve publishing to a spB topic structure. Each bioreactor (br-202 in this case) in the default tag provider has an instance mapped that is referencing those engine tags, and its also historizing it. I3X is directly grabbing that UDT — but **no coalesce is applied here, and none is needed**: `HistoryCoalesceMs` stays at the type default of 0. Sparkplug carries a payload timestamp that Engine applies to every metric in the message, so all the members land on one timestamp and one sample is already one row. Same UDT as pattern 1, same historian; the difference is entirely the ingestion, which is the point of running them side by side.

## Pattern 3

A cell analyzer is making its data structure available via OPC UA. Ignition is connected to that OPC UA Server and mapping the tags from the OPC UA Server into tags under the cell analyzer UDT instance (`cell-analyzer-01`, 52 bound tags). When the sample time is detected to have changed — a tag-change script on `result/sample_time` — it calls an Event Stream that **writes to Postgres and then publishes to the MQTT broker, in that order**.

The write is the stream's transform (`qc.analyzer_result`); the publish is its MQTT handler (`icc26/site1/qc/analyzers/cell-analyzer-01/result`, QoS 1). Store first, publish second, and the store is wrapped so it cannot throw: the handler's `failureStrategy` is ABORT, so a Postgres outage must cost the row and never the publish.

The stored document is the whole instance value, in the same shape `/objects/value` returns, and it is what `POST /objects/history` serves over i3X. That is the point of the store — unlike patterns 1 and 2, a coalesce window cannot rescue this one. Asked to reassemble one analysis out of ~38 tag-historian series, the server invented states the object was never in, dropped `result_json` (not historised, so no window brings it back), and collided five leaf names once the folders flattened — `osmo` came back as the module flag rather than the reading.
