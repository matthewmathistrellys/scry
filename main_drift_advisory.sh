#!/usr/bin/env bash
# main_drift_advisory.sh — SubagentStart hook: the stale-tree advisory.
#
# `health.sh` already reports that local main has fallen behind origin/main,
# but that report reaches the SESSION only. SessionStart output does not
# propagate into a spawned subagent, and neither does PreToolUse or
# PostToolUse `additionalContext` on the Agent tool — both of those land in
# the PARENT. `SubagentStart` is the one event whose `additionalContext` is
# delivered into the child ("Exit code 0 - JSON additionalContext shown to
# subagent", Claude Code 2.1.266), which is why this check lives here rather
# than being folded into health.sh.
#
# Born 2026-09-09. A session dispatched subagents to describe another repo's
# architecture. The dispatch brief hard-coded a checkout path and never
# mentioned origin/main; that checkout's local main was 427 commits and 14
# days behind. The agents grepped a working tree in which a whole pipeline
# stage did not yet exist, and reported a 5-stage pipeline that has 6. Nothing
# failed, nothing was flagged, and the wrong architecture was the deliverable.
#
# What makes that failure silent is that absence looks identical to
# non-existence: a file added upstream since the local tip is simply not on
# disk, so `grep` returns nothing and "nothing" reads as "the feature isn't
# there." The consequence, and the two commands that read around it
# (`git show origin/main:<path>`, `git grep <pattern> origin/main`), are the
# whole point of this hook — a bare commit count changes no behavior.
#
# Scope, deliberately narrow:
#   - Only when the agent's worktree is checked out on the repo's DEFAULT
#     branch. A feature branch is not in this trap: it is expected to diverge,
#     and its author knows what it is based on.
#   - Never fetches. SessionStart's health.sh already fetched origin/main this
#     session; a fetch on a subagent-spawn path would put network latency in
#     front of every dispatch. Missing refs mean silence, not a fetch.
#   - Never mutates. No merge, no checkout, no ref update — an automatic
#     `git merge --ff-only origin/main` was removed on 2026-09-06 (PR #11) and
#     is not coming back through this door.
#
# agent_type: no exemptions. Every agent type that can read files can read a
# stale one, and the built-in Explore agent — a heavy searcher whose entire
# output is "what is and isn't in this tree" — is the type MOST exposed to the
# absence-looks-like-non-existence failure, not least. A type-based exemption
# would also be a guess about tools this hook cannot see. The gate is the
# state of the tree, which is checkable, not the label on the agent.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

agent_cwd="$(SCRY_PAYLOAD="$payload" python3 -c '
import json, os
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    d = {}
print(d.get("cwd", "") or "")
' 2>/dev/null)"
[ -n "$agent_cwd" ] || agent_cwd="$PWD"

wt="$(git -C "$agent_cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$wt" ] || exit 0
g() { git -C "$wt" "$@"; }

branch="$(g symbolic-ref --quiet --short HEAD 2>/dev/null)" || exit 0
[ -n "$branch" ] || exit 0

# The default branch as the remote itself declares it; `main` only as a
# fallback for a remote that never had origin/HEAD set.
default_branch="$(g symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default_branch="${default_branch#origin/}"
[ -n "$default_branch" ] || default_branch="main"

# A feature branch is not in this trap.
[ "$branch" = "$default_branch" ] || exit 0

remote_ref="refs/remotes/origin/$default_branch"
g rev-parse --verify --quiet "$remote_ref" >/dev/null 2>&1 || exit 0

behind="$(g rev-list --count "$branch..origin/$default_branch" 2>/dev/null || echo 0)"
case "$behind" in ''|*[!0-9]*) exit 0 ;; esac
[ "$behind" -gt 0 ] || exit 0

# Days of drift, not just commits: 40 commits in an hour is a busy afternoon,
# 40 commits over three weeks is a different tree.
local_ct="$(g log -1 --format=%ct "$branch" 2>/dev/null || echo 0)"
origin_ct="$(g log -1 --format=%ct "origin/$default_branch" 2>/dev/null || echo 0)"
days=0
if [ "$origin_ct" -gt "$local_ct" ] && [ "$local_ct" -gt 0 ]; then
  days=$(( ( origin_ct - local_ct ) / 86400 ))
fi

repo="$(basename "$wt")"

# Escalation threshold. Past roughly a week, or fifty commits, a whole feature
# has usually landed upstream — which is the case where an answer from this
# tree is not merely dated but structurally wrong, as it was in the incident
# above (the repo that produced it runs ~30 commits/day).
STALE_DAYS="${SCRY_SUBAGENT_STALE_DAYS:-7}"
STALE_COMMITS="${SCRY_SUBAGENT_STALE_COMMITS:-50}"

verdict=""
if [ "$days" -ge "$STALE_DAYS" ] || [ "$behind" -ge "$STALE_COMMITS" ]; then
  verdict=" At this distance an architecture or inventory answer read from this working tree is UNRELIABLE: whole features land in a gap this size, so a component that exists upstream can be absent here and a stage of a pipeline can be missing entirely. Read origin/$default_branch, and say which of the two you read when you report."
fi

ADVISORY_TEXT="$repo: this checkout's $branch is $behind commit(s) / $days day(s) behind origin/$default_branch, and its files are the older ones. The working tree at $wt is stale — anything added upstream since is simply ABSENT from disk, so a grep here returns nothing for a feature that exists, and nothing reads as \"not there.\"

Read repo facts from the live tip instead of from disk: \`git -C $wt show origin/$default_branch:<path>\`, \`git -C $wt grep <pattern> origin/$default_branch\`, \`git -C $wt ls-tree -r --name-only origin/$default_branch\`. Nothing here is checked out or moved by those; they read the ref.$verdict"

CTX="$ADVISORY_TEXT" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SubagentStart",
        "additionalContext": os.environ["CTX"],
    },
    "suppressOutput": True,
}))
PY

exit 0
