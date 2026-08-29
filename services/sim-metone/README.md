# sim-metone — pattern 6, the instrument that never pushes

A simulated **MET ONE particle counter** serving a GraphQL API over HTTPS, plus the operator
touchscreen a real one has on its front.

The API is **not ours**. It is transcribed from
[`docs/reference/particle_counter_sim.md`](../../docs/reference/particle_counter_sim.md) and
treated as a vendor surface, the same way `opcua-novaflex` treats the FLEX2's OPC manual.
Nothing was added to it to make polling easier — see *What is deliberately missing*.

```
https://sim-metone:8443/graphql     from inside the compose network (Ignition)
https://localhost:8443/graphql      from the host (probe scripts, curl -k)
http://localhost:8089/              the instrument's operator touchscreen
```

Build spec: [`docs/plans/06-poll-metone.md`](../../docs/plans/06-poll-metone.md).

## The claim this container exists to make

**Nothing pushes.** The instrument samples on its own clock, holds results in a rolling buffer,
and has no idea Ignition exists. It holds no broker credentials, opens no outbound connection,
and would behave identically with the gateway switched off. Ignition finds out on a 30 s timer.

The honest consequence is a **detection gap**: 10 s of sampling plus up to 30 s of waiting, so a
reading reaches the backbone within ~40 s of the air going out of spec, worst case. Every
message on the wire shows it — `ts` is this instrument's clock, `meta.ingest_ts` is the
gateway's.

## The API

`POST /graphql`. A `GET` answers **401**: the bearer gate runs in front of the schema, and the
schema is configured with `ExplorerHttp405` behind it — so Ariadne's GraphiQL explorer is both
unreachable and disabled. It would otherwise pull a bundle from a CDN, and this stack has to run
with networking disabled.

| Operation | |
|---|---|
| `authenticate(username, password)` | returns a JWT string. `admin` / `password` |
| `getSamples(cursor, limit)` | records **after** the cursor, oldest first, plus a fresh cursor |
| `startSampling(input)` | vendor control surface. **Ignition never calls it** |
| `stopSampling` / `clearSamples` | ditto. `clearSamples` needs an admin-role token |

Everything except `authenticate` needs `Authorization: Bearer <jwt>`, and a missing, malformed
or expired token is an **HTTP 401** rather than a 200 with an `errors` array. That is what makes
a poller's "re-authenticate on 401" branch reachable at all; tokens live 5 minutes, so the
branch is exercised on every demo instead of being dead code that fails in a year.

### The cursor is a watermark the vendor did not call one

`eyJpZCI6MX0=` is base64 of `{"id": 1}` — a keyset marker, not an offset. Because new analyses
get ever-increasing ids appended to the end, *"everything after bookmark 42"* and *"everything
new since I last looked"* are the same sentence. **Paging through a static list and following a
growing one are the identical operation**; the vendor shipped a change feed and documented it as
pagination.

`hasMore: true` means the server truncated at `limit`, so a client drains a backlog by calling
again with the cursor it was just handed. An empty page returns the cursor it was given,
unchanged.

### The trap, and it is deliberate

**The sample buffer does not survive a container restart.** Sequence numbers start at 1 again
while a poller's stored bookmark still says 45, so the server answers *"nothing after 45"* —
correctly — and **the poll goes quiet forever with every health check green.** Measured: the
gateway is up, the timer fires, the HTTP call returns 200, `state/last_error` stays empty, and
nothing reaches the backbone.

Persisting the buffer would remove the best failure demo in pattern 6. Recovery on stage is
clearing `state/cursor` in Ignition's Tag Explorer.

What *does* survive a restart, in the `/config` volume, is the operator's sample point, the room
condition, and whether a run was going.

### What is deliberately missing

| Not added | What it would have made easy | What happens instead |
|---|---|---|
| `since_id` / `since_time` | a one-call poll with no state | Ignition walks the cursor and keeps the watermark in tags |
| an excursion `status` on the record | the flag would arrive with the data | `metone_poll` computes it at ingest |
| a `location` field | the room would be the instrument's own fact | the operator sets it; it rides in `deviceName` |
| a cursor reset | the stale-cursor trap would not exist | the trap stays |

The record's own `status` field is the vendor's — it means the *run* completed (`"COMPLETED"`),
not that the room is clean.

**`deviceName` carrying the location is an overload, and it is named rather than hidden.** The
vendor record has no location field and `deviceName` is its only free text. A portable counter
labelled with its sampling point is what actually happens in the field, so it is defensible; it
is still us putting our meaning in the vendor's box. If the vendor doc ever grows a `location`,
move it.

