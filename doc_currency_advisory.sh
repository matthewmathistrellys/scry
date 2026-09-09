#!/usr/bin/env bash
# doc_currency_advisory.sh — PostToolUse hook: the WRITE-side companion to
# code_prose_advisory.sh.
#
# That hook fires on Read and says what trust EXISTING embedded prose
# deserves (testimony, atomicity, stricken-if-wider-than-the-file). This one
# fires on Edit/Write of a source file and closes the loop at the moment
# stale testimony is CREATED: an edit that changes behavior while leaving the
# old moduledoc/docstring standing manufactures exactly the artifact the
# Read-side doctrine exists to defend against. Cheapest fix is in the same
# edit pass, while the change is still in working memory (1x now vs 10x when
# a later session re-derives what the prose should have said).
#
# Deliberately light (per Matt, 2026-09-09: "like the Python plugin, but not
# quite as extra"): fires once per file per session, injects three sentences,
# never blocks, never spawns a runtime. Test files are excluded — their code
# is its own documentation.
set -uo pipefail

read -r payload

parsed="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") not in ("Edit", "Write"):
    sys.exit(0)
fp = (d.get("tool_input") or {}).get("file_path") or ""
sid = d.get("session_id") or ""
print(sid + "\t" + fp)
')"

[ -n "$parsed" ] || exit 0
payload_session="${parsed%%$'\t'*}"
file_path="${parsed#*$'\t'}"

[ -n "$file_path" ] || exit 0
case "$file_path" in
  *_test.exs | */test/* | test_*.py | *_test.py | */tests/*) exit 0 ;;
  *.ex | *.exs | *.py) ;;
  *) exit 0 ;;
esac

# Once per file per session: a marker dir keyed by the payload session id
# (fall back to parent pid so a missing id degrades to once-per-process).
session_key="${payload_session:-${CLAUDE_SESSION_ID:-$PPID}}"
marker_dir="${TMPDIR:-/tmp}/scry-doc-currency-${session_key}"
mkdir -p "$marker_dir" 2>/dev/null || exit 0
marker="$marker_dir/$(printf '%s' "$file_path" | shasum | cut -c1-16)"
[ -e "$marker" ] && exit 0
: > "$marker"

ADVISORY_TEXT="$(cat <<'EOF'
You just edited a source file. If the edit changed what this module DOES — behavior, contract, inputs, failure modes — bring the inline documentation (@moduledoc/@doc/docstring) along in the same pass, and delete any sentence the change just falsified. Prose here may describe only the module in this file; never add claims about other modules, the pipeline, or production. If the edit was mechanical (rename, formatting, test plumbing), no doc change is owed — move on.
EOF
)"

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
