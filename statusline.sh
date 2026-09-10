#!/usr/bin/env bash
# statusline.sh — Scry's whole status line for Claude Code.
#
# One bar, read left to right from slow-moving to fast-moving (Matt,
# 2026-09-10: "usage for the week, usage for the session in context, and
# current cache"):
#
#   scry  3 uncommitted  2 unpushed  Fable 5.1  $4.20  👥 3  🗓️ 64% Wed  🌕 42k/200k  🔥 39m
#
#   folder          basename of the session's directory; in a worktree that is
#                   the branch name, which is why the branch segment stays
#                   quiet there
#   branch          only when it is not the default branch (yellow) — the one
#                   state worth seeing is a primary checkout left on a feature
#                   branch, which blocks the repo's merge path
#   N uncommitted   yellow; files git would show as changed or new, staged or
#                   not. Hidden when the tree is clean
#   N unpushed      yellow; commits on this branch its upstream does not have
#                   (no upstream: commits origin/<default> does not have).
#                   Hidden when there are none. Between them these two answer
#                   "what would be lost if this machine died now"
#   model           Claude Code's display name
#   $cost           the session so far, from the payload, exact
#   👥 N            yellow; OTHER live Claude sessions in this same directory
#                   in the last SCRY_FLEET_ACTIVE_MINUTES (15) — the same
#                   transcript scan fleet.sh does at session start, narrowed
#                   to this tree because that is where collisions happen.
#                   Hidden at zero
#   🗓️ 64% Wed      the seven-day window, used percent and the day it resets;
#                   yellow from 70, red from 90. The five-hour window and any
#                   pace multiplier are deliberately absent: the multiplier
#                   worktrunk showed was used ÷ elapsed, gated by a Bayesian
#                   model that hid and re-showed it and switched windows
#                   silently (Matt: "never seems 100% right"). This is the
#                   same number the Anthropic app shows
#   🌕 42k/200k     context: total input tokens over the window size, moon
#                   phase by percent used (worktrunk's thresholds: 🌕 to 51,
#                   🌔 to 77, 🌓 to 90, 🌒 to 97, 🌑 after)
#   🔥 39m          the prompt-cache segment, from cache_deadline_statusline.sh,
#                   which also writes the deadline record the handoff monitor
#                   reads. Nothing here duplicates that
#
# Segments with nothing to say are omitted, not blanked. Anything that fails
# — git missing, a field absent, a slow call — drops its own segment and the
# rest of the bar still renders; the script never exits non-zero.
#
# Reads from the payload: cwd / workspace.current_dir, model.display_name,
# cost.total_cost_usd, rate_limits.seven_day, context_window, session_id.
# Nothing else. No prompt or response text is ever read (AGENTS.md).
#
# Install: point statusLine.command at this file (README, Install).
# Dropped from worktrunk's bar on purpose (2026-09-10): the fish-style path,
# the ?^| glyph string, the line diff against LOCAL main, the 5h window.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
payload="$(cat 2>/dev/null || true)"

cache="$(printf '%s' "$payload" | bash "$here/cache_deadline_statusline.sh" 2>/dev/null || true)"

bar="$(SCRY_PAYLOAD="$payload" python3 - <<'PY' 2>/dev/null
import json, os, re, subprocess, sys, time
from datetime import datetime

YEL, RED, DIM, OFF = "\033[33m", "\033[31m", "\033[2m", "\033[0m"

try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    d = {}

def get(*path):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur

cwd = get("workspace", "current_dir") or get("cwd") or os.getcwd()
segs = []

# ── folder ───────────────────────────────────────────────────────────────
folder = os.path.basename(os.path.normpath(cwd)) if cwd else ""
if folder:
    segs.append(folder)

# ── git: branch, uncommitted, unpushed ───────────────────────────────────
def git(*args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True,
                           text=True, timeout=2)
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 else None

