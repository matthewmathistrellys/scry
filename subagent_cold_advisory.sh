#!/usr/bin/env bash
# subagent_cold_advisory.sh — PostToolUse (every tool), Claude Code.
#
# A subagent's prompt cache goes cold one TTL after its last request, and
# nothing wakes it: finished, nothing happens; still working, its next step
# re-reads its context once at the full rate; stuck, the only saving is to
# stop it before that read. The main agent can only act if it is told what the
# subagent is doing, and a file time cannot say that.
#
# So at the last quarter of SCRY_KEEPALIVE_FRESH_SECS (default 3600) of
# silence this hook starts a peek in the background and exits at once: peek.py
# reads the subagent's record, works out the facts line by script, has Haiku
# write the note, and stores both. peek_deliver.sh hands them to the main
# agent on its next hook, so nothing is ever woken to carry them. Every quiet
# subagent gets one, whatever its size (the size is in the facts line), except
# one that handed back with nothing left running: its facts line goes alone.
#
# Once per silence of a subagent. Silent inside a subagent (agent_id set).
# SCRY_PEEK=0, or the file ~/.claude/scry-peek.off, turns the peek off; the
# file is read on every run, so it reaches sessions already running.
set -uo pipefail

[ "${SCRY_PEEK:-1}" = "0" ] && exit 0
[ -n "${SCRY_PEEK_RUNNING:-}" ] && exit 0
[ -e "${SCRY_PEEK_OFF_FILE:-$HOME/.claude/scry-peek.off}" ] && exit 0

payload="$(cat 2>/dev/null || true)"
[ -n "$payload" ] || exit 0

FILE_PAYLOAD="$payload" \
SCRY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
SCRY_KA_FRESH="${SCRY_KEEPALIVE_FRESH_SECS:-3600}" \
SCRY_STATE="${SCRY_SUBAGENT_COLD_STATE_DIR:-${TMPDIR:-/tmp}/scry-subagent-cold}" \
python3 - <<'PY'
import glob, json, os, subprocess, sys, time

try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("agent_id"):
    sys.exit(0)
sid = d.get("session_id") or ""
tp = d.get("transcript_path") or ""
if not sid or not tp.endswith(".jsonl"):
    sys.exit(0)
sub = os.path.join(tp[:-len(".jsonl")], "subagents")
fresh = os.environ["SCRY_KA_FRESH"]
fresh = int(fresh) if fresh.isdigit() and int(fresh) > 0 else 3600
now = time.time()
state = os.environ["SCRY_STATE"]

sys.path.insert(0, os.environ["SCRY_ROOT"])
import peek

peek.maybe_sweep(state)
peek.maybe_sweep(peek.state_dir())

launch = []
for j in sorted(glob.glob(os.path.join(glob.escape(sub), "agent-*.jsonl"))):
    try:
        m = os.path.getmtime(j)
    except OSError:
        continue
    quiet = now - m
    if not (fresh * 3 // 4 <= quiet < fresh):
        continue
    aid = os.path.basename(j)[len("agent-"):-len(".jsonl")]
    # Exclusive create: two tool calls landing together must not both say it.
    # Keyed on the record's last write, so a subagent that wakes and goes quiet
    # again gets a peek of its own for that silence.
    marker = os.path.join(state, f"{sid}-{aid}-{int(m)}")
    try:
        os.makedirs(state, exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError:
        continue
    launch.append((sid, aid, j, quiet))
for sid_, aid, j, quiet in launch:
    peek.log_event(sid_, aid, "peek started")
    subprocess.Popen([sys.executable, os.path.join(os.environ["SCRY_ROOT"], "peek.py"), "run",
                      sid_, aid, j, str(int(quiet))],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
PY
exit 0
