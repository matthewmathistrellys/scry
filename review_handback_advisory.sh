#!/usr/bin/env bash
# review_handback_advisory.sh — UserPromptSubmit
#
# A subagent's report reaches its parent session as a turn of its own, and
# Claude Code runs UserPromptSubmit for it before the session acts (tested live
# 2026-10-14: a note added here was read on that same turn). When the report
# reads as a review, this says what is known about the kinds of finding a
# review returns and how each is settled.
#
# Observed 2026-10-12: a session sent an adverse reviewer, got back findings
# each with a "smallest fix", and built all of them within minutes, including a
# second "is this folder empty" check against a worry the reviewer itself
# called an unconfirmed suspicion.
#
# A report reads as a review when its wording says so: a line-leading
# "Verdict", "smallest fix", "safe to merge/ship/run/build", "ready to build", a
# Findings heading, "most serious first", or "blocker / must-fix / should-fix".
# Measured 2026-10-14 on 239 hand-backs: 20 of 25 defect-hunting reviews caught,
# 2 false alarms among 205 other reports. A small local model (Gemma 3 1B,
# Qwen3 4B) and Haiku all did worse than this wording.
#
# The note text was chosen by a blind test on 169 real findings from 26 real
# reviews, three runs each, scored on the 125 where two independent judges
# agreed: with no note, a session built 14% of the findings that should not
# have been built and acted on 83% of those that should; with this note, 3%
# and 78%. Three other drafts did worse on one or both.
#
# OWNER DECISION (AGENTS.md, metadata not content; Matt, 2026-10-14): this
# hook reads the text of a subagent's report. It reads only a prompt that opens
# as a subagent hand-back, matches it against the wording above, and discards
# it — never stored, never logged, never printed. A human message is never
# read past its first bytes.
#
# Exits zero always and never instructs.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

SCRY_PAYLOAD="$payload" python3 - <<'PY' 2>/dev/null || true
import json, os, re

try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    raise SystemExit(0)
prompt = d.get("prompt")
if not isinstance(prompt, str):
    raise SystemExit(0)
head = prompt[:400]
if "[Subagent hand-back]" not in head:
    raise SystemExit(0)
report = prompt.split("The report follows:", 1)[-1]
REVIEW = re.compile(
    r"(^|\n)\W*verdict\b|smallest fix|safe to (merge|ship|run|build)|ready to build"
    r"|(^|\n)\W*#+\s*(\d+\.\s*)?findings?\b|most serious first|\b(blocker|must.fix|should.fix)s?\b",
    re.I,
)
if not REVIEW.search(report):
    raise SystemExit(0)
del prompt, head, report

note = (
    "Scry: this report reads as a review. A review report mixes three kinds of finding, "
    "and each is checked differently. A defect (the code misbehaves) is settled by "
    "reproducing it: a failing input, a path that reaches the line. A quality finding "
    "(the code works but departs from the framework's idiom, the project's conventions, "
    "or its sibling code) is settled against the docs or neighbouring code, needs no "
    "failing test, and is most of what reviews lead to: about three in four changes from "
    "human code review are maintainability, one in four functional. A speculative risk "
    "(a new guard, retry or check for a danger nobody has shown) has no evidence behind "
    "it, and building it adds a moving part. Once someone shows it, it becomes a defect. "
    "AI reviewers are least reliable on the functional kind: in one study of 31,000 "
    "comments, 35% of their functional comments were rejected as invalid, against 25% of "
    "their maintainability comments. A defect claim is therefore cheap to confirm before "
    "the fix is written. A reviewer's \"smallest fix\" for a worry it labels unconfirmed "
    "repairs nothing that has been shown to be broken."
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": note,
    },
    "suppressOutput": True,
}))
PY

exit 0
