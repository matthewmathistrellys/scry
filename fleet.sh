#!/usr/bin/env bash
# fleet.sh — Claude Code SessionStart hook: answers "what else is
# happening right now?" — the question no session can currently ask.
#
# Every agent session believes it is alone. It is told its directory and
# its git state, and nothing at all about the other sessions editing the
# same files on the same machine. That blindness is structural, not
# careless: no session can yield to another it cannot see. On 2026-07-26
# six Claude sessions and a Codex process were live on one laptop, five
# of them in the same repo, three older than 36 hours — and the branch
# sprawl that produced is documented in ~/Dev/CLAUDE.md.
#
# Reports three things, all read locally, no network:
#   1. Other sessions live in this repo FAMILY (the repo and every one of
#      its linked worktrees — they are one work stream, so a session in
#      .worktrees/foo is a neighbour, not a stranger).
#   2. Other agent CLIs on the box (Codex, Gemini, aider, ...) — they
#      compete for the same files and the same 8 cores.
#   3. What the previous session in THIS directory was doing, by title.
#
# On that third point: it emits the session TITLE only, never the last
# prompt or a content summary. A title is a label and reads as a label.
# Content from a session you cannot see reads as current context when it
# may have been reversed an hour ago, and acting on it is worse than not
# having it. Claude Code already writes the title itself, so this costs
# no inference and no tokens.
#
# Silence is the default and the point. A signal speaks only when it
# would change a decision; one lone session in a quiet repo prints
# nothing. See README "The output budget".
set -uo pipefail

# A session counts as live if its transcript was written within this many
# minutes. Transcript mtime is used rather than process liveness because
# it is exact and cheap: a `claude` process tells you nothing about which
# repo it is in without an lsof per pid, and a session idle for an hour
# is not competing with you even though its process is still resident.
ACTIVE_MINUTES=${SCRY_FLEET_ACTIVE_MINUTES:-15}

# Other agent CLIs worth reporting. Matched on the executable's basename,
# exactly — substring matching here is a false-positive machine (macOS
# ships AMPDeviceDiscoveryAgent, which contains "amp", and a
# CursorUIViewService that has nothing to do with the editor).
AGENT_BINARIES="codex gemini aider opencode goose cursor-agent amp crush"

emit() { # $1 = additionalContext (model-visible)
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

# SessionStart hooks receive their payload as JSON on stdin. We need
# session_id to exclude ourselves from the fleet (a session reporting
# itself as a collision is worse than useless) and cwd because $PWD is
# not guaranteed to be the session's directory.
payload="$(cat 2>/dev/null || true)"
read -r self_id session_cwd <<<"$(SCRY_PAYLOAD="$payload" python3 - <<'PY'
import json, os
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    d = {}
print(d.get("session_id", ""), d.get("cwd", "") or os.getcwd())
PY
)"
[ -n "$session_cwd" ] || session_cwd="$PWD"

# The repo family = the repo root plus every linked worktree. Falling back
# to cwd keeps this working outside a git repo at all.
repo_root="$(git -C "$session_cwd" rev-parse --show-toplevel 2>/dev/null || echo "$session_cwd")"
# A worktree's --show-toplevel is the worktree, not the primary; --git-common-dir
# resolves to the primary's .git, so its parent is the family root.
common="$(git -C "$session_cwd" rev-parse --git-common-dir 2>/dev/null || true)"
case "$common" in
  */.git) family_root="$(cd "$(dirname "$common")" 2>/dev/null && pwd)" ;;
  *)      family_root="$repo_root" ;;
esac

# Real worktree paths, so an encoded transcript directory can be reported
# as a path a human recognises. The encoding maps '/', '.' and '_' all to
# '-', so it cannot be reversed by string manipulation — but it can be
# matched against the paths git already knows about.
worktree_paths="$(git -C "$session_cwd" worktree list --porcelain 2>/dev/null \
  | sed -n 's/^worktree //p')"

SELF_ID="$self_id" SESSION_CWD="$session_cwd" FAMILY_ROOT="$family_root" \
ACTIVE_MINUTES="$ACTIVE_MINUTES" AGENT_BINARIES="$AGENT_BINARIES" \
WORKTREE_PATHS="$worktree_paths" \
python3 - <<'PY' > /tmp/.scry_fleet_$$ 2>/dev/null
import json, os, re, subprocess, time

self_id  = os.environ["SELF_ID"]
cwd      = os.environ["SESSION_CWD"]
family   = os.environ["FAMILY_ROOT"]
window   = int(os.environ["ACTIVE_MINUTES"]) * 60
agents   = set(os.environ["AGENT_BINARIES"].split())
now      = time.time()

def encode(path):
    # Claude Code's transcript directory name: '/', '.' and '_' all become
    # '-'. Verified against three real project dirs on 2026-07-26.
    return re.sub(r"[/._]", "-", path)

projects = os.path.expanduser("~/.claude/projects")
lines = []

# encoded-dir-name -> readable path, for every worktree git knows about.
known = {}
for p in (os.environ.get("WORKTREE_PATHS") or "").splitlines():
    p = p.strip()
    if p:
        known[encode(p)] = p

