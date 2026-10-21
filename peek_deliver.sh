#!/usr/bin/env bash
# peek_deliver.sh — PostToolUse (every tool) and UserPromptSubmit, Claude Code.
#
# Hands the main agent the peek note on a quiet subagent (see
# subagent_cold_advisory.sh and peek.py): the facts line, then what a small
# model read from the subagent's record. It rides a hook that fires anyway, so
# nothing is woken to carry it, and it is said once per subagent. A note still
# being written is left for the next hook.
#
# Silent inside a subagent (agent_id set): a subagent is not told about its
# siblings. SCRY_PEEK=0, or the file ~/.claude/scry-peek.off, turns it off.
set -uo pipefail

[ "${SCRY_PEEK:-1}" = "0" ] && exit 0
[ -e "${SCRY_PEEK_OFF_FILE:-$HOME/.claude/scry-peek.off}" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

FILE_PAYLOAD="$payload" \
SCRY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
python3 - <<'PY' 2>/dev/null || true
import json, os, sys

try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("agent_id"):
    sys.exit(0)
sid = d.get("session_id") or ""
if not sid:
    sys.exit(0)
sys.path.insert(0, os.environ["SCRY_ROOT"])
import peek

notes = peek.take_unsent_notes(sid)
if not notes:
    sys.exit(0)
for n in notes:
    peek.log_event(sid, n.get("agent", "?"), "delivered")
event = d.get("hook_event_name") or "PostToolUse"
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": "\n\n".join(peek.format_note(n) for n in notes),
    },
    "suppressOutput": True,
}))
PY
exit 0
