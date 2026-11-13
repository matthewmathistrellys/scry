#!/usr/bin/env bash
# folder_pages.sh — SessionStart, and PostToolUse on Write|Edit of an
# AGENTS.md or CLAUDE.md. Claude Code and Codex.
#
# Claude Code loads CLAUDE.md files and never reads an AGENTS.md on its own;
# Codex and pi load AGENTS.md and never read CLAUDE.md. A folder whose page is
# an AGENTS.md with no CLAUDE.md beside it reaches Codex and pi sessions and
# no Claude session, and nothing anywhere says so: the page looks written and
# is not read. A CLAUDE.md that is a symlink to the AGENTS.md (or imports it
# with an `@AGENTS.md` line) gives both clients the same text.
#
# At SessionStart it reports two things:
#   1. Subfolders below the session's start folder that carry their own
#      AGENTS.md or CLAUDE.md, and that what they say is not in context during
#      early planning, search, naming, and builder briefs.
#   2. Any folder (from cwd up to home, or tracked in the repo) whose AGENTS.md
#      has no CLAUDE.md linked to or importing it.
# After a write to an AGENTS.md or CLAUDE.md it checks that one folder's pairing
# once per folder per session. Vendored trees (deps, node_modules, virtualenvs,
# plugin caches) are skipped.
#
# Advisory: exits zero, changes nothing. SCRY_FOLDER_PAGES=0 turns it off.
set -uo pipefail

[ "${SCRY_FOLDER_PAGES:-1}" = "0" ] && exit 0
payload="$(cat 2>/dev/null || true)"

PAGES_PAYLOAD="$payload" \
SCRY_STATE="${SCRY_FOLDER_PAGES_STATE:-${TMPDIR:-/tmp}/scry-folder-pages}" \
python3 - <<'PY' 2>/dev/null || true
import json, os, re, subprocess, sys

try:
    d = json.loads(os.environ.get("PAGES_PAYLOAD") or "{}")
except json.JSONDecodeError:
    d = {}
event = d.get("hook_event_name") or ("PostToolUse" if d.get("tool_name") else "SessionStart")
home = os.path.realpath(os.path.expanduser("~"))
cwd = os.path.realpath(d.get("cwd") or os.getcwd())
VENDORED = {"node_modules", "deps", "_build", ".venv", "venv", "site-packages", "vendor", ".git",
            ".elixir_ls", "dist", "build", "target", "__pycache__"}
SKIP_UNDER = [os.path.join(home, ".claude", "plugins")]


def vendored(folder):
    parts = set(folder.split(os.sep))
    return bool(parts & VENDORED) or any(folder.startswith(s + os.sep) or folder == s for s in SKIP_UNDER)


def state(folder):
    """'' when AGENTS.md is paired, else 'missing' or 'separate'."""
    agents = os.path.join(folder, "AGENTS.md")
    claude = os.path.join(folder, "CLAUDE.md")
    if not os.path.isfile(agents):
        return ""
    if not os.path.lexists(claude):
        return "missing"
    if os.path.realpath(claude) == os.path.realpath(agents):
        return ""
    try:
        with open(claude, encoding="utf-8", errors="replace") as f:
            if re.search(r"(?m)^\s*@\.?/?AGENTS\.md\s*$", f.read(200_000)):
                return ""
    except OSError:
        return ""
    return "separate"


def show(folder):
    if folder == home:
        return "~"
    if folder.startswith(home + os.sep):
        return "~/" + folder[len(home) + 1:]
    return folder


folders = []
below_folders = []
if event == "PostToolUse":
    fp = str((d.get("tool_input") or {}).get("file_path") or "")
    if os.path.basename(fp) not in ("AGENTS.md", "CLAUDE.md"):
        sys.exit(0)
    folder = os.path.realpath(os.path.dirname(os.path.abspath(fp)))
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{d.get('session_id') or 'nosession'}--{folder}")
    marker = os.path.join(os.environ["SCRY_STATE"], key)
    if os.path.exists(marker):
        sys.exit(0)
    folders = [folder]