parent = os.path.dirname(family)

def readable(entry):
    """Report a transcript directory as a path a human recognises."""
    if entry in known:
        path = known[entry]
    else:
        # Not a worktree — a session started in some subdirectory. The
        # encoding is lossy, so only trust a reconstruction the filesystem
        # confirms; otherwise show the raw name rather than invent a path.
        guess = entry.replace("-", "/")
        path = guess if os.path.isdir(guess) else None
    if not path:
        return entry.lstrip("-")
    return os.path.relpath(path, parent) if path.startswith(parent) else path

# ── 1. Other live sessions in this repo family ──────────────────────────
family_prefix = encode(family)
self_prefix   = encode(cwd)
live, here, oldest_start = [], 0, None

if os.path.isdir(projects):
    for entry in os.listdir(projects):
        if not entry.startswith(family_prefix):
            continue
        d = os.path.join(projects, entry)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            # Top-level transcripts only. subagents/ live one level down and
            # are not independent sessions — counting them would inflate the
            # number with this session's own helpers.
            if not f.endswith(".jsonl"):
                continue
            sid = f[:-6]
            if sid == self_id:
                continue
            p = os.path.join(d, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            if now - st.st_mtime > window:
                continue
            start = getattr(st, "st_birthtime", st.st_ctime)
            if oldest_start is None or start < oldest_start:
                oldest_start = start
            live.append(readable(entry))
            if entry == self_prefix:
                here += 1

def human(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, m = divmod(rem // 60, 60)
    if d: return f"{d}d{h}h"
    if h: return f"{h}h{m:02d}m"
    return f"{m}m"

if live:
    from collections import Counter
    grouped = ", ".join(
        f"{name} ({n})" if n > 1 else name
        for name, n in sorted(Counter(live).items(), key=lambda kv: -kv[1])
    )
    age = f" Oldest has been running {human(now - oldest_start)}." if oldest_start else ""
    lines.append(
        f"- {len(live)} other Claude session(s) active in this repo family "
        f"in the last {window // 60} min: {grouped}.{age}"
    )
    if here:
        which = "one of them is" if here == 1 else f"{here} of them are"
        lines.append(
            f"- COLLISION RISK: {which} in THIS exact directory. "
            "Check before editing shared files, and do not assume a clean "
            "tree stays clean."
        )

# ── 2. Other agent CLIs on the machine ──────────────────────────────────
try:
    ps = subprocess.run(["ps", "-Ao", "etime=,comm="], capture_output=True,
                        text=True, timeout=3).stdout
except Exception:
    ps = ""
found = {}
for row in ps.splitlines():
    row = row.strip()
    if not row:
        continue
    etime, _, comm = row.partition(" ")
    base = os.path.basename(comm.strip())
    if base in agents:
        # ps prints [[DD-]HH:]MM:SS — report it the same way session ages
        # are reported, so two numbers side by side mean the same thing.
        days, _, clock = etime.strip().rpartition("-")
        parts = [int(x) for x in clock.split(":")] if clock else [0]
        while len(parts) < 3:
            parts.insert(0, 0)
        secs = (int(days or 0) * 86400) + parts[0] * 3600 + parts[1] * 60 + parts[2]
        found.setdefault(base, human(secs))
if found:
    listed = ", ".join(f"{n} (running {t})" for n, t in sorted(found.items()))
    lines.append(f"- Other agent CLI(s) also running on this machine: {listed}.")

# ── 3. What the previous session here was doing ─────────────────────────
# Title only — see the header comment on why content is deliberately excluded.
sd = os.path.join(projects, self_prefix)
if os.path.isdir(sd):
    best = None
    for f in os.listdir(sd):
        if not f.endswith(".jsonl") or f[:-6] == self_id:
            continue
        p = os.path.join(sd, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if now - st.st_mtime <= window:      # that's a live one, already counted
            continue
        if best is None or st.st_mtime > best[1]:
            best = (p, st.st_mtime)
    if best:
        title = None
        try:
            # Titles are rewritten as the session evolves, so the LAST one
            # is current. Read a bounded tail rather than the whole file —
            # transcripts run to megabytes and this is a startup hook.
            with open(best[0], "rb") as fh:
                fh.seek(0, 2)
                fh.seek(max(0, fh.tell() - 262144))
                chunk = fh.read().decode("utf-8", "ignore")
            for ln in reversed(chunk.splitlines()):
                try:
                    d = json.loads(ln)
                except Exception:
                    continue
                if d.get("type") == "ai-title" and d.get("aiTitle"):
                    title = d["aiTitle"]
                    break
        except Exception:
            pass
        if title:
            lines.append(
                f'- Last session in this directory: "{title}" '
                f"(ended {human(now - best[1])} ago)."
            )

print("\n".join(lines))
PY

body="$(cat /tmp/.scry_fleet_$$ 2>/dev/null)"
rm -f /tmp/.scry_fleet_$$
[ -n "$body" ] && emit "Session fleet (SessionStart):
$body"
exit 0
