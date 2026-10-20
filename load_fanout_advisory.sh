#!/usr/bin/env bash
# load_fanout_advisory.sh — PreToolUse(Bash)
#
# A session cannot see the load it puts on something shared. A command that
# repeats calls to a service in a loop, in parallel (`xargs -P`, `parallel`)
# or in the background does it many times over, and the service's own health
# is not in front of the session at all. Retries and parallel callers multiply
# the load a service sees (in one published example, retries at each of
# several layers raised the load on the database 243-fold), and a service
# whose latency passes its callers' timeouts starts failing requests, which
# brings more retries.
#
# Speaks when a command's own words repeat a call to a remote tool (ssh, curl,
# psql, kubectl, aws, gh api, git fetch and the rest of scanners/remote_calls.py)
# in one of those three shapes. It names the shape, the tool and the target when
# the command shows one, and says what it observed: no load reading for the
# target has reached the session. Scry cannot see what else is watching, so it
# states the observation and the insight in terms of what can happen. Quoted text, comments and heredocs never count.
#
# Once per session per tool per SCRY_LOAD_NOTE_MINUTES (default 30), so a
# loop repeated by a retry does not repeat the note. Silent otherwise.
# SCRY_LOAD_NOTES=0 turns it off. Exits zero always; it denies nothing.
set -uo pipefail

[ "${SCRY_LOAD_NOTES:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

# Cheap gate: a command with no loop, pipe-to-xargs, parallel or `&` cannot be a fan-out.
case "$payload" in
  *for\ *|*while\ *|*until\ *|*xargs*|*parallel*|*" &"*|*"&\\n"*|*"&;"*) ;;
  *) exit 0 ;;
esac

LOAD_PAYLOAD="$payload" \
SCRY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners" \
SCRY_STATE="${SCRY_LOAD_STATE_DIR:-${TMPDIR:-/tmp}/scry-load-fanout}" \
SCRY_MINUTES="${SCRY_LOAD_NOTE_MINUTES:-30}" \
python3 - <<'PY' 2>/dev/null || true
import hashlib, json, os, sys, time

try:
    d = json.loads(os.environ["LOAD_PAYLOAD"])
except (ValueError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Bash":
    sys.exit(0)
command = (d.get("tool_input") or {}).get("command") or ""
if not command.strip():
    sys.exit(0)

sys.path.insert(0, os.environ["SCRY_DIR"])
import remote_calls

found = remote_calls.fanout(command)
if not found:
    sys.exit(0)

minutes = os.environ["SCRY_MINUTES"]
window = (int(minutes) if minutes.isdigit() and int(minutes) > 0 else 30) * 60
who = f"{d.get('session_id') or 'nosession'}|{d.get('agent_id') or ''}"
state = os.environ["SCRY_STATE"]
now = time.time()
spoken = []
try:
    os.makedirs(state, exist_ok=True)
    for shape, tool, detail in found:
        marker = os.path.join(state, hashlib.sha1(f"{who}|{tool}".encode()).hexdigest())
        if os.path.exists(marker) and now - os.path.getmtime(marker) < window:
            continue
        open(marker, "w").close()
        spoken.append((shape, tool, detail))
except OSError:
    sys.exit(0)
if not spoken:
    sys.exit(0)

how = {"loop": "in a loop", "parallel": "in parallel", "background": "in the background"}
parts = []
for shape, tool, detail in spoken:
    parts.append(f"`{tool}` {how[shape]}" + (f" ({detail})" if detail and shape == "parallel" else "") +
                 (f" against {detail}" if detail and shape != "parallel" else ""))
text = (f"SCRY: this command runs {'; '.join(dict.fromkeys(parts))}. Each repeat is another call to something other "
        "sessions and people may be using, and no health or load reading for that service has reached this session. "
        "If nothing else is watching it, the load from the repeats arrives unmeasured.\n"
        "Retries and parallel callers can multiply the load a service sees: in one published example (AWS Builders' Library), retries at each of "
        "several layers raised the load on the database 243-fold. A service whose latency passes its callers' timeouts can start failing requests, which can bring more retries.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text},
                  "suppressOutput": True}))
PY
exit 0
