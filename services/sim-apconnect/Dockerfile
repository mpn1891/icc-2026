# Patterns 5 and 6 — Anton Paar AP Connect, simulated.
#
# psycopg is the only dependency, on purpose. This service writes rows and serves one
# `http.server` page; it has no MQTT client and never will, because an application that
# could publish would make patterns 5 and 6 pointless. Every wheel not fetched is one fewer
# thing to go wrong building this on a conference network.
# See docs/plans/05-cdc-turbidity.md.

FROM python:3.12-slim

# PYTHONIOENCODING is load-bearing here, not boilerplate. Vendor unit strings contain
# non-ASCII (°C, ², ³, ·) and they have to survive from here to the broker — checkpoint 8.
# The slim image sets no locale, so a bare print of a degree sign can raise on a C-locale
# stdout.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    LANG=C.UTF-8

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py webui.py page.html ./

# Non-root. This service holds no state on disk — the state is the `measurement` table — so
# there is no volume and nothing here needs to be writable.
RUN useradd --create-home --uid 10005 apconnect && chown -R apconnect:apconnect /app
USER apconnect

EXPOSE 8080

# SIGTERM stops the (normally idle) free-run timer and drops out of main(). Nothing is
# buffered: every measurement is committed before POST /measure returns.
STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
