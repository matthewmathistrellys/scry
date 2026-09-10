#!/usr/bin/env bash
# cache_handoff_arm.sh — UserPromptSubmit hook: a new user message arms the
# cache-deadline handoff for one more cycle.
#
# `cache_handoff_monitor.sh` asks the session to write a handoff once, shortly
# before its prompt cache goes cold. Writing that handoff is itself a request,
# and a request pushes the expiry forward an hour — so a monitor that re-armed
# on every warm cache would ask for a handoff every hour, forever, with no
# user in the loop. The rule that stops that: ONLY a user message re-arms it.
# Tool calls, cache hits, monitor notifications, and the handoff itself never
# do (designed with Codex, 2026-09-10).
#
# This hook is that rule. It records "armed" for the session and nothing else.
# It reads the payload for session_id ONLY — the prompt field is never read,
# never stored (AGENTS.md: metadata, not conversation content).
#
# One report, and only here: if the previous cycle ended with the cache
# expiring before a handoff was requested (the monitor saw the deadline pass
# while the machine slept, or the lead time was too short), that is said once
# on this turn. It is said here and not by the monitor because a monitor
# notification wakes the model — a cold request, the exact cost the handoff
# exists to avoid — while this turn is already happening.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
state_dir="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}"

SCRY_PAYLOAD="$payload" SCRY_DIR="$state_dir" python3 - <<'PY' 2>/dev/null
import json, os, sys, time
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    sys.exit(0)
sid = (d.get("session_id") or "").strip()
if not sid:
    sys.exit(0)
state_dir = os.environ["SCRY_DIR"]
path = os.path.join(state_dir, sid + ".state")
previous = ""
try:
    with open(path) as f:
        previous = f.read().strip()
except Exception:
    pass
try:
    os.makedirs(state_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"armed {int(time.time())}\n")
    os.replace(tmp, path)
except Exception:
    sys.exit(0)
parts = previous.split()
if parts and parts[0] == "missed" and len(parts) >= 2 and parts[1].isdigit():
    when = time.strftime("%H:%M", time.localtime(int(parts[1])))
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Scry — cache deadline: this session's prompt cache expired at "
                f"{when} with no handoff requested (the deadline passed while "
                "nothing was watching — a sleeping machine, or a lead time "
                "shorter than the gap). The request now starting rebuilds the "
                "cache at full price; nothing to do about that, it is already "
                "spent. Say it in a sentence if it matters and carry on."
            ),
        },
        "suppressOutput": True,
    }))
PY

exit 0
