#!/usr/bin/env bash
# agent_start_folder_guard.sh — PreToolUse (Bash), Claude Code and Codex.
#
# An agent's start folder decides which folder pages (AGENTS.md, CLAUDE.md)
# it loads. Codex and pi read AGENTS.md from the start folder and its parents
# only; Claude Code reads a lower folder's CLAUDE.md only once it reads a file
# there. An agent started at a repository's top, in a repo whose domains keep
# their own page lower down, starts without the page of the domain it works
# in, and a brief that names the domain does not change that.
#
# Herdr is how sessions start other agents, and its defaults (split with the
# caller's cwd, a worktree opened at its root) land at the top. So when a Bash
# command starts an agent through Herdr -- `herdr agent start ... --pane ID`,
# or `herdr pane run ID "<agent cli> ..."` -- and the pane's folder has tracked
# folder pages BELOW it, the first start is turned down once with what Scry
# sees: the start folder, the folders below it that hold a page, and any of
# them the command itself names. The same start again within the window goes
# through. Only a deny arrives in time: a PreToolUse note reaches the session
# after the agent has already started.
#
# Fails open: not a Herdr start, a pane id held in a shell variable, `--machine`
# (a remote pane), herdr or git unavailable, a folder outside a git repo, no
# page below the folder. SCRY_AGENT_START_GUARD=0 turns it off.
# SCRY_AGENT_START_WINDOW (default 3600) is how long the second start stays free.
# SCRY_HERDR_BIN (default herdr) is the binary asked for a pane's folder.
set -uo pipefail

[ "${SCRY_AGENT_START_GUARD:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

# Every Bash call in every repo passes through here: no interpreter unless
# the command could be a Herdr start.
case "$payload" in
  *herdr*) ;;
  *) exit 0 ;;
esac

START_PAYLOAD="$payload" \
SCRY_WINDOW="${SCRY_AGENT_START_WINDOW:-3600}" \
SCRY_HERDR="${SCRY_HERDR_BIN:-herdr}" \
SCRY_STATE="${SCRY_AGENT_START_STATE:-${TMPDIR:-/tmp}/scry-agent-start}" \
python3 - <<'PY' 2>/dev/null || true
import json, os, re, shlex, subprocess, sys, time