## The touchscreen on :8089

Every demo surface in this stack is somebody's real product screen. This is the counter's, and
it carries exactly four things:

| Control | Why it exists |
|---|---|
| **Start / Stop** | the operator starts the instrument. Nothing free-runs, for the same reason the Nova stopped free-running on 2026-08-26 |
| **Sample point** | free text, persisted. This is the location, and the operator owns it |
| **Clean / dirty** | the excursion, as a physical gesture rather than a `curl` |
| **Live readout** | the last analysis, its counts, seconds to the next. Proof the instrument runs whether or not anybody is looking |

**There is no excursion light on this screen, and there must not be.** The instrument reports
counts and does not know what a cleanroom limit is. The threshold is Ignition's rule and lives
in exactly one place — `config/excursion_threshold` on the `particle_counter` UDT.

`SEED_SAMPLES: 0` means nothing exists until somebody presses Start, which makes that a pre-show
step that can be forgotten. Two mitigations: the panel shows a large idle banner when the buffer
is empty, and `python tasks.py health` reports the buffer count.

## Counts, and what raw counts cost

At 28.3 LPM for 10 s each analysis draws **~4.717 L**. The clean-room distribution is the vendor
sample response's own numbers scaled to that volume; the dirty room multiplies them by 25.

Measured 2026-08-29, at the 0.5 µm channel:

| | counts per analysis |
|---|---|
| clean | 113 – 137 |
| dirty | 3346 – 4350 |
| Ignition's threshold | **1660** ≡ 352,000 /m³ ≡ ISO 14644-1 Class 7 |

**A raw count means something only while `DURATION` is 10.** Change the duration and every
threshold downstream silently means something else, with nothing to catch it. The threshold tag
records the volume it was chosen for, and `total_volume_l` goes on the wire so a consumer can
normalise even though we did not.

`startSampling` accepts the vendor's `durationSeconds`, so the API can do exactly that damage.
It is the vendor's hazard, it is real, and Ignition never calls it.

## Configuration

Environment first, CLI flags on top. `python -m particle_sim --port 8443` — the reference doc's
quickstart — works, because a README that lies about its own quickstart is a bug in the
transcription.

| Setting | Vendor default | Here | |
|---|---|---|---|
| `PORT` | 8443 | 8443 | HTTPS |
| `SEED_SAMPLES` | 50 | **0** | nothing until Start is pressed |
| `DEVICE_ID` | SIM-001 | **particle-counter-01** | the site's own name for it, so nothing translates a serial into a topic segment |
| `DEVICE_NAME` | Simulator | a sample point | the default only; the operator overrides it |
| `DURATION` | 60 | **10** | a cadence an audience can watch |
| `CHANNELS` | 0.3,0.5,1,3,5,10 | same | ISO 21501 sizes |

Ours, not the vendor's: `PANEL_PORT` (8089), `CONFIG_DIR`, `CERT_DIR`, `BUFFER_MAX` (500, ~83
minutes at 10 s), `FLOW_RATE_LPM`, `DIRTY_MULTIPLIER`, `TOKEN_TTL_S`, `JWT_SECRET`,
`API_USERNAME` / `API_PASSWORD`, `LOG_LEVEL`.

## TLS

**Self-signed, generated at container start**, not at build — a rebuilt image must not ship a
fixed private key to everyone who ever pulled it. SANs: `sim-metone`, `icc26-sim-metone`,
`localhost`, `127.0.0.1`.

Ignition trusts it with `system.net.httpClient(bypass_cert_validation=True)` rather than by
importing a CA into the gateway truststore, and the gateway logs a warning on every request,
which is the right amount of noise for a trade this size. It is the right call for a simulator
on a private compose network and the wrong one in a plant; the honest version of that sentence
is more useful on stage than a truststore ceremony nobody would repeat.

## Layout

```
particle_sim/
  __main__.py   CLI entry point
  config.py     env + flags
  auth.py       JWT mint/verify
  data.py       the buffer, the cursor, the clean/dirty distributions
  schema.py     the SDL, transcribed, and its resolvers
  server.py     TLS, the 401 gate, the GraphQL app, the panel, the sampling loop
  panel.html    the touchscreen
```

Both listeners and the sampling loop are asyncio tasks in one process, so the instrument has one
copy of its state and there is not a lock anywhere in this service.
