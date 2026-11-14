#!/usr/bin/env bash
# elixir_advisory.sh — Claude Code PostToolUse hook (Edit|Write, *.ex files):
# prose drift in Elixir files, purely advisory. (Its Ash and Credo checks
# moved to Grimoire in 1.52.0 and 1.53.0: judging code is the work, not
# the environment.)
#
# Fires whenever an .ex file is edited or written. Reads the file's current
# content straight off disk (more reliable than tool_input fields, which vary
# by tool) and runs every check registered below against it. Findings are
# injected via hookSpecificOutput.additionalContext — informational only,
# never a blocking decision. Nothing here ever fails the tool call: every
# check fails open (skips silently) if its preconditions (a real Mix project,
# a specific dependency present) aren't met, matching the `[ -x "$f" ] &&
# ... || true` fail-open idiom used throughout the rest of Scry.
#
# Genuinely codebase-agnostic: no hardcoded project directories, no
# allowlist/exemption list of any kind. Detection of "is this a Mix project"
# and "does this project use dependency X" is generic (walk up to the
# nearest mix.exs, grep that project's own mix.lock) so this works dropped
# into any Elixir project with zero configuration.
#
# ── The checks ───────────────────────────────────────────────────────────
#   Each check carries its own comment below; the registry is the
#   add_regex_check calls plus the EXTRA_CHECKS array, so read those for the
#   current list rather than a copy kept here.
#
# ── How to add a check ───────────────────────────────────────────────────
#   - Simple regex over the file's text? Add one `add_regex_check` call in
#     the ANTI-PATTERN LIBRARY section below. Four arguments: a short name,
#     the regex, the mix.lock dependency required for it to fire (or "" for
#     none), and the advisory message. That's the whole change.
#   - Needs to shell out to a tool, walk the AST, or do anything a regex
#     can't? Write one check_<name> function (mix_root and file_path are
#     already in scope; append findings via `findings+=("...")`) and add its
#     name to the EXTRA_CHECKS array. See check_prose_drift below as the template.
#   Either path leaves every existing check untouched.
set -uo pipefail

payload="$(cat 2>/dev/null || true)"

file_path="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
if d.get("tool_name") not in ("Edit", "Write"):
    sys.exit(0)
fp = (d.get("tool_input") or {}).get("file_path") or ""
print(fp)
')"

[ -n "$file_path" ] || exit 0
case "$file_path" in
  *.ex) ;;
  *) exit 0 ;;
esac
[ -f "$file_path" ] || exit 0

# ── Locate the nearest mix.exs, walking upward from the edited file ────────
# This is the only "what kind of project is this" logic in the whole hook,
# and it is purely structural (does a mix.exs exist above this file?) — no
# knowledge of any particular project's layout.
dir="$(dirname "$file_path")"
mix_root=""
while [ "$dir" != "/" ] && [ -n "$dir" ]; do
  if [ -f "$dir/mix.exs" ]; then
    mix_root="$dir"
    break
  fi
  dir="$(dirname "$dir")"
done

# Does this project's own mix.lock depend on $1 (a hex package name)?
# Generic dependency probe — works for any dependency, any project.
mix_has_dep() {
  [ -n "$mix_root" ] && [ -f "$mix_root/mix.lock" ] || return 1
  grep -qE "\"$1\":[[:space:]]*\{:hex" "$mix_root/mix.lock" 2>/dev/null
}

findings=()

# ============================================================================
# ANTI-PATTERN LIBRARY (regex-based) — append here, one call per pattern.
# ============================================================================

REGEX_NAME=()
REGEX_PATTERN=()
REGEX_REQUIRES_DEP=()   # mix.lock dependency gating this check, or "" for none
REGEX_MESSAGE=()

add_regex_check() {
  REGEX_NAME+=("$1")
  REGEX_PATTERN+=("$2")
  REGEX_REQUIRES_DEP+=("$3")
  REGEX_MESSAGE+=("$4")
}

# To add anti-pattern #3: another add_regex_check call goes right here.

for i in "${!REGEX_NAME[@]}"; do
  dep="${REGEX_REQUIRES_DEP[$i]}"
  if [ -n "$dep" ]; then
    mix_has_dep "$dep" || continue
  fi
  hits="$(python3 -c '
import re, sys
pattern = re.compile(sys.argv[2])
try:
    content = open(sys.argv[1], "r", errors="replace").read()
except OSError:
    sys.exit(0)
lines = []
for lineno, line in enumerate(content.splitlines(), start=1):
    if pattern.search(line):
        lines.append(f"  line {lineno}: {line.strip()}")
if lines:
    print("\n".join(lines))
' "$file_path" "${REGEX_PATTERN[$i]}")"
  if [ -n "$hits" ]; then
    findings+=("[${REGEX_NAME[$i]}] $(basename "$file_path"):
$hits

${REGEX_MESSAGE[$i]}")
  fi
done

# ============================================================================
# EXTRA CHECKS (non-regex) — append the function name to EXTRA_CHECKS.
# ============================================================================




# Prose drift: this edit changed function-level code, but the file's embedded
# prose (moduledoc / @doc / CRISP block) was not part of the edit. Prose is
# atomic — it may describe only this module — and it is trusted only as far
# as it matches the code beside it, so a shape-level change that leaves the
# prose untouched deserves one conscious look. (Born 2026-08-27: a moduledoc
# that said "text-only" survived weeks of sibling-module image work and was
# re-asserted by four sessions.)
#
# Deliberately quiet by default: fires only when the Edit payload carries
# old_string/new_string (so full-file Writes and synthetic payloads skip),
# only when the edited region includes a def/defp line (shape change, not a
# body tweak), only when the edit itself touched no prose marker, and only
# when the file actually has a prose block to drift. Advisory, skippable —
# pressure toward good behavior, not a gate.
check_prose_drift() {
  local drift
  drift="$(FILE_PAYLOAD="$payload" python3 -c '
import json, os, re, sys
try:
    d = json.loads(os.environ["FILE_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)
ti = d.get("tool_input") or {}
old, new = ti.get("old_string"), ti.get("new_string")
if not isinstance(old, str) or not isinstance(new, str):
    sys.exit(0)
PROSE_MARKERS = ("@moduledoc", "@doc", "# CRISP", "# purpose:", "# insights:")
if any(m in old or m in new for m in PROSE_MARKERS):
    sys.exit(0)
if not re.search(r"^\s*defp?\s", old, re.M) and not re.search(r"^\s*defp?\s", new, re.M):
    sys.exit(0)
try:
    content = open(sys.argv[1], "r", errors="replace").read()
except OSError:
    sys.exit(0)
if not any(m in content for m in PROSE_MARKERS):
    sys.exit(0)
print("drift")
' "$file_path")"
  [ -n "$drift" ] || return 0
  findings+=("[prose-drift] $(basename "$file_path"):

This edit changed function-level code, and the file's prose (moduledoc / @doc / CRISP block) was not touched. Prose here is ATOMIC — it may describe only this module — and it is trusted only as far as it matches the code it sits next to. If this change altered what the module does or claims, the prose now describes code that is gone, and a future reader inherits testimony the code no longer supports. If it did not, the prose still holds.")
}

EXTRA_CHECKS=(check_prose_drift)

# To add anti-pattern #4 (or beyond) that needs more than a regex: write a
# check_<name> function above this line, then list its name here.

for fn in "${EXTRA_CHECKS[@]}"; do
  "$fn"
done

[ ${#findings[@]} -gt 0 ] || exit 0

ctx="$(printf '%s\n\n' "${findings[@]}")"

CTX="$ctx" python3 - <<'PY'
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
