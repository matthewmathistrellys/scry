#!/usr/bin/env bash
# architecture.sh — Claude Code SessionStart hook: answers "what is this
# codebase?" the instant a session starts, so a fresh agent doesn't have
# to go digging. Runs in well under a second.
#
# This file is a DISPATCHER, not a language scanner. It detects which
# stack it's looking at and hands off to the matching scanner under
# scanners/ — today that's Elixir/Ash (via mix.exs), with Python and
# TypeScript to follow. The language name belongs on the scanner, never
# on this entry point: one hook wired once has to work in every repo,
# including a polyglot tree, so it cannot require the installer to know
# the stack in advance. Adding a language means adding a scanner and a
# detection line here — never a second hook to install.
#
# If no scanner matches, it falls back to a quick layout of the
# subdirectories below cwd — some orientation beats none.
#
# Install: copy architecture.sh + scanners/ into your project (e.g.
# .claude/hooks/), then wire architecture.sh into settings.json — see
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

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners/elixir_ash.py"
if [ -f "$script" ]; then
  out="$(python3 "$script" --path "$mix_root/lib" --mix-root "$mix_root")"
  [ -n "$out" ] && printf '%s\n' "$out" || quick_layout
else
  quick_layout
fi
