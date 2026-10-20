#!/usr/bin/env python3
"""
Engine state — the one place that answers "how is the production engine, and
how many sessions from this Mac are open on it?"

Read by engine.sh (SessionStart) and engine_guard.sh (PreToolUse on Bash).
Both ask the same two questions, so both ask this module.

The state is shared with the machine-level engine-guard that wraps `fly`
(~/.engine-guard, or $ENGINE_GUARD_HOME): its health reading, its slot files,
its slot count and its OFF file. Scry reads them and writes only the health
reading, in the same format, so the wrapper and Scry share one `fly checks
list` call per minute. Scry never takes a slot and never removes one; a slot
whose process is gone is skipped, not cleaned.

Silent unless ~/.engine-guard exists: a machine without the guard has no
engine to ask about.

Run as a script: `engine.py status` prints one JSON object.
"""

import json
import os
import re
import subprocess
import sys
import time

DEFAULT_APP = "trellys-engine"
DEFAULT_SLOTS = 4
HEALTH_TTL = 60        # a passing or critical reading is reused this long
UNKNOWN_TTL = 10       # a failed reading, so a Fly outage costs one timeout, not one per call
FLY_TIMEOUT = 10


def ghome():
    return os.environ.get("ENGINE_GUARD_HOME") or os.path.expanduser("~/.engine-guard")


def app():
    return os.environ.get("SCRY_ENGINE_APP") or DEFAULT_APP


def enabled():
    if os.environ.get("SCRY_ENGINE_GUARD", "1") == "0":
        return False
    home = ghome()
    return os.path.isdir(home) and not os.path.exists(os.path.join(home, "OFF"))


def _real_fly():
    explicit = os.environ.get("ENGINE_GUARD_REAL_FLY")
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    cellar = "/opt/homebrew/Cellar/flyctl"
    candidates = ["/opt/homebrew/opt/flyctl/bin/flyctl"]
    try:
        versions = sorted(os.listdir(cellar), reverse=True)
    except OSError:
        versions = []
    candidates += [os.path.join(cellar, v, "bin", "flyctl") for v in versions]
    for path in candidates:
        if not os.access(path, os.X_OK):
            continue
        try:
            with open(path, "rb") as f:
                if f.read(2) == b"#!":      # the wrapper itself: never call it
                    continue
        except OSError:
            continue
        return path
    return None


def _statuses(node, out):
    if isinstance(node, dict):
        if "status" in node and "name" in node:
            out.append((str(node["status"]), str(node["name"])))
        for v in node.values():
            _statuses(v, out)
    elif isinstance(node, list):
        for v in node:
            _statuses(v, out)


def _fresh_reading():
    real = _real_fly()
    if not real:
        return "unknown", "the real fly binary was not found"
    try:
        run = subprocess.run([real, "checks", "list", "-a", app(), "--json"],
                             capture_output=True, text=True, timeout=FLY_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "unknown", f"fly checks list did not answer in {FLY_TIMEOUT}s"
    except OSError as e:
        return "unknown", f"fly checks list could not start ({e.__class__.__name__})"
    if run.returncode != 0:
        tail = " ".join((run.stdout + run.stderr).split())[:120]
        return "unknown", f"fly checks list failed (exit {run.returncode}): {tail}"
    try:
        found = []
        _statuses(json.loads(run.stdout), found)
    except ValueError:
        return "unknown", "the health check answer could not be read"
    if not found:
        return "unknown", "Fly returned no health checks (is the machine stopped?)"
    detail = ",".join(f"{s}={n}" for s, n in found)
    if any(s == "critical" for s, _ in found):
        return "critical", detail
    return "passing", detail


def read_health():
    """(status, detail, age_seconds). status: passing | critical | unknown."""
    cache = os.path.join(ghome(), "health")
    now = time.time()
    try:
        with open(cache, encoding="utf-8") as f:
            parts = f.readline().rstrip("\n").split(" ", 2)
        ts, status = int(parts[0]), parts[1]
        detail = parts[2] if len(parts) > 2 else ""
        age = now - ts
        ttl = UNKNOWN_TTL if status == "unknown" else HEALTH_TTL
        if 0 <= age < ttl:
            return status, detail.replace("_", " "), int(age)
    except (OSError, ValueError, IndexError):
        pass
    status, detail = _fresh_reading()
    try:
        tmp = f"{cache}.scry{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"{int(now)} {status} {detail.replace(' ', '_')}\n")
        os.replace(tmp, cache)
    except OSError:
        pass
    return status, detail, 0


def limit():
    try:
        with open(os.path.join(ghome(), "slots"), encoding="utf-8") as f:
            n = int(re.sub(r"\D", "", f.read()) or 0)
        if n > 0:
            return n
    except (OSError, ValueError):
        pass
    return DEFAULT_SLOTS


def _ps(*args):
    try:
        return subprocess.run(["ps", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def open_sessions():
    """Live engine connections from this Mac: slot files whose process is
    still the one that took the slot, plus fly processes to the engine that
    started without the guard. A list of one-line descriptions."""
    held, out = set(), []
    slots = os.path.join(ghome(), "slots.d")
    try:
        names = sorted(os.listdir(slots))
    except OSError:
        names = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(slots, name), encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            continue
        started = _ps("-o", "lstart=", "-p", name).strip()
        if started and started == (lines[0] if lines else "").strip():
            held.add(name)
            try:
                age = int(time.time() - int(lines[1]))
            except (ValueError, IndexError):
                age = 0
            out.append(f"pid {name}, open {age}s, from: {lines[2] if len(lines) > 2 else '?'}")
    target = re.compile(r"(?:^|\s)(?:-a|--app)[ =]?" + re.escape(app()) + r"(?:\s|$)")
    verb = re.compile(r"\s(?:ssh\s+console|(?:machines?|m)\s+exec)(?:\s|$)")
    for line in _ps("-axww", "-o", "pid=,args=").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) < 2 or parts[0] in held:
            continue
        first = parts[1].split(None, 1)[0]
        if os.path.basename(first) not in ("fly", "flyctl"):
            continue
        if verb.search(" " + parts[1]) and target.search(parts[1]):
            out.append(f"pid {parts[0]}, started without the guard")
    return out


def lapsed_wrappers():
    """Wrapper files listed in ~/.engine-guard/wrappers that no longer carry
    the guard (a flyctl upgrade puts the plain link back)."""
    lapsed = []
    try:
        with open(os.path.join(ghome(), "wrappers"), encoding="utf-8") as f:
            paths = [p.strip() for p in f if p.strip()]
    except OSError:
        return lapsed
    for path in paths:
        try:
            with open(path, "rb") as f:
                text = f.read(4096)
        except OSError:
            lapsed.append(path)
            continue
        if b"engine-guard" not in text:
            lapsed.append(path)
    return lapsed


def status():
    if not enabled():
        return {"enabled": False}
    health, detail, age = read_health()
    sessions = open_sessions()
    return {"enabled": True, "app": app(), "health": health, "detail": detail,
            "age": age, "limit": limit(), "open": len(sessions),
            "sessions": sessions, "lapsed": lapsed_wrappers()}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status()))
