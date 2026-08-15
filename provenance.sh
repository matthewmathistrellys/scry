#!/usr/bin/env bash
# provenance.sh — Claude Code SessionStart hook: answers "how much prose
# is in this repo claiming to describe reality, and how far should I
# trust it?"
#
# Documents are historical artifacts, not sensors. On 2026-08-14/15 four
# separate stale doc claims each derailed a session in the same repo in
# one night: an OCR service described as "suspended" that had two live
# machines on autostop; a "the nightly batch is the only pipeline" claim
# written after the real-time event backbone shipped; a classifier
# promotion recorded as pending six days after it went live; a schema
# change flagged "pending" the same day it merged. Every derailment came
# from trusting a prose SNAPSHOT of live state; every recovery came from
# a fresh look at code or production. Dated decisions and normative
# principles in the same files aged perfectly.
#
# So this hook does not block anything. It counts the artifacts, flags
# the ones carrying state-snapshot language, and states the consequence
# once per session: act on an unverified state-claim and you inherit its
# staleness as your own wrong action.
#
# Output-budget note (see README "The output budget"): unlike the other
# scanners, this one reports whenever instruction files exist at all —
# the owner explicitly wants the census + doctrine visible each session
# (Matt, 2026-08-15), because the failure mode is silent trust, not a
# threshold crossing. It pays for that exemption by being at most three
# lines.
set -uo pipefail

# State-snapshot language. Deliberately narrow: these are the exact idioms
# that have already caused wrong actions, not a general "is/are" hunt —
# a broad net fires constantly and trains skimming.
SNAPSHOT_PATTERN=${SCRY_SNAPSHOT_PATTERN:-'is (currently |now |still )?(live|down|suspended|disabled|running|deployed|broken)|has no deploy path|is not (yet )?(deployed|built|implemented|wired|enabled)|no (production )?tenant is on|there is no'}
# Cap on how many flagged files to name. Naming a few makes the warning
# actionable; naming forty makes it wallpaper.
FLAG_LIST_MAX=${SCRY_PROVENANCE_FLAG_LIST_MAX:-3}

emit() {
  [ -n "$1" ] || return 0
  CTX="$1" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": os.environ["CTX"],
    }
}))
PY
}

root=$(git rev-parse --show-toplevel 2>/dev/null) || root=$PWD
cd "$root" 2>/dev/null || exit 0

# Census. Instruction files (CLAUDE.md/AGENTS.md) are the highest-risk
# class: undated, "timeless"-looking, loaded into every session. docs/
# carries the dated design/decision record. Memory dirs are per-machine
# session residue with the same trust profile as docs.
instr=$(find . -maxdepth 4 \( -name CLAUDE.md -o -name AGENTS.md \) \
  -not -path '*/node_modules/*' -not -path '*/.worktrees/*' \
  -not -path '*/worktrees/*' -not -path '*/deps/*' 2>/dev/null | wc -l | tr -d ' ')
docs=0
[ -d docs ] && docs=$(find docs -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
mem=0
for d in .claude/memory memory-bank .memory; do
  [ -d "$d" ] && mem=$((mem + $(find "$d" -name '*.md' 2>/dev/null | wc -l)))
done

total=$((instr + docs + mem))
[ "$total" -eq 0 ] && exit 0

# Flag files carrying state-snapshot idioms. grep -El: file list only,
# extended regex, cheap even on large doc trees.
flagged=""
if [ "$instr" -gt 0 ] || [ "$docs" -gt 0 ]; then
  flagged=$( { find . -maxdepth 4 \( -name CLAUDE.md -o -name AGENTS.md \) \
      -not -path '*/node_modules/*' -not -path '*/.worktrees/*' \
      -not -path '*/worktrees/*' -not -path '*/deps/*' 2>/dev/null;
    [ -d docs ] && find docs -name '*.md' 2>/dev/null; } \
    | xargs grep -ilE "$SNAPSHOT_PATTERN" 2>/dev/null | head -51)
fi
nflag=0
[ -n "$flagged" ] && nflag=$(printf '%s\n' "$flagged" | wc -l | tr -d ' ')
ncap="$nflag"
if [ "$nflag" -gt 50 ]; then
  nflag=50
  ncap="50+"
fi

sample=""
if [ "$nflag" -gt 0 ]; then
  sample=$(printf '%s\n' "$flagged" | head "-$FLAG_LIST_MAX" | sed 's|^\./||' \
    | awk 'NR>1{printf ", "} {printf "%s", $0} END{print ""}')
  [ "$nflag" -gt "$FLAG_LIST_MAX" ] && sample="$sample, +more"
fi

msg="Doc provenance (SessionStart): $total markdown artifacts here ($instr instruction files, $docs in docs/, $mem memory). Docs are HISTORICAL ARTIFACTS — dated decisions and principles in them age fine; claims about current system state do not, and acting on one unverified makes its staleness your wrong action. Verify state against code/production, never prose."
if [ "$nflag" -gt 0 ]; then
  msg="$msg
$ncap file(s) carry state-snapshot language (the idiom class behind the 2026-08-14/15 derailments): $sample. Treat those claims as expired until re-verified."
fi

emit "$msg"
