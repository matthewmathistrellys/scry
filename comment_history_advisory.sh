#!/usr/bin/env bash
# comment_history_advisory.sh — PostToolUse (Edit|Write|MultiEdit)
#
# When an edit to a source file adds comment or doc text that names who decided
# or asked, carries a date, or tells the story of how something was found, this
# says what that costs a later reader. Read later, a name and a date look like
# that person's standing rule, and the line stays the same when the decision
# changes. Git and the pull request already hold who and when.
#
# Only the added lines are read: lines of the new text whose stripped form is
# not a line of the old text. A comment line starts with #, //, /*, *, -- or
# <!--, or sits inside a """ block (@moduledoc, @doc, a docstring). Doctests,
# sigil dates and dates inside a path are skipped.
#
# Measured on one week of transcripts: 38 of 85 code edits that added comment
# text added a name, a date or an incident story, in 13 of 17 sessions, and all
# were shipped as written. The rule caught 36 of the 38 with no false alarm
# among the other 47. On 12,215 existing comment blocks it was not tuned on,
# 54 of 56 sampled hits were history, and it found about 71% of the history.
#
# The note was chosen by a blind test on 18 real flagged edits, three runs each:
# asked only to reconsider, a session left the reason and dropped the history
# 59% of the time; with this note, 70%, and no more reasons were lost.
#
# Source files only, tests included. Silent for Markdown and for paths under
# $TMPDIR, /tmp/ or a scratchpad/ directory. Once per file per session.
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

lines = []
if tool == "Write":
    lines = added(ti.get("content"), None)
elif tool == "Edit":
    lines = added(ti.get("new_string"), ti.get("old_string"))
else:
    for e in ti.get("edits") or []:
        if isinstance(e, dict):
            lines += added(e.get("new_string"), e.get("old_string"))

COMMENT_START = re.compile(r"^\s*(?:#|//|/\*|\*|--|<!--)")
DOC_OPEN = re.compile(r'@(?:module)?doc\s+"""|^\s*(?:r|f)?"""')

def comment_lines(added_lines):
    out, inq = [], False
    for x in added_lines:
        s = x.strip()
        if inq:
            out.append(x)
            if '"""' in s or "'''" in s:
                inq = False
            continue
        if DOC_OPEN.search(x) or (s.endswith('"""') and s.count('"""') == 1):
            out.append(x)
            inq = s.count('"""') == 1
            continue
        if COMMENT_START.match(x) or (re.search(r"\s#\s", x) and ext in (".ex", ".exs", ".py", ".sh")):
            out.append(x)
    return out

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

if not any(DATE.search(x) or NAMED.search(x) or STORY.search(x)
           for x in comment_lines(lines) if not LITERAL.search(x)):
    raise SystemExit(0)

session = d.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or str(os.getppid())
marker_dir = os.path.join(tmp or "/tmp", "scry-comment-history-" + str(session).replace("/", "_"))
os.makedirs(marker_dir, exist_ok=True)
marker = os.path.join(marker_dir, hashlib.sha1(path.encode()).hexdigest()[:16])
if os.path.exists(marker):
    raise SystemExit(0)
open(marker, "w").close()

note = (
    "Scry: the comment text this edit added names who decided or asked, a date, or "
    "the story of how something was found. Read later, a name and a date in code look "
    "like that person's standing rule, which nobody can check from the code, and the "
    "line stays the same when the decision changes; git and the pull request already "
    "hold who and when. In one test, wrong documentation hurt language models' "
    "understanding of code far more than missing documentation did. The reason, and "
    "any measured fact behind it, read the same without the name, the date or the story."
)
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": note,
    },
    "suppressOutput": True,
}))
PY

exit 0
