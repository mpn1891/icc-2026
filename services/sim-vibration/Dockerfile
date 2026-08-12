# Pattern 1 — simulated wireless vibration gateway.
#
# paho-mqtt is the only dependency on purpose. The waveform synthesis is pure stdlib
# (a few ms for 4096 samples), and every wheel not fetched is one fewer thing to go
# wrong building this on a conference network. See docs/plans/01-native-mqtt.md.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# Non-root. The container talks to one broker and writes nothing.
RUN useradd --create-home --uid 10001 sim && chown -R sim:sim /app
USER sim

# SIGTERM reaches the handler in app.py, which publishes the retained "shutdown" state
# before disconnecting. That is what makes `docker stop` a graceful death and
# `docker kill` an LWT — two different demos out of the same container.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
