#!/usr/bin/env bash
# session_disposal_advisory.sh — Stop hook: what this session is leaving
# behind. Two kinds of leftover, one note, once.
#
#   1. Finished build worktrees. Agent sessions create isolated worktrees
#      (`.claude/worktrees/`, `.worktrees/`) and nothing tears the finished
#      ones down. On 2026-09-09 that reached 0 bytes free on the machine.
#   2. Scratch Markdown. Untracked .md files this session itself wrote. The
#      creation-time counterpart (md_creation_advisory.sh) said each one was
#      scratch as it appeared; this is the end of that sentence.
#
# `health.sh` already names stale and abandoned worktrees at SessionStart, but
# session start is the wrong end of the work: at that point what this session
# is about to create does not exist yet, and what it inherits is not yet its
# problem. The cost lands at the END, which is where this speaks. (Named
# worktree_disposal_advisory.sh until 2026-09-09, when the Markdown half made
# the old name an undersell.)
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
# That same birth time is the ONLY thing that makes "created this session" a
# fact rather than a guess, so it does double duty: it is both the age gate and
# the cutoff an untracked .md file's mtime is compared against. Where it cannot
# be read (a filesystem that does not record birth time), the age gate opens as
# before and the Markdown half stays silent — there is no second mechanism to
# fall back to. The transcript's mtime is its LAST write, not the session's
# first, and using it as a start time would silently misdate every file.
#
# ADVISORY ONLY. It counts, measures, lists, and names the command. It removes
# nothing — no worktree, no file, ever — and it never runs prune-worktrees for
# you.
set -uo pipefail

# shellcheck source=md_exemptions.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/md_exemptions.sh"

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
marker_dir="${TMPDIR:-/tmp}/scry-session-disposal"
mkdir -p "$marker_dir" 2>/dev/null || exit 0
marker="$marker_dir/$(printf '%s' "$session_key" | shasum | cut -c1-16)"
[ -e "$marker" ] && exit 0

# ── Gate 3: only once the session is old enough to have finished something ──
# A reminder about cleaning up finished work has nothing to say on turn one.
# Birth time of the transcript file, via stat; if it cannot be read, this gate
# opens rather than closing (the other two gates still bound the noise) and
# session_start stays 0, which is what silences the Markdown half below.
MIN_MINUTES="${SCRY_WORKTREE_REMINDER_MINUTES:-45}"
session_start=0
if [ -n "$transcript_path" ] && [ -f "$transcript_path" ]; then
  born="$(stat -c %W "$transcript_path" 2>/dev/null || stat -f %B "$transcript_path" 2>/dev/null || echo 0)"
  case "$born" in ''|*[!0-9]*) born=0 ;; esac
  if [ "$born" -gt 0 ]; then
    session_start="$born"
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

# Speak only when there is something true to say: something is reclaimable, or
# the pile is big enough to matter on its own.
DISK_MB_WARN="${SCRY_WORKTREE_DISK_MB_WARN:-2048}"
mb=$(( kb / 1024 ))
worktree_note=""
if [ "$linked" -gt 0 ] && { [ "$merged" -gt 0 ] || [ "$mb" -ge "$DISK_MB_WARN" ]; }; then
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

  worktree_note="Worktrees: $(basename "$main_wt") has $linked linked worktree(s) besides the primary checkout, occupying $size_phrase. $merged_phrase $cmd_phrase Unremoved build workspaces are what took this machine to 0 bytes free on 2026-09-09, and a full disk fails a session mid-write rather than at a boundary."
fi

# ── Scratch Markdown this session wrote ────────────────────────────────────
# Scope is the session's OWN working tree, not the primary checkout: a session
# running in a linked worktree writes its scratch there, and claiming to have
# surveyed a tree this session never touched would be a wider claim than the
# evidence supports.
#
# Untracked means `--others --exclude-standard`: the same set `git status`
# calls untracked. Files a .gitignore already covers are deliberately out —
# including them means walking node_modules/ and _build/ at the end of every
# turn to report files the repo has already decided it does not keep.
#
# Exemptions come from md_exemptions.sh, the same list md_creation_advisory.sh
# used when each file appeared. A file exempt at creation is exempt here.
md_count=0
md_names=""
MD_LIST_MAX="${SCRY_SCRATCH_MD_LIST_MAX:-5}"
if [ "$session_start" -gt 0 ]; then
  tree_root="$(git -C "$session_cwd" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -n "$tree_root" ] && ! scry_md_product_repo "$tree_root"; then
    while IFS= read -r -d '' mdrel; do
      [ -n "$mdrel" ] || continue
      scry_md_standard_file "$mdrel" && continue
      [ -f "$tree_root/$mdrel" ] || continue
      mdt="$(stat -c %Y "$tree_root/$mdrel" 2>/dev/null || stat -f %m "$tree_root/$mdrel" 2>/dev/null || echo 0)"
      case "$mdt" in ''|*[!0-9]*) mdt=0 ;; esac
      [ "$mdt" -ge "$session_start" ] || continue
      md_count=$((md_count+1))
      if [ "$md_count" -le "$MD_LIST_MAX" ]; then
        md_names="${md_names:+$md_names, }$mdrel"
      fi
    done < <(git -C "$tree_root" ls-files --others --exclude-standard -z -- '*.md' 2>/dev/null)
  fi
fi

md_note=""
if [ "$md_count" -gt 0 ]; then
  [ "$md_count" -gt "$MD_LIST_MAX" ] && md_names="$md_names, +$((md_count - MD_LIST_MAX)) more"
  md_note="Scratch Markdown: $md_count untracked .md file(s) in this tree were written after this session started — $md_names. Anything in them that needs to outlive the session belongs where long-lived work is tracked; the files themselves are scratch."
fi

[ -n "$worktree_note" ] || [ -n "$md_note" ] || exit 0

: > "$marker"

ADVISORY_TEXT="Scry — session disposal (advisory, once per session):"
[ -n "$worktree_note" ] && ADVISORY_TEXT="$ADVISORY_TEXT

$worktree_note"
[ -n "$md_note" ] && ADVISORY_TEXT="$ADVISORY_TEXT

$md_note"
ADVISORY_TEXT="$ADVISORY_TEXT

Nothing here has been or will be deleted by this note — no worktree, no file. Say this to the user in a sentence and stop; do not start cleaning up unless asked."

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
