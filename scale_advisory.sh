#!/usr/bin/env bash
# scale_advisory.sh — PostToolUse hook: answers "what does it cost to work in a
# file this large, and what should I do about it right now?"
#
# Fires on first contact with an oversized, actively-worked file — whether that
# contact is a Read or an Edit/Write — and says what a session cannot see for
# itself: that it is holding a fraction of the file, what specifically goes
# wrong because of that, and what the legitimate remedy is.
#
# The trigger is deliberately NOT raw line count. Line count alone flags the
# wrong files: trellys-app's single largest source file (extraction_registry.py,
# 3026 lines) is a flat declarative field registry that must not be split, while
# the file that genuinely costs the most (filevine/client.ex, 2498 lines, 20
# commits in six months) is well factored and would rank below it on any
# density measure. Size is therefore gated on CHURN: a large file nobody is
# touching costs nothing, and warning about it is noise that teaches sessions to
# skim every advisory. Large AND live is the pair that hurts.
#
# The size threshold self-calibrates to the repository rather than importing a
# folklore constant: p95 of the file-length distribution for that language,
# clamped so it can neither nag a codebase of small files nor go silent as a
# codebase bloats. In trellys-app today that is ~930 (.py) and ~585 (.ex).
#
# Advisory only: no blocking, no enforcement, just context injection. Fires at
# most once per file per session — the risk is first contact, not sustained
# work in a file the session is already reasoning about.
set -uo pipefail

read -r payload

# ── Parse: which file, reached how, in which session ───────────────────────
parsed="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
tool = d.get("tool_name") or ""
if tool not in ("Read", "Edit", "Write"):
    sys.exit(0)
fp = (d.get("tool_input") or {}).get("file_path") or ""
if not fp:
    sys.exit(0)
print(fp)
print(tool)
print(d.get("session_id") or "nosession")
')"

[ -n "$parsed" ] || exit 0
file_path="$(printf '%s' "$parsed" | sed -n 1p)"
tool_name="$(printf '%s' "$parsed" | sed -n 2p)"
session_id="$(printf '%s' "$parsed" | sed -n 3p)"

[ -n "$file_path" ] && [ -f "$file_path" ] || exit 0

# Source files only. Generated, vendored and one-shot trees are excluded: their
# size is not a design choice anyone is going to act on.
case "$file_path" in
  *.ex | *.exs | *.py) ;;
  *) exit 0 ;;
