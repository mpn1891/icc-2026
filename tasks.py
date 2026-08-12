#!/usr/bin/env python3
"""Task runner for the ICC 2026 demo stack.

    python tasks.py <task> [args]

This is the single implementation for every platform. It previously existed
twice -- tasks.ps1 for Windows and a Makefile mirroring it for everyone else --
and the mirror silently drifted away from the original, losing exactly the
checks step 1 was spent discovering (COMMISSIONING detection, module version
verification, the Chariot login race). One runner, one place for that knowledge.

Standard library only. No pip install, no venv. Python 3.8+.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))

# Line-buffer our own output. Python block-buffers stdout when it is not a tty,
# so piping the runner anywhere (tee, a log file, CI) would otherwise let the
# unbuffered output of every `docker compose` child overtake it and flush our
# prints at exit -- including the "waiting for you" commissioning prompt, which
# is worthless if it only appears after the wait is over.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

COMPOSE = ["docker", "compose"]
COMPOSE_SEED = ["docker", "compose", "-f", "docker-compose.seed.yml"]

IGNITION_DATA = "/usr/local/bin/ignition/data"


# ── output ───────────────────────────────────────────────────────────────────

def _ansi_ok():
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name != "nt":
        return True
    # Windows consoles need VT processing switched on explicitly. Windows
    # Terminal has it already; conhost (the old console host) does not.
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        return True
    except Exception:
        return False


_COLOR = _ansi_ok()


def _c(code, text):
    return "\033[%sm%s\033[0m" % (code, text) if _COLOR else text


def step(m):
    print(_c("36", "==> " + m))


def ok(m):
    print(_c("32", "  OK    " + m))


def warn(m):
    print(_c("33", "  WARN  " + m))


def bad(m):
    print(_c("31", "  FAIL  " + m))


class TaskError(Exception):
    """Fatal, already-explained failure. Prints and exits non-zero."""


# ── environment ──────────────────────────────────────────────────────────────

def dotenv():
    """Read .env into a dict.

    Compose reads .env itself; this is only so the task runner can see the same
    values (ports, credentials) without a second source of truth.
    """
    values = {}
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return values
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            if re.match(r"^\s*#", line):
                continue
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
            if m:
                values[m.group(1)] = m.group(2).strip().strip('"')
    return values


def env_value(env, key, default):
    value = env.get(key)
    return value if value else default


def assert_env():
    if not os.path.exists(os.path.join(ROOT, ".env")):
        raise TaskError("No .env found. Run: python tasks.py init")


# ── shelling out ─────────────────────────────────────────────────────────────

def run(args):
    """Run a command with inherited stdio. Returns the exit code."""
    try:
        return subprocess.call(args, cwd=ROOT)
    except FileNotFoundError:
        raise TaskError("'%s' not found on PATH. Is Docker Desktop installed and running?"
                        % args[0])


def run_checked(args, message):
    if run(args) != 0:
        raise TaskError(message)


def capture(args):
    """Run a command quietly. Returns (returncode, stdout stripped)."""
    try:
        p = subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True)
    except FileNotFoundError:
        return 127, ""
    return p.returncode, (p.stdout or "").strip()


# ── HTTP ─────────────────────────────────────────────────────────────────────

def http(url, method="GET", headers=None, body=None, timeout=5):
    """Returns (status, text). status is None if the host never answered."""
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # A 401/403 still proves something is listening, so the status matters
        # more than the exception.
        return e.code, e.read().decode("utf-8", "replace")
    except Exception:
        return None, ""


def ignition_auth(env):
    """Headers for the gateway's /data/api/v1 routes.

    Ignition 8.3 does not accept Basic auth here -- it wants an API key, created
    at Platform > Security > API Keys in the gateway UI and sent as
    X-Ignition-API-Token. Note the gateway also refuses API keys over plain HTTP
    until "Require secure connections for API Keys" is turned off.

    Basic is still sent underneath. It authenticates nothing on these routes, but
    keeping it means an unconfigured gateway answers a clean 401 instead of
    behaving as though no credentials were offered at all.
    """
    headers = {}
    token = env.get("IGNITION_API_TOKEN", "").strip()
    if token:
        headers["X-Ignition-API-Token"] = token

    user = env_value(env, "IGNITION_ADMIN_USERNAME", "admin")
    password = env_value(env, "IGNITION_ADMIN_PASSWORD", "password")
    pair = base64.b64encode(("%s:%s" % (user, password)).encode("ascii")).decode("ascii")
    headers["Authorization"] = "Basic " + pair
    return headers


# ── Chariot ──────────────────────────────────────────────────────────────────
#
# Chariot 3.x will NOT open its MQTT listener without an active license or a
# running trial, and neither one starts on its own in the container. A freshly
# started Chariot answers on its web port while port 1883 refuses connections,
# which looks exactly like a broken network config. It is not.
#
# Licensing is a BY-HAND step: web UI -> License -> start trial (or install a
# key). Nothing in this file changes license state. The only route for it was
# undocumented, and driving licensing from a script is not worth the stage risk.
# What follows READS state, so `up`, `trial` and `health` can tell you to go
# press the button.
#
# Calls run via `docker exec` against the container's own loopback because the
# API is token-based (Basic auth is rejected at the edge).

CHARIOT_ACCEPT = "Accept: application/json;api-version=1.0"
CHARIOT_BASE = "http://localhost:8080"


def _chariot_curl(args):
    """curl inside the Chariot container. Returns parsed JSON, or None.

    The argument list is handed to docker exec directly, with no shell in the
    path. That matters: the ';' in Chariot's Accept header used to be mangled by
    intermediate shell quoting and arrive as a bare 'Accept: application/json',
    which Chariot rejects with "'api-version' not specified".
    """
    rc, out = capture(["docker", "exec", "icc26-chariot", "curl", "-sf"] + args)
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def chariot_api(path, method="GET", body="{}"):
    env = dotenv()
    password = env_value(env, "CHARIOT_ADMIN_PASSWORD", "password")

    login = _chariot_curl(["-X", "POST", CHARIOT_BASE + "/login",
                           "-H", CHARIOT_ACCEPT, "-u", "admin:" + password])
    if not login or not login.get("access_token"):
        return None
    token = login["access_token"]

    return _chariot_curl(["-X", method, CHARIOT_BASE + path,
                          "-H", CHARIOT_ACCEPT,
                          "-H", "Authorization: Bearer " + token,
                          "-H", "Content-Type: application/json",
                          "-d", body])


def chariot_state():
    lic = chariot_api("/license")
    srv = chariot_api("/server")
    if lic is None or srv is None:
        return None
    return {
        "trial_running": bool(lic.get("trialRunning")),
        "trial_secs": int((lic.get("trialTimer") or 0) // 1000),
        "license_state": lic.get("state"),
        "server_running": bool(srv.get("running")),
        "listener": srv.get("nonSecureListenerStatus"),
    }


def wait_for_chariot(timeout_sec=180):
    # Chariot seeds its admin user asynchronously after the container starts and
    # rejects logins until it has. That takes appreciably longer than the web
    # port becoming reachable, so poll the API itself rather than the port.
    step("Waiting for the Chariot API (up to %d s)" % timeout_sec)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if chariot_api("/license") is not None:
            return True
        time.sleep(5)
    return False


def chariot_license_hint():
    env = dotenv()
    print("       Start it by hand: http://localhost:%s -> License -> start trial"
          % env_value(env, "CHARIOT_HTTP_PORT", "8081"))
    print("       (or install a Cirrus Link demo key on the same page)")


def check_chariot_listener(quiet=False):
    """Is Chariot's MQTT listener open? Read-only -- waits for the API, reports.

    Waiting matters even when the answer is bad news: Chariot's admin user is
    seeded asynchronously, so an immediate check right after `up` reports a
    dead API on a Chariot that is merely still starting.
    """
    state = chariot_state()
    if state is None:
        if not wait_for_chariot():
            if not quiet:
                bad("chariot   API never became reachable")
            return False
        state = chariot_state()
    if state is None:
        if not quiet:
            bad("chariot   API not reachable")
        return False

    if state["server_running"]:
        if not quiet:
            if state["trial_running"]:
                ok("chariot   MQTT listener running (%d min of trial left)"
                   % round(state["trial_secs"] / 60))
            else:
                ok("chariot   MQTT listener running (licensed)")
        return True

    if not quiet:
        bad("chariot   MQTT listener DOWN - no active license or trial")
        chariot_license_hint()
    return False


# ── Ignition gateway ─────────────────────────────────────────────────────────

def gateway_state(port="8088"):
    """RUNNING | COMMISSIONING | DOWN.

    /StatusPing reports {"state":"RUNNING"} once the gateway is genuinely up,
    but ALSO {"state":"RUNNING","details":"COMMISSIONING"} while it is parked in
    commissioning serving only the setup web app. Treating the second as healthy
    is how you end up exporting an empty config, so they are deliberately
    distinguished here -- a plain substring test for RUNNING matches both.
    """
    status, text = http("http://localhost:%s/StatusPing" % port, timeout=5)
    if status != 200:
        return "DOWN"
    if "COMMISSIONING" in text:
        return "COMMISSIONING"
    if "RUNNING" in text:
        return "RUNNING"
    return "DOWN"


def wait_for_gateway(timeout_sec=180, label="gateway"):
    env = dotenv()
    port = env_value(env, "IGNITION_HTTP_PORT", "8088")

    step("Waiting for %s on :%s (up to %d s)" % (label, port, timeout_sec))
    deadline = time.time() + timeout_sec
    announced = False
    while time.time() < deadline:
        state = gateway_state(port)
        if state == "RUNNING":
            ok("%s is RUNNING" % label)
            return True
        if state == "COMMISSIONING" and not announced:
            announced = True
            print("")
            warn("The gateway is parked in COMMISSIONING.")
            print("")
            print("  Ignition 8.3 requires a one-time interactive acceptance of any")
            print("  third-party module certificate. It cannot be pre-accepted by")
            print("  seeding modules.json -- that was tested and does not work.")
            print("")
            print("  Open  http://localhost:%s  and complete the setup wizard," % port)
            print("  accepting the Cirrus Link module certificates when prompted.")
            print("")
            print("  This is needed once per fresh data volume, i.e. after a nuke.")
            print("  Waiting for you...")
            print("")
        time.sleep(5)
    bad("%s did not reach RUNNING within %d s" % (label, timeout_sec))
    return False


# ── modules ──────────────────────────────────────────────────────────────────

def modl_info(path):
    """Read <id> and <version> out of a .modl.

    A .modl is a zip; module.xml carries both. Reading the real version out of
    the file is the only check that catches the most likely mistake --
    downloading the 8.1-era 4.x line instead of the 5.x line that Ignition 8.3
    requires. Filenames are identical between the two.
    """
    try:
        with zipfile.ZipFile(path) as z:
            if "module.xml" not in z.namelist():
                return None
            xml = z.read("module.xml").decode("utf-8", "replace")
    except Exception:
        return None
    version = re.search(r"<version>(.*?)</version>", xml, re.S)
    ident = re.search(r"<id>(.*?)</id>", xml, re.S)
    return {
        "version": version.group(1).strip() if version else None,
        "id": ident.group(1).strip() if ident else None,
    }


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def module_dir():
    return os.path.join(ROOT, "compose", "ignition", "modules")


def task_verify_modules():
    step("Verifying Cirrus Link modules")
    manifest_path = os.path.join(ROOT, "compose", "ignition", "modules.manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    problems = []
    wrong_version = False

    for m in manifest["modules"]:
        path = os.path.join(module_dir(), m["file"])
        if not os.path.exists(path):
            if m["required"]:
                bad("%s v%s - MISSING (%s)" % (m["name"], m["version"], m["file"]))
                problems.append(m["name"])
            else:
                warn("%s v%s - absent (optional)" % (m["name"], m["version"]))
            continue

        # Version first: a wrong-version module is worse than a missing one,
        # because the gateway will try to load it and misbehave.
        info = modl_info(path)
        if info is None or not info["version"]:
            warn("%s - present, but module.xml could not be read" % m["name"])
        elif not info["version"].startswith(m["version"]):
            bad("%s - WRONG VERSION" % m["name"])
            print("          manifest expects %s.x" % m["version"])
            print("          file actually is %s" % info["version"])
            problems.append(m["name"])
            wrong_version = True
            continue
        else:
            ok("%s %s" % (m["name"], info["version"]))

        if m.get("sha256", "").strip():
            actual = sha256(path)
            if actual != m["sha256"].lower():
                bad("%s - sha256 MISMATCH" % m["name"])
                print("          expected %s" % m["sha256"].lower())
                print("          actual   %s" % actual)
                problems.append(m["name"])

    if wrong_version:
        print("")
        bad("Ignition 8.3 requires the Cirrus 5.x line. The 4.x modules are the")
        bad("8.1-era build and will not work here. All three must be the SAME")
        bad("version -- Cirrus documents class-loading instability otherwise.")
        print("       See compose/ignition/MODULES.md")
        print("")
        return False
    if problems:
        print("")
        warn("The gateway will start, but without full MQTT capability.")
        warn("See compose/ignition/MODULES.md for where to download these.")
        print("")
        return False
    return True


def task_hash_modules():
    step("sha256 of modules present")
    if not os.path.isdir(module_dir()):
        warn("No modules directory at %s" % module_dir())
        return
    files = sorted(f for f in os.listdir(module_dir()) if f.endswith(".modl"))
    if not files:
        warn("No .modl files in %s" % module_dir())
        return
    for name in files:
        print("  %-40s %s" % (name, sha256(os.path.join(module_dir(), name))))
    print("")
    print("Paste these into compose/ignition/modules.manifest.json and commit.")


# ── tasks ────────────────────────────────────────────────────────────────────

def task_init():
    step("Creating .env")
    dest = os.path.join(ROOT, ".env")
    if os.path.exists(dest):
        ok(".env already exists, leaving it alone")
    else:
        shutil.copyfile(os.path.join(ROOT, ".env.example"), dest)
        ok("Created .env from .env.example")
    # Warn-only on purpose, unlike seed/up: init is the task you run BEFORE
    # dropping the licensed .modl files in, so missing modules are expected.
    task_verify_modules()


def _rmtree(path):
    # Windows refuses to unlink read-only files; clear the bit and retry.
    def on_error(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if os.path.exists(path):
        shutil.rmtree(path, onerror=on_error)


def is_populated(path):
    return os.path.isdir(path) and bool(os.listdir(path))


def compose_project():
    """Compose's project name -- the prefix it puts on every volume it creates.

    Same precedence compose itself uses: the process environment wins over
    .env, and the fallback matches .env.example.
    """
    return (os.environ.get("COMPOSE_PROJECT_NAME")
            or env_value(dotenv(), "COMPOSE_PROJECT_NAME", "icc26"))


def data_volume():
    return "%s_ign-data" % compose_project()


def volume_exists(name):
    rc, _ = capture(["docker", "volume", "inspect", name])
    if rc == 127:
        raise TaskError("'docker' not found on PATH. Is Docker Desktop installed and running?")
    return rc == 0


# Gateway identity: regenerated by every seed and keyed to this machine's
# gateway, so it is gitignored and each clone has to produce its own. Sources
# are relative to IGNITION_DATA inside the container; a trailing '/.' means
# "the contents of this directory".
IDENTITY_PATHS = [
    ("config/local/.",
     os.path.join("ignition", "config", "local")),
    ("config/resources/local/.",
     os.path.join("ignition", "config", "resources", "local")),
    ("config/ignition/tags/valueStore.idb",
     os.path.join("ignition", "config", "ignition", "tags", "valueStore.idb")),
]


def task_export_identity(container="icc26-ignition-seed"):
    """Copy ONLY the gitignored machine-local pieces out of a seeded gateway.

    The clone case already has the shared config from git and is missing just
    the identity this seed generated. Running the full export here would
    overwrite a teammate's checkout with the vanilla baseline -- which is
    exactly the bug this path exists to avoid.
    """
    step("Exporting gateway identity from %s" % container)

    for src_rel, dest_rel in IDENTITY_PATHS:
        dest = os.path.join(ROOT, dest_rel)
        # 'docker cp <dir>/.' needs the destination directory to exist; the
        # single-file copy needs its parent to exist.
        os.makedirs(dest if src_rel.endswith("/.") else os.path.dirname(dest),
                    exist_ok=True)

        src = "%s:%s/%s" % (container, IGNITION_DATA, src_rel)
        rc, _ = capture(["docker", "cp", src, dest])
        if rc == 0:
            ok(dest_rel)
        else:
            # Unverified whether 8.3 regenerates each of these when absent, so
            # this is insurance, not a requirement -- never fail the seed on it.
            warn("%s absent in the seed gateway - skipped" % src_rel)


def task_export_config(container="icc26-ignition-seed"):
    """Copy a vanilla gateway's config-as-code out into the repo. Seed-internal.

    Deliberately NOT a CLI task. It wipes ignition/config and ignition/projects
    before copying, and the normal stack bind-mounts both of those directories --
    so pointing it at icc26-ignition deletes the very files it is about to copy
    and leaves you with nothing. Only ever call it against the seed container,
    which runs without those mounts.
    """
    step("Exporting config-as-code from %s" % container)

    for sub in ("config", "projects"):
        dest = os.path.join(ROOT, "ignition", sub)
        _rmtree(dest)
        os.makedirs(dest)

        # 'docker cp <ctr>:/path/.' copies the CONTENTS of the directory, which
        # is what we want - ignition/config should hold config's children.
        src = "%s:%s/%s/." % (container, IGNITION_DATA, sub)
        run_checked(["docker", "cp", src, dest], "docker cp of %s failed" % sub)

        count = sum(len(files) for _, _, files in os.walk(dest))
        ok("ignition/%s  (%d files)" % (sub, count))


def task_seed():
    """One-time gateway initialization, in two flavours.

    Two independent axes decide which: does this machine have the gateway data
    volume, and is ignition/config populated? A single "is config populated"
    check cannot tell a first-run author (neither) from a fresh clone (config
    from git, no volume) -- and guessing wrong either breaks the gateway or
    overwrites committed config with the vanilla baseline.

        volume   config      -> action
        absent   empty          full seed: export the whole baseline out
        absent   populated      clone seed: export only the local identity
        exists   populated      already seeded
        exists   empty          half-initialized
    """
    assert_env()

    if not task_verify_modules():
        print("")
        bad("Refusing to seed: required modules are missing or the wrong version.")
        print("       Fix that first - see compose/ignition/MODULES.md")
        return False

    volume = data_volume()
    have_volume = volume_exists(volume)
    have_config = is_populated(os.path.join(ROOT, "ignition", "config"))

    if have_volume and have_config:
        bad("Already seeded: volume %s exists and ignition/config is populated." % volume)
        print("       To rebuild from scratch:  python tasks.py nuke")
        return False

    if have_volume and not have_config:
        bad("Half-initialized: volume %s exists but ignition/config is empty." % volume)
        print("       A previous seed did not finish its export. Start over:")
        print("       python tasks.py nuke")
        return False

    clone = have_config  # the volume is absent from here down

    step("Seed pass - one-time gateway initialization")
    print("")
    if clone:
        print("  Clone seed: ignition/config came from git, but this machine has no")
        print("  gateway volume yet. Booting once WITHOUT the bind mounts initializes")
        print("  the volume, then we copy out only the machine-local identity files.")
        print("  Your committed config and projects are never touched.")
    else:
        print("  Ignition 8.3 seeds data/ from the image on first launch. Bind-mounting")
        print("  empty host dirs over data/config at that moment breaks it, so we boot")
        print("  once WITHOUT the bind mounts and copy the baseline out.")
    print("")

    step("Starting seed gateway")
    run_checked(COMPOSE_SEED + ["up", "-d"], "Seed gateway failed to start")

    # Generous timeout: if third-party modules are present this blocks on a
    # human completing commissioning in the browser.
    if not wait_for_gateway(timeout_sec=1800):
        bad("Seed gateway never reached RUNNING. Leaving it up so you can inspect it.")
        print("       docker compose -f docker-compose.seed.yml logs -f")
        return False

    if clone:
        task_export_identity(container="icc26-ignition-seed")
    else:
        task_export_config(container="icc26-ignition-seed")

    step("Stopping seed gateway")
    run(COMPOSE_SEED + ["down"])

    print("")
    if clone:
        ok("Clone seed complete. The volume is initialized and git status should")
        print("        still be clean - only gitignored identity files were written.")
    else:
        ok("Seed complete. ign-data is initialized and ./ignition/ holds the baseline.")
    print("        Next:  python tasks.py up")
    return True


def task_up():
    assert_env()
    step("Starting stack")

    if not task_verify_modules():
        print("")
        bad("Refusing to start: required modules are missing or the wrong version.")
        print("       Fix that first - see compose/ignition/MODULES.md")
        return False

    # The volume is the real precondition. Committed config no longer implies a
    # seeded machine, which is the whole point of the clone-seed path.
    volume = data_volume()
    if not volume_exists(volume):
        bad("No gateway data volume (%s) - this machine has never been seeded." % volume)
        print("       Run:  python tasks.py seed")
        return False

    if not is_populated(os.path.join(ROOT, "ignition", "config")):
        bad("ignition/config is empty - the gateway will not start correctly.")
        print("       Run:  python tasks.py seed")
        return False

    run_checked(COMPOSE + ["up", "-d"], "docker compose up failed")

    # Chariot's MQTT listener stays closed until a trial or license is active,
    # and that is a manual step in its web UI. Check before the health check so
    # the pointer to it is the first thing on screen, not buried below.
    check_chariot_listener()

    started = wait_for_gateway(timeout_sec=300)
    print("")
    return task_health() and started


def task_down(rest):
    assert_env()
    run(COMPOSE + ["down"] + rest)


def task_ps(rest):
    assert_env()
    run(COMPOSE + ["ps"] + rest)


def task_logs(rest):
    assert_env()
    run(COMPOSE + ["logs", "-f"] + rest)


def task_restart(rest):
    assert_env()
    run(COMPOSE + ["restart"] + rest)


def task_nuke():
    assert_env()
    warn("This destroys ALL volumes: gateway state, Postgres data, Chariot users.")
    warn("Your committed ignition/config and ignition/projects are NOT touched.")
    if input("Type NUKE to confirm: ").strip() != "NUKE":
        print("Aborted.")
        return
    run(COMPOSE + ["down", "-v"])
    run(COMPOSE_SEED + ["down", "-v"])
    ok("Volumes destroyed. Next: python tasks.py seed")


def task_scan():
    assert_env()
    env = dotenv()
    port = env_value(env, "IGNITION_HTTP_PORT", "8088")
    headers = ignition_auth(env)

    step("Asking the gateway to re-read config and projects from disk")
    scanned = True
    unauthorized = False
    for what in ("config", "projects"):
        url = "http://localhost:%s/data/api/v1/scan/%s" % (port, what)
        status, _ = http(url, method="POST", headers=headers, body="", timeout=20)
        if status and 200 <= status < 300:
            ok("scanned %s" % what)
        else:
            bad("scan %s failed (%s)" % (what, status or "no response"))
            scanned = False
            unauthorized = unauthorized or status in (401, 403)

    if not scanned:
        print("")
        if unauthorized:
            warn("The gateway rejected the request. These routes need an 8.3 API key,")
            warn("not the admin password: Gateway UI > Platform > Security > API Keys,")
            warn("then put it in .env as IGNITION_API_TOKEN. Over plain HTTP you must")
            warn("also disable 'Require secure connections for API Keys'.")
        print("")
        print("       Meanwhile, to apply pulled changes:")
        print("         python tasks.py restart ignition")
        print("       The gateway reads config from disk at startup. It does NOT watch")
        print("       the files -- verified empirically, an edit alone changes nothing.")
    return scanned


def task_trial():
    assert_env()
    env = dotenv()
    port = env_value(env, "IGNITION_HTTP_PORT", "8088")

    both_clocks_read = True

    step("Ignition trial status")
    # GET /data/api/v1/trial, not /license-status - the latter does not exist on 8.3.8 and
    # 404s regardless of auth. Read out of LicensingRoutes in the gateway jar and confirmed
    # live. Works on plain basic auth. Read-only on purpose: resetting the trial is a
    # by-hand step in the gateway UI, and nothing here tries to do it for you.
    status, text = http("http://localhost:%s/data/api/v1/trial" % port,
                        headers=ignition_auth(env), timeout=15)
    if status and 200 <= status < 300:
        try:
            info = json.loads(text)
        except ValueError:
            info = None
        print(json.dumps(info, indent=2) if info is not None else text)
        left = (info or {}).get("trialSecondsLeft")
        if left is not None:
            print("")
            mins = round(left / 60, 1)
            if left <= 0:
                bad("Trial EXPIRED - reset it by hand: http://localhost:%s -> "
                    "Config -> Licensing" % port)
            elif left < 900:
                warn("%s minutes left" % mins)
            else:
                ok("%s minutes left" % mins)
    else:
        both_clocks_read = False
        bad("Could not read trial status (%s)" % (status or "no response"))
        if status == 404:
            print("       404 on /data/api/v1/trial is unexpected - it exists on 8.3.8 and")
            print("       answers to basic auth. Check IGNITION_ADMIN_* in .env first.")
        else:
            print("       GET /trial needs only basic auth, so this is a credential")
            print("       problem. Check IGNITION_ADMIN_* in .env.")

    # Two independent clocks. Both must be alive when you walk on stage.
    step("Chariot trial status")
    state = chariot_state()
    if state is None:
        bad("Chariot API not reachable")
        return False
    mins = round(state["trial_secs"] / 60, 1)
    listener = "RUNNING" if state["server_running"] else "DOWN"
    print("  license=%s  trialRunning=%s  listener=%s"
          % (state["license_state"], state["trial_running"], listener))
    if not state["server_running"]:
        bad("MQTT listener DOWN - no active license or trial")
        chariot_license_hint()
        both_clocks_read = False
    elif state["trial_running"] and mins < 15:
        warn("%s minutes left" % mins)
    elif state["trial_running"]:
        ok("%s minutes left" % mins)
    else:
        ok("Licensed (no trial clock)")
    return both_clocks_read


def task_health():
    assert_env()
    env = dotenv()
    ign_port = env_value(env, "IGNITION_HTTP_PORT", "8088")
    chariot_port = env_value(env, "CHARIOT_HTTP_PORT", "8081")
    pg_user = env_value(env, "POSTGRES_SUPERUSER", "postgres")
    pg_db = env_value(env, "POSTGRES_DEMO_DB", "icc26")
    healthy = True

    step("Health check")

    def psql(query):
        return capture(["docker", "exec", "icc26-postgres", "psql",
                        "-U", pg_user, "-d", pg_db, "-tAc", query])

    rc, wal = psql("SHOW wal_level;")
    if rc == 0 and wal == "logical":
        ok("postgres  reachable, wal_level=logical")
    elif rc == 0:
        bad("postgres  wal_level=%s (expected logical) - pattern 5 will not work" % wal)
        healthy = False
    else:
        bad("postgres  not reachable")
        healthy = False

    rc, tables = psql("SELECT count(*) FROM information_schema.tables "
                      "WHERE table_schema IN ('lims','mes','plant');")
    if rc == 0 and tables.isdigit() and int(tables) >= 4:
        ok("postgres  demo schemas present (%s tables)" % tables)
    else:
        bad("postgres  demo schemas missing - initdb may not have run")
        healthy = False

    state = gateway_state(ign_port)
    if state == "RUNNING":
        ok("ignition  RUNNING on :%s" % ign_port)
    elif state == "COMMISSIONING":
        bad("ignition  parked in COMMISSIONING on :%s" % ign_port)
        print("       Open http://localhost:%s and accept the module certificates" % ign_port)
        healthy = False
    else:
        bad("ignition  not responding on :%s" % ign_port)
        healthy = False

    # A 401/403 still proves the server is listening.
    status, _ = http("http://localhost:%s" % chariot_port, timeout=8)
    if status in (401, 403):
        ok("chariot   web UI on :%s (auth required)" % chariot_port)
    elif status and 200 <= status < 400:
        ok("chariot   web UI on :%s" % chariot_port)
    else:
        bad("chariot   not responding on :%s" % chariot_port)
        healthy = False

    # The web UI answering proves nothing about MQTT -- Chariot serves the UI
    # while the broker is down. Check the listener itself.
    cs = chariot_state()
    if cs is None:
        bad("chariot   API not reachable, cannot confirm the MQTT listener")
        healthy = False
    elif cs["server_running"]:
        mins = round(cs["trial_secs"] / 60)
        if not cs["trial_running"]:
            ok("chariot   MQTT listener RUNNING on :%s (licensed)"
               % env_value(env, "CHARIOT_MQTT_PORT", "1883"))
        elif mins < 15:
            warn("chariot   MQTT listener RUNNING but only %d min of trial left" % mins)
        else:
            ok("chariot   MQTT listener RUNNING on :%s (trial %d min)"
               % (env_value(env, "CHARIOT_MQTT_PORT", "1883"), mins))
    else:
        bad("chariot   MQTT listener DOWN - no active license or trial")
        chariot_license_hint()
        healthy = False

    print("")
    if healthy:
        ok("Step-1 stack looks healthy")
    else:
        warn("Some checks failed - see above")
    return healthy


HELP = """\
Task runner for the ICC 2026 demo stack.

  python tasks.py <task> [args]        (make <task> also works on Linux/macOS)

