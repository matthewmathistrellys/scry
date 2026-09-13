#!/usr/bin/env bash
# cache_handoff_arm.sh — UserPromptSubmit hook: a new user message arms the
# cache-deadline handoff for one more cycle. Also a SessionStart hook: records
# which session this Claude process is running, for the monitor to follow.
#
# `cache_handoff_monitor.sh` asks the session to write a handoff once, shortly
# before its prompt cache goes cold. Writing that handoff is itself a request,
# and a request pushes the expiry forward an hour — so a monitor that re-armed
# on every warm cache would ask for a handoff every hour, forever, with no
# user in the loop. The rule that stops that: ONLY a user message re-arms it.
# Tool calls, cache hits, monitor notifications, and the handoff itself never
# do (designed with Codex, 2026-09-10).
#
# The one bounded exception is the monitor's keep-alive: while work this
# session started is still running, the monitor asks for a one-line ack
# instead of the handoff, so the cache is warm when that work reports back. It
# does not re-arm anything either; it is counted and capped in the monitor
# (SCRY_KEEPALIVE_MAX per user message, SCRY_KEEPALIVE_MAX_PER_SESSION in
# all), and only an arm from this hook starts a new per-message count.
#
# UserPromptSubmit is not only user messages (binary 2.1.270, 2026-09-13):
# Claude Code runs it for every queued "task-notification" too — a background
# task finishing, a workflow completing, and every plugin-monitor line,
# Scry's own handoff request included. The payload has no field that says
# which (session_id, transcript_path, cwd, prompt_id, permission_mode,
# agent_id, agent_type, effort, prompt, session_title — no origin). Two
# consequences were live: b1746f6b.state read "armed 1789322148", the second a
# workflow's completion notice arrived, with no user message near it; and
# 62aa5e9f, one human message on 09-12, was asked for three handoffs an hour
# apart because each request re-armed the next.
#
# So this hook looks at the first bytes of `prompt` and nothing further: a
# prompt that opens with `<task-notification>` or `Another Claude session sent
# a message:` does not arm. Those are the two openings observed on the
# non-human turns of the b1746f6b transcript (origin task-notification, or
# none); every human turn there opened otherwise. The prefix is compared and
# discarded — never stored, never logged, never printed. OWNER DECISION
# (AGENTS.md, metadata not content): this is the one place Scry reads any part
# of a prompt, because no metadata field separates a user from a notification.
#
# Every run with CLAUDE_PID set also writes pid/<CLAUDE_PID> = "<session_id>
# <time>" — the session this process is running now. /clear changes the
# session inside a process whose monitor never restarts; this record, beside
# Claude Code's own ~/.claude/sessions/<pid>.json, is how the monitor keeps up.
#
# One report, and only here: if the previous cycle ended with the cache
# expiring before a handoff was requested (the monitor saw the deadline pass
# while the machine slept, or the lead time was too short), that is said once
# on the next user turn. It is said here and not by the monitor because a
# monitor notification wakes the model — a cold request, the exact cost the
# handoff exists to avoid — while this turn is already happening.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
state_dir="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}"

SCRY_PAYLOAD="$payload" SCRY_DIR="$state_dir" SCRY_PID="${CLAUDE_PID:-}" python3 - <<'PY' 2>/dev/null
import json, os, re, sys, time
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
                                 "Another Claude session sent a message:"))
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
                f"{when} with no handoff requested (the deadline passed while "
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
