# The i3X consumer — pattern 7 read back by a stranger. Subscribes to the LIMS
# review objects over the CESMII i3X API and, on each one, asks the same API for
# the particle counter's history around that sample. Optionally publishes what
# it found back onto the broker. See README.md.
#
# FastAPI + requests + paho, all manylinux wheels, so nothing compiles here and
# nothing needs a toolchain on a conference network.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py page.html ./

# Non-root. Two credentials and no more — the gateway login it reads with, and
# a broker account granted the one topic it may publish, which is off unless
# somebody presses the button. Writes nothing to disk and keeps every episode it
# has seen in memory only — restart it and the page is empty, which is correct:
# this is a consumer, not a store.
RUN useradd --create-home --uid 10007 i3xclient && chown -R i3xclient:i3xclient /app
USER i3xclient

EXPOSE 8092

# SIGTERM drops uvicorn out of its serve loop; the subscription worker is a
# daemon and dies with the process. The subscription itself lives in the
# gateway's globals and is torn down on exit, best effort.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
