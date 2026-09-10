#!/usr/bin/env bash
# cache_handoff_monitor.sh — plugin monitor: ask this session for one handoff
# shortly before its prompt cache goes cold.
#
# Claude Code keeps a prompt cache with a time-to-live (an hour on this
# account's plan; the payload says which). Every request refreshes it. A
# session that goes idle — the user stepped away, the work paused on a
# question — lets it run out, and the next request re-reads the whole context
# at the full rate. An hour-old session with a large context is the expensive
# case, and it is also the one where the most is lost if the session is
# simply abandoned instead. A handoff written in the last minutes of the warm
# window costs one cached request and leaves a file the next session can
# start from.
#
# How the pieces fit (designed with Codex, 2026-09-10):
#
#   cache_deadline_statusline.sh  writes <session>.deadline — when the cache
#                                 expires, from the only place Claude Code
#                                 reports it (the status-line payload).
#   cache_handoff_arm.sh          writes <session>.state = armed on every
#                                 user message, and on nothing else.
#   this script                   polls both, with no model calls, and when
#                                 the session is armed and the deadline is
#                                 inside the lead window, prints ONE line.
#                                 Claude Code delivers each stdout line of a
#                                 plugin monitor to the model as a
#                                 notification — that is what wakes an idle
#                                 session to write the handoff.
#
# One handoff per user-work cycle, and only a user message starts a new one.
# The state machine, in the .state file:
#
#   armed      a user message arrived; follow the deadline.
#   requested  the notification was printed; print nothing more.
#   saved      the handoff file appeared (silent bookkeeping).
#   missed     the deadline passed while armed with no request — the machine
#              slept through it, or the lead time was shorter than the poll
#              gap. Silent HERE, deliberately: a monitor line wakes the model,
#              and waking it onto a cold cache is the cost this whole thing
#              exists to avoid. cache_handoff_arm.sh says it on the next turn,
#              which is already happening.
#
# The transition to `requested` is written BEFORE the line is printed, so a
# restart between the two cannot produce a second request. The monitor reads
# no conversation content: two small files of numbers, and the process env.
#
# Bounded: a request happens at most once per user message, and never at all
# without a user message. SCRY_CACHE_HANDOFF=0 disables it (monitor exits at
# once). Claude only — Codex has no monitor equivalent as of 2026-09-10.
set -uo pipefail

if [ "${SCRY_CACHE_HANDOFF:-1}" = "0" ]; then
  exit 0
fi

sid="${CLAUDE_CODE_SESSION_ID:-}"
if [ -z "$sid" ]; then
  # Never silently wrong: the watch cannot run without knowing whose cache it
  # is watching, and exiting quietly would look like "watching".
  echo "Scry — cache deadline: automatic handoff unavailable in this session (no session id in the monitor environment). Nothing is watching the prompt-cache expiry."
  exit 0
fi

state_dir="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}"
lead="${SCRY_CACHE_HANDOFF_LEAD_SECONDS:-120}"
poll="${SCRY_CACHE_HANDOFF_POLL_SECONDS:-15}"
handoff_root="${SCRY_CACHE_HANDOFF_DIR:-$HOME/.claude/scry/handoffs}"
case "$lead" in ''|*[!0-9]*) lead=120 ;; esac
case "$poll" in ''|*[!0-9]*) poll=15 ;; esac

repo_name="$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")"

pass() {
  SCRY_SID="$sid" SCRY_DIR="$state_dir" SCRY_LEAD="$lead" \
  SCRY_HANDOFF_ROOT="$handoff_root" SCRY_REPO="$repo_name" python3 - <<'PY' 2>/dev/null
import json, os, sys, time

sid = os.environ["SCRY_SID"]
state_dir = os.environ["SCRY_DIR"]
lead = int(os.environ["SCRY_LEAD"])
now = int(time.time())

state_path = os.path.join(state_dir, sid + ".state")
deadline_path = os.path.join(state_dir, sid + ".deadline")

def read_state():
    try:
        with open(state_path) as f:
            parts = f.read().split()
    except Exception:
        return ("unarmed", 0, "")
    if not parts:
        return ("unarmed", 0, "")
    when = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    extra = parts[2] if len(parts) > 2 else ""
    return (parts[0], when, extra)

def write_state(name, when, extra=""):
    os.makedirs(state_dir, exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"{name} {when} {extra}".rstrip() + "\n")
    os.replace(tmp, state_path)

state, since, extra = read_state()

if state == "requested":
    if extra and os.path.exists(extra):
        write_state("saved", now, extra)
    sys.exit(0)
if state != "armed":
    sys.exit(0)

try:
    with open(deadline_path) as f:
        d = json.load(f)
except Exception:
    sys.exit(0)
expires = d.get("expires_at")
if not d.get("warm") or not isinstance(expires, int):
    sys.exit(0)

# A deadline observed before this cycle was armed is not this cycle's: the
# user message that armed it is itself a request, and the status line will
# report the refreshed expiry shortly. Waiting for that avoids acting on a
# stale number in the seconds between the two.
if int(d.get("observed_at") or 0) < since:
    sys.exit(0)

if now >= expires:
    write_state("missed", expires)
    sys.exit(0)
if now < expires - lead:
    sys.exit(0)

repo = os.environ["SCRY_REPO"] or "session"
stamp = time.strftime("%Y-%m-%d-%H%M", time.localtime(now))
handoff_dir = os.path.join(os.environ["SCRY_HANDOFF_ROOT"], repo)
handoff = os.path.join(handoff_dir, f"{stamp}-{sid[:8]}.md")
try:
    os.makedirs(handoff_dir, exist_ok=True)
except Exception:
    pass

# Written before printing: a restart between the two cannot ask twice.
write_state("requested", now, handoff)

minutes = max(0, (expires - now) // 60)
at = time.strftime("%H:%M", time.localtime(expires))
print(
    "Scry — cache deadline (this is a Scry monitor notification, not a user "
    f"message): this session's prompt cache goes cold at {at}, in about "
    f"{minutes} min. The first request after that re-reads the whole context "
    "at the full rate instead of the cached one. While it is still warm, write "
    f"one handoff to {handoff} — the objective and the latest user direction; "
    "decisions, constraints, and open questions; what was done and what was "
    "actually verified; what remains and the next concrete step; the working "
    "directory, branch, and any running jobs. Where agent-comms is available, "
    "post the same text there as a handoff and pin it. Then stop and wait for "
    "the user. This will not repeat until the user sends another message."
)
PY
}

if [ "${SCRY_CACHE_HANDOFF_ONCE:-0}" = "1" ]; then
  pass
  exit 0
fi

while :; do
  if [ -n "${CLAUDE_PID:-}" ] && ! kill -0 "$CLAUDE_PID" 2>/dev/null; then
    exit 0
  fi
  pass
  sleep "$poll"
done
