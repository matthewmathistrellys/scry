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
# stdin this script received; its stdout is the status line, with the cache
# segment appended to its last line. Unset, the cache segment is the whole
# bar. Any failure here degrades to "no deadline recorded", never to a
# broken status line.
#
# The segment (Matt, 2026-09-10: "the TTL thing is critically important …
# have that at the bottom"; later that day: no "cache 1h" label, icon then a
# space like worktrunk's own segments, fire while warm, snowflake once cold,
# no middle icon — "KISS" — the colour carries the warning):
#   🔥 43m            default text; warm, minutes until it goes cold
#   🔥 4m ~150k       yellow; warm but inside the last ten minutes. The
#                     number is what the next request re-reads at the full
#                     rate if nobody speaks before then
#   ❄️ ~150k          blue; cold. The next request pays that re-read no matter
#                     what — /compact included, since the summary request
#                     reads the same history (prompt-caching reference, read
#                     2026-09-10)
# Claude Code re-runs the status line when a warm cache reaches expires_at,
# so ❄️ appears on time; the minute count only ticks between events unless
# statusLine.refreshInterval is set (README, Install).
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
recache = pc.get("recache_tokens_if_cold")
size = ""
if isinstance(recache, (int, float)) and recache > 0:
    size = f" ~{int(recache) // 1000}k" if recache >= 1000 else f" ~{int(recache)}"
YEL, BLU, OFF = "\033[33m", "\033[34m", "\033[0m"
if rec["warm"] and rec["expires_at"]:
    left = max(0, rec["expires_at"] - rec["observed_at"])
    mins = left // 60
    if left <= 600:
        print(f"\U0001f525 {YEL}{mins}m{size}{OFF}")
    else:
        print(f"\U0001f525 {mins}m")
else:
    print(f"\u2744\ufe0f {BLU}{size.strip() or 'cold'}{OFF}")
PY
)"

if [ -n "${SCRY_STATUSLINE_INNER:-}" ]; then
  inner="$(printf '%s' "$payload" | bash -c "$SCRY_STATUSLINE_INNER" 2>/dev/null || true)"
  if [ -z "$summary" ]; then
    printf '%s\n' "$inner"
  elif [ -z "$inner" ]; then
    printf '%s\n' "$summary"
  else
    # Append to the inner's LAST line so a multi-line status line keeps its
    # shape and the cache segment sits at the end of the bar.
    printf '%s  %s\n' "$inner" "$summary"
  fi
  exit 0
fi

printf '%s\n' "${summary:-}"
exit 0