esac
case "$file_path" in
  */migrations/* | */alembic*/* | */deps/* | */_build/* | */node_modules/* \
  | */.venv/* | */site-packages/* | */scripts/* | */priv/repo/* ) exit 0 ;;
esac

lines="$(wc -l < "$file_path" 2>/dev/null | tr -d ' ')"
[ -n "$lines" ] || exit 0
# Cheapest possible rejection first: nothing under the clamp floor can qualify
# under any calibration, so the overwhelming majority of files exit here having
# cost one wc.
[ "$lines" -gt 400 ] 2>/dev/null || exit 0

repo_root="$(cd "$(dirname "$file_path")" && git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$repo_root" ] || exit 0

state_dir="${TMPDIR:-/tmp}/scry-scale-advisory"
mkdir -p "$state_dir" 2>/dev/null || exit 0

# ── Once per file per session ──────────────────────────────────────────────
# Read then Edit on the same file is one contact, not two.
seen_key="$(printf '%s|%s' "$session_id" "$file_path" | shasum | cut -d' ' -f1)"
seen_marker="$state_dir/seen-$seen_key"
[ -e "$seen_marker" ] && exit 0

# ── Threshold: p95 of this repo's own files, per language, cached daily ────
case "$file_path" in
  *.py) ext="py" ;;
  *)    ext="ex" ;;
esac
repo_key="$(printf '%s' "$repo_root" | shasum | cut -d' ' -f1)"
cache="$state_dir/p95-$repo_key-$ext"

threshold=""
if [ -f "$cache" ]; then
  cache_age=$(( $(date +%s) - $(stat -f %m "$cache" 2>/dev/null || stat -c %Y "$cache" 2>/dev/null || echo 0) ))
  [ "$cache_age" -lt 86400 ] && threshold="$(cat "$cache" 2>/dev/null)"
fi

if [ -z "$threshold" ]; then
  threshold="$(cd "$repo_root" && git ls-files -- "*.$ext" 2>/dev/null \
    | grep -vE '(^|/)(tests?|migrations|alembic[^/]*|scripts|deps|_build|node_modules)/' \
    | grep -vE '_test\.exs?$' \
    | while IFS= read -r f; do [ -f "$f" ] && wc -l < "$f"; done \
    | tr -d ' ' | sort -n \
    | awk '{a[NR]=$1} END{
        if (NR < 20) { print 0; exit }      # too few files to calibrate against
        p = a[int(NR*0.95)];
        if (p < 400) p = 400;               # floor: do not nag a tidy codebase
        if (p > 1200) p = 1200;             # ceiling: do not go silent as one bloats
        print p }')"
  [ -n "$threshold" ] && printf '%s' "$threshold" > "$cache" 2>/dev/null
fi

# 0 means the repo is too small to calibrate against; fall back to the floor.
[ -n "$threshold" ] || threshold=400
[ "$threshold" -eq 0 ] 2>/dev/null && threshold=400
[ "$lines" -gt "$threshold" ] 2>/dev/null || exit 0

# ── Liveness: a big file nobody touches costs nothing ──────────────────────
rel="${file_path#"$repo_root"/}"
churn="$(cd "$repo_root" && git log --oneline --since='6 months ago' -- "$rel" 2>/dev/null | wc -l | tr -d ' ')"
[ -n "$churn" ] || churn=0
[ "$churn" -ge 3 ] 2>/dev/null || exit 0

# Qualified. Record the contact so the rest of the session stays quiet.
: > "$seen_marker" 2>/dev/null

# ── Detail that makes the number concrete ──────────────────────────────────
case "$file_path" in
  *.py) defs="$(grep -cE '^(def |class |async def )' "$file_path" 2>/dev/null || echo 0)" ;;
  *)    defs="$(grep -cE '^  (def|defp) ' "$file_path" 2>/dev/null || echo 0)" ;;
esac

shown="$lines"
truncation_note=""
if [ "$tool_name" = "Read" ] && [ "$lines" -gt 2000 ]; then
  shown=2000
  truncation_note="A default Read returns the first 2000 lines, so roughly $(( lines - 2000 )) lines of this file were not returned at all and nothing marked their absence. "
fi

density_note=""
if [ "$defs" -gt 0 ]; then
  per=$(( lines / defs ))
  if [ "$per" -gt 100 ]; then
    density_note="It holds $defs definitions averaging $per lines each, so the units inside it are large as well as numerous. "
  else
    density_note="It holds $defs definitions, so it is well divided internally — it is the sheer breadth, not tangled functions, that exceeds what one session can hold. "
  fi
else
  density_note="It declares no ordinary functions, so it is likely declarative (a schema, resource, router or registry) — length is normal for that shape and splitting it would probably make it worse. "
fi

TEMPLATE="$(cat <<'EOF'
SCALE — @REL@ is @LINES@ lines and has changed @CHURN@ times in the last six months. That is above the 95th percentile for this repository, and it is actively worked, so what happens here is not hypothetical. @TRUNCATION@@DENSITY@

What goes wrong in a file this size is specific and silent. You are holding a fraction of it, so absence from the part you have seen is not absence from the file: a helper, constant, clause or private function you are about to add may already exist hundreds of lines away, and the duplicate will compile, pass review, and diverge from the original later — when the two disagree in production and neither is obviously wrong. A second failure runs the other way: behavior you did not read is behavior you can still break, because a pattern match, guard, default or branch further down the file may depend on the shape you just changed. Neither announces itself. The tests that would catch them are the tests nobody wrote, because nobody knew the second copy was there.

Before adding anything here, grep THIS FILE for the name you are about to introduce. Before changing a shared function, grep THIS FILE for its callers. Both take seconds, and neither is implied by having read part of the file.

This is a look, not a gate. Finish the work you were asked to do. If the file needs splitting, that is separate work with its own commit and its own tests — and split it by job, not by size: the plumbing a module does (auth, retries, rate limits, transport) is a different job from what it does with that plumbing, and the test of a good split is that each resulting file states its purpose in one clause with no "and". A long flat registry or schema is not a defect and should be left alone.
EOF
)"

TEMPLATE="$TEMPLATE" REL="$rel" LINES="$lines" CHURN="$churn" TRUNCATION="$truncation_note" DENSITY="$density_note" python3 - <<'PY'
import json, os
text = os.environ["TEMPLATE"]
for key in ("REL", "LINES", "CHURN", "TRUNCATION", "DENSITY"):
    text = text.replace("@%s@" % key, os.environ.get(key, ""))
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": text,
    },
    "suppressOutput": True,
}))
PY

exit 0
