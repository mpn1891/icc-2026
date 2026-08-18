# Pattern 1 — smart sample valve assembly, plain MQTT 3.1.1.
#
# paho-mqtt is the only dependency on purpose. The valve is a state machine and the
# configuration page is `http.server` from the standard library, so nothing else needs
# fetching — and every wheel not fetched is one fewer thing to go wrong building this on a
# conference network. See docs/plans/01-native-mqtt.md.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py valve.py webui.py page.html ./

# Non-root. /data holds the commissioned topic/QoS/retain and is chowned here so the named
# volume mounted over it inherits the right owner when Docker creates it.
RUN useradd --create-home --uid 10003 valve \
    && mkdir -p /data \
    && chown -R valve:valve /app /data
USER valve

EXPOSE 8080

# SIGTERM reaches the handler in app.py, which publishes a final state and disconnects
# cleanly — so the broker discards the will and it never fires. `docker stop` is therefore a
# quiet death and `docker kill` is the one that proves the Last Will works.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
