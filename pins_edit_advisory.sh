#!/usr/bin/env bash
# pins_edit_advisory.sh — PostToolUse hook (Edit/Write): when the file just
# edited is a dependency manifest or a pinned-tool config, say what Scry's
# pin cache holds about THAT file — advisories on what it pins, and what is
# behind its latest release.
#
# This is the moment the finding can change a decision: the session is in the
# manifest already. The same findings at every session start were noise
# (pins.sh), so this is where the detail went.
#
# At most once per session per file. Silent when the file has no findings,
# and for every file that is not a manifest — which is decided here, on the
# raw payload, before any interpreter starts. Reads the cache only: no
# network call, and no refresh is started. Never blocks; exits 0 always.
set -uo pipefail

[ "${SCRY_PINS:-1}" = "0" ] && exit 0

payload="$(cat 2>/dev/null || true)"
case "$payload" in
  *mix.exs* | *mix.lock* | *package.json* | *package-lock.json* | \
  *npm-shrinkwrap.json* | *yarn.lock* | *pnpm-lock.yaml* | *pyproject.toml* | \
  *uv.lock* | *poetry.lock* | *Pipfile* | *requirements*.txt* | \
  *.tool-versions* | *config/*.exs*) ;;
  *) exit 0 ;;
esac

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners/pins.py"
[ -f "$script" ] || exit 0

printf '%s' "$payload" | python3 "$script" --edited 2>/dev/null
exit 0
