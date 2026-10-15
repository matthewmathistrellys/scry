#!/usr/bin/env bash
# cache_handoff_arm.sh — UserPromptSubmit hook: a new user message arms the
# cache-deadline summary request for one more cycle. Also a SessionStart hook:
# records which session this Claude process is running, for the monitor to
# follow. (The "handoff" in the name is from when the summary was a file; it
# is now written into the conversation.)
#
# `cache_handoff_monitor.sh` asks the session once, shortly before its prompt
# cache goes cold, to write a summary as its reply. Writing that summary is
# itself a request, and a request pushes the expiry forward an hour — so a
# monitor that re-armed on every warm cache would ask every hour, forever,
# with no user in the loop. The rule that stops that: ONLY a user message
# re-arms it. Tool calls, cache hits, monitor notifications, and the summary
# itself never do.
#
# The one bounded exception is the monitor's keep-alive: while work this
# session started is unfinished, the monitor sends the roster and asks for one
# status line instead of the summary, so the cache is warm when that work
# reports back. It does not re-arm anything either; it is counted and capped
# in the monitor (SCRY_KEEPALIVE_MAX per user message), and only an arm from
# this hook starts a new per-message count.
#
# UserPromptSubmit is not only user messages (Claude Code 2.1.270): it also
# runs for every queued notification — a background task finishing, a
# workflow completing, every plugin-monitor line (Scry's own summary request
# included), and a subagent's hand-back. The payload has no field that says
# which (session_id, transcript_path, cwd, prompt_id, permission_mode,
# agent_id, agent_type, effort, prompt, session_title — no origin). Arming on
# those means a workflow's completion arms the session with no user near it,
# and each summary request arms the next one an hour later.
#
# So this hook looks at the first bytes of `prompt` and nothing further: a
# prompt that opens with `<task-notification>`, `Another Claude session sent
# a message:`, or `<agent-message from=` does not arm. Those are the openings
# observed on non-human turns; a subagent's hand-back shows in the transcript
# with the "Another Claude session" preamble, but the hook's `prompt` drops
# it and opens with `<agent-message from="...">`. The prefix is compared and
# discarded — never stored, never logged, never printed. OWNER DECISION
# (AGENTS.md, metadata not content): this reads a prompt's first bytes,
# because no metadata field separates a user from a notification. The only
# other prompt reader is review_handback_advisory.sh, which reads a subagent's
# report and never a human message.
#
# Every run with CLAUDE_PID set also writes pid/<CLAUDE_PID> = "<session_id>
# <time>" — the session this process is running now — and appends the id to
# pid/<CLAUDE_PID>.sessions, every session the process has held. /clear
# changes the session inside a process whose monitor never restarts; this
# record, beside Claude Code's own ~/.claude/sessions/<pid>.json, is how the
# monitor keeps up.
#
# One report, and only here: if the previous cycle ended with the cache
# expiring before a summary was requested (the monitor saw the deadline pass
# while the machine slept, or the lead time was too short), that is said once
# on the next user turn. It is said here and not by the monitor because a
# monitor notification wakes the model — a cold request, the exact cost the
# summary request exists to avoid — while this turn is already happening.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
state_dir="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}"

SCRY_PAYLOAD="$payload" SCRY_DIR="$state_dir" SCRY_PID="${CLAUDE_PID:-}" python3 - <<'PY' 2>/dev/null
import json, os, re, subprocess, sys, time
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    sys.exit(0)
sid = (d.get("session_id") or "").strip()
if not re.match(r"^[A-Za-z0-9._-]{1,128}$", sid):
    sys.exit(0)
state_dir = os.environ["SCRY_DIR"]
now = int(time.time())

def atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)

pid = os.environ.get("SCRY_PID", "")
if pid.isdigit():
    try:
        atomic_write(os.path.join(state_dir, "pid", pid), f"{sid} {now}\n")
        # And every session this process has ever held, one id per line, so
        # a teammate spawned by a session this process has since /cleared
        # can still be tied to the process it lives in (roster.py, 2026-09-21).
        chain = os.path.join(state_dir, "pid", pid + ".sessions")
        held = []
        try:
            # A pid is reused; a chain written before this process started
            # is a dead process's and starts over.
            started = subprocess.run(["ps", "-o", "lstart=", "-p", pid], capture_output=True,
                                     text=True, timeout=5).stdout.strip()
            started = time.mktime(time.strptime(started, "%a %b %d %H:%M:%S %Y"))
            if os.path.getmtime(chain) >= started:
                held = open(chain).read().split()
        except Exception:
            held = []
        if sid not in held:
            atomic_write(chain, "".join(h + "\n" for h in held + [sid]))
    except Exception:
        pass

# A SessionStart payload carries no prompt; one without an event name is
# judged by that, so a start can never arm.
event = d.get("hook_event_name") or ("UserPromptSubmit" if "prompt" in d else "SessionStart")
if event != "UserPromptSubmit":
    sys.exit(0)

prompt = d.get("prompt")
opening = prompt[:64].lstrip() if isinstance(prompt, str) else ""
not_a_user = opening.startswith(("<task-notification>",
                                 "Another Claude session sent a message:",
                                 "<agent-message from="))
del prompt, opening
if not_a_user:
    sys.exit(0)

path = os.path.join(state_dir, sid + ".state")
previous = ""
try:
    with open(path) as f:
        previous = f.read().strip()
except Exception:
    pass
try:
    atomic_write(path, f"armed {now}\n")
except Exception:
    sys.exit(0)
parts = previous.split()
if parts and parts[0] == "missed" and len(parts) >= 2 and parts[1].isdigit():
    when = time.strftime("%H:%M", time.localtime(int(parts[1])))
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "Scry — cache deadline: this session's prompt cache expired at "
                f"{when} with no summary requested (the deadline passed while "
                "nothing was watching — a sleeping machine, or a lead time "
                "shorter than the gap). The request now starting rebuilds the "
                "cache at full price; nothing to do about that, it is already "
                "spent. Say it in a sentence if it matters and carry on."
            ),
        },
        "suppressOutput": True,
    }))
PY

exit 0
