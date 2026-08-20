# Pattern 4 — LIMS approval webhook. Subscribes to analyzer results, holds them
# for a human, POSTs the released sample to Ignition through a transactional
# outbox. See docs/plans/04-lims-webhook.md.
#
# FastAPI + psycopg + paho-mqtt. All have manylinux wheels, so nothing compiles
# here and nothing needs a toolchain on a conference network.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py page.html ./

# Non-root. The server listens on 8000 and writes nothing to disk.
RUN useradd --create-home --uid 10004 lims && chown -R lims:lims /app
USER lims

EXPOSE 8000

# SIGTERM drops uvicorn out of its serve loop; the MQTT loop and the outbox
# drainer are daemons and die with the process. Pending deliveries stay in
# Postgres, which is the whole point of the outbox.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
