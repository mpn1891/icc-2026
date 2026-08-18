"""Exercise the seed/up state machine and exit codes without touching Docker.

    python tests/test_tasks.py        (exits non-zero if anything regresses)

Only the functions that shell out are stubbed -- volume_exists, is_populated,
task_verify_modules, the docker calls -- so the branching under test is the real
thing. No Docker, no gateway, no network, so this is safe to run anywhere and is
the cheapest way to re-check the guardrails after editing tasks.py.

Standard library only, matching tasks.py. No pytest.
"""
import importlib.util
import io
import os
import sys
import contextlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("tasks", os.path.join(ROOT, "tasks.py"))
tasks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tasks)

calls = []


def stub(name, fn):
    setattr(tasks, name, fn)


def reset(volume, config, modules=True, commissioned=True):
    del calls[:]
    stub("assert_env", lambda: None)
    stub("volume_exists", lambda n: volume)
    stub("is_populated", lambda p: config)
    stub("task_verify_modules", lambda: modules)
    stub("wait_for_gateway", lambda **kw: commissioned)
    stub("run_checked", lambda a, m: calls.append("run_checked:" + " ".join(a[-3:])))
    stub("run", lambda a: calls.append("run:" + " ".join(a[-2:])))
    stub("task_export_config", lambda container=None: calls.append("EXPORT_FULL"))
    stub("task_export_identity", lambda container=None: calls.append("EXPORT_IDENTITY"))
    stub("_enable_ssl", lambda compose, label: calls.append("SSL:" + label) or True)
    stub("check_chariot_listener", lambda: calls.append("chariot-check"))
    stub("task_health", lambda: True)


def run_task(name):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = tasks.main([name])
    return rc, buf.getvalue(), list(calls)


failures = []


def check(label, got, want):
    mark = "ok  " if got == want else "FAIL"
    if got != want:
        failures.append(label)
    print("  %s %-58s got=%r" % (mark, label, got))


print("== seed: volume x config matrix ==")

reset(volume=False, config=False)
rc, out, c = run_task("seed")
check("absent/empty -> full seed, exit 0", (rc, "EXPORT_FULL" in c, "EXPORT_IDENTITY" in c), (0, True, False))
check("full seed configures SSL before export", c.index("SSL:seed gateway") < c.index("EXPORT_FULL"), True)
# Seeding is the only moment a module version can change -- the volume is copied
# from the image just once -- so a stale image tag here is baked in for good.
check("seed forces a rebuild", "run_checked:up -d --build" in c, True)

reset(volume=False, config=True)
rc, out, c = run_task("seed")
check("absent/populated -> clone seed, exit 0", (rc, "EXPORT_IDENTITY" in c, "EXPORT_FULL" in c), (0, True, False))
check("clone seed configures SSL before export", c.index("SSL:seed gateway") < c.index("EXPORT_IDENTITY"), True)
check("clone seed never rmtrees committed config", "EXPORT_FULL" in c, False)

reset(volume=True, config=True)
rc, out, c = run_task("seed")
check("exists/populated -> refuse, exit 1", (rc, c), (1, []))
check("  points at nuke", "nuke" in out, True)

reset(volume=True, config=False)
rc, out, c = run_task("seed")
check("exists/empty -> half-initialized, exit 1", (rc, c), (1, []))
check("  points at nuke", "nuke" in out, True)

print("== A2: verify-modules hard gate ==")

reset(volume=False, config=False, modules=False)
rc, out, c = run_task("seed")
check("seed aborts on bad modules, exit 1", (rc, c), (1, []))
check("  points at MODULES.md", "compose/ignition/MODULES.md" in out, True)

reset(volume=True, config=True, modules=False)
rc, out, c = run_task("up")
check("up aborts on bad modules, exit 1", (rc, c), (1, []))
check("  points at MODULES.md", "compose/ignition/MODULES.md" in out, True)

print("== A4: up gates on the volume ==")

