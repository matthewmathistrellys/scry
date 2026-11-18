#!/usr/bin/env bash
# worktree_merge_insight.sh — PostToolUse(Bash)
#
# A merge is the one moment when work in a linked worktree has actually ended.
# After `gh pr merge` or `git merge`, when the command ran inside a linked
# worktree, Scry reports what it can observe about that worktree and what that
# kind of state can mean. It says nothing when the PR is not merged yet (a
# merge queue or --auto only queues it), when the cwd is the primary checkout,
# or when `gh` cannot answer.
#
# Observations: `gh` merged-PR state for the branch (as of now), the number of
# changed paths `git status` shows, commits ahead of the upstream branch.
# Guidance wording only ("typically", "can"); no verdicts, no instructions.
# Exits zero always.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

eval "$(SCRY_PAYLOAD="$payload" python3 -c '
import json, os, shlex
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    d = {}
print("agent_cwd=%s" % shlex.quote(d.get("cwd", "") or ""))
print("cmd=%s" % shlex.quote((d.get("tool_input") or {}).get("command", "") or ""))
' 2>/dev/null)" || exit 0

[ -n "${cmd:-}" ] || exit 0
[ -n "${agent_cwd:-}" ] || agent_cwd="$PWD"

printf '%s' "$cmd" | grep -Eq '(^|[;&| ])(gh[[:space:]]+pr[[:space:]]+merge|git[[:space:]]+merge)([[:space:]]|$)' || exit 0

wt="$(git -C "$agent_cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
gd="$(git -C "$wt" rev-parse --absolute-git-dir 2>/dev/null)" || exit 0
common="$(git -C "$wt" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
# Primary checkout: git dir and common dir are the same.
[ "$gd" != "$common" ] || exit 0

branch="$(git -C "$wt" symbolic-ref --quiet --short HEAD 2>/dev/null)" || exit 0
[ -n "$branch" ] || exit 0
command -v gh >/dev/null 2>&1 || exit 0

pr="$(cd "$wt" && gh pr list --head "$branch" --state merged --json number --limit 1 --jq '.[0].number // empty' 2>/dev/null)" || exit 0
[ -n "$pr" ] || exit 0

changed="$(git -C "$wt" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
if up="$(git -C "$wt" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"; then
  ahead="$(git -C "$wt" rev-list --count "$up..HEAD" 2>/dev/null || echo '?')"
  ahead_txt="$ahead commit(s) ahead of $up"
else
  ahead_txt="no upstream branch configured, so unpushed commits were not counted"
fi

SCRY_TEXT="gh reports PR #$pr from branch $branch as merged. In this linked worktree ($wt), git status showed $changed changed path(s) just now and $ahead_txt. Scry did not look at files git ignores or at anything open in an editor.
A worktree in this state typically holds nothing the default branch lacks. It stays registered and editable until it is removed, and an old copy can be mistaken for current code." python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": os.environ["SCRY_TEXT"],
    },
    "suppressOutput": True,
}))
PY

exit 0
