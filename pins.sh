#!/usr/bin/env bash
# pins.sh — SessionStart hook: answers "is a version this repo pins
# known-vulnerable, or far behind what is released?"
#
# Nothing else in Scry looks at a pinned version, and two cases in one week
# (2026-10-09, same repo) went unseen for that reason: a Tailwind standalone
# binary pinned in config/config.exs at a version that cannot run on the
# current macOS — in no lockfile, so no tool tracks it — and a mix.lock with
# a batch of published security advisories that surfaced only when someone
# ran an audit by hand.
#
# Session start does NOT wait on the network. This prints from a local cache
# and, when the cache is missing answers or is more than a day old, starts a
# detached refresh and returns without waiting for it. Offline, that refresh
# fails by itself in the background; the session sees whatever the cache
# held. See scanners/pins.py for what is read, what is sent, and to whom.
#
# Silent unless a pin has an advisory or is past the behind-threshold
# (SCRY_PINS_BEHIND_MINORS). Outside a git repository it does nothing at all:
# without a repo root there is no bound on what a downward search would read,
# and package names found under a home directory are not Scry's to send out.
set -uo pipefail

[ "${SCRY_PINS:-1}" = "0" ] && exit 0

root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners/pins.py"
[ -f "$script" ] || exit 0

python3 "$script" --root "$root" 2>/dev/null
exit 0
