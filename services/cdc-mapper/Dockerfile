# Pattern 5 — Debezium change event → the backbone envelope.
#
# paho-mqtt is the only dependency. This service holds no state, talks to nothing but the
# broker, and serves no page: it subscribes to Debezium's internal topic and republishes
# each INSERT as the envelope. See docs/plans/05-cdc-turbidity.md.
#
# There is deliberately no shared library with the other services. The ~20-line envelope
# helper is duplicated here — house convention, see services/README.md.

FROM python:3.12-slim

# PYTHONIOENCODING is load-bearing here, not boilerplate. Vendor unit strings contain
# non-ASCII (°C) and the log lines echo values that came from them — checkpoint 8 says to
# check the mapper log, not just the final envelope, and a C-locale stdout would raise
# rather than print.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# Non-root. Nothing is written to disk.
RUN useradd --create-home --uid 10006 mapper && chown -R mapper:mapper /app
USER mapper

# SIGTERM drops out of the keepalive loop and disconnects cleanly. Nothing is buffered:
# each event is published as it is consumed, so a restart loses nothing that Debezium's
# offset file will not resend.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
