#!/usr/bin/env bash
# tab_colour.sh — colour this session's iTerm2 tab by its prompt cache:
# `none`, `yellow` (warm, last ten minutes) or `red` (cold). Called by
# cache_deadline_statusline.sh when the state changes, and with `none` by the
# SessionEnd hook so a tab where Claude has stopped does not stay red.
#
# Nothing on the tabs that are fine, so a glance across tabs picks out the
# cold ones; the colours match the status line's cache segment. It replaces
# any colour set by hand. Outside iTerm2 (LC_TERMINAL) it does nothing.
#
# Hooks and the status line run detached; the tab is the terminal of the
# nearest ancestor that has one (the claude process). Exits non-zero when
# the colour was not delivered, so the caller tries again next time.
set -uo pipefail

[ "${LC_TERMINAL:-}" = "iTerm2" ] || exit 0

rgb() { printf '\033]6;1;bg;red;brightness;%s\a\033]6;1;bg;green;brightness;%s\a\033]6;1;bg;blue;brightness;%s\a' "$1" "$2" "$3"; }
case "${1:-none}" in
  yellow) seq="$(rgb 230 190 60)" ;;
  red)    seq="$(rgb 220 80 70)" ;;
  *)      seq="$(printf '\033]6;1;bg;*;default\a')" ;;
esac

pid=$PPID
for _ in 1 2 3 4 5 6 7 8 9 10; do
  read -r ppid tty < <(ps -o ppid=,tty= -p "$pid" 2>/dev/null) || exit 1
  if [ -n "${tty:-}" ] && [ "$tty" != "??" ]; then
    printf '%s' "$seq" > "/dev/$tty" 2>/dev/null
    exit $?
  fi
  pid=$ppid
done
exit 1
