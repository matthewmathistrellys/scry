#!/usr/bin/env bash
# service_distress_advisory.sh — PostToolUse(Bash) and PostToolUseFailure(Bash)
#
# A session sees how a remote service is doing only through the answers it
# gets: calls that run past their timeout (the harness moves them to the
# background and says so), timeouts, refused or reset connections,
# 502/503/504, 429 and "too many connections". A 503 says the server cannot
# handle the request, and a call slower than its caller's timeout fails; the
# results alone do not say whether the service or the network is the cause. A
# retry or a parallel caller adds load to a service that is already behind.
#
# Speaks when the commands this session ran against remote tools (ssh, curl,
# psql, kubectl, aws, gh api, git fetch and the rest of scanners/remote_calls.py)
# have returned two or more of those answers within SCRY_DISTRESS_WINDOW_MINUTES
# (default 10), then again each time the count in the window doubles. It
# names the answers seen, the tool and the target when the command showed one.
# Only a command that ran a remote tool counts, so a file that contains the
# word "timeout" does not.
#
# Reads the command and its output for this one call and keeps a list of
# timestamps per session. SCRY_DISTRESS_NOTES=0 turns it off; SCRY_DISTRESS_MIN
# sets the count that first speaks. Exits zero always.
set -uo pipefail

[ "${SCRY_DISTRESS_NOTES:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

# Cheap gate: no distress word in the payload, nothing to read.
case "$payload" in
  *imeout*|*imed\ out*|*eadline*|*efused*|*onnection\ reset*|*ECONNRESET*|*50[234]*|*429*|*oo\ many*|*navailable*|*Bad\ Gateway*|*ate\ limit*|*could\ not\ connect*|*ailed\ to\ connect*|*verloaded*) ;;
  *) exit 0 ;;
esac

DISTRESS_PAYLOAD="$payload" \
SCRY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners" \
SCRY_STATE="${SCRY_DISTRESS_STATE_DIR:-${TMPDIR:-/tmp}/scry-service-distress}" \
SCRY_WINDOW="${SCRY_DISTRESS_WINDOW_MINUTES:-10}" \
SCRY_FIRST="${SCRY_DISTRESS_MIN:-2}" \
python3 - <<'PY' 2>/dev/null || true
import hashlib, json, os, re, sys, time

try:
    d = json.loads(os.environ["DISTRESS_PAYLOAD"])
except (ValueError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Bash":
    sys.exit(0)
command = (d.get("tool_input") or {}).get("command") or ""

sys.path.insert(0, os.environ["SCRY_DIR"])
import remote_calls

tools = [t for t in (remote_calls.remote_tool(w) for w, _ in remote_calls.segments(command)) if t]
if not tools:
    sys.exit(0)


def text_of(node, out):
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            text_of(v, out)
    elif isinstance(node, list):
        for v in node:
            text_of(v, out)


chunks = []
text_of(d.get("tool_response"), chunks)
text_of(d.get("error"), chunks)
output = "\n".join(chunks)[:200_000]

SIGNALS = [
    ("calls slow enough to be moved to the background", r"did not complete within[^\n]{0,40}moved to the background"),
    ("timeouts", r"timed? ?out|timeout|deadline exceeded|ETIMEDOUT"),
    ("refused or reset connections", r"connection (?:refused|reset)|ECONNREFUSED|ECONNRESET|could not connect|failed to connect|server closed the connection"),
    ("502/503/504", r"\b50[234]\b(?=[^\n]{0,40}(?:gateway|unavailable|time-?out|HTTP|status))|bad gateway|service unavailable|gateway time-?out"),
    ("429 or rate limits", r"too many requests|\b429\b|rate.?limit"),
    ("too many connections", r"too many connections|remaining connection slots|overloaded"),
]
seen = [name for name, rx in SIGNALS if re.search(rx, output, re.I)]
if not seen:
    sys.exit(0)

minutes = os.environ["SCRY_WINDOW"]
window = (int(minutes) if minutes.isdigit() and int(minutes) > 0 else 10) * 60
first = os.environ["SCRY_FIRST"]
first = int(first) if first.isdigit() and int(first) > 0 else 2
who = f"{d.get('session_id') or 'nosession'}|{d.get('agent_id') or ''}"
path = os.path.join(os.environ["SCRY_STATE"], hashlib.sha1(who.encode()).hexdigest() + ".json")
now = time.time()
try:
    os.makedirs(os.environ["SCRY_STATE"], exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    events = [e for e in state.get("events", []) if now - e[0] < window] + [[now, seen, tools[0]]]
    said = state.get("said", 0) if state.get("events") and events[:-1] else 0
    count = len(events)
    speak = count >= first and (said == 0 or count >= said * 2)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"events": events, "said": count if speak else said}, f)
    os.replace(path + ".tmp", path)
except OSError:
    sys.exit(0)
if not speak:
    sys.exit(0)

names = list(dict.fromkeys(n for _, s, _ in events for n in s))
tool = tools[0]
host = remote_calls.target(command)
latest = time.strftime("%H:%M", time.localtime(now))
text = (f"SCRY: {count} remote calls in the last {window // 60} minutes came back with {', '.join(names)} "
        f"(latest `{tool}`{' to ' + host if host else ''} at {latest}).\n"
        "A 503 says the server is unable to handle the request, and a call slower than its caller's timeout fails; retries then add load to a service that is already struggling "
        "(Google's SRE book notes retries can keep a backend in an overloaded state). A timeout, a refused connection or a slow call does not show on its own whether the service or the network is the cause.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text},
                  "suppressOutput": True}))
PY
exit 0
