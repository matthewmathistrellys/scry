#!/usr/bin/env bash
# engine.sh — Claude Code / Codex SessionStart hook: answers "is the
# production engine healthy, and how many sessions from this Mac are open on
# it?" — two facts no session can see for itself.
#
# Every session believes the engine is free capacity. Each one that runs a
# script through `fly ssh console` adds CPU load to one shared machine, and
# nothing in a session shows the others doing the same.
#
# Reads the engine's Fly health check (cached for 60 seconds, shared with the
# machine-level guard that wraps `fly`) and the engine connections open from
# this Mac. Speaks when the health check is failing or cannot be read, when
# any connection is open, and when a wrapper listed in the guard's `wrappers`
# file no longer carries the guard. Silent otherwise, and silent on a machine
# with no ~/.engine-guard.
#
# SCRY_ENGINE_GUARD=0 turns it off; SCRY_ENGINE_APP names the Fly app.
# Exits zero always.
set -uo pipefail

[ "${SCRY_ENGINE_GUARD:-1}" = "0" ] && exit 0

SCRY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners" python3 - <<'PY' 2>/dev/null || true
import json
import os
import sys

sys.path.insert(0, os.environ["SCRY_DIR"])
import engine

s = engine.status()
if not s.get("enabled"):
    sys.exit(0)

app, lines = s["app"], []
if s["health"] == "critical":
    lines.append(f"- Production engine ({app}): Fly health check is critical ({s['detail']}), read {s['age']}s ago. "
                 "A machine failing its health check has no CPU left to answer with, and every session opened now adds to that load.")
elif s["health"] == "unknown":
    lines.append(f"- Production engine ({app}): health not checked — {s['detail']}.")
if s["open"]:
    lines.append(f"- Engine sessions open from this Mac: {s['open']} of {s['limit']}. "
                 "They all run inside the one engine machine that every firm shares.")
for path in s["lapsed"]:
    lines.append(f"- {path} no longer carries the engine guard (a flyctl upgrade replaces it), "
                 "so engine sessions opened by scripts are not capped.")
if not lines:
    sys.exit(0)

print(json.dumps({"hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "Production engine (SessionStart):\n" + "\n".join(lines),
}}))
PY
exit 0
