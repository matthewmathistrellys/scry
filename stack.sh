#!/usr/bin/env bash
# stack.sh — Claude Code SessionStart hook: answers "what is this system
# actually built ON?" from live config, so a session never has to take an
# instruction file's word for it.
#
# Separate from architecture.sh on purpose. architecture.sh is a LANGUAGE
# dispatcher that walks UP from cwd looking for a project marker; in a
# monorepo whose mix.exs files live DOWN in apps/*, it never fires. Stack
# facts are orthogonal to language and must be reported in every repo, so
# this scans DOWN from the git root instead, and is wired as its own entry
# alongside provenance.sh rather than nested behind a language match.
#
# Reads .env because that is where a database variable's binding is
# actually resolvable. It emits NO SECRETS: connection strings are reduced
# to a provider label and region during parsing, and credentials are
# discarded before any value is retained. See scanners/stack.py.
#
# Output-budget note (see README "The output budget"): like provenance.sh,
# this one reports whenever it finds anything -- the owner wants the stack
# stated every session (Matt, 2026-08-21), because the failure mode is a
# doc quietly going stale, which crosses no threshold and fires no alarm.
# It pays for that exemption by being at most six lines.
set -uo pipefail

root=$(git rev-parse --show-toplevel 2>/dev/null) || root=$PWD

script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/scanners/stack.py"
[ -f "$script" ] || exit 0

python3 "$script" --root "$root" 2>/dev/null
