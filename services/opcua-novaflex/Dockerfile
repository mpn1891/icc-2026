# Pattern 3 — simulated Nova Biomedical BioProfile FLEX2 bioanalyzer, served over OPC UA.
#
# asyncua is the only dependency. It pulls cryptography (for the security policies this
# server does not enable) and a handful of small pure-Python packages; all have manylinux
# wheels, so nothing compiles here and nothing needs a toolchain on a conference network.
# See docs/reference/novaflex2-opcua-model.md.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py webui.py page.html ./

# Non-root. The server listens on 4840 and writes nothing.
RUN useradd --create-home --uid 10003 novaflex && chown -R novaflex:novaflex /app
USER novaflex

# 4840 OPC UA, 8087 the instrument's sample-login touchscreen.
EXPOSE 4840 8087

# SIGTERM sets the stop event, which drops out of the serve loop and closes the endpoint
# cleanly, so a connected Ignition client sees a proper disconnect rather than a timeout.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
