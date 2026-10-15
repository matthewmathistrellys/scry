#!/usr/bin/env bash
# code_comment_advisory.sh — PostToolUse (Edit|Write|MultiEdit)
#
# When an edit to a source file adds comment or doc text, this says what keeps
# a comment true for a later reader, and what a comment loses when it does not.
# A code-only edit stays silent, whatever comments the file already holds.
#
# The lines an edit added are those of the new text whose stripped form is not
# a line of the old text; a Write adds all of its content. A comment line
# starts with #, //, /*, *, -- or <!--, or sits inside a """ block (@moduledoc,
# @doc, a docstring). Doctests, sigil dates and dates inside a path are skipped.
#
# Two kinds of comment get a sentence of their own, found in the added comment
# lines and, counted by block, in the rest of the file's comments: one that
# names a module or file other than this one (a dotted alias, a function with
# its arity, a source file name), leaving out this file's own modules and
# common library modules; and one that carries a name bound to a decision, a
# date, or the story of how something went wrong. On 300 labelled comment blocks, 81% of what the
# other-module rule flagged was labelled as describing other modules. A third
# kind, the normative rule word, was tried and left out: on 40 sampled hits,
# 7 stated a rule that nothing in the code checked.
#
# Source files only, tests included. Silent for Markdown and for paths under
# $TMPDIR, /tmp/ or a scratchpad/ directory. Once per file per session, and
# the marker is written only when the note is said.
#
# Exits zero always and never instructs.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

SCRY_PAYLOAD="$payload" python3 - <<'PY' 2>/dev/null || true
import hashlib, json, os, re

try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    raise SystemExit(0)
if not isinstance(d, dict):
    raise SystemExit(0)
tool = d.get("tool_name")
ti = d.get("tool_input")
if tool not in ("Edit", "Write", "MultiEdit") or not isinstance(ti, dict):
    raise SystemExit(0)
path = ti.get("file_path")
if not isinstance(path, str) or not path:
    raise SystemExit(0)

CODE = (".ex", ".exs", ".py", ".sh", ".ts", ".tsx", ".js", ".rb", ".go",
        ".heex", ".yml", ".yaml", ".toml")
ext = os.path.splitext(path)[1]
if ext not in CODE:
    raise SystemExit(0)
if "/tmp/" in path or "/scratchpad/" in path or path.startswith("scratchpad/"):
    raise SystemExit(0)
tmp = os.environ.get("TMPDIR") or ""
if tmp:
    for root in {tmp.rstrip("/"), os.path.realpath(tmp)}:
        if root and root != "/" and (path == root or path.startswith(root + "/")
                                     or os.path.realpath(path).startswith(root + "/")):
            raise SystemExit(0)

def added(new, old):
    if not isinstance(new, str):
        return []
    seen = {x.strip() for x in old.splitlines()} if isinstance(old, str) else set()
    return [x for x in new.splitlines() if x.strip() and x.strip() not in seen]

lines, text = [], None
if tool == "Write":
    text = ti.get("content")
    lines = added(text, None)
else:
    if tool == "Edit":
        lines = added(ti.get("new_string"), ti.get("old_string"))
    else:
        for e in ti.get("edits") or []:
            if isinstance(e, dict):
                lines += added(e.get("new_string"), e.get("old_string"))
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(2_000_000)
    except Exception:
        text = None
if not isinstance(text, str):
    text = ""

COMMENT_START = re.compile(r"^\s*(?:#|//|/\*|\*|--|<!--)")
DOC_OPEN = re.compile(r'@(?:module)?doc\s+"""|^\s*(?:r|f)?"""')

def comment_index(src):
    """Indexes of the comment and doc lines in src."""
    out, inq = [], False
    for i, x in enumerate(src):
        s = x.strip()
        if inq:
            out.append(i)
            if '"""' in s or "'''" in s:
                inq = False
            continue
        if DOC_OPEN.search(x) or (s.endswith('"""') and s.count('"""') == 1):
            out.append(i)
            inq = s.count('"""') == 1
            continue
        if COMMENT_START.match(x) or (re.search(r"\s#\s", x) and ext in (".ex", ".exs", ".py", ".sh")):
            out.append(i)
    return out

added_comments = [lines[i] for i in comment_index(lines)]
if not added_comments:
    raise SystemExit(0)

LITERAL = re.compile(r'iex>|~[UDNT]\[|^\s*"[^"]*"\s*$|\.csv"|\$\s')
DATE = re.compile(r"(?<![/\w-])20\d\d-\d\d-\d\d(?![\w-])")
NAMED = re.compile(
    r"\(\s*[A-Z][a-z]+(?: [A-Z][a-z]+)?(?: via [^,)]{1,30})?\s*[,;:]\s*20\d\d"
    r"|\b[A-Z][a-z]+(?:'s)? (?:decision|call|ruling|instruction|direction)\b"
    r"|\bper (?!the\b|a\b|an\b|[A-Z]{2,}|[a-z])[A-Z][a-z]+\b(?!\.)"
    r"|\bvia the (?:orchestrator|coordinat\w+)\b"
    r"|\b[A-Z][a-z]+ (?:asked|said|decided|wants|ruled)\b")
