#!/usr/bin/env bash
# engine_guard.sh — Claude Code PreToolUse hook (Bash): denies a command that
# would open a session on the production engine while the engine is failing
# its Fly health check or the cap on open sessions from this Mac is reached.
#
# An engine session runs inside the one machine every firm shares, and some
# scripts compile their code once per session. Past a handful at once the
# machine has no CPU left to answer its health check, and sessions started
# while it is down only lengthen the outage.
#
# A command reaches the engine when it STARTS with a typed `fly ssh console`
# or `fly machine exec` call naming the engine with -a, or with bash, sh or
# python3 followed by a script file that makes such a call (after cd x &&,
# ; or a pipe too). Text that only mentions the call is never a command
# start: a commit message, a heredoc, an echo, a grep, a `cat` of a script.
# The state comes from scanners/engine.py: the health check (cached 60
# seconds), the open connections and the cap (default 4,
# ~/.engine-guard/slots). Whatever the rule does not see (loops, functions,
# a script's own calls) the wrapper around `fly` caps.
#
# Denies with exit zero, as elixir_build_guard.sh does. Unreadable health,
# no ~/.engine-guard, a malformed payload and every other command pass
# silently. SCRY_ENGINE_GUARD=0 turns it off; ~/.engine-guard/OFF too.
set -uo pipefail

[ "${SCRY_ENGINE_GUARD:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

# Matches every Bash call, so a machine with no engine guard pays nothing.
[ -d "${ENGINE_GUARD_HOME:-$HOME/.engine-guard}" ] || exit 0

ENGINE_PAYLOAD="$payload" SCRY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners" python3 - <<'PY' 2>/dev/null || true
import json
import os
import sys

try:
    d = json.loads(os.environ["ENGINE_PAYLOAD"])
except (ValueError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Bash":
    sys.exit(0)
command = (d.get("tool_input") or {}).get("command") or ""
if not command.strip():
    sys.exit(0)

sys.path.insert(0, os.environ["SCRY_DIR"])
import engine

if not engine.enabled():
    sys.exit(0)

app = engine.app()
bound = engine.engine_command(command, d.get("cwd") or os.getcwd())
if not bound:
    sys.exit(0)

health, detail, age = engine.read_health()
sessions = engine.open_sessions()
cap = engine.limit()

if health == "critical":
    reason = (f"SCRY: {bound} opens an engine session, and the production engine ({app}) is failing its Fly health check "
              f"({detail}; read {age}s ago).\n\n"
              "A machine that cannot answer its health check is out of CPU or connections. A session opened now adds load to it, "
              "and the outage lasts for every firm on the engine until it recovers or is restarted. "
              "The health check is read again once the 60-second reading expires.")
elif len(sessions) >= cap:
    listing = "\n".join(f"  {s}" for s in sessions)
    reason = (f"SCRY: {bound} opens an engine session, and {len(sessions)} are already open from this Mac (cap {cap}).\n\n"
              "Every session runs inside the one engine machine that every firm shares; past the cap the sessions compete with "
              "firm work for CPU instead of finishing sooner, and a script that compiles its code once per session multiplies it.\n"
              f"Open now:\n{listing}")
else:
    sys.exit(0)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    },
    "systemMessage": reason,
}))
PY
exit 0
