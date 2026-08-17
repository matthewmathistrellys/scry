#!/usr/bin/env bash
# md_advisory.sh — Claude Code PostToolUse hook: answers "can this markdown
# file's claims about system state be trusted?"
#
# A markdown file is a snapshot of what someone believed, intended, or
# claimed at the moment they wrote it — not a live view of the system.
# Citing it instead of checking the actual code or the live system is one
# of the most common ways real work goes wrong: a claim that was accurate
# for a moment, or was never quite accurate, gets repeated as current and
# everything built on top of it inherits the error.
#
# Fires on every Read of any .md file and injects a standing reminder to
# verify state-claims (done/live/deployed/wired/a date/a count) against
# the code or the live system before relying on them, rather than passing
# them along dressed as fact — or as a hedge that still relies on them.
#
# provenance.sh (SessionStart) already sets this doctrine once per session
# and censuses/flags instruction files and docs/*.md carrying
# state-snapshot idioms. This hook re-stamps the same doctrine at the
# exact moment a session reads any markdown file — not just the
# instruction/docs subset provenance.sh censuses, since a session may
# read a *.md anywhere in the tree (README, a scratchpad, a plan outside
# docs/).
#
# Advisory only: no blocking, no enforcement, just context injection.
set -uo pipefail

read -r payload

file_path="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Read":
    sys.exit(0)
fp = (d.get("tool_input") or {}).get("file_path") or ""
print(fp)
')"

[ -n "$file_path" ] || exit 0
case "$file_path" in
  *.md) ;;
  *) exit 0 ;;
esac

ADVISORY_TEXT="$(cat <<'EOF'
This file is a snapshot of what someone believed, intended, planned, or claimed at the moment they wrote it — not a live view of the system. There's no guarantee it was even accurate the day it was written: people write down what they hope is true, what they intend to do next, what they believe just shipped. None of that becomes real by the act of writing it down.

Citing a markdown file instead of checking the actual code or the live system is one of the most common ways real work goes wrong. A claim that was accurate for a moment, or was never quite accurate, gets repeated as if it were current — and everything built on top of it inherits the error. That shows up downstream as confusion, decisions made on a foundation that wasn't there, and time spent undoing work built on a wrong premise.

The cost that matters most isn't the wasted time — it's trust. Every time stale, unverified, or simply wrong information reaches the user dressed as fact, it does real harm, and it teaches them to double-check everything you say next — including the things you actually did verify. Trust, once spent, is expensive to rebuild, far more expensive than the few minutes a check would have taken.

So when a claim in this file about the state of something — done, live, deployed, wired, a date, a count — matters to what you're about to say or do, go verify it against the code or the live system before you rely on it. And notice the trap: relabeling the claim "unverified" and using it anyway isn't caution, it's the same mistake wearing a hedge — the words change, the risk to the user doesn't. If you can't check right now, say plainly what you'd check, and leave the claim out of your answer rather than pass it along dressed as caution.
EOF
)"

CTX="$ADVISORY_TEXT" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": os.environ["CTX"],
    },
    "suppressOutput": True,
}))
PY

exit 0
