#!/usr/bin/env bash
# elixir_build_guard.sh — Claude Code PreToolUse hook (Bash): a speed bump in
# front of the commands that throw away compiled Elixir artifacts.
#
# `mix compile --force`, `mix deps.compile --force`, `mix clean --deps` and
# `rm -rf _build` are not destructive -- nothing is corrupted and nothing needs
# repairing afterwards. Their entire cost is wall-clock: on a project the size
# of a real Ash app (140+ deps, Spark DSL doing heavy compile-time work) a cold
# rebuild is tens of minutes, and it saturates the machine while it runs. On a
# laptop already deep in swap that is the difference between a working evening
# and a lost one.
#
# Because the harm is time rather than damage, a hard block is the wrong
# instrument -- sometimes a forced rebuild genuinely is the answer. So this is a
# TWO-STRIKE gate (Matt, 2026-08-22): the first attempt is denied with the cost
# stated plainly and the cheaper options named; running the same command again
# within the window lets it straight through. An unthinking reflex becomes a
# deliberate choice, and nothing is ever truly blocked.
#
# Fails open everywhere: not Bash, not an Elixir project, malformed payload,
# unwritable state directory -- all allow the command silently.
#
# Tuning:
#   SCRY_BUILD_GUARD_WINDOW=300   # seconds a first strike stays valid
#   SCRY_BUILD_GUARD=0            # disable entirely
set -uo pipefail

[ "${SCRY_BUILD_GUARD:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

BUILD_PAYLOAD="$payload" python3 - <<'PY'
import hashlib
import json
import os
import re
import sys
import time

try:
    d = json.loads(os.environ["BUILD_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)

if d.get("tool_name") != "Bash":
    sys.exit(0)

command = (d.get("tool_input") or {}).get("command") or ""
if not command.strip():
    sys.exit(0)

# ── What counts as an expensive rebuild ────────────────────────────────────
# Deliberately narrow. A plain `mix compile` is INCREMENTAL and is exactly what
# we want people running -- matching it would be the fastest possible way to
# train everyone to ignore this hook. Only the artifact-discarding forms fire.
PATTERNS = [
    (r"\bmix\s+(?:do\s+)?compile\b[^|;&]*--force", "mix compile --force"),
    (r"\bmix\s+deps\.compile\b[^|;&]*--force", "mix deps.compile --force"),
    (r"\bmix\s+(?:do\s+)?clean\b[^|;&]*--deps", "mix clean --deps"),
    # The lookbehind keeps `my_build/` and `subdeps/` from matching, while
    # still allowing a path prefix such as `apps/engine/_build`.
    (r"\brm\s+(?:-[a-zA-Z]+\s+)+[^|;&]*(?<!\w)_build\b", "rm -rf _build"),
    (r"\brm\s+(?:-[a-zA-Z]+\s+)+[^|;&]*(?<!\w)deps\b", "rm -rf deps"),
]

matched = ""
for pattern, label in PATTERNS:
    if re.search(pattern, command):
        matched = label
        break
if not matched:
    sys.exit(0)


def nearest_mix_root(start):
    """Walk up for mix.exs. Generic -- no hardcoded project paths."""
    cur = os.path.abspath(start)
    while cur != "/":
        if os.path.isfile(os.path.join(cur, "mix.exs")):
            return cur
        cur = os.path.dirname(cur)
    return ""


cwd = d.get("cwd") or os.getcwd()
mix_root = nearest_mix_root(cwd)
if not mix_root:
    # `rm -rf _build` outside an Elixir project is somebody else's business.
    sys.exit(0)

# ── Two-strike state ───────────────────────────────────────────────────────
# Keyed on the project and the INTENT, never the raw command string. Keying on
# the string looked right and was wrong: an agent that retries almost never
# repeats itself byte-for-byte. `mix compile --force`, `mix compile --force
# 2>&1 | tail`, `mix  compile  --force` and `cd apps/foo && mix compile
# --force` are four strings but one intention, so a string key denied the
# retry a SECOND time -- turning "run it again and it goes through" into
# exactly the thrash this gate exists to avoid. Caught by the Grimoire
# advisory council and confirmed by test (2026-08-22).
#
# Still scoped per project, so clearing one project's build never silently
# pre-authorises clearing another's.
window = int(os.environ.get("SCRY_BUILD_GUARD_WINDOW", "300") or 300)
key = hashlib.sha256(f"{mix_root}\x00{matched}".encode()).hexdigest()[:32]
state_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"), "scry-build-guard")

try:
    os.makedirs(state_dir, exist_ok=True)
    marker = os.path.join(state_dir, key)
    now = time.time()
    if os.path.exists(marker) and (now - os.path.getmtime(marker)) < window:
        os.remove(marker)          # second strike -- let it through
        sys.exit(0)
    with open(marker, "w") as f:
        f.write(command.strip()[:500])
except OSError:
    sys.exit(0)                    # cannot track state -> never block

# ── Cost estimate ──────────────────────────────────────────────────────────
deps_dir = os.path.join(mix_root, "deps")
try:
    dep_count = len([x for x in os.listdir(deps_dir)
                     if os.path.isdir(os.path.join(deps_dir, x))])
except OSError:
    dep_count = 0

project = os.path.basename(mix_root)
scale = (f"{dep_count} dependenc" + ("y" if dep_count == 1 else "ies")
         ) if dep_count else "every dependency"
uses_ash = uses_tidewave = False
try:
    with open(os.path.join(mix_root, "mix.lock"), encoding="utf-8") as f:
        lock = f.read()
    uses_ash = '"ash"' in lock
    uses_tidewave = '"tidewave"' in lock
except OSError:
    pass
ash_note = (" This project uses Ash, whose Spark DSL does heavy compile-time "
            "work, so a cold rebuild is far slower than the dependency count "
            "alone suggests.") if uses_ash else ""

tidewave_note = (" This project already depends on Tidewave — connect to the "
                 "running app and evaluate there rather than rebuilding."
                 ) if uses_tidewave else ""

reason = f"""SCRY: `{matched}` discards compiled artifacts in {project} and forces a cold rebuild of {scale}.{ash_note}

Nothing is corrupted by this and nothing needs repairing — the entire cost is time, typically tens of minutes, saturating the machine while it runs.

Try these first:
  • `mix compile` on its own is already incremental — it rebuilds only what changed.
  • Stale-artifact symptoms usually mean a compile-time dependency cascade, not a corrupt build. `mix xref graph --format stats` and `mix compile --profile time` find the real culprit; forcing a rebuild only hides it until next time.
  • For exploring or checking behaviour, keep a session running (`iex -S mix phx.server`) and evaluate against it instead of recompiling.{tidewave_note}

If you genuinely need the full rebuild, run it again and it will go through — this gate only stops the first, reflexive attempt."""

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    },
    "systemMessage": reason,
}))
PY
