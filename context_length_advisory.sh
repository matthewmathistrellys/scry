#!/usr/bin/env bash
# context_length_advisory.sh — UserPromptSubmit
#
# A session cannot see how much it is carrying. The status line reports the
# context size; the session never reads it. Every request re-reads the whole
# context, once the prompt cache has gone cold the next request writes it again
# at the full rate, and long inputs have been measured to cost accuracy
# gradually, below the window's limit, more for tasks that reason over the
# text than for looking up a literal phrase (Chroma "Context Rot", 2025;
# NoLiMa, 2025), on older Claude models; no measurement is published for
# current ones.
#
# So this speaks when the context size, as the status line last reported it,
# first reaches each of SCRY_CONTEXT_BANDS (tokens in thousands, default
# 150,300,500,750), once per band per session. It says the size, the share of
# the window, and how old the reading is. The bands are where a note is
# worth saying, not limits: where a session should reset depends on the work,
# and nothing here says it is past one.
#
# The size comes from the file cache_deadline_statusline.sh writes for the
# session. With no status-line wrapper installed, no file, a reading older
# than SCRY_CONTEXT_STALE_MINUTES (default 120), or a subagent's turn, it is
# silent; silence here is not a statement that the context is small.
# Dropping below a band (after a /compact) lets that band speak again.
# SCRY_CONTEXT_NOTES=0 turns it off. Exits zero always.
set -uo pipefail

[ "${SCRY_CONTEXT_NOTES:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

CTX_PAYLOAD="$payload" \
SCRY_CACHE_DIR="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}" \
SCRY_STATE="${SCRY_CONTEXT_STATE_DIR:-${TMPDIR:-/tmp}/scry-context-length}" \
SCRY_BANDS="${SCRY_CONTEXT_BANDS:-150,300,500,750}" \
SCRY_STALE="${SCRY_CONTEXT_STALE_MINUTES:-120}" \
python3 - <<'PY' 2>/dev/null || true
import json, os, re, sys, time

try:
    d = json.loads(os.environ["CTX_PAYLOAD"])
except (ValueError, KeyError):
    sys.exit(0)
sid = re.sub(r"[^\w.-]", "", d.get("session_id") or "")
if not sid or d.get("agent_id"):
    sys.exit(0)

try:
    with open(os.path.join(os.environ["SCRY_CACHE_DIR"], sid + ".deadline"), encoding="utf-8") as f:
        rec = json.load(f)
except (OSError, ValueError):
    sys.exit(0)
tokens, window, seen = rec.get("context_tokens"), rec.get("context_window"), rec.get("observed_at")
if not isinstance(tokens, int) or not isinstance(seen, (int, float)):
    sys.exit(0)
now = time.time()
stale = os.environ["SCRY_STALE"]
if now - seen > (int(stale) if stale.isdigit() and int(stale) > 0 else 120) * 60:
    sys.exit(0)

bands = sorted({int(b) * 1000 for b in re.findall(r"\d+", os.environ["SCRY_BANDS"]) if int(b) > 0})
reached = max([b for b in bands if tokens >= b], default=0)

path = os.path.join(os.environ["SCRY_STATE"], sid + ".said")
try:
    os.makedirs(os.environ["SCRY_STATE"], exist_ok=True)
    try:
        said = int(open(path, encoding="utf-8").read().strip() or 0)
    except (OSError, ValueError):
        said = 0
    speak = reached > said
    if speak or reached < said:
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(reached))
except OSError:
    sys.exit(0)
if not speak:
    sys.exit(0)

age = int(now - seen)
size = f"{tokens // 1000}k" + (f" of a {window // 1000}k window ({round(100 * tokens / window)}%)"
                              if isinstance(window, int) and window > 0 else "")
when = f", as of the last status-line refresh {age // 60} minutes ago" if age >= 120 else ""
text = (f"SCRY: this session's context is {size}{when}.\n"
        "Every request re-reads all of it, and once the prompt cache has gone cold the next request writes it again at the full rate. "
        "Long inputs have been measured to cost accuracy gradually, even below the window's limit, more for tasks that reason over the text than for looking up a literal phrase "
        "(Chroma 2025, NoLiMa 2025, on older Claude models; none is published for current ones). "
        "Work written to files can carry into a new session; work held only in this context may not.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text},
                  "suppressOutput": True}))
PY
exit 0
