#!/bin/bash
# scry.sh — Claude Code SessionStart hook: surfaces Ash domain structure
# the instant a session starts, so a fresh agent doesn't have to go
# digging for architecture. No mix/BEAM boot required — pure text
# parsing, runs in well under a second.
#
# Skips silently outside Elixir/Mix projects: walks up from the
# session's cwd looking for the nearest mix.exs, and scans that
# project's lib/. If no mix.exs is found (e.g. a session started
# outside any Elixir app in a polyglot monorepo), it's a no-op.
#
# Install: copy scry.sh + scry_domains.py into your project (e.g.
# .claude/hooks/), then wire scry.sh into .claude/settings.json — see
# README for the exact snippet.

dir="$PWD"
mix_root=""
while [ "$dir" != "/" ]; do
  if [ -f "$dir/mix.exs" ]; then
    mix_root="$dir"
    break
  fi
  dir="$(dirname "$dir")"
done

[ -n "$mix_root" ] || exit 0

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scry_domains.py"
[ -f "$script" ] && python3 "$script" --path "$mix_root/lib" || true
