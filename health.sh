#!/usr/bin/env bash
# SessionStart hook: git/worktree dev-environment health report.
#
# Always operates on the PRIMARY worktree, no matter which worktree or
# subdirectory the session started in. Fast-forward only for main — it can
# never rewrite or lose anything.
#
# Emits its findings via `additionalContext` so a fresh agent session sees
# the repo's actual state up front — primary-worktree cleanliness, whether
# local main has drifted from origin/main, and a worktree hygiene sweep
# (missing directories, stale/merged branches, quietly abandoned branches) —
# every session, not just when something's wrong. Terminal-only systemMessage
# output is easy to miss across many sessions; additionalContext reaches the
# model directly, which is the point.
set -uo pipefail

emit() { # $1 = systemMessage (user-visible, optional), $2 = additionalContext (model-visible)
  MSG="$1" CTX="$2" python3 - <<'PY'
import json, os
out = {}
if os.environ["MSG"]:
    out["systemMessage"] = os.environ["MSG"]
if os.environ["CTX"]:
    out["hookSpecificOutput"] = {
        "hookEventName": "SessionStart",
        "additionalContext": os.environ["CTX"],
    }
print(json.dumps(out))
PY
}

common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
main_wt="${common%/.git}"
[ -d "$main_wt" ] || exit 0
g() { git -C "$main_wt" "$@"; }

offline=0
g fetch origin main --quiet 2>/dev/null || offline=1

branch="$(g symbolic-ref --quiet --short HEAD || echo '(detached)')"
modified="$(g status --porcelain | grep -cv '^??')"
untracked="$(g status --porcelain | grep -c '^??')"

lines=()
lines+=("Primary worktree: $main_wt")

if [ "$branch" = "main" ]; then
  lines+=("- On main. Modified: $modified, untracked: $untracked.")
else
  lines+=("- WARNING: on branch '$branch', not main. The main worktree should always sit on main; feature work belongs in a linked worktree. Modified: $modified, untracked: $untracked.")

  # ── Why this warning carries its receipts ────────────────────────────────
  # The bare warning above is not enough on its own. On one project it was
  # correct and identical at every session start for 15 days while the
  # condition got worse; being told "you're not on main" reads the same on day
  # 1 as on day 15, so nothing ever forced the issue. What actually makes it
  # undeniable is the cost — how long it has been true, whether the branch was
  # already merged (making the work pointless as well as stranded), and how
  # much of what is sitting here exists nowhere in git. Those are facts, not
  # policy, which is the only kind of pressure this hook should apply.

  # How long parked here — the most recent checkout onto this branch.
  since="$(g reflog show --date=unix HEAD 2>/dev/null \
           | grep -m1 -F "to $branch" | sed -n 's/^[^@]*@{\([0-9]\{1,\}\)}.*/\1/p')"
  if [ -n "$since" ]; then
    days=$(( ( $(date +%s) - since ) / 86400 ))
    [ "$days" -ge 1 ] && lines+=("- Parked on '$branch' for $days day(s). This warning has been repeating, unchanged, that whole time.")
  fi

  # Already merged? Then nothing here is pending — it is purely stranded.
  # Ancestor check first (cheap); patch-id equivalence second, so a squash or
  # rebase merge — which rewrites SHAs and hides from --merged — is still seen.
  if [ "$offline" -eq 0 ]; then
    merged=""
    if [ "$(g rev-list --count origin/main..HEAD 2>/dev/null || echo 1)" = "0" ]; then
      merged=yes
    else
      mb="$(g merge-base origin/main HEAD 2>/dev/null)"
      tr="$(g rev-parse 'HEAD^{tree}' 2>/dev/null)"
      if [ -n "$mb" ] && [ -n "$tr" ]; then
        syn="$(GIT_AUTHOR_NAME=scry GIT_AUTHOR_EMAIL=scry@local \
               GIT_COMMITTER_NAME=scry GIT_COMMITTER_EMAIL=scry@local \
               GIT_AUTHOR_DATE=2000-01-01T00:00:00 GIT_COMMITTER_DATE=2000-01-01T00:00:00 \
               g commit-tree "$tr" -p "$mb" -m _ 2>/dev/null)"
        [ -n "$syn" ] && g cherry origin/main "$syn" 2>/dev/null | head -1 | grep -q '^-' && merged=yes
      fi
    fi
    [ -n "$merged" ] && lines+=("- This branch's content is ALREADY IN origin/main. Nothing here is pending review — the worktree is simply stranded on finished work.")
  fi

  # What would a checkout or reset here actually destroy? Only files that exist
  # in no commit on any ref are truly unrecoverable, so that is what we count.
  # Bounded by a wall-clock budget: the per-file history walk is slowest for
  # exactly the files we care about, and a session hook that hangs is worse
  # than one that reports partial results — so it says how far it got.
  cands="$( { g diff --cached --name-only --diff-filter=A 2>/dev/null
              g ls-files --others --exclude-standard 2>/dev/null; } | sort -u)"
  total="$(printf '%s' "$cands" | grep -c . )"
  if [ "$total" -gt 0 ]; then
    deadline=$(( $(date +%s) + 3 ))
    checked=0; orphans=0
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      [ "$(date +%s)" -ge "$deadline" ] && break
      checked=$(( checked + 1 ))
      [ -z "$(g log --all --max-count=1 --format=%h -- "$f" 2>/dev/null)" ] && orphans=$(( orphans + 1 ))
    done <<EOF
