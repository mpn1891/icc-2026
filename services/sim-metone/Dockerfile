# Pattern 6 — a simulated MET ONE particle counter, GraphQL over HTTPS.
#
# Transcribed from docs/reference/particle_counter_sim.md and treated as a
# vendor surface: the schema gets nothing added to it. The operator touchscreen
# on 8089 is ours and is not part of that surface.
#
# ariadne + uvicorn + PyJWT + cryptography, all wheels, so nothing compiles here
# — every wheel not built is one fewer thing to go wrong on a conference
# network. See docs/plans/06-poll-metone.md.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY particle_sim ./particle_sim

# Non-root. /config holds the operator's sample point and room condition, and is
# chowned here so the named volume mounted over it inherits the right owner when
# Docker creates it — same reason as the valve services' /data.
#
# /tmp/particle-sim is where the self-signed certificate is written at START,
# not at build: a rebuilt image must not ship a fixed private key to everyone
# who ever pulled it.
RUN useradd --create-home --uid 10005 metone \
    && mkdir -p /config /tmp/particle-sim \
    && chown -R metone:metone /app /config /tmp/particle-sim
USER metone

EXPOSE 8443 8089

# SIGTERM drops uvicorn out of both serve loops. The sample buffer is in memory
# and is GONE on restart, on purpose: sequence numbers start at 1 again while a
# poller's stored bookmark still points past the end, and the poll goes silent
# with every health check green. That is pattern 6's best failure demo, not a
# durability gap to fix.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "-m", "particle_sim", "--port", "8443"]
