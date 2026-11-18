#!/usr/bin/env bash
# worktree_insights.sh — PreToolUse(Bash)
#
# Speaks at the moment a branch or worktree is about to be created, when what
# it saw can bear on the choice. Two observations, each said only when there
# is something to observe:
#
#   1. Recent traces of activity in this checkout: files modified in the last
#      SCRY_WT_ACTIVITY_MINUTES (default 10) and when HEAD last moved. Scry
#      cannot see which agent tool or person made them (pi, Herdr tabs, an
#      editor), so it reports the traces and never a guess about who.
#   2. For `git worktree add` only: the other worktrees this repo already has,
#      how many have branch content already in origin/main as of the last
#      fetch, how old the oldest is, and what a new worktree begins without.
#
# Per AGENTS.md: observations and insights in guidance wording ("can", "may",
# "typically"), never a verdict or an instruction. Exits zero always; quiet on
# every internal error.
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

kind="$(CMD="$cmd" python3 -c '
import os, re
c = os.environ["CMD"]
if re.search(r"\bgit\b[^;&|]*\bworktree\s+add\b", c):
    print("worktree")
elif any(re.search(p, c) for p in (
    r"\bgit\b[^;&|]*\bcheckout\b[^;&|]*\s-{1,2}[bB]\b",
    r"\bgit\b[^;&|]*\bswitch\b[^;&|]*\s-{1,2}[cC]\b",
    r"\bgit\b[^;&|]*\bbranch\s+(?!-)[A-Za-z0-9_./][A-Za-z0-9_./-]*",
)):
    print("branch")
else:
    print("none")
' 2>/dev/null)" || exit 0
[ "$kind" != "none" ] || exit 0

wt="$(git -C "$agent_cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$wt" ] || exit 0

SCRY_KIND="$kind" SCRY_WT="$wt" python3 - <<'PY' 2>/dev/null || true
import json, os, subprocess, time

kind = os.environ["SCRY_KIND"]
wt = os.environ["SCRY_WT"]
minutes = int(os.environ.get("SCRY_WT_ACTIVITY_MINUTES", "10"))
budget = float(os.environ.get("SCRY_WT_STAT_BUDGET", "3"))
now = time.time()


def git(*args, cwd=wt):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def ago(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return "%d seconds" % seconds
    if seconds < 5400:
        return "%d minutes" % round(seconds / 60)
    if seconds < 172800:
        return "%d hours" % round(seconds / 3600)
    return "%d days" % round(seconds / 86400)


notes = []

# 1. Traces of activity in this checkout.
listing = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
changed, newest, partial = 0, None, False
if listing is not None:
    start = time.time()
    cutoff = now - minutes * 60
    for rel in listing.split("\0"):
        if not rel:
            continue
        if time.time() - start > budget:
            partial = True
            break
        try:
            m = os.stat(os.path.join(wt, rel)).st_mtime
        except OSError:
            continue
        if m >= cutoff:
            changed += 1
            newest = m if newest is None else max(newest, m)
head_ts = (git("reflog", "-1", "--format=%ct", "HEAD") or "").strip()
head_age = now - int(head_ts) if head_ts.isdigit() else None
head_recent = head_age is not None and head_age <= minutes * 60

if changed or head_recent:
    bits = []
    if changed:
        bits.append("%d file(s) in this checkout were modified in the last %d minutes (newest %s ago%s)"
                    % (changed, minutes, ago(now - newest), ", counted for part of the tree before a time limit" if partial else ""))
    if head_recent:
        bits.append("HEAD last moved %s ago" % ago(head_age))
    notes.append(
        "Scry looked at file modification times and the HEAD reflog here: "
        + " and ".join(bits)
        + ". Scry cannot tell who or what made those changes."
        "\nWhen files change under a checkout, a branch switch there can swap them out from whoever made the changes."
    )

# 2. Other worktrees, for `git worktree add`.
if kind == "worktree":
    out = git("worktree", "list", "--porcelain") or ""
    entries, cur = [], {}
    for line in out.splitlines() + [""]:
        if not line:
            if cur:
                entries.append(cur)
            cur = {}
        elif " " in line:
            k, v = line.split(" ", 1)
            cur[k] = v
        else:
            cur[line] = ""
    others = [e for e in entries[1:] if "prunable" not in e]
    if others:
        merged, oldest, unchecked = 0, 0, 0
        has_remote = git("rev-parse", "--verify", "--quiet", "refs/remotes/origin/main") is not None
        for e in others:
            path = e.get("worktree", "")
            ts = (git("log", "-1", "--format=%ct", "HEAD", cwd=path) or "").strip() if os.path.isdir(path) else ""
            if ts.isdigit():
                oldest = max(oldest, now - int(ts))
            if not has_remote or not os.path.isdir(path):
                unchecked += 1
                continue
            ahead = (git("rev-list", "--count", "origin/main..HEAD", cwd=path) or "x").strip()
            if ahead == "0":
                merged += 1
        gitdir = (git("rev-parse", "--git-common-dir") or "").strip()
        if gitdir and not os.path.isabs(gitdir):
            gitdir = os.path.join(wt, gitdir)
        fetch_age = None
        try:
            fetch_age = now - os.stat(os.path.join(gitdir, "FETCH_HEAD")).st_mtime
        except OSError:
            pass
        fresh = ("origin was last fetched %s ago" % ago(fetch_age)) if fetch_age is not None else "origin has not been fetched from this checkout"
        text = ("This repo has %d other registered worktree(s); the newest commit of the oldest is %s old. "
                "Git shows %d whose HEAD has no commits beyond origin/main (%s, so that reflects what had landed by then)"
                % (len(others), ago(oldest), merged, fresh))
        text += "; %d could not be checked.\n" % unchecked if unchecked else ".\n"
        text += ("Copies of older code can be mistaken for the current code, and a new worktree is a fresh checkout, "
                 "so files git ignores (deps, build output, node_modules, .env) typically are not in it.")
        notes.append(text)

if notes:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "\n\n".join(notes),
        },
        "suppressOutput": True,
    }))
PY

exit 0
