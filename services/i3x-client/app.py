#!/usr/bin/env python3
"""The i3X consumer -- pattern 7's question, asked from outside the gateway.

Everything else in this repo is a producer. This is the only thing that reads,
and it reads the way a stranger would have to: over the CESMII i3X 1.0 API, with
a username and a password, holding no database credential, no knowledge of a tag
path, and no copy of anybody's rule.

It can also publish what it finds, and that -- and only that -- is what the
broker credential below is for. It is off by default and it is a button on the
page, because the read path standing entirely on one API login is the claim
being made, and a credential quietly present is a claim quietly weaker.

**It is the argument the whole demo is for, made in one loop:**

  1. `GET /objecttypes`, then `GET /objects?typeElementId=...` -- find the review
     objects and the particle counter by *type*, by browsing. Nothing here is
     told where anything lives, and no element id is ever computed locally.
  2. `POST /subscriptions` + `/subscriptions/register` -- watch those reviews.
  3. `POST /subscriptions/stream` -- Server-Sent Events; the gateway pushes the
     whole review object the moment an analyst signs one off.
  4. `POST /objects/history` -- on the particle counter, around *that sample's*
     instant, and read `status` off the nearest reading.

Four systems contributed to that answer -- a valve, an analyzer, a LIMS and a
particle counter -- and this process has never heard of any of them.

**It computes nothing, and that is deliberate.** `status` reads `normal` or
`excursion` because `particle_counter_poll` compared the 0.5 um count against
`config/excursion_threshold`, which is the only copy of the cleanroom limit in
the stack. This app reads that flag. It holds no threshold, does no arithmetic
on counts, and if it ever grew a comparison there would be two copies of the
rule and two copies drift. Same position `sample_chain` takes inside the
gateway, for the same reason -- docs/00-architecture.md, "Derived flags travel
with the fact that produced them".

**What this proves that the in-gateway switch cannot.** `sample_chain` can flip
`CONTEXT_SOURCE` between `sql` and `i3x` and produce the same composite either
way, which shows the facts are *available* over the API. It does not show they
are available to somebody who is not the gateway. This does: another container,
another language, another HTTP client, no shared credential but one API login,
and the same two facts come back.

**It also writes, once you let it.** Everything above is the read path, and the
read path is the argument. The publish is the sentence after it: with the button
on the page turned on, each finding goes back onto the backbone as
`icc26/site1/qc/i3x-meta-review-completed` -- the verdict, the environmental reading it
was drawn from, and enough identity to correlate. That takes a **second
credential**, and it is the only one this process holds beyond the API login:
reading the model needs a gateway account, putting anything back needs a broker
account, and the two are separate grants issued by separate systems. Browsing
the whole model bought no right whatsoever to speak on it. The default is
**off**, so the paragraph above stays true until somebody deliberately stops it
being true.

**The button stops the publish, not the connection.** The broker session is held
from startup either way, exactly as pattern 4's drainer keeps writing its outbox
while delivery is paused: the toggle governs what this client *says*, not what
it is holding, and doing it the other way would put a reconnect between the
button and the first message in front of an audience. A withheld finding is
counted, and the document that was not sent is kept beside it on the page --
"we chose not to say this" and "we had nothing to say" must not look the same.

**Every verdict publishes, `in specification` included.** 07's `icc26/site1/qc/deviation`
is a gate: it fires only when something is wrong, which is correct for a topic
called deviation. This one is a review log, and a consumer of it wants to know
that a sample was looked at and found in specification -- a different question, and one a
topic that only ever carries bad news cannot answer.

**Two of 07's rules are copied here on purpose, and no others.**

  * The sample instant is `collection.sample_completion` -- the valve close,
    pattern 1's fact, and the only candidate that describes the *sample* rather
    than the analysis or the review. `collected_at` stands in behind it.
  * The reading is the **nearest either side**, no tolerance, age always
    reported. A reading three seconds after the valve closed is better evidence
    than one twenty-five seconds before, so the search is not restricted to the
    past, and there is no cutoff at which this refuses to answer: it reports the
    nearest reading and its age, and lets the reader judge.

They are copied because the exercise is a third party reaching 07's answer, and
a third party using a different axis would be answering a different question.

**The window is smaller than 07's, and only for the screen.**
`/objects/history` is a range query and must be given bounds;
`sample_chain.EM_WINDOW_S` is an hour either side, which at the counter's
ten-second cadence is about 720 readings -- correct, and unreadable as raw JSON
on a page whose whole purpose is to show the raw JSON. `HISTORY_WINDOW_S`
defaults to five minutes, which is sixty. The nearest-either-side rule is
identical; only how far it may reach differs, and every episode on the page
names the window it used.

**Certificate validation is off**, exactly as `i3x_client` turns it off inside
the gateway and for the same reason: the gateway's certificate is self-signed
with `localhost` in its SAN and this container reaches it as `ignition`. Right
on a demo network, wrong in a plant, so it is logged at startup every single
time rather than hidden in a constant.

House style from services/lims/app.py: `_env` helpers, a `Config` class,
docstrings that say why. FastAPI in the main thread, the subscription worker on
its own.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import paho.mqtt.client as mqtt
import requests
import urllib3
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")

LOG = logging.getLogger("i3x-client")


# -- config -------------------------------------------------------------------

def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """`1/true/yes/on`, any case. Everything else is false, junk included.

    Deliberately not a lenient parse. This flag decides whether this process
    puts messages on somebody else's backbone, and a typo must fail closed.
    """
    return _env(name, "true" if default else "false").lower() in ("1", "true", "yes", "on")


class Config:
    """Every knob, read once at startup so the values are visible in one place."""

    def __init__(self) -> None:
        # The gateway on the compose network. NOT :8088 -- Ignition 302s that to
        # :8043, and a redirect across schemes is a class of failure that reads
        # as an empty body rather than as an error anybody could act on.
        self.base_url = _env("I3X_BASE_URL",
                             "https://ignition:8043/system/webdev/i3x").rstrip("/")
        self.username = _env("I3X_USERNAME", "admin")
        self.password = _env("I3X_PASSWORD", "password")
        self.http_port = _env_int("HTTP_PORT", 8092)

        # The search radius either side of the sample instant. See the module
        # docstring for why this is smaller than 07's hour.
        self.history_window_s = _env_int("HISTORY_WINDOW_S", 300)

        # How long to wait before rebuilding after the stream drops. A gateway
        # restart drops every subscription -- they live in gateway globals -- so
        # reconnecting means creating a new one, not resuming the old.
        self.reconnect_s = _env_int("RECONNECT_S", 5)

        # Bounds on what the page can show. This process keeps nothing: restart
        # it and the page is empty, which is correct for a consumer.
        self.max_episodes = _env_int("MAX_EPISODES", 25)
        self.max_body_chars = _env_int("MAX_BODY_CHARS", 24000)

        self.log_level = _env("LOG_LEVEL", "INFO").upper()

        # -- the publish side, and it is off unless asked --------------------
        # Everything above this line is the read path. See the module
        # docstring: the read path is the argument, and it is only true while
        # this stays false.
        self.publish_enabled = _env_bool("PUBLISH_ENABLED", False)

        # A *broker* account, from a different system than the login above,
        # granted exactly one topic in compose/chariot/mqtt-users.json. The
        # client id is set so this shows up by name in Chariot's client list --
        # a reader that turned into a writer should be visible as one.
        self.broker_host = _env("BROKER_HOST", "chariot")
        self.broker_port = _env_int("BROKER_PORT", 1883)
        self.mqtt_username = _env("MQTT_USERNAME", "i3x-client")
        self.mqtt_password = _env("MQTT_PASSWORD", "i3x-client")
        self.mqtt_client_id = _env("MQTT_CLIENT_ID", "i3x-client")

        # Beside `qc/deviation` rather than under a device, because a finding is
        # about a *sample* and there is no box it belongs to -- the same reason
        # 07's aggregate is not device-addressed. It is the one topic on this
        # bus whose last segment names a mechanism; the namespace rule
        # (docs/00-architecture.md) forbids that, and the exception is taken
        # knowingly, for the same reason `audit/bes/batch-event` takes one: the
        # mechanism IS the subject. Nothing inside the payload repeats it.
        #
        # No Engine custom namespace matches it, so this lands on the backbone
        # without creating tags and without a second registrant on anybody's
        # Event Stream source topic.
        self.publish_topic = _env("PUBLISH_TOPIC", "icc26/site1/qc/i3x-meta-review-completed")
        self.publish_qos = _env_int("PUBLISH_QOS", 1)


# The two types this client goes looking for, BY DISPLAY NAME on
# `GET /objecttypes`. That is the one thing it knows about the server's model,
# and a type name is the right thing to know: it means br-202's review is picked
# up without this file naming a vessel, and it means no tag path and no element
# id is ever spelled out here.
REVIEW_TYPE_NAME = "lims_review"
COUNTER_TYPE_NAME = "particle_counter"

# The flag's two values, as `particle_counter_poll` writes them. Read, never
# recomputed -- see the module docstring.
STATUS_EXCURSION = "excursion"
STATUS_NORMAL = "normal"


# -- small helpers ------------------------------------------------------------

def _iso(when: datetime) -> str:
    """ISO-8601 UTC with milliseconds -- the format the server parses and emits.

    `i3x.ignition.DATE_FORMAT` is "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", and
    `startTime`/`endTime` are rejected with a 400 in any other shape.
    """
    when = when.astimezone(timezone.utc)
    return when.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (when.microsecond // 1000)


def _instant(raw: Any) -> Optional[datetime]:
    """A DateTime out of an i3X document, whichever encoding it arrived in.

    **Epoch milliseconds is what this server actually sends** -- a DateTime tag
    goes out through Ignition's own JSON encoder as a bare number, which is not
    part of the spec and is worth knowing before trusting a string parse. The
    ISO form is accepted too: that is what the LIMS wrote inside the envelope,
    and what a different server might format instead.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _channels(current: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`ch_0_5: 121` back into `[{"size_um": 0.5, "count": 121}, ...]`.

    Read off the keys rather than from a list of sizes held here, so a seventh
    channel added to the instrument arrives on this page without this file being
    edited -- and so this file holds no opinion about which particle sizes
    exist. Ascending by size, which is the order the vendor uses.
    """
    out: List[Dict[str, Any]] = []
    for key, count in current.items():
        if not key.startswith("ch_") or count is None:
            continue
        try:
            size = float(key[3:].replace("_", "."))
        except ValueError:
            continue
        out.append({"size_um": size, "count": count})
    out.sort(key=lambda channel: channel["size_um"])
    return out


# -- one HTTP exchange, kept whole --------------------------------------------

class Call:
    """One request and its response, recorded so the page can show it raw.

    The point of this application is not the verdict -- `sample_chain` already
    computes that inside the gateway. The point is that the verdict was reached
    over an API anybody can call, so the exchanges *are* the artifact and they
    are kept verbatim, bodies and all, rather than summarised into a status
    line. A failed exchange is kept for the same reason: an empty history and a
    refused connection must never look the same to a reader.
    """

    def __init__(self, method: str, path: str, request_body: Any = None) -> None:
        self.method = method
        self.path = path
        self.request_body = request_body
        self.status: Optional[int] = None
        self.elapsed_ms: Optional[float] = None
        self.response_text = ""
        self.truncated = False
        self.error: Optional[str] = None
        self.at = _iso(datetime.now(timezone.utc))
        self._started = time.monotonic()

    def _stop(self) -> None:
        self.elapsed_ms = round((time.monotonic() - self._started) * 1000.0, 1)

    def finish(self, status: Optional[int], text: str, limit: int) -> "Call":
        self._stop()
        self.status = status
        if len(text) > limit:
            self.response_text = text[:limit]
            self.truncated = True
        else:
            self.response_text = text
        return self

    def fail(self, error: str) -> "Call":
        self._stop()
        self.error = error
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "at": self.at,
            "method": self.method,
            "path": self.path,
            "request": self.request_body,
            "status": self.status,
            "elapsed_ms": self.elapsed_ms,
            "response": self.response_text,
            "truncated": self.truncated,
            "error": self.error,
        }


class Client:
    """The i3X endpoints this consumer uses, and nothing else.

    Every method returns its result *and* the `Call` that produced it, so the
    caller can hand the exchange to the page whether it succeeded or not.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session = requests.Session()
        self.session.auth = (cfg.username, cfg.password)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # See the module docstring. Off deliberately, and said out loud.
        self.session.verify = False

    # transport ---------------------------------------------------------------

    def _call(self, method: str, path: str, body: Any = None,
              timeout: float = 15.0) -> Tuple[Optional[Dict[str, Any]], Call]:
        call = Call(method, path, body)
        url = self.cfg.base_url + path
        try:
            if method == "GET":
                response = self.session.get(url, timeout=timeout)
            else:
                response = self.session.post(url, data=json.dumps(body), timeout=timeout)
        except requests.RequestException as exc:
            # A refused connection, a DNS failure, a timeout, a TLS error. All
            # of them are "the server did not answer", and all of them belong on
            # the page rather than in a log nobody is watching.
            return None, call.fail("%s: %s" % (type(exc).__name__, exc))

        call.finish(response.status_code, response.text, self.cfg.max_body_chars)
        try:
            document = response.json()
        except ValueError:
            return None, call
        return (document if isinstance(document, dict) else None), call

    # endpoints ---------------------------------------------------------------

    def info(self) -> Tuple[Optional[Dict[str, Any]], Call]:
        """`GET /info`: the one endpoint that is not behind auth, and so the
        cheapest proof that the server is up and this client can reach it."""
        document, call = self._call("GET", "/info")
        return (document or {}).get("result"), call

    def object_types(self) -> Tuple[List[Dict[str, Any]], Call]:
        """`GET /objecttypes`: every type the server publishes, with a display
        name this client can recognise and an elementId it can filter on."""
        document, call = self._call("GET", "/objecttypes")
        result = (document or {}).get("result")
        return (result if isinstance(result, list) else []), call

    def objects(self, type_element_id: Optional[str] = None
                ) -> Tuple[List[Dict[str, Any]], Call]:
        """`GET /objects`, optionally filtered to one type.

        **No element id is computed locally anywhere in this file.** The
        gateway's own `i3x_client` does compute them, from the tag path, and
        flags it as a shortcut the spec discourages -- an elementId is meant to
        be opaque, and a client that browses for it is the one that would still
        work against a server somebody else wrote. The query parameter is
        `typeElementId`, not `typeId`; the wrong name is accepted silently and
        returns the entire address space unfiltered.
        """
        path = "/objects"
        if type_element_id:
            path = path + "?typeElementId=" + type_element_id
        document, call = self._call("GET", path)
        result = (document or {}).get("result")
        return (result if isinstance(result, list) else []), call

    def value(self, element_id: str) -> Tuple[Optional[Dict[str, Any]], Call]:
        """`POST /objects/value`: one object's live member document."""
        document, call = self._call("POST", "/objects/value",
                                    {"elementIds": [element_id], "maxDepth": 1})
        results = (document or {}).get("results") or []
        if not results or not isinstance(results[0], dict) or not results[0].get("success"):
            return None, call
        return (results[0].get("result") or {}).get("value"), call

    def create_subscription(self, display_name: str) -> Tuple[Optional[str], str, Call]:
        client_id = "i3x-client-" + uuid.uuid4().hex[:8]
        document, call = self._call("POST", "/subscriptions",
                                    {"clientId": client_id, "displayName": display_name})
        result = (document or {}).get("result") or {}
        return result.get("subscriptionId"), client_id, call

    def register(self, client_id: str, subscription_id: str,
                 element_ids: List[str]) -> Tuple[bool, Call]:
        document, call = self._call("POST", "/subscriptions/register", {
            "clientId": client_id,
            "subscriptionId": subscription_id,
            "elementIds": element_ids,
            # 1 is this object's own members. A review has no nested instances,
            # so depth would buy nothing and add noise.
            "maxDepth": 1,
        })
        return bool((document or {}).get("success")), call

    def delete_subscription(self, client_id: str, subscription_id: str) -> None:
        """Best effort, on the way out. A subscription left behind holds tag
        listeners in the gateway until it restarts."""
        try:
            self._call("POST", "/subscriptions/delete",
                       {"clientId": client_id, "subscriptionIds": [subscription_id]},
                       timeout=5.0)
        except Exception:
            pass

    def history(self, element_id: str, start: datetime,
                end: datetime) -> Tuple[Optional[Dict[str, Any]], Call]:
        """`POST /objects/history` over [start, end].

        Both bounds are required -- the server answers 400 without them, which
        is why a window has to be *chosen* rather than avoided.
        """
        document, call = self._call("POST", "/objects/history", {
            "elementIds": [element_id],
            "startTime": _iso(start),
            "endTime": _iso(end),
            "maxDepth": 1,
        }, timeout=30.0)
        results = (document or {}).get("results") or []
        if not results or not isinstance(results[0], dict) or not results[0].get("success"):
            return None, call
        return results[0].get("result"), call

    def stream(self, client_id: str, subscription_id: str) -> requests.Response:
        """`POST /subscriptions/stream`: Server-Sent Events, one event per drain.

        **This is the half that could not be done inside the gateway.** Phase 3b
        of the i3X plan parked the idea of 07 waking on i3X rather than on MQTT,
        because Jython's `system.net.httpClient` buffers a whole response before
        returning and an SSE stream never ends. Out here it is four lines of
        `requests` -- which is the point: the constraint was the gateway's
        scripting environment, not the API.

        Each event is `data: [...]`, a JSON array of
        `{elementId, value, quality, timestamp}`. The server writes
        `: keep-alive` comments while idle, so a silent stream still proves
        itself alive, and at-most-once is what SSE gives -- an update staged
        while nothing is connected is dropped, not replayed. `/subscriptions/sync`
        is the acknowledged mode if that ever matters; for watching a demo it
        does not.
        """
        return self.session.post(
            self.cfg.base_url + "/subscriptions/stream",
            data=json.dumps({"clientId": client_id, "subscriptionId": subscription_id}),
            stream=True,
            headers={"Accept": "text/event-stream"},
            # A connect timeout, and deliberately NO read timeout: an idle
            # subscription is silent but for the keep-alive, and a read timeout
            # here would tear down a perfectly healthy stream every time the
            # plant went quiet.
            timeout=(15.0, None),
        )


# -- the one thing this client writes -----------------------------------------

class Publisher:
    """The finding, back onto the backbone as `icc26/site1/qc/i3x-meta-review-completed`.

    Everything else in this file reads. This is the half that writes, and it is
    the reason the process holds a second credential -- a broker account, issued
    by a different system than the i3X login, granted exactly one topic and
    nothing to subscribe to. Worth saying out loud on stage: an API login let
    this client browse a model nobody had told it about, and it bought no right
    at all to put anything back. Those are separate grants because they are
    separate questions.

    `enabled` gates the publish and nothing else -- the session is held from
    startup either way, the same way pattern 4's drainer keeps filling its
    outbox while delivery is paused. That makes Enable instant rather than a
    reconnect in front of an audience, and it makes the honest statement the
    precise one: the credential is held, the toggle decides whether it is used.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self._enabled = threading.Event()
        if cfg.publish_enabled:
            self._enabled.set()
        self.client: Optional[mqtt.Client] = None
        self.connected = False
        self.counts = {"published": 0, "withheld": 0, "failed": 0}
        self.last: Optional[Dict[str, Any]] = None

    # lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        """Connect, whatever the toggle says. See the class docstring."""
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.cfg.mqtt_client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self.cfg.mqtt_username, self.cfg.mqtt_password)
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        self.client = client
        # `connect_async` + `loop_start`, so a broker that is down at boot is a
        # red light on the page rather than a container that will not start.
        client.connect_async(self.cfg.broker_host, self.cfg.broker_port, keepalive=30)
        client.loop_start()
        LOG.info("mqtt connecting to %s:%s as %s -- publish is %s, topic %s",
                 self.cfg.broker_host, self.cfg.broker_port, self.cfg.mqtt_username,
                 "ON" if self.is_enabled() else "off", self.cfg.publish_topic)

    def stop(self) -> None:
        if self.client is None:
            return
        self.client.loop_stop()
        self.client.disconnect()

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties) -> None:
        # paho 2.x ReasonCode compares to int but is not itself an int.
        if reason_code != 0:
            self.connected = False
            # The likely cause, and worth naming: `mqtt-users.json` seeds on
            # FIRST RUN ONLY, so on a Chariot volume older than this account the
            # credential simply does not exist. See compose/chariot/README.md.
            LOG.error("mqtt connect refused: %s -- is there an %s account on this broker?",
                      reason_code, self.cfg.mqtt_username)
            return
        self.connected = True
        # Subscribes to nothing, deliberately. This client's inbound side is the
        # i3X subscription; a broker subscription here would be a second way in
        # and would make the page ambiguous about which one it woke on.
        LOG.info("mqtt connected as %s", self.cfg.mqtt_username)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected = False
        LOG.warning("mqtt disconnected: %s", reason_code)

    # the toggle --------------------------------------------------------------

    def is_enabled(self) -> bool:
        return self._enabled.is_set()

    def set_enabled(self, on: bool) -> None:
        if on:
            self._enabled.set()
        else:
            self._enabled.clear()
        LOG.info("publishing %s", "enabled" if on else "disabled")

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.is_enabled(),
                "connected": self.connected,
                "broker": "%s:%d" % (self.cfg.broker_host, self.cfg.broker_port),
                "username": self.cfg.mqtt_username,
                "topic": self.cfg.publish_topic,
                "qos": self.cfg.publish_qos,
                "counts": dict(self.counts),
                "last": dict(self.last) if self.last else None,
            }

    # the message -------------------------------------------------------------

    def document(self, episode: Dict[str, Any]) -> Dict[str, Any]:
        """The finding, in the envelope every other pattern on this bus uses.

        `ts` and `values`, and nothing else -- no `seq`, no `source`, no `meta`,
        and nothing naming the mechanism. The topic is the provenance, which is
        the rule docs/00-architecture.md states for patterns 4 and 7, and it
        holds here even though this topic's own last segment happens to name
        i3X: a subscriber already knows where this was read.

        `ts` is the sample instant and `values.assessed_at` is when this client
        reached the verdict. The gap between them is the document's entire
        provenance, the same reading 07's envelope invites.

        **What is in here is what this client contributed** -- the verdict, and
        the reading it was drawn from, plus enough identity to correlate. The
        analyzer's numbers are deliberately absent: they are already on the bus
        as pattern 3 and again inside pattern 4's review, and a third copy
        arriving from a *reader* would be this process putting its name on
        somebody else's measurement.
        """
        review = episode.get("review") or {}
        return {
            "ts": episode.get("sample_instant"),
            "values": {
                "verdict": episode.get("verdict"),

                # Populated on `unverifiable`, and then always. A verdict this
                # client declined to reach says why in the message rather than
                # leaving a subscriber to infer it from a null.
                "reason": episode.get("reason"),

                "sample_id": review.get("sample_id"),
                "equipment_id": review.get("equipment_id"),
                "disposition": review.get("disposition"),
                "analyst": review.get("analyst"),
                "assessed_at": episode.get("received_at"),

                # How the verdict was arrived at, so it can be audited without
                # re-running the query: which member gave the time axis, how far
                # the search was allowed to reach, and how many rows it saw. A
                # `in specification` drawn from one row in a 600 s span is a different
                # statement from one drawn from sixty, and the difference has to
                # travel with it.
                "sample_instant_from": episode.get("sample_instant_from"),
                "history_window_s": episode.get("window_s"),
                "rows_returned": episode.get("rows_returned"),

                # Always present, null when nothing was found -- the shape does
                # not change with the outcome, so a consumer reads the same key
                # either way and a gap is never a missing field. `age_s` inside
                # it is what decides whether any of this is evidence at all.
                "environment": episode.get("environment"),
            },
        }

    def announce(self, episode: Dict[str, Any]) -> None:
        """Publish the finding, or record why not. Sets `episode["publish"]`.

        **A startup read is never published.** Those episodes are this client
        asking rather than the server telling, and every one of them would go
        out again on every container restart -- a subscriber would watch the
        same finding announced as new each time this process was rebuilt. The
        loop this topic exists for starts at an analyst signing something off.
        """
        if episode.get("trigger") != "push":
            episode["publish"] = {
                "state": "skipped",
                "detail": "a startup read of the current value, not a review -- see announce()",
            }
            return

        record: Dict[str, Any] = {
            "topic": self.cfg.publish_topic,
            "qos": self.cfg.publish_qos,
            "at": _iso(datetime.now(timezone.utc)),
            # Built either way. What was withheld is as interesting as what was
            # sent, and showing it is the difference between an audience taking
            # the shape of the message on trust and reading it.
            "document": self.document(episode),
            "error": None,
        }
        episode["publish"] = record

        if not self.is_enabled():
            record["state"] = "withheld"
            record["detail"] = "publishing is off -- this is the document that was not sent"
            self._count("withheld", record)
            return

        try:
            if self.client is None:
                raise RuntimeError("no MQTT client; start() was never called")
            info = self.client.publish(self.cfg.publish_topic,
                                       json.dumps(record["document"]),
                                       qos=self.cfg.publish_qos, retain=False)
            # Waited on rather than fired and forgotten, because the page says
            # "published" and that word has to mean the broker took it. This
            # raises on both halves of the failure -- a publish refused outright
            # (no connection, over the queue) and a QoS 1 PUBACK that never
            # arrived -- so there is no silent third case. Five seconds is long
            # for a compose network and short enough not to visibly stall the
            # subscription thread.
            info.wait_for_publish(timeout=5.0)
            record["state"] = "published"
            record["mid"] = info.mid
            self._count("published", record)
        except Exception as exc:
            record["state"] = "failed"
            record["error"] = "%s: %s" % (type(exc).__name__, exc)
            self._count("failed", record)
            LOG.warning("publish to %s failed -- %s", self.cfg.publish_topic, record["error"])

    def _count(self, outcome: str, record: Dict[str, Any]) -> None:
        with self._lock:
            self.counts[outcome] += 1
            self.last = {"state": record.get("state"), "at": record.get("at"),
                         "error": record.get("error")}


# -- what the page is looking at ----------------------------------------------

class State:
    """Everything the page shows, behind one lock.

    Written by the worker thread, read by uvicorn's. A deque with a maxlen is
    the whole retention policy: this is a consumer, and a consumer that quietly
    became a store would be a worse demo and a worse citizen.
    """

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()
        self.connection: Dict[str, Any] = {"state": "starting", "detail": "", "since": None}
        self.server: Optional[Dict[str, Any]] = None
        self.watching: List[Dict[str, str]] = []
        self.counter: Optional[Dict[str, str]] = None
        self.session_calls: deque = deque(maxlen=16)
        self.episodes: deque = deque(maxlen=cfg.max_episodes)
        self.counts = {"pushes": 0, "episodes": 0, "sessions": 0, "keepalives": 0}
        self._next_id = 1

    def set_connection(self, state: str, detail: str = "") -> None:
        with self._lock:
            self.connection = {"state": state, "detail": detail,
                               "since": _iso(datetime.now(timezone.utc))}
        LOG.info("connection: %s%s", state, (" -- " + detail) if detail else "")

    def start_session(self) -> None:
        with self._lock:
            self.session_calls.clear()
            self.counts["sessions"] += 1

    def record_session_call(self, call: Call) -> None:
        with self._lock:
            self.session_calls.append(call.as_dict())

    def set_discovery(self, server: Optional[Dict[str, Any]],
                      watching: List[Dict[str, str]],
                      counter: Optional[Dict[str, str]]) -> None:
        with self._lock:
            self.server = server
            self.watching = watching
            self.counter = counter

    def note_push(self, n: int = 1) -> None:
        with self._lock:
            self.counts["pushes"] += n

    def note_keepalive(self) -> None:
        with self._lock:
            self.counts["keepalives"] += 1

    def add_episode(self, episode: Dict[str, Any]) -> None:
        with self._lock:
            episode["id"] = self._next_id
            self._next_id += 1
            self.counts["episodes"] += 1
            self.episodes.appendleft(episode)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "now": _iso(datetime.now(timezone.utc)),
                "connection": dict(self.connection),
                "server": self.server,
                "watching": list(self.watching),
                "counter": self.counter,
                "window_s": self.cfg.history_window_s,
                "base_url": self.cfg.base_url,
                "counts": dict(self.counts),
                "session_calls": list(self.session_calls),
                "episodes": list(self.episodes),
            }


# -- reading a review, and answering the question about it --------------------

def _review(value: Dict[str, Any]) -> Dict[str, Any]:
    """The review object's members, plus the envelope inside `document`.

    `document` is the whole pattern-4 message as it went onto the backbone,
    carried as a member so that an i3X consumer and an MQTT consumer are looking
    at the same bytes. This client reads the *members* for everything it needs
    and keeps the envelope only to show it -- which is the honest way round: the
    members are what makes this an object rather than a message with extra
    steps.
    """
    envelope: Optional[Dict[str, Any]] = None
    raw = value.get("document")
    if isinstance(raw, dict):
        envelope = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            envelope = parsed if isinstance(parsed, dict) else None
        except ValueError:
            envelope = None

    return {
        "sample_id": value.get("sample_id"),
        "equipment_id": value.get("equipment_id"),
        "disposition": value.get("disposition"),
        "analyst": value.get("analyst"),
        "badge_holder": value.get("badge_holder"),
        "cycle_result": value.get("cycle_result"),
        "ts": _maybe_iso(value.get("ts")),
        "collected_at": _maybe_iso(value.get("collected_at")),
        "verified_at": _maybe_iso(value.get("verified_at")),
        "sample_completion": _maybe_iso(value.get("sample_completion")),
        "glucose_g_l": value.get("glucose_g_l"),
        "lactate_g_l": value.get("lactate_g_l"),
        "osmolality_mosm_kg": value.get("osmolality_mosm_kg"),
        "document": envelope,
    }


def _maybe_iso(raw: Any) -> Optional[str]:
    when = _instant(raw)
    return _iso(when) if when else None


def _sample_instant(value: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[str]]:
    """The instant material left the reactor -- the axis of the whole lookup.

    `sample_completion` is the valve close: pattern 1's fact, and the only
    candidate that describes the *sample* rather than the analysis or the
    review. `collected_at` (when the analyzer ran) stands in behind it only so
    that a review which somehow reached this client without a close time still
    resolves to a real instant instead of to now(). 07's rule, copied
    deliberately -- see the module docstring.

    Both are read off the object's own members rather than out of the envelope
    inside `document`, because a member is the thing an i3X client is entitled
    to; the envelope is a courtesy.
    """
    for name in ("sample_completion", "collected_at"):
        when = _instant(value.get(name))
        if when is not None:
            return when, name
    return None, None


def _nearest(result: Dict[str, Any], instant: datetime
             ) -> Tuple[Optional[Dict[str, Any]], Optional[float], int]:
    """The reading nearest the sample instant, either side. (current, offset_s, rows).

    **Nearest to the row's own `current.ts`**, which is the instrument's
    completedAt -- not to the row's envelope timestamp. They are the same
    instant now that the counter's history comes from `em.reading` rather than
    from the tag historian, but reading the member keeps this correct if that
    ever changes back.

    A row whose value carries no `current` is skipped rather than read as a
    reading with every field absent. That is the shape the historian used to
    return, and treating it as a reading is exactly how a good measurement gets
    reported as an unverifiable one.
    """
    rows = result.get("values") or []
    best: Optional[Dict[str, Any]] = None
    best_offset: Optional[float] = None
    for row in rows:
        value = row.get("value")
        if not isinstance(value, dict):
            continue
        current = value.get("current")
        if not isinstance(current, dict):
            continue
        when = _instant(current.get("ts")) or _instant(row.get("timestamp"))
        if when is None:
            continue
        offset = (when - instant).total_seconds()
        if best_offset is None or abs(offset) < abs(best_offset):
            best, best_offset = current, offset
    return best, best_offset, len(rows)


def _assess(cfg: Config, client: Client, state: State, trigger: str,
            label: str, element_id: str, value: Dict[str, Any]) -> Dict[str, Any]:
    """One review in, one episode out: the verdict and every call that made it.

    The verdict has three values and only one of them is arithmetic-free by
    accident. `in specification` and `out of specification` are `status` mapped
    from `normal` and `excursion`;
    `unverifiable` is this client declining to answer, and it says why in a
    sentence rather than leaving a null for somebody to interpret. A sample
    whose room cannot be evidenced cannot be released, so "I could not find out"
    has to be as loud as "it was out of specification".
    """
    calls: List[Dict[str, Any]] = []
    review = _review(value)
    instant, instant_from = _sample_instant(value)

    episode: Dict[str, Any] = {
        "trigger": trigger,
        "received_at": _iso(datetime.now(timezone.utc)),
        "object": label,
        "element_id": element_id,
        "review": review,
        "sample_instant": _iso(instant) if instant else None,
        "sample_instant_from": instant_from,
        "window_s": cfg.history_window_s,
        "rows_returned": 0,
        "environment": None,
        "verdict": "unverifiable",
        "reason": None,
        "calls": calls,
    }

    if not review["sample_id"]:
        episode["reason"] = ("the review object carries no sample_id -- nothing has been "
                             "reviewed on this vessel yet")
        return episode

    if instant is None:
        episode["reason"] = "the review carried no usable sample instant"
        return episode

    counter = state.counter
    if not counter:
        episode["reason"] = "no particle_counter object was found on this server"
        return episode

    span = timedelta(seconds=cfg.history_window_s)
    result, call = client.history(counter["elementId"], instant - span, instant + span)
    calls.append(call.as_dict())

    if result is None:
        episode["reason"] = ("the history request for %s did not return a result -- see the "
                             "exchange below" % counter["label"])
        return episode

    current, offset_s, rows = _nearest(result, instant)
    episode["rows_returned"] = rows
    if current is None or offset_s is None:
        episode["reason"] = ("no reading for %s within %ss of the sample"
                             % (counter["label"], cfg.history_window_s))
        return episode

    status = current.get("status")
    episode["environment"] = {
        # First, and deliberately. A reading is evidence about this sample only
        # if it is close to it in time, and burying its age inside the block
        # would let a forty-minute-old count read as current.
        "age_s": round(abs(offset_s), 1),
        "nearest_side": "after" if offset_s >= 0 else "before",
        "device_id": counter["label"],
        "status": status,
        "occurred_at": _maybe_iso(current.get("ts")),
        "location": current.get("location"),
        "operator": current.get("operator"),
        "sequence_number": current.get("sequence_number"),
        "total_volume_l": current.get("total_volume_l"),
        "channels": _channels(current),
        "conditions": current.get("conditions"),
    }

    if status == STATUS_EXCURSION:
        episode["verdict"] = "out of specification"
    elif status == STATUS_NORMAL:
        episode["verdict"] = "in specification"
    else:
        episode["reason"] = ("the nearest reading carries status %r, which this client does "
                             "not recognise and will not interpret" % (status,))
    return episode


# -- the worker ---------------------------------------------------------------

def _label_for(obj: Dict[str, Any], parents: Dict[str, str]) -> str:
    """A name a person can read. Both review objects are called `last_review`,
    so the vessel has to come from the parent -- which is the composition edge
    the server derives from UDT nesting, and the reason the review was nested
    inside the bioreactor in the first place."""
    name = str(obj.get("displayName") or "?")
    parent = parents.get(str(obj.get("parentId") or ""))
    return "%s/%s" % (parent, name) if parent else name


def _discover(cfg: Config, client: Client, state: State) -> Optional[Dict[str, Any]]:
    """`/info`, the type list, and the two object listings. Returns the plan.

    Every one of these calls is kept on the page. A demo where the audience sees
    the client *find* the objects is a different demo from one where the client
    already knew.
    """
    info, call = client.info()
    state.record_session_call(call)
    if info is None:
        state.set_connection("error", "GET /info did not answer -- is the i3x project enabled?")
        return None

    types, call = client.object_types()
    state.record_session_call(call)
    by_name = {str(t.get("displayName")): t for t in types if isinstance(t, dict)}

    review_type = by_name.get(REVIEW_TYPE_NAME)
    counter_type = by_name.get(COUNTER_TYPE_NAME)
    if not review_type or not counter_type:
        state.set_connection("error", "this server publishes no %s or no %s type"
                             % (REVIEW_TYPE_NAME, COUNTER_TYPE_NAME))
        return None

    # The whole address space once, only to put a vessel name on each review.
    everything, call = client.objects()
    state.record_session_call(call)
    parents = {str(o.get("elementId")): str(o.get("displayName"))
               for o in everything if isinstance(o, dict)}

    reviews, call = client.objects(str(review_type.get("elementId")))
    state.record_session_call(call)
    counters, call = client.objects(str(counter_type.get("elementId")))
    state.record_session_call(call)

    if not reviews:
        state.set_connection("error", "no %s objects on this server" % REVIEW_TYPE_NAME)
        return None
    if not counters:
        state.set_connection("error", "no %s objects on this server" % COUNTER_TYPE_NAME)
        return None

    watching = [{"elementId": str(o.get("elementId")), "label": _label_for(o, parents)}
                for o in reviews]
    # One counter serves the room, which is why `sample_chain` holds its device
    # id as a constant rather than deriving it from the sample. If a second one
    # ever appears, the choice stops being obvious and this is where it shows.
    counter = {"elementId": str(counters[0].get("elementId")),
               "label": str(counters[0].get("displayName"))}
    if len(counters) > 1:
        LOG.warning("%d particle_counter objects; watching %s",
                    len(counters), counter["label"])

    state.set_discovery(info, watching, counter)
    return {"info": info, "watching": watching, "counter": counter}


def _seed(cfg: Config, client: Client, state: State, publisher: Publisher,
          watching: List[Dict[str, str]], seen: set) -> None:
    """Read each review's live value once, so the page is not empty on arrival.

    Marked `startup` rather than `push`, because it is this client asking rather
    than the server telling -- and the difference between those two is most of
    what the page is for. It also does the history lookup, so the whole loop is
    demonstrated before anybody has approved anything.
    """
    for target in watching:
        value, call = client.value(target["elementId"])
        state.record_session_call(call)
        if not isinstance(value, dict):
            continue
        episode = _assess(cfg, client, state, "startup", target["label"],
                          target["elementId"], value)
        key = (target["elementId"], episode["review"].get("sample_id"),
               episode["review"].get("verified_at"))
        seen.add(key)
        # Records why it published nothing rather than saying nothing about it.
        # `announce` refuses every startup episode -- see its docstring.
        publisher.announce(episode)
        state.add_episode(episode)


def _consume(cfg: Config, client: Client, state: State, publisher: Publisher,
             client_id: str, subscription_id: str, labels: Dict[str, str],
             seen: set) -> None:
    """Read the SSE stream until it ends, turning each push into an episode."""
    response = client.stream(client_id, subscription_id)
    if response.status_code != 200:
        raise RuntimeError("stream refused with HTTP %s: %s"
                           % (response.status_code, response.text[:200]))

    state.set_connection("streaming", "subscription %s" % subscription_id)
    for line in response.iter_lines(decode_unicode=True):
        if line is None:
            continue
        line = line.strip()
        if not line:
            continue
        if line.startswith(":"):
            # `: keep-alive`. Proof of life on an idle plant, and the only thing
            # that distinguishes a quiet stream from a dead one.
            state.note_keepalive()
            continue
        if not line.startswith("data:"):
            continue
        try:
            updates = json.loads(line[5:].strip())
        except ValueError:
            LOG.warning("unparseable SSE payload: %s", line[:200])
            continue
        if not isinstance(updates, list):
            continue
        state.note_push(len(updates))

        for update in updates:
            if not isinstance(update, dict):
                continue
            element_id = str(update.get("elementId"))
            if element_id not in labels:
                continue
            value = update.get("value")
            if not isinstance(value, dict):
                continue
            # **One review is one push**, because Ignition coalesces the
            # fourteen members `model_feed.write_review` writes in a single
            # writeBlocking into one UDT change event. This guard is for the day
            # that stops being true: a second push for a review already seen
            # would otherwise re-ask the same question and double the page.
            key = (element_id, value.get("sample_id"), _maybe_iso(value.get("verified_at")))
            if key in seen:
                continue
            seen.add(key)
            episode = _assess(cfg, client, state, "push",
                              labels[element_id], element_id, value)
            # After the verdict and before the page, so an episode never
            # appears without saying what was done with it.
            publisher.announce(episode)
            state.add_episode(episode)


def worker(cfg: Config, state: State, publisher: Publisher) -> None:
    """Discover, subscribe, stream, and rebuild the whole thing when it breaks.

    **Reconnecting means starting over, not resuming.** Subscriptions live in
    the gateway's script globals and are torn down with it, so after a gateway
    restart the old subscriptionId resolves to nothing and a `sync` or `stream`
    against it answers 404. Re-running discovery as well is deliberate: element
    ids are the server's to change, and a client that cached them across a
    restart would be asserting something the spec does not promise.
    """
    client = Client(cfg)
    seen: set = set()

    while True:
        client_id = subscription_id = None
        try:
            state.start_session()
            state.set_connection("connecting", cfg.base_url)
            plan = _discover(cfg, client, state)
            if plan is None:
                time.sleep(cfg.reconnect_s)
                continue

            watching = plan["watching"]
            labels = {t["elementId"]: t["label"] for t in watching}

            subscription_id, client_id, call = client.create_subscription("icc26 i3x consumer")
            state.record_session_call(call)
            if not subscription_id:
                state.set_connection("error", "the server would not create a subscription")
                time.sleep(cfg.reconnect_s)
                continue

            ok, call = client.register(client_id, subscription_id,
                                       [t["elementId"] for t in watching])
            state.record_session_call(call)
            if not ok:
                state.set_connection("error", "the server would not register the review objects")
                time.sleep(cfg.reconnect_s)
                continue

            _seed(cfg, client, state, publisher, watching, seen)
            _consume(cfg, client, state, publisher, client_id, subscription_id,
                     labels, seen)
            state.set_connection("reconnecting", "the stream ended")
        except Exception as exc:
            state.set_connection("reconnecting", "%s: %s" % (type(exc).__name__, exc))
            LOG.warning("session ended -- %s: %s", type(exc).__name__, exc)
        finally:
            if client_id and subscription_id:
                client.delete_subscription(client_id, subscription_id)
        time.sleep(cfg.reconnect_s)


# -- the web app --------------------------------------------------------------

def build_app(cfg: Config, state: State, publisher: Publisher) -> FastAPI:
    app = FastAPI(title="icc26 i3X consumer", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        with open(PAGE_PATH, "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())

    @app.get("/api/state")
    def api_state() -> JSONResponse:
        """Everything the page draws, in one document. The page polls this; it
        does not re-stream SSE to the browser, because two hops of the same
        stream would only make the interesting one harder to see."""
        snapshot = state.snapshot()
        # Merged here rather than held in `State`, so the read side and the
        # write side stay separable in the code the way they are on the page.
        snapshot["publish"] = publisher.status()
        return JSONResponse(snapshot)

    @app.post("/api/publish/enable")
    def publish_enable() -> JSONResponse:
        """Start publishing findings. Nothing already seen is replayed.

        The episodes on the page were withheld at the moment they were
        assessed, and they stay withheld: re-announcing them now would date
        every one of them to this button press. Approve another sample.
        """
        publisher.set_enabled(True)
        return JSONResponse(publisher.status())

    @app.post("/api/publish/disable")
    def publish_disable() -> JSONResponse:
        """Stop publishing. The broker session stays up -- see `Publisher`."""
        publisher.set_enabled(False)
        return JSONResponse(publisher.status())

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        snapshot = state.snapshot()
        connected = snapshot["connection"]["state"] == "streaming"
        # Deliberately 200 either way. A client that cannot reach the gateway is
        # a client doing its job of saying so, and a container restart would
        # only hide the message on the page. The publish side is reported for
        # the same reason and gates nothing: a reader that is not currently
        # allowed to speak is not an unhealthy reader.
        return JSONResponse({"ok": True, "streaming": connected,
                             "episodes": snapshot["counts"]["episodes"],
                             "publishing": publisher.is_enabled(),
                             "broker": publisher.connected})

    return app


def main() -> int:
    cfg = Config()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    LOG.info("i3X consumer -> %s as %s", cfg.base_url, cfg.username)
    LOG.warning("TLS certificate validation is OFF -- the gateway's certificate is "
                "self-signed and names localhost, and this container reaches it as a "
                "compose hostname. Right here, wrong in a plant.")
    LOG.info("history window +/- %ss (sample_chain uses 3600)", cfg.history_window_s)

    state = State(cfg)
    publisher = Publisher(cfg)
    publisher.start()
    if publisher.is_enabled():
        # Said loudly, because it is the one setting that turns this container
        # from a reader into a participant, and a stack that came up publishing
        # because of an env var nobody remembered setting is worth a line.
        LOG.warning("PUBLISH_ENABLED is on -- findings will go to %s from the first "
                    "review onward, with no further confirmation", cfg.publish_topic)

    threading.Thread(target=worker, args=(cfg, state, publisher), daemon=True,
                     name="i3x-subscription").start()

    import uvicorn
    uvicorn.run(build_app(cfg, state, publisher), host="0.0.0.0", port=cfg.http_port,
                log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
