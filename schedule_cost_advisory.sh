#!/usr/bin/env bash
# schedule_cost_advisory.sh — PostToolUse (CronCreate, ScheduleWakeup), Claude Code.
#
# A repeating check-in is a turn in this session each time it fires, and a
# turn re-reads the whole context. This says what each run re-reads (this
# session's context, from the last assistant record) and how many runs the
# schedule makes in a day. An observation: whether it is worth it is the
# reader's call. Silent inside a subagent (agent_id set).
# SCRY_SCHEDULE_NOTES=0 turns it off.
set -uo pipefail

[ "${SCRY_SCHEDULE_NOTES:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

FILE_PAYLOAD="$payload" \
SCRY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
python3 - <<'PY' 2>/dev/null || true
import json, os, re, sys

try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("agent_id"):
    sys.exit(0)
tool = d.get("tool_name")
inp = d.get("tool_input") or {}
tp = d.get("transcript_path") or ""
sys.path.insert(0, os.environ["SCRY_ROOT"])
import peek


def field_matches(spec, value, lo, hi):
    """One cron field: *, */n, a, a-b, a-b/n, comma lists."""
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
            if step != 1:
                b = hi
        if a <= value <= b and (value - a) % step == 0:
            return True
    return False


def per_day(cron):
    """Runs on a day the cron's date fields allow, from its minute and hour."""
    f = cron.split()
    if len(f) != 5:
        return None
    return sum(1 for h in range(24) for m in range(60)
               if field_matches(f[0], m, 0, 59) and field_matches(f[1], h, 0, 23))


if tool == "ScheduleWakeup":
    try:
        delay = max(60, min(3600, float(inp.get("delaySeconds"))))
    except (TypeError, ValueError):
        sys.exit(0)
    runs = f"{86400 / delay:.0f} runs a day if it keeps re-arming at {peek.age(delay)}"
    once = " Each wake-up is one turn in this session."
elif tool == "CronCreate":
    try:
        n = per_day(str(inp.get("cron") or ""))
    except ValueError:
        sys.exit(0)
    if n is None:
        sys.exit(0)
    if inp.get("recurring") is False:
        sys.exit(0)
    runs = f"{n} runs a day, for up to 7 days"
    once = ""
else:
    sys.exit(0)

ctx = peek.session_ctx(tp) if tp.endswith(".jsonl") else 0
size = f"{ctx // 1000}k tokens" if ctx else "this session's whole context"
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": (
            f"Scry — this schedule makes {runs}. Each run re-reads this session's context, "
            f"{size} now: at the cached rate while runs come inside the cache's life, "
            f"rewritten at the full rate when they come after it has gone cold.{once}"),
    },
    "suppressOutput": True,
}))
PY
exit 0