$cands
EOF
    if [ "$orphans" -gt 0 ]; then
      scope="all $total"; [ "$checked" -lt "$total" ] && scope="$checked of $total (time budget)"
      lines+=("- EXPOSURE: $orphans file(s) here exist in NO commit on ANY branch — checked $scope. A checkout, reset or clean in this worktree destroys them permanently. Inspect before moving: git -C $main_wt status --porcelain")
    fi
  fi
fi

if [ "$offline" -eq 1 ]; then
  lines+=("- Could not fetch origin (offline?) — main-sync and staleness checks skipped this session.")
else
  # Compare local main vs origin/main (the live/canonical tip) unconditionally —
  # this is a ref comparison, independent of what's actually checked out, so it
  # must run even when the primary worktree is sitting on some other branch.
  behind="$(g rev-list --count main..origin/main 2>/dev/null || echo 0)"
  ahead="$(g rev-list --count origin/main..main 2>/dev/null || echo 0)"
  if [ "$ahead" -gt 0 ]; then
    lines+=("- Local main has $ahead commit(s) NOT on origin — possibly stranded work needing a PR: git -C $main_wt log --oneline origin/main..main")
  elif [ "$behind" -gt 0 ]; then
    lines+=("- Local main is $behind commit(s) behind origin/main (the live tip).")
  fi

  # Only actually mutate main when it's the checked-out branch — fast-forwarding
  # a branch that isn't checked out would silently move the ref out from under
  # whatever's sitting in the working tree of wherever else it's checked out.
  if [ "$branch" = "main" ] && [ "$behind" -gt 0 ]; then
    if ff_err="$(g merge --ff-only origin/main 2>&1 >/dev/null)"; then
      lines+=("- Fast-forwarded checked-out main to $(g rev-parse --short main).")
    else
      lines+=("- Could not fast-forward cleanly ($(printf '%s' "$ff_err" | head -1)).")
    fi
  fi
fi

# ── Deploy drift ────────────────────────────────────────────────────────────
# Merged is not deployed. Everything above compares git refs to other git refs
# and never asks what production is actually running, so work can sit merged and
# live-less indefinitely with nothing to notice it. On 2026-07-18 four PRs sat
# undeployed for about eight hours while work continued on top of them.
#
# This block NEVER stays silent. If it cannot tell — no URL configured, offline,
# endpoint unreachable, unparseable response — it says so. A check that quietly
# reports nothing teaches you it looked and found nothing wrong, which is worse
# than no check at all.
#
# Config is optional so Scry stays drop-in for any repo. Put this in the
# project's .claude/scry.env (parsed as plain KEY=VALUE, never sourced, so a
# config file can't execute anything):
#
#     SCRY_HEALTH_URL=https://your-app.example.com/health
#     SCRY_HEALTH_SHA_FIELD=git_sha        # optional, this is the default
#
# Only works where the running app exposes its own commit. If a project doesn't,
# the honest fix is to add that to its health endpoint rather than guess here.
scry_env="$main_wt/.claude/scry.env"
health_url="${SCRY_HEALTH_URL:-}"
sha_field="${SCRY_HEALTH_SHA_FIELD:-git_sha}"

if [ -z "$health_url" ] && [ -f "$scry_env" ]; then
  health_url="$(grep -E '^[[:space:]]*SCRY_HEALTH_URL=' "$scry_env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'[:space:]')"
  cfg_field="$(grep -E '^[[:space:]]*SCRY_HEALTH_SHA_FIELD=' "$scry_env" 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"'[:space:]')"
  [ -n "$cfg_field" ] && sha_field="$cfg_field"
fi

if [ -z "$health_url" ]; then
  lines+=("- Deploy state UNKNOWN: no health URL configured, so this session cannot tell whether merged work is actually live. Set SCRY_HEALTH_URL in $scry_env to enable.")
elif [ "$offline" -eq 1 ]; then
  lines+=("- Deploy state UNKNOWN: could not reach origin, so there is nothing trustworthy to compare production against.")
else
  deployed_sha="$(curl -sf --max-time 3 "$health_url" 2>/dev/null \
    | SHA_FIELD="$sha_field" python3 -c 'import json,os,sys
try:
    print(json.load(sys.stdin).get(os.environ["SHA_FIELD"], ""))
