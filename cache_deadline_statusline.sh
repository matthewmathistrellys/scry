#!/usr/bin/env bash
# cache_deadline_statusline.sh — status-line adapter: record when this
# session's prompt cache goes cold, then hand the payload to whatever status
# line was already configured.
#
# Claude Code bills a request that lands on a warm prompt cache at the cached
# rate and one that lands on a cold cache at the full rate — for a session that
# has been running an hour that is the whole context re-read at full price.
# The cache is time-to-live: every request pushes the expiry forward, and an
# idle session lets it run out. The ONLY place Claude Code reports when that
# happens is the status-line payload (`prompt_cache.expires_at`, verified
# against the 2.1.267 binary on 2026-09-10) — no hook event carries it, and
# no plugin can install a status line (settings.json is the user's). So this
# is a wrapper the user points `statusLine.command` at, once. It keeps only
# the deadline metadata and passes everything else straight through.
#
# What it stores, per session, in a file keyed on session_id:
#   observed_at, warm, ttl, expires_at, requests — numbers and one boolean.
# Nothing else from the payload is read or kept (AGENTS.md: metadata, not
# conversation content). `cache_handoff_monitor.sh` reads that file; this
# script never decides anything.
#
# Inner status line: the command in SCRY_STATUSLINE_INNER runs with the same
# stdin this script received and its stdout is the status line. Unset, this
# prints a one-word cache status so the bar is not blank. Any failure here
# degrades to "no deadline recorded", never to a broken status line.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"
state_dir="${SCRY_CACHE_STATE_DIR:-${TMPDIR:-/tmp}/scry-cache-deadline}"

summary="$(SCRY_PAYLOAD="$payload" SCRY_DIR="$state_dir" python3 - <<'PY' 2>/dev/null
import json, os, sys, time
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    sys.exit(0)
sid = (d.get("session_id") or "").strip()
pc = d.get("prompt_cache")
if not sid or not isinstance(pc, dict):
    sys.exit(0)
expires = pc.get("expires_at")
rec = {
    "session_id": sid,
    "observed_at": int(time.time()),
    "warm": bool(pc.get("warm")),
    "ttl": pc.get("ttl"),
    "expires_at": int(expires) if isinstance(expires, (int, float)) else None,
    "requests": pc.get("requests"),
}
try:
    os.makedirs(os.environ["SCRY_DIR"], exist_ok=True)
    path = os.path.join(os.environ["SCRY_DIR"], sid + ".deadline")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f)
    os.replace(tmp, path)
except Exception:
    pass
if rec["warm"] and rec["expires_at"]:
    left = max(0, rec["expires_at"] - rec["observed_at"]) // 60
    print(f"cache warm {left}m")
else:
    print("cache cold")
PY
)"

if [ -n "${SCRY_STATUSLINE_INNER:-}" ]; then
  printf '%s' "$payload" | bash -c "$SCRY_STATUSLINE_INNER"
  exit 0
fi

printf '%s\n' "${summary:-}"
exit 0
