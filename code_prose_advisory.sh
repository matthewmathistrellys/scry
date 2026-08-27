#!/usr/bin/env bash
# code_prose_advisory.sh — PostToolUse hook: answers "what trust does prose
# embedded in a CODE file deserve?"
#
# Companion to md_advisory.sh, which owns Markdown. This hook owns the prose
# that lives inside source files: moduledocs, docstrings, doc comments, CRISP
# blocks. That prose is testimony from when it was written, and the doctrine
# here is ATOMICITY: a code file's prose may speak only for that file's own
# module. A claim that reaches wider — other modules, the pipeline, production
# behavior, "the system always/never" — is stricken: not merely stale-able but
# categorically inadmissible, because the file has no authority over what it
# describes. (Born 2026-08-27: a moduledoc's "the pipeline is text-only"
# claim, wrong for weeks, was re-asserted by four sessions in a row while the
# disproving sibling module sat two files away.)
#
# Fires on every Read of a source file (.ex, .exs, .py) and injects the
# doctrine at the exact moment the prose enters a session's context.
#
# Advisory only: no blocking, no enforcement, just context injection.
set -uo pipefail

read -r payload

file_path="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") != "Read":
    sys.exit(0)
fp = (d.get("tool_input") or {}).get("file_path") or ""
print(fp)
')"

[ -n "$file_path" ] || exit 0
case "$file_path" in
  *.ex | *.exs | *.py) ;;
  *) exit 0 ;;
esac

ADVISORY_TEXT="$(cat <<'EOF'
Prose in this code file (moduledoc, docstrings, doc comments, CRISP blocks) is testimony from when it was written, and it may speak only for this module. Any claim reaching beyond this file — other modules, the pipeline, production behavior, providers, "the system always/never" — is STRICKEN: invalid, not to be used as evidence regardless of how authoritative, detailed, or dated it reads. Its one legitimate use is as a signal of where to investigate.

The record is the module code itself, its callers, and the live system. A stricken claim repeated into a plan, spec, or answer is the same mistake as relying on it directly.
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