except Exception:
    print("")' 2>/dev/null)"

  if [ -z "$deployed_sha" ]; then
    lines+=("- Deploy state UNKNOWN: $health_url did not answer within 3s, or returned no '$sha_field'. Production may be behind and this session cannot tell.")
  elif ! g cat-file -e "${deployed_sha}^{commit}" 2>/dev/null; then
    lines+=("- Deploy state UNKNOWN: production reports commit $deployed_sha, which this repo does not have. Fetch, or check the URL points at the right app.")
  else
    undeployed="$(g rev-list --count "${deployed_sha}..origin/main" 2>/dev/null || echo 0)"
    if [ "$undeployed" -gt 0 ]; then
      lines+=("- PRODUCTION IS $undeployed COMMIT(S) BEHIND origin/main. Merged work is not live. Say this plainly before starting: git -C $main_wt log --oneline ${deployed_sha}..origin/main")
    else
      lines+=("- Production is up to date with origin/main ($(g rev-parse --short "$deployed_sha")).")
    fi
  fi
fi

# ── Worktree hygiene sweep ──────────────────────────────────────────────────
# Parse `git worktree list --porcelain` into parallel path/branch arrays.
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

# Abandoned = not merged into main AND last commit older than this many days —
# a branch nobody merged, pushed, or touched again. The riskiest state, since
# it looks like it might still matter but nothing is actually watching it.
ABANDONED_DAYS=3
ABANDONED_SECONDS=$((ABANDONED_DAYS * 86400))
now_epoch="$(date +%s)"

total=${#wt_path[@]}
missing=0
missing_list=()
detached=0
detached_list=()
stale=0
stale_list=()
abandoned=0
abandoned_list=()

i=0
while [ "$i" -lt "$total" ]; do
  wt="${wt_path[$i]}"
  br="${wt_branch[$i]}"
  i=$((i+1))
  [ "$wt" = "$main_wt" ] && continue
  if [ ! -d "$wt" ]; then
    missing=$((missing+1))
    missing_list+=("$wt")
    continue
  fi
  if [ -z "$br" ]; then
    detached=$((detached+1))
    detached_list+=("$wt")
    continue
  fi
  [ "$br" = "main" ] && continue
  if [ "$offline" -eq 0 ] && git -C "$main_wt" merge-base --is-ancestor "refs/heads/$br" origin/main 2>/dev/null; then
    stale=$((stale+1))
    stale_list+=("$br")
    continue
  fi
  # Not merged — check whether it's just quietly aging instead of active.
  last_commit_epoch="$(git -C "$main_wt" log -1 --format=%ct "refs/heads/$br" 2>/dev/null || echo "$now_epoch")"
  age_seconds=$((now_epoch - last_commit_epoch))
  if [ "$age_seconds" -gt "$ABANDONED_SECONDS" ]; then
    age_days=$((age_seconds / 86400))
    abandoned=$((abandoned+1))
    abandoned_list+=("$br ($age_days d, $wt)")
  fi
done

other=$((total - 1))
lines+=("Worktrees: $total total ($other besides primary).")
if [ "$detached" -gt 0 ]; then
  sample="$(printf '%s, ' "${detached_list[@]:0:6}")"
  lines+=("- $detached in detached HEAD (not on a named branch): ${sample%, }")
fi
if [ "$missing" -gt 0 ]; then
  sample="$(printf '%s, ' "${missing_list[@]:0:6}")"
  lines+=("- $missing worktree(s) point at a missing directory (git worktree prune would clean the list): ${sample%, }")
fi
if [ "$stale" -gt 0 ]; then
  sample=""
  si=0
  for b in "${stale_list[@]}"; do
    [ "$si" -ge 6 ] && { sample="$sample, ..."; break; }
    [ -n "$sample" ] && sample="$sample, "
    sample="$sample$b"
    si=$((si+1))
  done
  lines+=("- $stale worktree(s) already fully merged into origin/main and safe to remove (git worktree remove): $sample")
fi
if [ "$abandoned" -gt 0 ]; then
  sample=""
  si=0
  for b in "${abandoned_list[@]}"; do
    [ "$si" -ge 6 ] && { sample="$sample, ..."; break; }
    [ -n "$sample" ] && sample="$sample, "
    sample="$sample$b"
    si=$((si+1))
  done
  lines+=("- $abandoned worktree(s) UNMERGED but untouched for over $ABANDONED_DAYS days — likely abandoned, needs a human look (not auto-removable, may hold real work): $sample")
fi

ctx="Dev environment health (SessionStart):
$(printf '%s\n' "${lines[@]}")

If production is behind origin/main, if deploy state is UNKNOWN, if the primary worktree isn't on main, is dirty, or several worktrees are stale/merged/abandoned, say so directly and plainly at the start of your first response — don't wait to be asked. Terminal systemMessages are easy to miss across sessions; this additionalContext block is the channel that reliably reaches you."

emit "" "$ctx"
exit 0
