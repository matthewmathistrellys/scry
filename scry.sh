#!/usr/bin/env bash
# scry.sh — Claude Code SessionStart hook: surfaces Ash domain structure
# the instant a session starts, so a fresh agent doesn't have to go
# digging for architecture. No mix/BEAM boot required — pure text
# parsing, runs in well under a second.
#
# Walks up from the session's cwd looking for the nearest mix.exs, and
# scans that project's lib/. If no mix.exs is found anywhere above cwd
# (e.g. a session started outside any Elixir app, or in a language this
# tool doesn't have a scanner for yet), it falls back to a quick layout
# of the subdirectories below cwd — some orientation beats none.
#
# Install: copy scry.sh + scry_domains.py into your project (e.g.
# .claude/hooks/), then wire scry.sh into .claude/settings.json — see
# README for the exact snippet.

emit_context() {
  CTX="$1" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": os.environ["CTX"],
    }
}))
PY
}

# Noise directories a "downstream folders" glance should never show.
NOISE='.git|node_modules|_build|deps|dist|build|target|.venv|venv|__pycache__|.next|coverage|.worktrees|.pytest_cache'

quick_layout() {
  local base="$PWD"
  local listing
  listing="$(find "$base" -mindepth 1 -maxdepth 1 -type d ! -name '.*' \
    | grep -Ev "/($NOISE)\$" \
    | sed "s|^$base/||" \
    | sort)"
  [ -n "$listing" ] || return 0
  local body
  body="Quick layout of $base (no mix.exs — or other recognized project marker — found above this directory):
$(printf '%s\n' "$listing" | sed 's/^/  /')"
  emit_context "$body"
}

dir="$PWD"
mix_root=""
while [ "$dir" != "/" ]; do
  if [ -f "$dir/mix.exs" ]; then
    mix_root="$dir"
    break
  fi
  dir="$(dirname "$dir")"
done

if [ -z "$mix_root" ]; then
  quick_layout
  exit 0
fi

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scry_domains.py"
if [ -f "$script" ]; then
  out="$(python3 "$script" --path "$mix_root/lib")"
  [ -n "$out" ] && printf '%s\n' "$out" || quick_layout
else
  quick_layout
fi
