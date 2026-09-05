"""The GraphQL surface, transcribed from the vendor reference.

`docs/reference/particle_counter_sim.md` is treated the way
`docs/reference/novaflex2-opcua-model.md` is treated for the analyzer: as a vendor
document. The SDL below carries every field that document's `getSamples` query
selects and every argument its mutations take, in the same shape and the same
camelCase, and **nothing else**.

Four things a poller would obviously like are therefore absent, and each absence
costs us something on our side of the boundary rather than theirs:

  * no `since_id` / `since_time` on `getSamples`  -> we walk the cursor and keep
    the watermark in Ignition tags
  * no excursion `status` on the record           -> `metone_poll` computes it
    (the record's own `status` is the *run* status, "COMPLETED" -- the vendor's
    field, not ours)
  * no `location` field                           -> the operator's sample point
    rides in `deviceName`
  * no "give me everything since the restart"     -> the stale-cursor trap stays,
    and is cleared by hand from Tag Explorer

`startSampling`, `stopSampling` and `clearSamples` are implemented because the
vendor documents them. **Ignition calls none of them.** The instrument is started
by a person on the touchscreen, which is the same change-control boundary
pattern 3 keeps with the analyzer's 104 writable bits.
"""

from __future__ import annotations

import logging

from ariadne import MutationType, QueryType, make_executable_schema

from . import auth

LOG = logging.getLogger("particle_sim.schema")

TYPE_DEFS = """
type Measurement {
  units: String
  value: Float
}

type Average {
  average: Measurement
}

type Environment {
  flowRate: Average
  temperature: Average
  humidity: Average
}

type Channel {
  sizeUm: Float!
  particleCount: Int!
}

type Results {
  channels: [Channel!]!
  totalVolume: Measurement
  environment: Environment
}

type Operator {
  name: String
  username: String
  role: String
}

type SampleConfig {
  mode: String
  durationSeconds: Int
  repeatCount: Int
  volume: Measurement
}

type Sample {
  id: ID!
  deviceId: String!
  deviceName: String
  sequenceNumber: Int!
  startedAt: String!
  completedAt: String!
  status: String!
  config: SampleConfig
  results: Results
  operator: Operator
}

type Pagination {
  nextCursor: String
  hasMore: Boolean!
}

type SamplePage {
  samples: [Sample!]!
  pagination: Pagination!
}

input VolumeInput {
  units: String
  value: Float
}

input SamplingInput {
  channels: [Float!]!
  mode: String!
  durationSeconds: Int
  volume: VolumeInput
  repeatCount: Int
  delaySeconds: Int
  pauseSeconds: Int
}

type Query {
  getSamples(cursor: String, limit: Int): SamplePage!
}

type Mutation {
  authenticate(username: String!, password: String!): String
  startSampling(input: SamplingInput!): Boolean!
  stopSampling: Boolean!
  clearSamples: Boolean!
}
"""

query = QueryType()
mutation = MutationType()


def _instrument(info):
    return info.context["instrument"]


def _cfg(info):
    return info.context["config"]


@query.field("getSamples")
def resolve_get_samples(_, info, cursor=None, limit=None):
    return _instrument(info).get_samples(cursor=cursor, limit=limit)


@mutation.field("authenticate")
def resolve_authenticate(_, info, username, password):
    """The one operation reachable without a bearer token.

    Returns the JWT string itself, not an object -- the vendor's schema says
    `authenticate(...): String`, so a client reads `data.authenticate`.
    Bad credentials return null rather than an HTTP error: the request itself
    was fine, the credentials were not.
    """
    cfg = _cfg(info)
    if not auth.check_credentials(cfg, username, password):
        LOG.warning("authentication failed for %r", username)
        return None
    LOG.info("authenticated %r", username)
    return auth.mint(cfg.jwt_secret, username, cfg.operator_role, cfg.token_ttl_s)


@mutation.field("startSampling")
def resolve_start_sampling(_, info, input):
    """Start a run. Honours the vendor's own duration and channel arguments.

    Changing `durationSeconds` here changes the volume every analysis draws, and
    therefore silently changes what every raw-count threshold downstream means.
    That hazard is the vendor's, it is real, and it is why the Ignition-side
    threshold tag records the duration it was chosen for.
    """
    instrument = _instrument(info)
    cfg = _cfg(info)
    duration = input.get("durationSeconds")
    if duration and int(duration) > 0:
        cfg.duration = int(duration)
    channels = input.get("channels")
    if channels:
        cfg.channels = [float(c) for c in channels]
    started = instrument.start()
    LOG.info("startSampling(mode=%s, duration=%s, repeat=%s) -> %s",
             input.get("mode"), cfg.duration, input.get("repeatCount"), started)
    return True


@mutation.field("stopSampling")
def resolve_stop_sampling(_, info):
    _instrument(info).stop()
    return True


@mutation.field("clearSamples")
def resolve_clear_samples(_, info):
    """Admin only, per the vendor doc. The role comes off the verified token."""
    claims = info.context.get("claims") or {}
    if str(claims.get("role") or "").upper() != "ADMIN":
        raise Exception("clearSamples requires an admin-role user")
    return _instrument(info).clear()


def build_schema():
    return make_executable_schema(TYPE_DEFS, query, mutation)
