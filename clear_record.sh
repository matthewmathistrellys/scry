#!/usr/bin/env bash
# clear_record.sh — Claude Code SessionEnd hook: when a session ends because
# the user ran /clear, leave one small record saying which session that was.
#
# /clear ends the session and starts a new one. The new session's
# SessionStart hooks are told only that it began with source "clear"; nothing
# in the payload, the transcript, or the binary links it back to the session
# it replaced (searched 2.1.268 on 2026-09-10 — parentSessionId is for
# subagents and teams). Guessing by transcript mtime is wrong exactly when it
# matters: ten sessions open, several in one directory (Matt, 2026-09-10).
# The session that is ending knows its own id. This writes it down.
#
# Where: $TMPDIR/scry-last-cleared/<session_id>.json — one file per cleared
# session, so two clears in one directory in the same second cannot clobber
# each other. Contents: session_id, transcript_path, cwd, ended_at. Nothing
# from the conversation (AGENTS.md: metadata, not content). Lifecycle: the
# next SessionStart with source "clear" in that directory reads its record
# and deletes it; records nobody claims within a day are swept here on every
# run, and the OS reaps the temp directory anyway. Nothing under $HOME.
#
# Any other end reason (logout, exit, other) writes nothing. Stdout from a
# SessionEnd hook reaches no one (README, "Session disposal"); this hook's
# whole effect is the file. Exits zero always.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
dir="${SCRY_CLEAR_STATE_DIR:-${TMPDIR:-/tmp}/scry-last-cleared}"

SCRY_PAYLOAD="$payload" SCRY_DIR="$dir" python3 - <<'PY' 2>/dev/null || true
import json, os, sys, time
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    sys.exit(0)
now = int(time.time())
dirp = os.environ["SCRY_DIR"]
# Sweep first: a record nobody claimed within a day is a session whose
# directory never saw a new start (terminal closed, machine changed).
try:
    for f in os.listdir(dirp):
        p = os.path.join(dirp, f)
        try:
            if f.endswith(".json") and now - os.path.getmtime(p) > 86400:
                os.unlink(p)
        except OSError:
            pass
except OSError:
    pass
if d.get("reason") != "clear":
    sys.exit(0)
sid = (d.get("session_id") or "").strip()
if not sid or "/" in sid:
    sys.exit(0)
rec = {
    "session_id": sid,
    "transcript_path": d.get("transcript_path") or "",
    "cwd": os.path.realpath(d.get("cwd") or os.getcwd()),
    "ended_at": now,
}
try:
    os.makedirs(dirp, exist_ok=True)
    path = os.path.join(dirp, sid + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, path)
except Exception:
    pass
PY
exit 0
