#!/usr/bin/env bash
# pr_open_advisory.sh — PreToolUse(Bash)
#
# Opening a pull request is the last cheap moment for a second reader. In a
# standard GitHub flow a person still reviews and a person still deploys after
# it; where auto-merge is armed on open and merge deploys (trellys-app, read
# 2026-10-14), nothing does. One week there, 44 of 57 PRs had no reviewer
# dispatched before they opened, and on 2026-10-12 a session built every
# finding a reviewer returned, including a guard against a worry the reviewer
# itself called an unconfirmed suspicion.
#
# Scry does not try to know whether a review happened. It says, once per branch
# per session, what the research shows about who should read a change and what
# to do with what they find. Every number is from a source opened during the
# 2026-10-14 research (see the README section "Before a pull request opens").
#
# Quiet for anything but `gh pr create`, for a change under 10 lines, for a
# change that touches only prose, on a repeat for the same branch, and on every
# internal error. Per AGENTS.md this exits zero always and never instructs.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

eval "$(SCRY_PAYLOAD="$payload" python3 -c '
import json, os, shlex
try:
    d = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
except Exception:
    d = {}
print("agent_cwd=%s" % shlex.quote(d.get("cwd", "") or ""))
print("session_id=%s" % shlex.quote(d.get("session_id", "") or ""))
print("cmd=%s" % shlex.quote((d.get("tool_input") or {}).get("command", "") or ""))
' 2>/dev/null)" || exit 0

[ -n "${cmd:-}" ] || exit 0
[ -n "${agent_cwd:-}" ] || agent_cwd="$PWD"

opens="$(CMD="$cmd" python3 -c '
import os, re
print("yes" if re.search(r"\bgh\b[^;&|]*\bpr\s+create\b", os.environ["CMD"]) else "no")
' 2>/dev/null)" || exit 0
[ "$opens" = "yes" ] || exit 0

wt="$(git -C "$agent_cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$wt" ] || exit 0
g() { git -C "$wt" "$@"; }

branch="$(g rev-parse --abbrev-ref HEAD 2>/dev/null)" || exit 0

state_dir="${TMPDIR:-/tmp}/scry-pr-open-advisory"
mkdir -p "$state_dir" 2>/dev/null || exit 0
key="$(printf '%s|%s|%s' "$session_id" "$wt" "$branch" | shasum | cut -d' ' -f1)"
marker="$state_dir/said-$key"
[ -e "$marker" ] && exit 0

default_branch="$(g symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default_branch="${default_branch#origin/}"
[ -n "$default_branch" ] || default_branch="main"
g rev-parse --verify --quiet "refs/remotes/origin/$default_branch" >/dev/null 2>&1 || exit 0

base="$(g merge-base HEAD "origin/$default_branch" 2>/dev/null)" || exit 0
[ -n "$base" ] || exit 0

# Lines and files changed, and whether any changed file is something other
# than prose.
read -r lines files code <<EOF
$(g diff --numstat "$base" HEAD 2>/dev/null | python3 -c '
import re, sys
prose = re.compile(r"(\.(md|markdown|mdx|txt|rst|adoc)$)|(^|/)(docs?|LICENSE[^/]*)(/|$)", re.I)
lines = files = code = 0
for row in sys.stdin:
    parts = row.rstrip("\n").split("\t")
    if len(parts) < 3:
        continue
    added, removed, path = parts[0], parts[1], parts[2]
    files += 1
    if added.isdigit() and removed.isdigit():
        lines += int(added) + int(removed)
    if not prose.search(path):
        code += 1
print(lines, files, code)
' 2>/dev/null)
EOF
case "${lines:-}" in ''|*[!0-9]*) exit 0 ;; esac
[ "$lines" -ge 10 ] || exit 0
[ "${code:-0}" -gt 0 ] || exit 0

: > "$marker" 2>/dev/null || true

ADVISORY_TEXT="Scry: this pull request changes $lines lines across $files files. Where merge is automatic, opening it is the last moment a second reader costs minutes. About 70% of outages come from changes, and parts with low review coverage ship up to 2–5 more bugs.

The author is a weak reader of its own work: studies show the same model rechecking its own work makes results worse (91.5% → 88.0% in one). What has evidence: a reader in a fresh context (\`/code-review\`, a separate agent, or a person); more than one pass combined; dropping findings the reader can't confirm, along with style nits (filtered reviews got 65% of comments acted on; unfiltered got 7–8%); and giving the reader the diff, the code around it and what the change is for, not the whole codebase (in one benchmark, every model did worse as more code was added).

A finding is input, not a decision. Reviewers usually report something even when the work is sound, and 20–30% of oversized AI fixes carried defensive code nothing needed. A finding becomes a change once the risk is sized: how likely, how bad, and what already covers it."

CTX="$ADVISORY_TEXT" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": os.environ["CTX"],
    },
    "suppressOutput": True,
}))
PY

exit 0