reset(volume=False, config=True)
rc, out, c = run_task("up")
check("no volume -> refuse, exit 1, compose never runs", (rc, c), (1, []))
check("  points at seed", "tasks.py seed" in out, True)

reset(volume=True, config=False)
rc, out, c = run_task("up")
check("volume but empty config -> refuse, exit 1", (rc, c), (1, []))

reset(volume=True, config=True)
rc, out, c = run_task("up")
check("both present -> starts stack, exit 0", (rc, any("up" in x for x in c)), (0, True))
check("up verifies SSL", "SSL:gateway" in c, True)
# A bare `up` reuses an existing image tag, so a source edit never reaches the
# container. This assertion is the regression guard for that whole class of bug.
check("up forces a rebuild", "run_checked:up -d --build" in c, True)

print("== A5: build ==")

reset(volume=True, config=True)
rc, out, c = run_task("build")
check("build shells out to compose build, exit 0", (rc, c), (0, ["run_checked:docker compose build"]))

print("== A1: exit-code plumbing ==")

reset(volume=True, config=True)
stub("task_health", lambda: False)
rc, out, c = run_task("up")
check("unhealthy stack -> exit 1", rc, 1)

reset(volume=True, config=True)
stub("wait_for_gateway", lambda **kw: False)
rc, out, c = run_task("up")
check("gateway never RUNNING -> exit 1", rc, 1)

reset(volume=True, config=True)
stub("task_health", lambda: False)
rc, out, c = run_task("health")
check("health failing -> exit 1", rc, 1)

reset(volume=True, config=True)
stub("task_verify_modules", lambda: False)
rc, out, c = run_task("verify-modules")
check("verify-modules failing -> exit 1", rc, 1)

reset(volume=True, config=True, modules=False)
rc, out, c = run_task("init")
check("init stays warn-only -> exit 0", rc, 0)

rc, out, c = run_task("help")
check("help -> exit 0", rc, 0)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = tasks.main(["nonsense"])
check("unknown task -> exit 2", rc, 2)

print("== SSL host trust ==")

trust_calls = []
stub("_host_os", lambda: "windows")
stub("_cert_is_self_signed", lambda path: True)
stub("_cert_thumbprint", lambda path: "AABBCC")
stub("capture", lambda args: (1, ""))
stub("run", lambda args: trust_calls.append(args) or 0)
with contextlib.redirect_stdout(io.StringIO()):
    trusted = tasks._trust_ssl_certificate({}, "gateway.crt")
check("Windows trusts cert in current-user Root", trusted, True)
check("  uses certutil current-user store",
      trust_calls[0][:5],
      ["certutil.exe", "-user", "-f", "-addstore", "Root"])

trust_calls = []
trust_checks = []
stub("_host_os", lambda: "macos")
stub("_mac_login_keychain", lambda: "/Users/demo/Library/Keychains/login.keychain-db")
stub("capture", lambda args: trust_checks.append(args) or (1, ""))
stub("run", lambda args: trust_calls.append(args) or 0)
with contextlib.redirect_stdout(io.StringIO()):
    trusted = tasks._trust_ssl_certificate({}, "gateway.crt")
check("macOS trusts cert in the login keychain", trusted, True)
check("  uses security add-trusted-cert",
      trust_calls[0][:4],
      ["security", "add-trusted-cert", "-r", "trustRoot"])
check("  verifies SSL hostname against the keychain",
      trust_checks[0][3:8],
      ["gateway.crt", "-p", "ssl", "-n", "localhost"])
check("  scopes trust to basic and SSL policies",
      ("basic" in trust_calls[0], "ssl" in trust_calls[0]),
      (True, True))

trust_calls = []
stub("capture", lambda args: (0, "certificate verification successful"))
stub("run", lambda args: trust_calls.append(args) or 0)
with contextlib.redirect_stdout(io.StringIO()):
    trusted = tasks._trust_ssl_certificate({}, "gateway.crt")
check("macOS trust step is idempotent", (trusted, trust_calls), (True, []))

print("")
if failures:
    print("FAILURES: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("all checks passed")
