# Topics we publish

Subscribe-only topics are not listed. The Change column is blank on purpose.


| #   | Topic                                                               | Publisher                                        | What lands there                                                                 | Change                    |
| --- | ------------------------------------------------------------------- | ------------------------------------------------ | -------------------------------------------------------------------------------- | ------------------------- |
| 1   | `icc26/site1/upstream/br-201/sample-valve-01/event/badge-scan`      | sim-valve-mqtt                                   | Every badge, granted or denied                                                   | vlv-badge-scanned         |
| 1   | `icc26/site1/upstream/br-201/sample-valve-01/event/sample-complete` | sim-valve-mqtt                                   | A sample that actually ran                                                       | sample-acq-completed      |
| 1   | `icc26/site1/upstream/br-201/sample-valve-01/status`                | sim-valve-mqtt                                   | Online/offline. Retained, and the last-will                                      |                           |
| 1   | `icc26/site1/upstream/br-201/sample-valve-01/telemetry`             | sim-valve-mqtt                                   | Air supply and enclosure temperature, every 5 s                                  |                           |
| 2   | `spBv1.0/ICC26-Site1-UPSTREAM/NBIRTH/SAMPLE-VALVE-02`               | sim-valve-spb                                    | Edge-node birth                                                                  |                           |
| 2   | `spBv1.0/ICC26-Site1-UPSTREAM/NDEATH/SAMPLE-VALVE-02`               | sim-valve-spb                                    | Edge-node death. The MQTT will                                                   |                           |
| 2   | `spBv1.0/ICC26-Site1-UPSTREAM/DBIRTH/SAMPLE-VALVE-02/SV-202`        | sim-valve-spb                                    | Device birth                                                                     |                           |
| 2   | `spBv1.0/ICC26-Site1-UPSTREAM/DDATA/SAMPLE-VALVE-02/SV-202`         | sim-valve-spb                                    | Report-by-exception data                                                         |                           |
| 2   | `spBv1.0/ICC26-Site1-UPSTREAM/DDEATH/SAMPLE-VALVE-02/SV-202`        | sim-valve-spb                                    | Device death                                                                     |                           |
| 3   | `icc26/site1/qc/analyzers/cell-analyzer-01/result`                  | Ignition, event stream `cell-analyzer-result`    | Analyzer result on sample complete                                               | sample-analyzed           |
| 4   | `icc26/site1/qc/lims/sample-result`                                 | Ignition, `lims_webhook`                         | Reviewed record: analyst and pass/fail                                           | sample-results-released   |
| 5   | `icc26/site1/upstream/br-201/batch/event`                           | Ignition, `bes_cdc`                              | CDC insert of `bes.batch_event`. The equipment id is the third-from-last segment |                           |
| 5   | `icc26/site1/audit/bes/batch-event`                                 | Ignition, `bes_cdc`                              | An update or delete of that record                                               |                           |
| 6   | `icc26/site1/env_monitoring/particle-counter-01/result`             | Ignition, event stream `particle-counter-result` | One particle-count analysis                                                      | sample-analyzed           |
| 7   | `icc26/site1/qc/deviation`                                          | Ignition, event stream `lims-review`             | The aggregate, and only when something was violated                              |                           |
| 7   | `icc26/site1/qc/i3x_event_review`                                   | i3x-client                                       | The external reader's verdict. Off until Start publishing is pressed             | i3x-meta-review-completed |