if cwd and git("rev-parse", "--is-inside-work-tree") == "true":
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD") or "detached"
    default = None
    ref = git("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if ref and "/" in ref:
        default = ref.split("/", 1)[1]
    if default is None:
        for cand in ("main", "master"):
            if git("rev-parse", "--verify", "--quiet", f"refs/heads/{cand}") is not None:
                default = cand
                break
    if branch != default and branch != folder:
        segs.append(f"{YEL}{branch}{OFF}")

    porcelain = git("status", "--porcelain")
    if porcelain:
        n = len([l for l in porcelain.splitlines() if l.strip()])
        if n:
            segs.append(f"{YEL}{n} uncommitted{OFF}")

    ahead = None
    if git("rev-parse", "--verify", "--quiet", "@{u}") is not None:
        ahead = git("rev-list", "--count", "@{u}..HEAD")
    elif default and git("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default}") is not None:
        ahead = git("rev-list", "--count", f"origin/{default}..HEAD")
    if ahead and ahead.isdigit() and int(ahead) > 0:
        segs.append(f"{YEL}{ahead} unpushed{OFF}")

# ── model ────────────────────────────────────────────────────────────────
model = get("model", "display_name") or get("model", "id")
if model:
    segs.append(str(model))

# ── session cost ─────────────────────────────────────────────────────────
cost = get("cost", "total_cost_usd")
if isinstance(cost, (int, float)) and cost > 0:
    segs.append(f"${cost:.2f}")

# ── other live sessions in this directory ────────────────────────────────
def encode(path):
    # the transcript directory name Claude Code uses (fleet.sh, verified 2026-07-26).
    return re.sub(r"[/._]", "-", path)

try:
    projects = os.environ.get("SCRY_CLAUDE_PROJECTS") or os.path.expanduser("~/.claude/projects")
    window = int(os.environ.get("SCRY_FLEET_ACTIVE_MINUTES", "15")) * 60
    self_id = get("session_id") or ""
    pdir = os.path.join(projects, encode(os.path.realpath(cwd)))
    others = 0
    now = time.time()
    if os.path.isdir(pdir):
        for f in os.listdir(pdir):
            if not f.endswith(".jsonl") or f[:-6] == self_id:
                continue
            try:
                if now - os.stat(os.path.join(pdir, f)).st_mtime <= window:
                    others += 1
            except OSError:
                pass
    if others:
        segs.append(f"\U0001f465 {YEL}{others}{OFF}")
except Exception:
    pass

# ── weekly usage ─────────────────────────────────────────────────────────
used = get("rate_limits", "seven_day", "used_percentage")
resets = get("rate_limits", "seven_day", "resets_at")
if isinstance(used, (int, float)):
    day = ""
    if isinstance(resets, (int, float)) and resets > 0:
        day = " " + datetime.fromtimestamp(resets).strftime("%a")
    body = f"{used:.0f}%{day}"
    if used >= 90:
        body = f"{RED}{body}{OFF}"
    elif used >= 70:
        body = f"{YEL}{body}{OFF}"
    segs.append(f"\U0001f5d3️ {body}")

# ── context ──────────────────────────────────────────────────────────────
def k(n):
    n = int(n)
    return f"{n // 1000}k" if n >= 1000 else str(n)

used_tok = get("context_window", "total_input_tokens")
size = get("context_window", "context_window_size")
pct = get("context_window", "used_percentage")
if isinstance(size, (int, float)) and size > 0 and isinstance(used_tok, (int, float)):
    if not isinstance(pct, (int, float)):
        pct = 100.0 * used_tok / size
    p = max(0, min(100, int(pct)))
    moon = "\U0001f315" if p <= 51 else "\U0001f314" if p <= 77 else "\U0001f313" if p <= 90 else "\U0001f312" if p <= 97 else "\U0001f311"
    segs.append(f"{moon} {k(used_tok)}/{k(size)}")

print("  ".join(segs))
PY
)"

if [ -n "$bar" ] && [ -n "$cache" ]; then
  printf ' %s  %s\n' "$bar" "$cache"
elif [ -n "$bar" ]; then
  printf ' %s\n' "$bar"
else
  printf ' %s\n' "$cache"
fi
exit 0
