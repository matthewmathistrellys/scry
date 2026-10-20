#!/usr/bin/env bash
# context_length_advisory.sh — UserPromptSubmit
#
# A session cannot see how much it is carrying. The status line reports the
# context size; the session never reads it. What a long context costs depends
# on the work: matching by meaning has been measured to lose accuracy earliest,
# review next, build and coding later, orchestration later still, and none of
# it has been measured on current Claude models. So the note is a ladder. Each
# rung adds the work type that matters from that size and updates the earlier
# ones, newest first:
#
#    60k   reading and classifying by meaning
#    80k   review
#   100k   build and coding
#   200k   orchestration
#   300k to 800k, every 100k   the types above, restated for the size
#   900k   all of it at once, beyond every measured length, near the window
#
# The marks sit on the context itself, since what a long input costs follows
# its length. The first two are set above the 40k or so a new session already
# starts with (Claude Code's own prompt and tools), so they do not fire at the
# first message. Each rung speaks once per session. Dropping below one (after a /compact) lets it speak again. Every
# line says whether it rests on a measurement (older models, other tasks) or is
# a prior; the ladder is the one table below, so new measurements change it in
# one place.
#
# The size comes from the file cache_deadline_statusline.sh writes for the
# session. With no status-line wrapper installed, no file, a reading older than
# SCRY_CONTEXT_STALE_MINUTES (default 120), or a subagent's turn, it is silent;
# silence is not a statement that the context is small.
# SCRY_CONTEXT_NOTES=0 turns it off. Exits zero always.
set -uo pipefail

[ "${SCRY_CONTEXT_NOTES:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

CTX_PAYLOAD="$payload" \
SCRY_CACHE_DIR="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}" \
SCRY_STATE="${SCRY_CONTEXT_STATE_DIR:-${TMPDIR:-/tmp}/scry-context-length}" \
SCRY_STALE="${SCRY_CONTEXT_STALE_MINUTES:-120}" \
python3 - <<'PY' 2>/dev/null || true
import json, os, re, sys, time

# ── The ladder ──────────────────────────────────────────────────────────────
# (size in tokens, work type introduced at this rung)
RUNGS = [(60_000, "read"), (80_000, "review"), (100_000, "build"), (200_000, "orch")] + \
        [(k * 1000, None) for k in range(300, 900, 100)] + [(900_000, "hard")]

# For each work type: the line when its rung is reached, and the line after.
# [measured] = a published measurement, on older models or other tasks;
# [prior] = reasoning from that evidence, not a measurement.
LINES = {
    "read": (
        "Reading and classifying by meaning: this kind of work has been measured to lose accuracy earliest as input grows. "
        "In Chroma's tests every model did better on a focused 300-token prompt than on the same question inside about 113k tokens, "
        "Claude models included, on older generations [measured].",
        "Reading and classifying by meaning: the measured losses (NoLiMa: 11 of 13 models below half their short-context score at 32k tokens) "
        "were taken at far shorter lengths than this; none exists for current Claude models [measured, other models]."),
    "review": (
        "Review: reasoning over text has been measured to lose accuracy earlier than looking up a phrase. "
        "A reviewer in a fresh context, holding the artifact and its criteria, is not anchored on the author's context [prior].",
        "Review: the context that wrote the work is the one now judging it; a reader in a fresh context is the arrangement the evidence favours [prior]."),
    "build": (
        "Build and coding: in one bug-fixing study (small non-Claude models), runs that passed averaged about 8-9k tokens and runs that failed about 16-18k; "
        "that is a correlation, since harder tasks also run longer [measured]. Work is often split by task, with state in files and commits [prior].",
        "Build and coding: a commit or a pull request puts the work on disk, and a new session can begin from it [prior]."),
    "orch": (
        "Orchestration: no study isolates it. Models have been measured losing about 39% on average in long multi-turn exchanges, mostly in reliability, "
        "and committing early to assumptions they often fail to recover from (Laban et al., 2025) [measured, other tasks]. "
        "Anthropic's long-running-agent guidance keeps the plan in files outside the context [prior].",
        "Orchestration: a plan and statuses held in files can be read by the next session; ones held only here may not be [prior]."),
}
ORDER = ["read", "review", "build", "orch"]
COST = ("Every request re-reads all of it, and once the prompt cache has gone cold the next request writes it again at the full rate.")
BEYOND = ("The long-input studies cited reach 32k tokens (NoLiMa) and 128k (RULER); what holds beyond that has not been measured [measured].")
HARD = ("Beyond every measured length, with the context near the window's limit, the earlier lines apply together; "
        "summaries written at compaction can lose instructions (Anthropic's long-running-agent post) [measured].")

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

path = os.path.join(os.environ["SCRY_STATE"], sid + ".json")
try:
    os.makedirs(os.environ["SCRY_STATE"], exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    reached = max([i for i, (size, _) in enumerate(RUNGS) if tokens >= size], default=-1)
    said = state.get("said", -1)
    speak = reached > said
    if speak or reached < said:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"said": reached}, f)
        os.replace(path + ".tmp", path)
except OSError:
    sys.exit(0)
if not speak:
    sys.exit(0)

introduced = {RUNGS[i][1]: i for i in range(reached + 1) if RUNGS[i][1]}
lines = []
for t in reversed(ORDER):                # newest work type first
    if t in introduced:
        lines.append(LINES[t][0] if introduced[t] > said else LINES[t][1])
age = int(now - seen)
size = f"{tokens // 1000}k" + (f" of a {window // 1000}k window ({round(100 * tokens / window)}%)"
                              if isinstance(window, int) and window > 0 else "")
when = (f", as of the last status-line refresh {age // 60} minutes ago" if age >= 120
        else ", as of its last reply")
extra = []
if tokens >= 500_000:
    extra.append(BEYOND)
if "hard" in introduced:
    extra.append(HARD)
text = "\n".join([f"SCRY: this session's context is {size}{when}.", *lines, COST, *extra])
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text},
                  "suppressOutput": True}))
PY
exit 0
