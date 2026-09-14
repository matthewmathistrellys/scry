#!/usr/bin/env bash
# roster.sh — on demand: what this session started that has not finished,
# read from file metadata and the process table (see roster.py for what is
# listed and why). Run from the /scry skill or any Bash call; the session id
# comes from CLAUDE_CODE_SESSION_ID / CLAUDE_PID in the environment, or from
# a hook payload on stdin. Prints plain lines; exits 0 always.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$here/roster.py" </dev/null 2>/dev/null
exit 0
