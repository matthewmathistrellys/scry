#!/usr/bin/env bash
# worktree_disposal_advisory.sh — Stop hook: what the finished build
# workspaces are still costing.
#
# Agent sessions create isolated worktrees (`.claude/worktrees/`,
# `.worktrees/`) and nothing tears the finished ones down. On 2026-09-09 that
# reached 0 bytes free on the machine. `health.sh` already names stale and
# abandoned worktrees at SessionStart, but session start is the wrong end of
# the work: at that point the worktrees this session is about to create do not
# exist yet, and the ones it inherits are not yet its problem. The cost lands
# at the END, which is where this speaks.
#
# ── Why Stop and not SessionEnd ────────────────────────────────────────────
# SessionEnd is the obvious event and its output goes NOWHERE. Verified two
# ways against Claude Code 2.1.266 on 2026-09-09:
#
#   1. The CLI's own event contract: SessionEnd is documented as "Exit code 0
#      - command completes successfully" — no stdout path, no additionalContext
#      path, unlike SessionStart ("stdout shown to Claude") or SubagentStart
#      ("JSON additionalContext shown to subagent").
#   2. Empirically: a probe SessionEnd hook that printed a unique token on
#      stdout AND returned it as hookSpecificOutput.additionalContext ran (it
#      wrote its payload to disk, proving it fired) and the token appeared
#      zero times in the CLI output and zero times in the session transcript.
#      The same probe on Stop appeared three times and the model quoted it
#      back. Stop's documented contract matches: "additionalContext is
#      non-error feedback delivered to the model; the conversation continues
#      so the model can act on it."
#
# So a SessionEnd version of this hook would be inert — it would run, cost
# nothing, and tell no one. Stop is the only end-shaped event whose output
# actually lands. Its cost is that it fires on every turn, which is handled
# below by three gates, in order of cheapness:
#
#   - stop_hook_active: the loop guard. When this hook's own additionalContext
#     is what caused the model to continue, the next Stop arrives with this
#     true. Return silently, always — otherwise the reminder re-triggers
#     itself forever.
#   - once per session: a marker keyed on session_id (the same pattern
#     doc_currency_advisory.sh uses per file). Said twice, it is nagging.
#   - not before the work has happened: the reminder is about finished
#     workspaces, so it waits until the session is old enough to have finished
#     something. Age comes from the transcript file's birth time — file
#     metadata, never its contents (AGENTS.md: metadata, not conversation
#     content).
#
# ADVISORY ONLY. It counts, measures, and names the command. It removes
# nothing, and it never runs prune-worktrees for you.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

parsed="$(SCRY_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    sys.exit(0)
if d.get("stop_hook_active"):
    sys.exit(0)
# No session id means no throttle key, and no throttle key on a Stop hook
# means the reminder can re-fire without bound. A payload this hook cannot
# identify is one it stays silent on — unlike the SessionStart hooks, there is
# no safe cwd fallback here, because the loop guard lives in the payload too.
if not (d.get("session_id") or "").strip():
    sys.exit(0)
print("\t".join([
    d.get("cwd", "") or "",
    d.get("session_id", "") or "",
    d.get("transcript_path", "") or "",
]))
' 2>/dev/null)"

[ -n "$parsed" ] || exit 0
IFS=$'\t' read -r session_cwd session_id transcript_path <<< "$parsed"
[ -n "$session_cwd" ] || session_cwd="$PWD"
[ -n "$session_id" ] || exit 0

common="$(git -C "$session_cwd" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
main_wt="${common%/.git}"
[ -d "$main_wt" ] || exit 0
g() { git -C "$main_wt" "$@"; }

# ── Gate 2: once per session ───────────────────────────────────────────────
session_key="$session_id"
marker_dir="${TMPDIR:-/tmp}/scry-worktree-disposal"
mkdir -p "$marker_dir" 2>/dev/null || exit 0
marker="$marker_dir/$(printf '%s' "$session_key" | shasum | cut -c1-16)"
[ -e "$marker" ] && exit 0

# ── Gate 3: only once the session is old enough to have finished something ──
# A reminder about cleaning up finished work has nothing to say on turn one.
# Birth time of the transcript file, via stat; if it cannot be read, this gate
# opens rather than closing (the other two gates still bound the noise).
MIN_MINUTES="${SCRY_WORKTREE_REMINDER_MINUTES:-45}"
if [ -n "$transcript_path" ] && [ -f "$transcript_path" ]; then
  born="$(stat -f %B "$transcript_path" 2>/dev/null || stat -c %W "$transcript_path" 2>/dev/null || echo 0)"
  case "$born" in ''|*[!0-9]*) born=0 ;; esac
  if [ "$born" -gt 0 ]; then
    age_min=$(( ( $(date +%s) - born ) / 60 ))
    [ "$age_min" -ge "$MIN_MINUTES" ] || exit 0
  fi
fi

# ── Survey ─────────────────────────────────────────────────────────────────
# Existing refs only. No fetch: this runs at the end of every turn, and the
# question ("is this branch's work already upstream") is answered by refs the
# session already has.

