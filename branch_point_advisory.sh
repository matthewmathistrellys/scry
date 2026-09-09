#!/usr/bin/env bash
# branch_point_advisory.sh — PreToolUse(Bash)
#
# A branch is created from whatever ref the command names, and when it names
# none, from HEAD. Nothing in git objects to starting a branch from a commit
# that origin moved past hours ago; the branch is created happily and the work
# begins on files that are already the older ones.
#
# The exposure this reports was observed on 2026-09-09 on a freshly built box:
# an agent asked to make one change ran `git checkout -b <name>` with no start
# point and no preceding fetch. `git reflog` recorded "Created from HEAD", and
# the checkout had no FETCH_HEAD at all — origin had never been contacted. The
# branch happened to be current, because the clone was minutes old. On a
# checkout that had sat for a day the same command would have branched from a
# day-old commit, silently.
#
# Two distinct facts decide whether there is anything to say:
#
#   - How far the start point sits behind the remote default branch. This is
#     countable and it is what lands in the new branch.
#   - How long ago origin was last fetched. This bounds how much the first
#     number can be trusted: a `behind` count computed against a remote-tracking
#     ref that was last updated yesterday is itself yesterday's answer, and a
#     zero from it means "nothing had landed as of yesterday", not "nothing has
#     landed".
#
# Quiet when the command names its own start point (the reader has already
# decided where to branch from), when the tree is current and origin was
# fetched recently, and on every internal error. Per AGENTS.md this exits zero
# always and states consequences, never actions.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

# cwd and the command, from the PreToolUse payload.
eval "$(SCRY_PAYLOAD="$payload" python3 -c '
import json, os, shlex
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    d = {}
cwd = d.get("cwd", "") or ""
cmd = (d.get("tool_input") or {}).get("command", "") or ""
print("agent_cwd=%s" % shlex.quote(cwd))
print("cmd=%s" % shlex.quote(cmd))
' 2>/dev/null)" || exit 0

[ -n "${cmd:-}" ] || exit 0
[ -n "${agent_cwd:-}" ] || agent_cwd="$PWD"

# Does this command create a branch? `git branch <name>` without -d/-D/-m/-a/-r
# counts; so do checkout -b/-B, switch -c/-C, and worktree add -b/-B.
creates="$(CMD="$cmd" python3 -c '
import os, re
c = os.environ["CMD"]
pats = [
    r"\bgit\b[^;&|]*\bcheckout\b[^;&|]*\s-{1,2}[bB]\b",
    r"\bgit\b[^;&|]*\bswitch\b[^;&|]*\s-{1,2}[cC]\b",
    r"\bgit\b[^;&|]*\bworktree\s+add\b",
    # `git branch <name>` creates; every flag form (-d, -a, --merged, ...)
    # reads or removes, so the name must not begin with a dash at all.
    r"\bgit\b[^;&|]*\bbranch\s+(?!-)[A-Za-z0-9_./][A-Za-z0-9_./-]*",
]
print("yes" if any(re.search(p, c) for p in pats) else "no")
' 2>/dev/null)" || exit 0
[ "$creates" = "yes" ] || exit 0

# An explicit start point means the reader already chose one. Naming any
# remote-tracking ref, a tag, or an explicit SHA is a decision, not a default.
explicit="$(CMD="$cmd" python3 -c '
import os, re
c = os.environ["CMD"]
print("yes" if re.search(r"(origin/|upstream/|refs/remotes/|\bFETCH_HEAD\b|\b[0-9a-f]{7,40}\b)", c) else "no")
' 2>/dev/null)" || exit 0
[ "$explicit" = "yes" ] && exit 0

wt="$(git -C "$agent_cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$wt" ] || exit 0
g() { git -C "$wt" "$@"; }

default_branch="$(g symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default_branch="${default_branch#origin/}"
[ -n "$default_branch" ] || default_branch="main"

g rev-parse --verify --quiet "refs/remotes/origin/$default_branch" >/dev/null 2>&1 || exit 0

behind="$(g rev-list --count "HEAD..origin/$default_branch" 2>/dev/null || echo 0)"
case "$behind" in ''|*[!0-9]*) behind=0 ;; esac

# Age of the last fetch. No FETCH_HEAD means origin has never been contacted
# from this checkout -- the remote-tracking ref is whatever the clone shipped.
FETCH_STALE_HOURS="${SCRY_BRANCH_POINT_FETCH_HOURS:-2}"
gitdir="$(g rev-parse --git-dir 2>/dev/null)" || exit 0
case "$gitdir" in /*) ;; *) gitdir="$wt/$gitdir" ;; esac

never_fetched=no
fetch_hours=0
if [ -f "$gitdir/FETCH_HEAD" ]; then
  now="$(date +%s)"
  mt="$(date -r "$gitdir/FETCH_HEAD" +%s 2>/dev/null || stat -c %Y "$gitdir/FETCH_HEAD" 2>/dev/null || echo "$now")"
  fetch_hours=$(( ( now - mt ) / 3600 ))
  [ "$fetch_hours" -ge 0 ] || fetch_hours=0
else
  never_fetched=yes
fi

# Quiet when the start point is current AND that answer is fresh enough to
# mean something.
if [ "$behind" -eq 0 ] && [ "$never_fetched" = "no" ] && [ "$fetch_hours" -lt "$FETCH_STALE_HOURS" ]; then
  exit 0
fi

repo="$(basename "$wt")"
short="$(g rev-parse --short HEAD 2>/dev/null || echo HEAD)"

if [ "$never_fetched" = "yes" ]; then
  freshness="origin has never been fetched from this checkout, so refs/remotes/origin/$default_branch still holds whatever the clone shipped with and the count above is measured against that, not against the live tip. The true distance is unknown and can only be larger."
elif [ "$fetch_hours" -ge "$FETCH_STALE_HOURS" ]; then
  freshness="origin was last fetched ${fetch_hours}h ago, so that count is ${fetch_hours}h old: it reports what had landed as of then, not what has landed."
else
  freshness="origin was fetched within the last ${FETCH_STALE_HOURS}h, so the count is current."
fi

ADVISORY_TEXT="$repo: this command creates a branch with no start point named, so it starts at HEAD ($short). HEAD is $behind commit(s) behind origin/$default_branch, and $freshness

What the new branch carries is the older tree: anything merged upstream since $short is absent from it, so a grep in the new branch returns nothing for code that exists on the default branch, and a change written here is written against files that have already moved. At push time the divergence surfaces as a rebase or as a conflict, and a re-implementation of something already merged surfaces as neither -- it merges cleanly and duplicates the upstream work."

CTX="$ADVISORY_TEXT" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": os.environ["CTX"],
    },
    "suppressOutput": True,
}))
PY

exit 0