try:
    d = json.loads(os.environ["START_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Bash":
    sys.exit(0)
command = str((d.get("tool_input") or {}).get("command") or "")
sid = str(d.get("session_id") or "")
if "herdr" not in command:
    sys.exit(0)

# Agent CLIs a `pane run` may start: Herdr's own agent kinds, plus pi-local.
AGENT_CLIS = {
    "pi", "pi-local", "claude", "codex", "gemini", "cursor", "cursor-agent", "devin",
    "agy", "cline", "omp", "mastracode", "opencode", "copilot", "kimi", "kiro",
    "droid", "amp", "grok", "hermes", "kilo", "qodercli", "qwen", "letta", "maki", "muse",
}


def words(text):
    try:
        lex = shlex.shlex(text, posix=True, punctuation_chars=";&|")
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return []


def segments(toks):
    seg = []
    for t in toks:
        if t and set(t) <= set(";&|"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(t)
    if seg:
        yield seg


def starts(cmd):
    """(pane id, subfolder or '') for each agent start in the command."""
    out = []
    for seg in segments(words(cmd)):
        if not seg or os.path.basename(seg[0]) != "herdr" or "--machine" in seg:
            continue
        rest = seg[1:]
        i = 0
        while i < len(rest) and rest[i].startswith("--"):  # --session NAME and the like
            i += 2
        rest = rest[i:]
        if rest[:2] == ["agent", "start"]:
            head = rest[: rest.index("--")] if "--" in rest else rest
            if "--pane" in head:
                j = head.index("--pane")
                if j + 1 < len(head):
                    out.append((head[j + 1], ""))
        elif rest[:2] == ["pane", "run"] and len(rest) >= 4:
            sub = ""
            for inner in segments(words(rest[3])):
                if inner[0] == "cd" and len(inner) > 1:
                    sub = inner[1]
                    continue
                if os.path.basename(inner[0]) in AGENT_CLIS:
                    out.append((rest[2], sub))
                break
    return out


found = starts(command)
if not found:
    sys.exit(0)
here = str(d.get("cwd") or "")


def split_folder():
    """A pane id held in a shell variable usually comes from a `pane split` in
    the same command: its --cwd (with $PWD read as this session's folder), or
    this session's folder when the split names none."""
    for seg in segments(words(command)):
        if "split" not in seg or "pane" not in seg:
            continue
        if "--cwd" in seg:
            k = seg.index("--cwd")
            if k + 1 < len(seg):
                v = seg[k + 1]
                return here if v in ("$PWD", "${PWD}") else ("" if "$" in v else v)
        return here
    return ""


def pane_folder(pane):
    if "$" in pane or "`" in pane:
        return split_folder()
    try:
        r = subprocess.run([os.environ["SCRY_HERDR"], "pane", "get", pane],
                           capture_output=True, text=True, timeout=4)
        p = json.loads(r.stdout)["result"]["pane"]
    except Exception:
        return ""
    return str(p.get("foreground_cwd") or p.get("cwd") or "")


def git(folder, *args):
    try:
        r = subprocess.run(["git", "-C", folder, *args], capture_output=True, text=True, timeout=4)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None


for pane, sub in found:
    folder = pane_folder(pane)
    if not folder:
        continue
    if sub:
        folder = os.path.normpath(os.path.join(folder, os.path.expanduser(sub)))
    if not os.path.isdir(folder):
        continue
    top = (git(folder, "rev-parse", "--show-toplevel") or "").strip()
    listed = git(folder, "ls-files", "--", "*AGENTS.md", "*CLAUDE.md")
    if not top or listed is None:
        continue
    below = sorted({os.path.dirname(p) for p in listed.splitlines()
                    if os.path.basename(p) in ("AGENTS.md", "CLAUDE.md") and "/" in p})
    if not below:
        continue

    window = os.environ["SCRY_WINDOW"]
    window = int(window) if window.isdigit() and int(window) > 0 else 3600
    marks = os.environ["SCRY_STATE"]
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{sid}--{pane}--{folder}")
    marker = os.path.join(marks, key)
    try:
        if time.time() - os.path.getmtime(marker) < window:
            continue  # second start inside the window: let it through
    except OSError:
        pass

    # The folders below, grouped by parent so a repo with fifty domains stays one line each.
    groups = {}
    for b in below:
        parent, name = os.path.split(b)
        groups.setdefault(parent, []).append(name)
    shown = []
    for parent, names in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:4]:
        head = ", ".join(names[:8]) + (f", +{len(names) - 8} more" if len(names) > 8 else "")
        shown.append(f"{parent + '/' if parent else './'} ({head})")
    if len(groups) > 4:
        shown.append(f"+{len(groups) - 4} more parent folders")
    # A folder counts as named by its full path or, when nested, its last two parts.
    named = [b for b in below
             if b in command or (b.count("/") >= 1 and "/".join(b.split("/")[-2:]) in command)]

    where = "the top of its repository" if os.path.realpath(folder) == os.path.realpath(top) else "a folder above them"
    text = (f"Scry — this agent would start in {folder} ({where}). "
            f"{len(below)} folder{'s' if len(below) != 1 else ''} below it hold their own AGENTS.md or CLAUDE.md: "
            + "; ".join(shown) + ". "
            "Codex and pi read AGENTS.md from the start folder and its parents only; Claude Code reads a "
            "lower folder's CLAUDE.md only once it reads a file there. Started here, none of those pages "
            "is loaded at start, and a brief that names a domain does not change that.")
    if named:
        text += " This command names " + ", ".join(named[:3]) + "."
    text += (" This is a one-time pause, not a failure: the same start again goes through "
             f"(for {window // 60} min).")
    try:
        os.makedirs(marks, exist_ok=True)
        open(marker, "w").close()
    except OSError:
        sys.exit(0)  # could not record the pause: let the start through rather than refuse it twice
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": text,
        },
        "suppressOutput": True,
    }))
    sys.exit(0)
PY
exit 0
