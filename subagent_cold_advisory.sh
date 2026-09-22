#!/usr/bin/env bash
# subagent_cold_advisory.sh — PostToolUse (every tool), Claude Code.
#
# A subagent's prompt cache goes cold one TTL after its last request, and
# nothing wakes it: finished, nothing happens; still working, its next step
# re-reads its context once at the full rate; stuck, the only saving is to
# stop it before that read. The main agent can only act if it is told in
# time, and telling it costs a turn — unless the turn is already happening.
# So this hook rides a tool result the main agent is about to read anyway
# (Matt, 2026-09-22: "say it whenever it is relevant, using whichever channel
# is already open" — awake, the next tool result; idle, the keep-alive line
# the cache monitor sends, which carries the same note from roster.py).
#
# Speaks once per subagent, when its transcript has been silent for the last
# quarter of SCRY_KEEPALIVE_FRESH_SECS (default 3600). Reads file names and
# mtimes only. Silent inside a subagent (agent_id set): a subagent is not
# told about itself or its siblings.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

FILE_PAYLOAD="$payload" \
SCRY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
SCRY_KA_FRESH="${SCRY_KEEPALIVE_FRESH_SECS:-3600}" \
SCRY_STATE="${SCRY_SUBAGENT_COLD_STATE_DIR:-${TMPDIR:-/tmp}/scry-subagent-cold}" \
python3 - <<'PY'
import glob, json, os, sys, time

try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("agent_id"):
    sys.exit(0)
sid = d.get("session_id") or ""
tp = d.get("transcript_path") or ""
if not sid or not tp.endswith(".jsonl"):
    sys.exit(0)
sub = os.path.join(tp[:-len(".jsonl")], "subagents")
fresh = os.environ["SCRY_KA_FRESH"]
fresh = int(fresh) if fresh.isdigit() and int(fresh) > 0 else 3600
now = time.time()
state = os.environ["SCRY_STATE"]

sys.path.insert(0, os.environ["SCRY_ROOT"])
from roster import age, cold_note, label

said = []
for j in sorted(glob.glob(os.path.join(glob.escape(sub), "agent-*.jsonl"))):
    try:
        m = os.path.getmtime(j)
    except OSError:
        continue
    quiet = now - m
    if not (fresh * 3 // 4 <= quiet < fresh):
        continue
    aid = os.path.basename(j)[len("agent-"):-len(".jsonl")]
    # Exclusive create: two tool calls landing together must not both say it.
    marker = os.path.join(state, f"{sid}-{aid}")
    try:
        os.makedirs(state, exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        continue
    desc = ""
    try:
        with open(j[:-len(".jsonl")] + ".meta.json") as f:
            meta = json.load(f)
        desc = label(meta.get("description") or meta.get("name"))
    except Exception:
        pass
    named = f' "{desc}"' if desc else ""
    said.append(f"subagent {aid}{named}: silent {age(quiet)}")
if not said:
    sys.exit(0)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            "Scry — a subagent this session started is going quiet: " + "; ".join(said)
            + cold_note(fresh) + ". Scry reads its transcript's age only; ListAgents or "
            "its task result says whether it is done. Said once per subagent."),
    },
    "suppressOutput": True,
}))
PY
exit 0
