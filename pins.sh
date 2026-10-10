#!/usr/bin/env bash
# pins.sh — answers "is a version this repo pins known-vulnerable, or far
# behind what is released?"
#
# Nothing else in Scry looks at a pinned version, and two cases in one week
# (2026-10-09, same repo) went unseen for that reason: a Tailwind standalone
# binary pinned in config/config.exs at a version that cannot run on the
# current macOS — in no lockfile, so no tool tracks it — and a mix.lock with
# a batch of published security advisories that surfaced only when someone
# ran an audit by hand.
#
#   pins.sh                 the SessionStart hook (source `startup` only).
#                           Silent, except for ONE line when a runtime
#                           dependency has a high or critical advisory that
#                           this branch does not itself change — and that line
#                           is said once per repository per advisory set.
#   pins.sh --full [dir]    everything, for whoever asks. Reads the cache.
#   pins.sh --refresh [dir] asks the registries now, in the foreground.
#
# The first cut of this hook listed every advisory and every out-of-date
# package at every session start, resume, clear and compact: ~1,300
# characters, several times a day, nearly all of it already said. The count
# now lives in the status line (statusline.sh, `pins 4!`), the detail in
# --full, and a note scoped to one file in pins_edit_advisory.sh.
#
# Session start does NOT wait on the network. It reads a local cache and,
# when the cache is missing answers or is more than a day old, starts a
# detached refresh and returns without waiting for it. Offline, that refresh
# fails by itself in the background. See scanners/pins.py for what is read,
# what is sent, and to whom.
#
# Outside a git repository it does nothing at all: without a repo root there
# is no bound on what a downward search would read, and package names found
# under a home directory are not Scry's to send out.
set -uo pipefail

[ "${SCRY_PINS:-1}" = "0" ] && exit 0

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners/pins.py"
[ -f "$script" ] || exit 0

mode="--startup"
case "${1:-}" in
  --full | --all) mode="--full"; shift ;;
  --refresh) mode="--refresh"; shift ;;
esac

root=$(git -C "${1:-.}" rev-parse --show-toplevel 2>/dev/null) || exit 0

if [ "$mode" = "--startup" ]; then
  python3 "$script" --root "$root" --startup 2>/dev/null
else
  python3 "$script" --root "$root" "$mode" </dev/null
fi
exit 0
