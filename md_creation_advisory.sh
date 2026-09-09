#!/usr/bin/env bash
# md_creation_advisory.sh — PostToolUse hook: says, at the moment a new
# Markdown file is created, that it is scratch and will be treated as scratch.
#
# Companion to md_advisory.sh (which stamps trust doctrine on every Read of a
# .md file). This hook fires at the other end of a Markdown file's life: the
# moment one is CREATED, before it has a chance to become the clutter the
# read-side hook has to keep warning about. Its pair at the far end is
# session_disposal_advisory.sh, which lists the same files at Stop.
#
# It states two facts and stops. The file is not one this repo's conventions
# expect, and scratch Markdown written this session may be cleared out when the
# session ends. Which of several homes the content "belongs" in is not a fact
# this hook can check, and it used to say it anyway — that framing was cut
# 2026-09-09 as moralizing, along with the client detection that existed only
# to gate its one client-specific line.
#
# Exemptions are shared with session_disposal_advisory.sh via
# md_exemptions.sh — one list, sourced by both, so the two ends of the file's
# life cannot disagree about which files are standard.
#
# "New" means untracked in git, not merely "written this call" — Write also
# fully overwrites files a session already owns, and that isn't creation.
#
# Advisory only: no blocking, no enforcement, just context injection. Fires
# at most once per file per session, same as scale_advisory.sh, so an agent
# iterating on the same scratch file isn't renagged on every draft.
set -uo pipefail

# shellcheck source=md_exemptions.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/md_exemptions.sh"

read -r payload

parsed="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Write":
    sys.exit(0)
fp = (d.get("tool_input") or {}).get("file_path") or ""
if not fp:
    sys.exit(0)
print(fp)
print(d.get("session_id") or "nosession")
')"

[ -n "$parsed" ] || exit 0
file_path="$(printf '%s' "$parsed" | sed -n 1p)"
session_id="$(printf '%s' "$parsed" | sed -n 2p)"

case "$file_path" in
  *.md) ;;
  *) exit 0 ;;
esac
[ -f "$file_path" ] || exit 0

repo_root="$(cd "$(dirname "$file_path")" && git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -n "$repo_root" ] || exit 0
rel="${file_path#"$repo_root"/}"

# ── Exempt: this repo's product IS Markdown content ─────────────────────────
scry_md_product_repo "$repo_root" && exit 0

# ── Exempt: the standard, ecosystem-recognized files ────────────────────────
scry_md_standard_file "$rel" && exit 0

# ── "New" = untracked in git, not merely written this call ──────────────────
git -C "$repo_root" ls-files --error-unmatch -- "$rel" >/dev/null 2>&1 && exit 0

# ── Once per file per session ────────────────────────────────────────────────
state_dir="${TMPDIR:-/tmp}/scry-md-creation-advisory"
mkdir -p "$state_dir" 2>/dev/null || exit 0
seen_key="$(printf '%s|%s' "$session_id" "$file_path" | shasum | cut -d' ' -f1)"
seen_marker="$state_dir/seen-$seen_key"
[ -e "$seen_marker" ] && exit 0
: > "$seen_marker" 2>/dev/null

ADVISORY_TEXT="NEW MARKDOWN — $rel is not one of this repo's standard Markdown files. Scratch Markdown created this session may be cleared out at session end. If what is in it needs to outlive the session, put it where long-lived work is tracked, not in a repo file."

CTX="$ADVISORY_TEXT" python3 - <<'PY'
import json, os
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "PostToolUse",
        "additionalContext": os.environ["CTX"],
    },
    "suppressOutput": True,
}))
PY

exit 0
