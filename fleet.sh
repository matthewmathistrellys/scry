#!/usr/bin/env bash
# fleet.sh — Claude Code / Codex SessionStart hook: answers "what else is
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
#   1. Other Claude and Codex sessions live in this repo FAMILY (the repo
#      and every one of
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
read -r self_id session_cwd transcript_path <<<"$(SCRY_PAYLOAD="$payload" python3 - <<'PY'
import json, os
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    d = {}
print(d.get("session_id", ""), d.get("cwd", "") or os.getcwd(),
      d.get("transcript_path", "") or "")
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

fleet_tmp="$(mktemp "${TMPDIR:-/tmp}/scry-fleet.XXXXXX")" || exit 0
trap 'rm -f "$fleet_tmp"' EXIT
SELF_ID="$self_id" SESSION_CWD="$session_cwd" FAMILY_ROOT="$family_root" \
TRANSCRIPT_PATH="$transcript_path" ACTIVE_MINUTES="$ACTIVE_MINUTES" \
AGENT_BINARIES="$AGENT_BINARIES" WORKTREE_PATHS="$worktree_paths" \
python3 - >"$fleet_tmp" 2>/dev/null <<'PY'
import glob, json, os, re, subprocess, time

self_id  = os.environ["SELF_ID"]
cwd      = os.path.realpath(os.environ["SESSION_CWD"])
family   = os.path.realpath(os.environ["FAMILY_ROOT"])
window   = int(os.environ["ACTIVE_MINUTES"]) * 60
agents   = set(os.environ["AGENT_BINARIES"].split())
now      = time.time()
transcript_path = os.environ.get("TRANSCRIPT_PATH", "")

def encode(path):
    # Claude Code's transcript directory name: '/', '.' and '_' all become
    # '-'. Verified against three real project dirs on 2026-07-26.
    return re.sub(r"[/._]", "-", path)

projects = os.path.expanduser("~/.claude/projects")
codex_home = os.path.expanduser(os.environ.get("CODEX_HOME", "~/.codex"))
lines = []

# encoded-dir-name -> readable path, for every worktree git knows about.
known = {}
for p in (os.environ.get("WORKTREE_PATHS") or "").splitlines():
    p = os.path.realpath(p.strip())
    if p:
        known[encode(p)] = p

parent = os.path.dirname(family)

def resolve_path(entry):
    """Absolute path an encoded transcript dir represents, if resolvable.
    Not a worktree — a session started in some subdirectory. The encoding
    is lossy, so only trust a reconstruction the filesystem confirms;
    otherwise there is no path to give back."""
    if entry in known:
        return known[entry]
    guess = entry.replace("-", "/")
    return guess if os.path.isdir(guess) else None

def readable(entry):
    """Report a transcript directory as a path a human recognises."""
    path = resolve_path(entry)
    if not path:
        return entry.lstrip("-")
    return os.path.relpath(path, parent) if path.startswith(parent) else path

# Which known worktree root contains a path, if any — longest match first
# so a nested worktree wins over its parent. This is what makes "here"
# mean "the same checkout" rather than "the same directory": two sessions
# in /repo and /repo/apps/web are not in the same directory, but a
# checkout/reset/clean in one is just as destructive to the other's
# uncommitted work, because it's the same working tree either way.
worktree_roots = sorted(known.values(), key=len, reverse=True)

def worktree_of(path):
    if not path:
        return None
    path = os.path.realpath(path)
    for root in worktree_roots:
        if path == root or path.startswith(root + os.sep):
            return root
    return None

# ── 1. Other live sessions in this repo family ──────────────────────────
family_prefix = encode(family)
self_prefix   = encode(cwd)
self_worktree = worktree_of(cwd)
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
            if self_worktree and worktree_of(resolve_path(entry)) == self_worktree:
                here += 1

def human(seconds):
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, m = divmod(rem // 60, 60)
    if d: return f"{d}d{h}h"
    if h: return f"{h}h{m:02d}m"
    return f"{m}m"

# ── 1b. Subagents working in this family ────────────────────────────────
# A subagent is not a session, but it IS a concurrent writer, and it does
# not necessarily work where its parent session lives: a session in the
# repo root routinely dispatches one into a worktree. Counting only
# top-level transcripts therefore reports an actively-edited worktree as
# empty — verified 2026-07-26, when a worktree with 28k files touched in
# four hours had no session cwd'd into it at all. So subagents are read
# separately and attributed by the cwd they record for themselves, never
# by their parent's.
def tail_cwds(path, limit=65536):
    """Every cwd a transcript recorded recently. Bounded read — these
    reach MBs. Returns a set, not the latest, deliberately: a subagent
    moves between directories (repo root, then a worktree), and for a
    collision check the question is "has it been editing here", not
    "where is it standing now". Erring toward a false warning costs a
    line of output; erring the other way costs a lost edit."""
    seen = set()
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - limit))
            chunk = fh.read().decode("utf-8", "ignore")
        for ln in chunk.splitlines():
            try:
                d = json.loads(ln)
            except Exception:
                continue
            if d.get("cwd"):
                seen.add(d["cwd"])
    except Exception:
        pass
    return seen

sub_here, sub_elsewhere = 0, []
import glob as _glob
for p in _glob.glob(os.path.join(projects, "*", "*", "subagents", "*.jsonl")):
    # Our own subagents are not a collision with ourselves.
    if self_id and os.sep + self_id + os.sep in p:
        continue
    try:
        if now - os.stat(p).st_mtime > window:
            continue
    except OSError:
        continue
    seen = tail_cwds(p)
    if not seen:
        continue
    if self_worktree and any(worktree_of(c) == self_worktree for c in seen):
        sub_here += 1
        continue
    inside = [c for c in seen if c == family or c.startswith(family + os.sep)]
    if inside:
        # One subagent, one entry — report the deepest path it touched,
        # which is the most specific thing true about it.
        sub_elsewhere.append(os.path.relpath(max(inside, key=len), parent))

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

