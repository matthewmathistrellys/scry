#!/usr/bin/env bash
# shell_writes.sh — PreToolUse, PostToolUse and PostToolUseFailure (Bash)
#
# The write notes (the PostToolUse hooks bound to Edit or Write) see only the
# file tools, so a file a shell command changes gets no note. This gives it
# one by comparing the disk before and after each Bash tool call. The command text
# is never read: how the file was written does not matter, only that it
# changed.
#
# Before: when the command's folder is in a git work tree, HEAD and the
# `git status` list (untracked files one by one, ignored files left out) with
# each listed file's size and mtime; and, in or out of a repo, the size, mtime
# and a copy of ~/AGENTS.md, ~/CLAUDE.md and ~/.claude/CLAUDE.md. Status runs
# with --no-optional-locks, so it never takes the index lock the command may
# need. Kept under $TMPDIR by session and tool-use id.
#
# After: the same picture again. A file changed when it is new in the list or
# its size or mtime moved, when a commit made during the command names it and
# the file was written during the command, or when one of the home files
# moved. Commits are those between the two HEADs with a commit date no earlier
# than the snapshot, so a checkout or pull of older work is not counted; the
# mtime keeps out a commit of files edited before the command, which the
# commit does not touch on disk. Deleted files are left out.
#
# For each changed file, at most 10, the payload a file tool would have sent
# goes to every write hook in hooks/hooks.json, with that hook's timeout and
# one overall budget: a file that did not exist before as a Write with its
# content; any other as an Edit whose old text is the HEAD version (or the
# saved copy) and whose new text is the file now, so the hooks that read added
# lines find them. Over 256 KB the text is left out. The notes come back under
# one line naming the files. The comparison cannot tell which process changed
# a file, so the line says they changed while the command ran.
#
# Silent when no write hook speaks, and when there is no snapshot.
# SCRY_SHELL_WRITES=0 turns it off. Snapshots older than a day are swept on
# each run. Exits zero always.
set -uo pipefail

[ "${SCRY_SHELL_WRITES:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"

SCRY_PAYLOAD="$payload" SCRY_ROOT="$(cd "$(dirname "$0")" && pwd)" python3 - <<'PY' 2>/dev/null || true
import hashlib, json, os, re, subprocess, time
from concurrent.futures import ThreadPoolExecutor

CAP = 10
MAX_TEXT = 256 * 1024
BUDGET = 40
HOME_FILES = ("AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md")

try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    raise SystemExit(0)
if not isinstance(d, dict) or d.get("tool_name") != "Bash":
    raise SystemExit(0)
event = d.get("hook_event_name")
session, use = d.get("session_id"), d.get("tool_use_id")
if not session or not use:
    raise SystemExit(0)
cwd = d.get("cwd") or os.getcwd()

state = os.path.join(os.environ.get("TMPDIR") or "/tmp", "scry-shell-writes")
key = hashlib.sha1(f"{session}\0{use}".encode()).hexdigest()
snap_path = os.path.join(state, key + ".json")

def sweep():
    try:
        cutoff = time.time() - 86400
        for n in os.listdir(state):
            p = os.path.join(state, n)
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
    except Exception:
        pass

def git(top, *args):
    r = subprocess.run(["git", "--no-optional-locks", "-C", top, *args],
                       capture_output=True, timeout=10)
    return r.stdout if r.returncode == 0 else None

def stat(p):
    try:
        s = os.lstat(p)
    except OSError:
        return None
    if not os.path.isfile(p) or os.path.islink(p):
        return None
    return [s.st_size, s.st_mtime_ns]

def status(top):
    """[(path, xy)] in git's order; a rename lists its new path."""
    raw = git(top, "status", "--porcelain", "-z", "--untracked-files=all")
    if raw is None:
        return None
    out, parts, i = [], raw.decode("utf-8", "replace").split("\0"), 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) < 4:
            continue
        xy, path = e[:2], e[3:]
        if xy[0] in "RC":
            i += 1
        out.append((path, xy))
    return out

def head(top):
    raw = git(top, "rev-parse", "-q", "--verify", "HEAD")
    return raw.decode().strip() if raw else None

def picture():
    pic = {"time": time.time(), "top": None, "head": None, "status": {}, "home": {}}
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        top = r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        top = ""
    if top:
        st = status(top)
        if st is not None:
            pic["top"], pic["head"] = top, head(top)
            pic["order"] = [p for p, _ in st]
            pic["status"] = {p: [xy, stat(os.path.join(top, p))] for p, xy in st}
    home = os.path.expanduser("~")
    for name in HOME_FILES:
        p = os.path.join(home, name)
        s = stat(p)
        copy = None
        if s and s[0] <= MAX_TEXT:
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    copy = f.read()
            except Exception:
                pass
        pic["home"][p] = [s, copy]
    return pic

if event == "PreToolUse":
    sweep()
    try:
        pic = picture()
        os.makedirs(state, exist_ok=True)
        tmp = snap_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(pic, f)
        os.replace(tmp, snap_path)
    except Exception:
        pass
    raise SystemExit(0)

if event not in ("PostToolUse", "PostToolUseFailure"):
    raise SystemExit(0)
try:
    with open(snap_path) as f:
        old = json.load(f)