Setup
  init             Create .env from .env.example if absent
  verify-modules   Check compose/ignition/modules against the manifest
  hash-modules     Print sha256 for each .modl present (paste into the manifest)
  seed             ONE-TIME gateway seed - first run OR fresh clone
                   (see docs/00-architecture.md)

Running
  up               Start the whole stack
  down             Stop the stack, keep volumes
  restart [svc]    Restart everything, or one service
  ps               Service status
  logs [svc]       Follow logs
  nuke             Stop and DESTROY all volumes (asks first)

Gateway / broker
  scan             Tell the gateway to re-read config + projects from disk
  trial            Show BOTH trial clocks (Ignition and Chariot)
                   Licensing itself is by hand, in each product's web UI
  health           Step-1 health check across all services

Example
  python tasks.py init && python tasks.py seed && python tasks.py up\
"""


def main(argv):
    task = argv[0] if argv else "help"
    rest = argv[1:]

    if task in ("help", "-h", "--help"):
        print(HELP)
        return 0

    dispatch = {
        "init": task_init,
        "verify-modules": task_verify_modules,
        "hash-modules": task_hash_modules,
        "seed": task_seed,
        "up": task_up,
        "down": lambda: task_down(rest),
        "restart": lambda: task_restart(rest),
        "ps": lambda: task_ps(rest),
        "logs": lambda: task_logs(rest),
        "nuke": task_nuke,
        "scan": task_scan,
        "trial": task_trial,
        "health": task_health,
    }

    handler = dispatch.get(task.lower())
    if handler is None:
        bad("Unknown task: %s" % task)
        print("")
        print(HELP)
        return 2

    # Handlers that can fail meaningfully return a bool; the rest return None
    # and are judged only by whether they raised. Without this, every task
    # exited 0 and no caller -- CI, a teammate's shell, the E-phase tests --
    # could tell a green run from a broken one.
    return 1 if handler() is False else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except TaskError as e:
        print("")
        bad(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        print("")
        sys.exit(130)