# Codex rollouts expose all fleet fields needed in their first session_meta
# record. Read only that record: conversation content is neither needed nor a
# stable interface. File mtime is the activity signal, matching Claude above.
codex_live, codex_here, codex_oldest = [], 0, None
codex_sub_here, codex_sub_elsewhere = 0, []
for p in glob.glob(os.path.join(codex_home, "sessions", "*", "*", "*", "*.jsonl")):
    try:
        st = os.stat(p)
        if now - st.st_mtime > window:
            continue
        with open(p, encoding="utf-8") as fh:
            meta = json.loads(fh.readline()).get("payload", {})
    except Exception:
        continue
    sid = meta.get("id") or meta.get("session_id") or ""
    if sid == self_id or (
        transcript_path
        and os.path.realpath(p) == os.path.realpath(transcript_path)
    ):
        continue
    raw_cwd = meta.get("cwd") or ""
    if not raw_cwd:
        continue
    other_cwd = os.path.realpath(raw_cwd)
    if not (other_cwd == family or other_cwd.startswith(family + os.sep)):
        continue
    other_wt = worktree_of(other_cwd)
    is_subagent = meta.get("thread_source") == "subagent" or isinstance(meta.get("source"), dict)
    if is_subagent:
        if self_worktree and other_wt == self_worktree:
            codex_sub_here += 1
        else:
            codex_sub_elsewhere.append(os.path.relpath(other_cwd, parent))
        continue
    start = getattr(st, "st_birthtime", st.st_ctime)
    codex_oldest = start if codex_oldest is None else min(codex_oldest, start)
    codex_live.append(os.path.relpath(other_cwd, parent))
    if self_worktree and other_wt == self_worktree:
        codex_here += 1

if codex_live:
    from collections import Counter
    grouped = ", ".join(
        f"{name} ({n})" if n > 1 else name
        for name, n in sorted(Counter(codex_live).items(), key=lambda kv: -kv[1])
    )
    age = f" Oldest has been running {human(now - codex_oldest)}." if codex_oldest else ""
    lines.append(
        f"- {len(codex_live)} other Codex session(s) active in this repo family "
        f"in the last {window // 60} min: {grouped}.{age}"
    )

if codex_sub_here or codex_sub_elsewhere:
    from collections import Counter
    total = codex_sub_here + len(codex_sub_elsewhere)
    where = Counter(codex_sub_elsewhere)
    detail = ", ".join(f"{n} ({c})" if c > 1 else n for n, c in where.items())
    if codex_sub_here:
        detail = f"{codex_sub_here} in this directory" + (f"; {detail}" if detail else "")
    noun, verb = ("subagent", "is") if total == 1 else ("subagents", "are")
    lines.append(
        f"- {total} Codex {noun} from other sessions {verb} working in this "
        f"repo family: {detail}. Subagents edit files without a session of "
        f"their own, so a directory can be under active change with no session in it."
    )
if sub_elsewhere or sub_here:
    from collections import Counter
    total_subs = len(sub_elsewhere) + sub_here
    where = Counter(sub_elsewhere)
    detail = ", ".join(f"{n} ({c})" if c > 1 else n for n, c in where.items())
    if sub_here:
        detail = (f"{sub_here} in this directory" + (f"; {detail}" if detail else ""))
    noun = "subagent" if total_subs == 1 else "subagents"
    verb = "is" if total_subs == 1 else "are"
    lines.append(
        f"- {total_subs} {noun} from other sessions {verb} working in this "
        f"repo family: {detail}. Subagents edit files without a session of "
        f"their own, so a directory can be under active change with no "
        f"session in it."
    )

if here or sub_here or codex_here or codex_sub_here:
    n = here + sub_here + codex_here + codex_sub_here
    which = "one of them is" if n == 1 else f"{n} of them are"
    # The consequence, not the instruction: what a checkout/reset/clean
    # here would actually cost right now, stated as a fact about current
    # exposure — never as a recommendation to branch or worktree.
    dirty = 0
    try:
        st = subprocess.run(["git", "-C", cwd, "status", "--porcelain"],
                             capture_output=True, text=True, timeout=3).stdout
        dirty = len([ln for ln in st.splitlines() if ln.strip()])
    except Exception:
        pass
    exposure = (
        f" This tree has {dirty} file(s) modified or untracked right now — "
        "if either side commits or resets first, the other's changes are "
        "what's exposed."
    ) if dirty else ""
    lines.append(
        f"- COLLISION RISK: {which} working in this same tree, not just "
        f"this repo family.{exposure} Check before editing shared files, "
        "and do not assume a clean tree stays clean."
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
        found.setdefault(base, []).append(secs)
# The current client is necessarily present and is not "other". Subtract one
# process for it; session metadata above still reports additional same-client
# activity in this repository with much better attribution.
current_client = "codex" if f"{os.sep}.codex{os.sep}" in transcript_path else "claude"
if current_client in found and found[current_client]:
    found[current_client].pop(0)
    if not found[current_client]:
        del found[current_client]
if found:
    listed = ", ".join(
        f"{n} ({len(times)} running; oldest {human(max(times))})"
        for n, times in sorted(found.items())
    )
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
body="$(<"$fleet_tmp")"
rm -f "$fleet_tmp"
trap - EXIT
[ -n "$body" ] && emit "Session fleet (SessionStart):
$body"
exit 0
