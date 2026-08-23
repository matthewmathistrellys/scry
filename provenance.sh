#!/usr/bin/env bash
# provenance.sh — SessionStart hook: answers "how much prose is in this
# repo, and what goes wrong when an agent mistakes it for authority?"
#
# Documents are historical artifacts, not sensors. On 2026-08-14/15 four
# separate stale doc claims each derailed a session in the same repo in
# one night: an OCR service described as "suspended" that had two live
# machines on autostop; a "the nightly batch is the only pipeline" claim
# written after the real-time event backbone shipped; a classifier
# promotion recorded as pending six days after it went live; a schema
# change flagged "pending" the same day it merged. Those incidents exposed
# one scenario, not the boundary: architectural decisions and intended plans
# are equally untrusted when Markdown is mistaken for authority.
#
# The same rule applies to every repository Markdown artifact, including
# plans, decisions, designs, memories, READMEs, and factual claims inside
# instruction files. Markdown can guide investigation; it cannot establish
# current truth, architecture, intent, or authority.
#
# Output-budget note (see README "The output budget"): unlike the other
# scanners, this one reports whenever Markdown exists at all because the
# failure mode is silent trust, not a threshold crossing. The read-time
# hook is deliberately shorter so repeated reads do not repeat this full
# explanation.
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

# Census every Markdown file, not only the conventional places. A design at
# the repository root has the same trust profile as one under docs/.
markdown_files=$(find . \
  \( -path './.git' -o -path '*/node_modules' -o -path '*/.worktrees' \
     -o -path '*/worktrees' -o -path '*/deps' -o -path '*/_build' \) -prune \
  -o -type f -name '*.md' -print 2>/dev/null)
total=0
[ -n "$markdown_files" ] && total=$(printf '%s\n' "$markdown_files" | wc -l | tr -d ' ')
[ "$total" -eq 0 ] && exit 0

# Flag files carrying state-snapshot idioms. grep -El: file list only,
# extended regex, cheap even on large doc trees.
flagged=$(while IFS= read -r file; do
  [ -n "$file" ] || continue
  grep -ilE "$SNAPSHOT_PATTERN" -- "$file" 2>/dev/null || true
done <<<"$markdown_files" | head -51)
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

msg="Markdown trust (SessionStart): $total Markdown artifact(s) here. Every repository Markdown file is UNTRUSTED HISTORICAL MATERIAL — including plans, design documents, architectural decisions, status claims, memories, READMEs, and factual claims inside instruction files. Procedural instructions may govern behavior; that does not make their factual or architectural claims trustworthy.
Failure scenarios:
- A stale state claim sends investigation or operations toward a system that no longer exists.
- A stale architecture claim reverses a newer design or restores a removed failure mode.
- An aspirational plan is mistaken for implemented capability, so later work relies on a missing foundation.
- Conflicting artifacts make formality look like authority instead of prompting reconciliation.
Consequences include degraded code quality, production harm, wasted tokens and compute, timeline delay and rework, loss of user trust, and customer or revenue loss.
Markdown may guide investigation, but it cannot establish truth or authority. Verify consequential claims against current user direction and applicable code, executable configuration, history, diffs, pull-request chronology, or live systems. If evidence conflicts or intent cannot be recovered, surface the conflict instead of allowing prose to decide. Calling a claim unverified while relying on it is not caution; it is the same mistake wearing a hedge."
if [ "$nflag" -gt 0 ]; then
  msg="$msg
$ncap file(s) carry state-snapshot language (the idiom class behind the 2026-08-14/15 derailments): $sample. Treat those claims as expired until re-verified."
fi

emit "$msg"