STORY = re.compile(
    r"\b(?:incident|born (?:from|of)|post-?mortem|outage)\b"
    r"|\bon 20\d\d-\d\d-\d\d(?![\w-])"
    r"|\b(?:observed|measured|calibrated|found|seen|proven|read over)\b[^.\n]{0,40}\b20\d\d-\d\d-\d\d",
    re.I)

LIBRARY = set("""Enum String Map Kernel List Keyword Integer Float Process Task
GenServer Agent Logger Application Code File Path IO System DateTime
NaiveDateTime Date Time Calendar Access Stream Regex Base URI Jason Ecto Repo
Phoenix Plug Oban Ash Req Finch Tesla Decimal MapSet Tuple Atom Module Registry
Supervisor DynamicSupervisor Mix ExUnit Function Port Node Inspect Exception
Record Bitwise Range Protocol Macro Version Config""".split())
ALIAS = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9]*(?:\.[A-Z][A-Za-z0-9]*)+)(?![\w])")
MFA = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9]*)\.[a-z_][a-z0-9_]*[!?]?/\d")
FILE = re.compile(r"(?<![\w-])([\w-]+\.(?:ex|exs|py|ts|tsx|js|sh|rb|go))(?![\w])")
own = set(re.findall(r"defmodule\s+([A-Z][\w.]*)", text + "\n" + "\n".join(lines)))
base = os.path.basename(path)

def other_file(x):
    for m in ALIAS.finditer(x):
        name = m.group(1)
        if name.split(".")[0] in LIBRARY:
            continue
        if any(name == o or o.startswith(name + ".") for o in own):
            continue
        return True
    for m in MFA.finditer(x):
        name = m.group(1)
        if name not in LIBRARY and not any(name == o or o.startswith(name + ".") for o in own):
            return True
    return any(m.group(1) != base for m in FILE.finditer(x))

def kinds(x):
    if LITERAL.search(x):
        return set()
    out = set()
    if other_file(x):
        out.add("other")
    if DATE.search(x) or NAMED.search(x) or STORY.search(x):
        out.add("history")
    return out

ORDER = ["other", "history"]
PHRASE = {
    "other": "describes another module or file",
    "history": "carries a name, a date or the story of a decision",
}
SENTENCE = {
    "other": "A comment about another module or file drifts when that file "
             "changes, because the edit happens elsewhere; in one codebase such "
             "comments had drifted from the code about one time in six, three times "
             "the rate for comments about their own file. A search for what it names "
             "shows whether it still matches; one that still matches tells the next "
             "reader exactly where to look, and that is lost when it is made vague.",
    "history": "A name, a date or the story of a decision reads later as a standing "
               "rule nobody can check from the code, and stays the same when the "
               "decision changes; git and the pull request already hold who and when.",
}

in_added = set()
for x in added_comments:
    in_added |= kinds(x)

# The rest of the file: comment blocks (runs of adjacent comment lines), with
# the added lines left out. A block counts once per kind.
in_file, file_blocks = set(), 0
if tool != "Write" and text:
    src = text.splitlines()
    fresh = {x.strip() for x in lines}
    idx = [i for i in comment_index(src) if src[i].strip() not in fresh]
    blocks, prev = [], None
    for i in idx:
        if prev is not None and i == prev + 1:
            blocks[-1].append(src[i])
        else:
            blocks.append([src[i]])
        prev = i
    for b in blocks:
        k = set()
        for x in b:
            k |= kinds(x)
        if k:
            file_blocks += 1
            in_file |= k

session = d.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or str(os.getppid())
marker_dir = os.path.join(tmp or "/tmp", "scry-code-comment-" + str(session).replace("/", "_"))
marker = os.path.join(marker_dir, hashlib.sha1(path.encode()).hexdigest()[:16])
if os.path.exists(marker):
    raise SystemExit(0)

found = [k for k in ORDER if k in in_added or k in in_file]
if found:
    here = f"already in this file ({file_blocks} comment{'' if file_blocks == 1 else 's'})"
    if in_added and in_file:
        where = f"this edit added, and {here},"
    elif in_added:
        where = "this edit added"
    else:
        where = here
    lead = f"Scry: comment text {where} {' and '.join(PHRASE[k] for k in found)}."
else:
    lead = "Scry: this edit adds comment text."

parts = [
    lead,
    "A later reader, human or model, relies on a comment as written: in one test "
    "of 17 models, plausible but misleading comments cut accuracy by about a "
    "third, while in another study missing documentation made no measurable "
    "difference. Almost all comment updates ship in the same revision as the code "
    "beside them, which is what keeps a comment in step with its code.",
]
parts += [SENTENCE[k] for k in found]
parts.append(
    "What lasts is what the code cannot show: the why, an invariant, a unit, a "
    "failure mode, a deliberate choice, and the measured fact behind it. Those "
    "read the same without the name or the date, and "
    "comments that plainly restate the code rarely drift.")

os.makedirs(marker_dir, exist_ok=True)
open(marker, "w").close()
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": " ".join(parts),
    },
    "suppressOutput": True,
}))
PY

exit 0