# Is this ref's content already in origin/<default>? Ancestry first, then
# patch-id equivalence, so a squash or rebase merge — which rewrites SHAs and
# hides from --is-ancestor — is still recognised as merged. Same verdict
# health.sh reaches, deliberately: two answers to one question is how finished
# work ends up filed under "might be precious".
default_branch="$(g symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default_branch="${default_branch#origin/}"
[ -n "$default_branch" ] || default_branch="main"
upstream="origin/$default_branch"
have_upstream=0
g rev-parse --verify --quiet "refs/remotes/$upstream" >/dev/null 2>&1 && have_upstream=1

content_is_in_main() {
  local ref="$1" mb tr syn
  [ "$have_upstream" -eq 1 ] || return 1
  [ "$(g rev-list --count "$upstream..$ref" 2>/dev/null || echo 1)" = "0" ] && return 0
  mb="$(g merge-base "$upstream" "$ref" 2>/dev/null)"
  tr="$(g rev-parse "$ref^{tree}" 2>/dev/null)"
  [ -n "$mb" ] && [ -n "$tr" ] || return 1
  # The identity is pinned so commit-tree cannot fail on a repo with no
  # configured user, and so the synthetic commit is deterministic. Same body
  # as health.sh's, deliberately: two answers to one question is how finished
  # work ends up filed under "might be precious".
  syn="$(GIT_AUTHOR_NAME=scry GIT_AUTHOR_EMAIL=scry@local \
         GIT_COMMITTER_NAME=scry GIT_COMMITTER_EMAIL=scry@local \
         GIT_AUTHOR_DATE=2000-01-01T00:00:00 GIT_COMMITTER_DATE=2000-01-01T00:00:00 \
         g commit-tree "$tr" -p "$mb" -m _ 2>/dev/null)"
  [ -n "$syn" ] || return 1
  g cherry "$upstream" "$syn" 2>/dev/null | head -1 | grep -q '^-'
}

wt_path=()
wt_branch=()
current_path=""
while IFS= read -r wline; do
  case "$wline" in
    "worktree "*) current_path="${wline#worktree }" ;;
    "branch refs/heads/"*)
      wt_path+=("$current_path")
      wt_branch+=("${wline#branch refs/heads/}")
      ;;
    "detached")
      wt_path+=("$current_path")
      wt_branch+=("")
      ;;
  esac
done < <(g worktree list --porcelain 2>/dev/null)

total=${#wt_path[@]}
linked=0
merged=0
kb=0
measured=0
# `du` on a worktree with _build/ or node_modules/ is the expensive part of
# this hook, and this runs at the end of a turn. Bound it and report the total
# as a floor when the budget runs out, rather than reporting a number that
# took four seconds to be exact about.
DU_BUDGET_SECONDS="${SCRY_WORKTREE_DU_BUDGET:-4}"
du_deadline=$(( $(date +%s) + DU_BUDGET_SECONDS ))

i=0
while [ "$i" -lt "$total" ]; do
  p="${wt_path[$i]}"
  b="${wt_branch[$i]}"
  i=$((i+1))
  [ "$p" = "$main_wt" ] && continue
  [ -d "$p" ] || continue
  linked=$((linked+1))
  if [ -n "$b" ] && [ "$b" != "$default_branch" ] && content_is_in_main "refs/heads/$b"; then
    merged=$((merged+1))
  fi
  if [ "$(date +%s)" -lt "$du_deadline" ]; then
    sz="$(du -sk "$p" 2>/dev/null | awk '{print $1}')"
    case "$sz" in ''|*[!0-9]*) sz=0 ;; esac
    kb=$((kb + sz))
    measured=$((measured+1))
  fi
done

[ "$linked" -gt 0 ] || exit 0

# Speak only when there is something true to say: something is reclaimable, or
# the pile is big enough to matter on its own.
DISK_MB_WARN="${SCRY_WORKTREE_DISK_MB_WARN:-2048}"
mb=$(( kb / 1024 ))
if [ "$merged" -eq 0 ] && [ "$mb" -lt "$DISK_MB_WARN" ]; then
  exit 0
fi

: > "$marker"

size_phrase="~${mb} MB"
[ "$mb" -lt 1 ] && size_phrase="under 1 MB"
[ "$measured" -lt "$linked" ] && size_phrase="at least ~${mb} MB (measured $measured of $linked; time budget)"

if [ "$merged" -eq 1 ]; then
  merged_phrase="1 of them holds a branch whose content is already in $upstream, so nothing in it is pending — that is finished work still occupying disk."
elif [ "$merged" -gt 1 ]; then
  merged_phrase="$merged of them hold branches whose content is already in $upstream, so nothing in those is pending — that is finished work still occupying disk."
else
  merged_phrase="None are provably merged into $upstream from local refs, so none can be called safe to remove from here."
fi

if command -v prune-worktrees >/dev/null 2>&1; then
  cmd_phrase="\`prune-worktrees --dry-run $main_wt\` lists what it would remove; \`prune-worktrees $main_wt\` removes them. It removes a worktree only when GitHub reports its branch's PR merged, and keeps anything dirty, detached, or without a merged PR."
else
  cmd_phrase="\`git -C $main_wt worktree list\` shows them; \`git -C $main_wt worktree remove <path>\` removes one. Check \`git -C <path> status --porcelain\` first — a worktree with uncommitted files is not safe to remove."
fi

ADVISORY_TEXT="Scry — workspace disposal (advisory, once per session): $(basename "$main_wt") has $linked linked worktree(s) besides the primary checkout, occupying $size_phrase. $merged_phrase

Nothing here has been or will be deleted by this note. $cmd_phrase

Say this to the user in a sentence and stop; do not start cleaning up unless asked. Unremoved build workspaces are what took this machine to 0 bytes free on 2026-09-09, and a full disk fails a session mid-write rather than at a boundary."

CTX="$ADVISORY_TEXT" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "Stop",
        "additionalContext": os.environ["CTX"],
    },
    "suppressOutput": True,
}))
PY

exit 0
