# MM Notes

## Open thoughts

- **Pattern 7 is only deviation / exception**
  - If you take the sample in the wrong operation
  - If LIMS rejects the sample
  - Environment is dirty
- Make pattern 3 generic
- Add bioreactor temperature

## Which patterns have a database behind them

| Pattern | Table(s) | Written by |
| --- | --- | --- |
| 1 native MQTT | `lims.sample` | lims service |
| 2 Sparkplug | — none — | — |
| 3 OPC UA | `lims.sample_result` | lims service |
| 4 webhook | `lims.sample`<br>`lims.sample_result`<br>`lims.webhook_delivery` | lims service |
| 5 CDC | `bes.batch_event` | `bes_batch` (Ignition) |
| 6 poll | `em.reading` | `particle_counter_poll` (Ignition) |
| 7 aggregate | reads `bes.batch_event`<br>reads `em.reading` | nothing — read only |

Only three things speak SQL: `services/lims/app.py` (psycopg), and the two
Ignition scripts `bes_batch` and `particle_counter_poll`. `sample_chain` reads.

**1 and 3 have rows in tables but own no storage.** Those rows exist because
pattern 4's service subscribed to their topics. Kill the lims container and
1 and 3 still run fine — they just stop being recorded.

**5 and 6 are the reverse.** The write is inseparable from the pattern.

### Looks like storage, isn't

- `pg-historian` provider exists, but no tag has `historyEnabled`
- `lims_webhook` dedupes in an in-memory dict (~500 keys), not a table
- `plant.equipment` / `plant.batch` are seeded, read by nothing at runtime