else:
    # The session's folder and its parents, up to home (or the root, outside home).
    f = cwd
    while True:
        folders.append(f)
        if f == home or os.path.dirname(f) == f:
            break
        f = os.path.dirname(f)
    try:
        top = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=4)
        top = top.stdout.strip() if top.returncode == 0 else ""
        if top:
            ls = subprocess.run(["git", "-C", top, "ls-files", "--", "*AGENTS.md", "*CLAUDE.md"],
                                capture_output=True, text=True, timeout=4)
            for p in ls.stdout.splitlines():
                if os.path.basename(p) in ("AGENTS.md", "CLAUDE.md"):
                    full = os.path.realpath(os.path.join(top, os.path.dirname(p)))
                    if os.path.basename(p) == "AGENTS.md":
                        folders.append(full)
                    if full.startswith(cwd + os.sep) and not vendored(full):
                        below_folders.append(full)
    except Exception:
        top = ""
    if not top and cwd != home:
        seen = 0
        for root, dirs, files in os.walk(cwd):
            dirs[:] = [x for x in dirs if x not in VENDORED and not x.startswith(".")]
            if root[len(cwd):].count(os.sep) >= 4:
                dirs[:] = []
            seen += 1
            full = os.path.realpath(root)
            if "AGENTS.md" in files:
                folders.append(full)
            if ("AGENTS.md" in files or "CLAUDE.md" in files) and full.startswith(cwd + os.sep) and not vendored(full):
                below_folders.append(full)
            if seen > 3000:
                break

found = {}
for f in dict.fromkeys(folders):
    if vendored(f):
        continue
    s = state(f)
    if s:
        found.setdefault(s, []).append(f)

below = sorted({os.path.relpath(f, cwd) for f in below_folders})

if event == "PostToolUse":
    try:
        os.makedirs(os.environ["SCRY_STATE"], exist_ok=True)
        open(marker, "w").close()
    except OSError:
        pass
if not found and not below:
    sys.exit(0)


def listing(fs):
    names = [show(f) for f in fs[:6]]
    return ", ".join(names) + (f", +{len(fs) - 6} more" if len(fs) > 6 else "")


sections = []
if below:
    groups = {}
    for b in below:
        parent, name = os.path.split(b)
        groups.setdefault(parent, []).append(name)
    shown = []
    for parent, names in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:4]:
        head = ", ".join(names[:8]) + (f", +{len(names) - 8} more" if len(names) > 8 else "")
        shown.append(f"{parent + '/' if parent else './'} ({head})")
    if len(groups) > 4:
        shown.append(f"+{len(groups) - 4} more parent folders")
    n = len(below)
    sections.append(
        f"Scry — folder pages below {show(cwd)}: {n} folder{'s' if n != 1 else ''} under this "
        f"directory {'carry their' if n != 1 else 'carries its'} own AGENTS.md or CLAUDE.md: "
        + "; ".join(shown) + ". "
        "Codex and pi never load pages below the start folder, and Claude Code loads a subfolder's "
        "CLAUDE.md only after Read opens a file inside it — not during initial planning, Grep/Glob "
        "searches, or agent dispatch. Until a subfolder's page is in context, what it says is absent "
        "from early plans, search terms, names, and builder briefs."
    )

if found:
    parts = []
    if found.get("missing"):
        n = len(found["missing"])
        parts.append(f"{n} folder{'s have' if n != 1 else ' has'} an AGENTS.md and no CLAUDE.md: "
                     + listing(found["missing"]) + ".")
    if found.get("separate"):
        n = len(found["separate"])
        parts.append(f"{n} folder{'s have' if n != 1 else ' has'} a CLAUDE.md that is neither a link to its "
                     "AGENTS.md nor imports it (`@AGENTS.md`): " + listing(found["separate"])
                     + ". What the AGENTS.md says reaches a Claude session only if that CLAUDE.md's own "
                     "words lead the model to open it.")
    sections.append(
        "Scry — folder pages: " + " ".join(parts) +
        " Claude Code loads CLAUDE.md files and never reads an AGENTS.md on its own; Codex and pi load "
        "AGENTS.md and never read CLAUDE.md. What is written only in an AGENTS.md reaches Codex and pi "
        "sessions and no Claude session, and nothing shows it: it looks written and is not read. "
        "A CLAUDE.md that is a symlink to the AGENTS.md gives both clients the same text."
    )

print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n\n".join(sections)}}))
PY
exit 0
