#!/usr/bin/env bash
# guidance_attribution_advisory.sh — PostToolUse (Edit|Write|MultiEdit)
#
# When an edit to an instruction file adds a line that opens with a person's
# name and a date, this quotes the line and says how it reads later: as one
# person's note from one day, which a later session weighs as dated and
# possibly drifted, not as standing guidance. Instruction files are read as
# standing guidance at every session start, so a dated note in one keeps
# speaking long after the day it was written.
#
# Files: AGENTS.md, CLAUDE.md and CLAUDE.local.md anywhere, in a repo or not,
# and any .md under a .claude/ directory. Every other path exits on the raw
# payload, before Python starts.
#
# Lines: only those this call added. For Edit and MultiEdit, the lines of the
# new text whose stripped form is not a line of the old text. For Write, the
# lines not in the file's git HEAD version when it is tracked, otherwise all of
# the content. Lines inside a fenced code block are skipped.
#
# A match opens, after optional list, quote, dash or emphasis markers, with a
# capitalised name of one to three words, then a date: YYYY-MM-DD,
# Mon D, YYYY, Month D, YYYY or D Month YYYY. A date later in a
# sentence, or a date with no name before it, does not match.
#
# Once per file per session; the marker is written only when the note is said.
# SCRY_GUIDANCE_ATTRIBUTION=0 turns it off. Exits zero always and never
# instructs.
set -uo pipefail

[ "${SCRY_GUIDANCE_ATTRIBUTION:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"

case "$payload" in
  *AGENTS.md*|*CLAUDE.md*|*CLAUDE.local.md*|*.claude/*) ;;
  *) exit 0 ;;
esac

SCRY_PAYLOAD="$payload" python3 - <<'PY' 2>/dev/null || true
import hashlib, json, os, re, subprocess

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

base = os.path.basename(path)
parts = path.replace("\\", "/").split("/")
if not (base in ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md")
        or (base.endswith(".md") and ".claude" in parts[:-1])):
    raise SystemExit(0)

def added(new, old):
    if not isinstance(new, str):
        return []
    seen = {x.strip() for x in old.splitlines()} if isinstance(old, str) else set()
    return [x for x in new.splitlines() if x.strip() and x.strip() not in seen]

def head_version(p):
    folder = os.path.dirname(os.path.abspath(p))
    try:
        r = subprocess.run(["git", "-C", folder, "show", "HEAD:./" + os.path.basename(p)],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return r.stdout if r.returncode == 0 else None

lines, snippet = [], None
if tool == "Write":
    snippet = ti.get("content")
    lines = added(snippet, head_version(path))
elif tool == "Edit":
    lines = added(ti.get("new_string"), ti.get("old_string"))
    snippet = ti.get("new_string")
else:
    news = []
    for e in ti.get("edits") or []:
        if isinstance(e, dict):
            lines += added(e.get("new_string"), e.get("old_string"))
            if isinstance(e.get("new_string"), str):
                news.append(e["new_string"])
    snippet = "\n".join(news)
if not lines:
    raise SystemExit(0)

try:
    with open(path, encoding="utf-8", errors="replace") as f:
        text, numbered = f.read(2_000_000), True
except Exception:
    text, numbered = snippet if isinstance(snippet, str) else "", tool == "Write"

MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?"
         r"|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?")
NOT_NAME = (r"(?:" + MONTH[:-3] + r"|Mon|Tue|Wed|Thu|Fri|Sat|Sun|Monday|Tuesday|Wednesday"
            r"|Thursday|Friday|Saturday|Sunday|Updated|Released|Revised|Since|Until"
            r"|From|On|As|Last|Next|Due|Dated?|Effective|Before|After)")
WORD = r"(?!" + NOT_NAME + r"\b)[A-Z][A-Za-z'’.-]*"
DATE = (r"(?:(?:19|20)\d\d-\d\d-\d\d(?![\w-])"
        r"|" + MONTH + r"\s+\d{1,2},?\s+\d{4}(?!\d)"
        r"|\d{1,2}\s+" + MONTH + r"\s+\d{4}(?!\d))")
ATTRIBUTION = re.compile(
    r"^[\s>*_\-—–]*" + WORD + r"(?:[ ]+" + WORD + r"){0,2}[*_]*"
    r"(?:\s*[,:]\s*|\s+)" + DATE)

fresh = {x.strip() for x in lines}
hit = None
fence = None
for i, x in enumerate(text.splitlines(), 1):
    s = x.strip()
    m = re.match(r"(?:>\s*)*(`{3,}|~{3,})", s)
    if m:
        mark = m.group(1)
        if fence is None:
            fence = mark
        elif mark[0] == fence[0] and len(mark) >= len(fence):
            fence = None
        continue
    if fence is None and s in fresh and ATTRIBUTION.match(x):
        hit = (i, s)
        break
if hit is None:
    raise SystemExit(0)

tmp = os.environ.get("TMPDIR") or "/tmp"
session = d.get("session_id") or os.environ.get("CLAUDE_SESSION_ID") or str(os.getppid())
marker_dir = os.path.join(tmp, "scry-guidance-attribution-" + str(session).replace("/", "_"))
marker = os.path.join(marker_dir, hashlib.sha1(path.encode()).hexdigest()[:16])
if os.path.exists(marker):
    raise SystemExit(0)

home = os.path.expanduser("~")
shown = "~" + path[len(home):] if home and path.startswith(home + "/") else path
quote = hit[1] if len(hit[1]) <= 200 else hit[1][:197] + "..."
where = f"line {hit[0]} of {shown}" if numbered else f"a line this edit added to {shown}"

note = (f"Scry: {where} opens with a name and a date: \"{quote}\". "
        "A line that opens with a name and a date reads as one person's note from "
        "one day; a later session weighs it as dated and possibly drifted from how "
        "things stand now, not as standing guidance.")

os.makedirs(marker_dir, exist_ok=True)
open(marker, "w").close()
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": note,
    },
    "suppressOutput": True,
}))
PY

exit 0
