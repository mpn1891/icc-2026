# Pattern 4 — BioProfile FLEX2 with a callback URL and nothing else.
#
# NO dependencies at all, not even paho. An instrument whose entire outward surface is one
# HTTPS POST needs urllib and ssl, both in the standard library, and the configuration page
# is `http.server`. There is no requirements.txt and no `pip install` layer on purpose:
# every wheel not fetched is one fewer thing to go wrong building this on a conference
# network, and this is the only service in the tree that can make that claim outright.
#
# See services/webhook-novaflex/README.md and docs/04-novaflex-webhook.md.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY app.py webui.py page.html ./

# Non-root. Nothing is persisted: the callback URL, secret and on/off switch are stage
# controls, and a restart is meant to put the instrument back to its factory environment —
# unlike the valves, whose commissioned topic survives in a named volume.
RUN useradd --create-home --uid 10004 flex2 \
    && chown -R flex2:flex2 /app
USER flex2

EXPOSE 8080

STOPSIGNAL SIGTERM

CMD ["python", "-u", "app.py"]
