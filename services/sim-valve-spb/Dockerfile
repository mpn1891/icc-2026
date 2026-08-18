# Pattern 2 — the same smart sample valve assembly, speaking Sparkplug B v3.0.0.
#
# paho-mqtt is the only dependency, exactly as in sim-valve-mqtt. That is deliberate: the
# two Sparkplug libraries on PyPI both pin paho to the 1.x line, and a container that
# differed from its twin in its MQTT client would confound the one comparison these two
# services exist to make. sparkplug.py encodes the protobuf payload directly and
# `python selftest.py` proves it against Eclipse Tahu's own generated code.
#
# See docs/plans/02-sparkplug-b.md.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py valve.py webui.py sparkplug.py selftest.py page.html ./

# The encoder check runs at build time. A wire-format regression fails the image build
# rather than turning into a device Ignition silently refuses to birth.
RUN python selftest.py

# Non-root. /data holds the commissioned group/node/device ids and is chowned here so the
# named volume mounted over it inherits the right owner when Docker creates it.
RUN useradd --create-home --uid 10004 valve \
    && mkdir -p /data \
    && chown -R valve:valve /app /data
USER valve

EXPOSE 8080

# SIGTERM reaches the handler in app.py, which publishes DDEATH and an explicit NDEATH
# before disconnecting — a clean DISCONNECT discards the will, so a graceful stop has to say
# so itself. `docker kill` is the case where the broker publishes the will instead.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