except Exception:
    raise SystemExit(0)
finally:
    try:
        os.remove(snap_path)
    except Exception:
        pass
    sweep()
started = time.monotonic()

# ── What changed ────────────────────────────────────────────────────────────
top = old.get("top")
changed = {}  # absolute path -> base text ("" for none, None when new)

def head_text(rev, rel):
    if not rev:
        return None
    raw = git(top, "show", f"{rev}:{rel}")
    return None if raw is None else raw.decode("utf-8", "replace")

if top and os.path.isdir(top):
    st = status(top) or []
    was = old.get("status") or {}
    new_head = head(top)
    names = []
    for rel, xy in st:
        now = stat(os.path.join(top, rel))
        if now is None:
            continue
        if rel not in was or was[rel][1] != now:
            names.append(rel)
    if new_head and new_head != old.get("head"):
        since = int(old.get("time", 0)) - 2
        written_since = int(old.get("time", 0) * 1e9) - 1_000_000
        span = new_head if not old.get("head") else f"{old['head']}..{new_head}"
        revs = (git(top, "rev-list", "--max-count=20", f"--since={since}", span) or b"").split()
        for rev in revs:
            raw = git(top, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z",
                      "--root", rev.decode()) or b""
            for rel in raw.decode("utf-8", "replace").split("\0"):
                now = stat(os.path.join(top, rel)) if rel and rel not in names else None
                if now and now[1] >= written_since:
                    names.append(rel)
    for rel in names:
        base = head_text(old.get("head"), rel)
        if base is None and rel in was and "D" not in was[rel][0]:
            base = ""  # untracked before the command, with no saved text
        changed[os.path.join(top, rel)] = base

for p, (s, copy) in (old.get("home") or {}).items():
    now = stat(p)
    if now is None or now == s:
        continue
    real = os.path.realpath(p)
    if any(os.path.realpath(c) == real for c in changed):
        continue
    changed[p] = None if s is None else (copy if copy is not None else "")

if not changed:
    raise SystemExit(0)

# ── The write hooks, from hooks.json ────────────────────────────────────────
root = os.environ.get("SCRY_ROOT") or ""
hooks = {}  # script -> [timeout, tools]
try:
    with open(os.path.join(root, "hooks", "hooks.json")) as f:
        groups = json.load(f)["hooks"]["PostToolUse"]
except Exception:
    raise SystemExit(0)
for g in groups:
    tools = set((g.get("matcher") or "").split("|")) & {"Edit", "Write"}
    if not tools:
        continue
    for h in g.get("hooks") or []:
        m = re.search(r"/([\w.-]+\.sh)\b", h.get("command") or "")
        if not m:
            continue
        entry = hooks.setdefault(m.group(1), [h.get("timeout") or 10, set()])
        entry[1] |= tools
if not hooks:
    raise SystemExit(0)

def text_of(p):
    try:
        if os.path.getsize(p) > MAX_TEXT:
            return None
        with open(p, "rb") as f:
            raw = f.read()
    except Exception:
        return None
    if b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", "replace")

def run(script, timeout, body):
    left = BUDGET - (time.monotonic() - started)
    if left <= 0.5:
        return ""
    try:
        r = subprocess.run(["bash", os.path.join(root, script)], input=body,
                           capture_output=True, text=True, cwd=cwd if os.path.isdir(cwd) else None,
                           timeout=min(float(timeout), left))
        out = json.loads(r.stdout) if r.stdout.strip() else {}
        ctx = (out.get("hookSpecificOutput") or {}).get("additionalContext")
        return ctx.strip() if isinstance(ctx, str) else ""
    except Exception:
        return ""

home = os.path.expanduser("~")
def shown(p):
    if top and p.startswith(top + "/"):
        return p[len(top) + 1:]
    return "~" + p[len(home):] if p.startswith(home + "/") else p

files = list(changed.items())
notes = []
with ThreadPoolExecutor(max_workers=8) as pool:
    for p, base in files[:CAP]:
        if time.monotonic() - started >= BUDGET:
            break
        now = text_of(p)
        if base is None:
            tool, ti = "Write", {"file_path": p}
            if now is not None:
                ti["content"] = now
        else:
            tool, ti = "Edit", {"file_path": p}
            if now is not None and len(base) <= MAX_TEXT:
                ti["old_string"], ti["new_string"] = base, now
        body = json.dumps({"session_id": session, "transcript_path": d.get("transcript_path"),
                           "cwd": cwd, "hook_event_name": "PostToolUse",
                           "tool_name": tool, "tool_input": ti})
        jobs = [pool.submit(run, s, t, body) for s, (t, tools) in hooks.items() if tool in tools]
        notes += [n for n in (j.result() for j in jobs) if n]

if not notes:
    raise SystemExit(0)

n = len(files)
lead = (f"Scry: {n} file{'' if n == 1 else 's'} changed while that command ran: "
        + ", ".join(shown(p) for p, _ in files[:CAP])
        + (f", and {n - CAP} more not read" if n > CAP else "") + ". "
        "Each note below is the one a file-tool write of that file gets.")
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": "\n\n".join([lead] + notes),
    },
    "suppressOutput": True,
}))
PY

exit 0
