#!/usr/bin/env bash
# elixir_advisory.sh — Claude Code PostToolUse hook (Edit|Write, *.ex files):
# a growing library of Elixir/Ash anti-pattern checks, purely advisory.
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
# ── V1 checks ────────────────────────────────────────────────────────────
#   1. mix credo --strict, scoped to the edited file — only if the nearest
#      mix.exs's mix.lock actually depends on credo.
#   2. Direct Ecto Repo.* calls (Repo.get, Repo.insert, etc.) — warned on
#      unconditionally, everywhere, no directory exemptions — but only in
#      projects whose mix.lock depends on ash, since the whole point of the
#      warning is "this bypasses Ash's pipeline."
#
# ── How to add anti-pattern #3 (and beyond) ─────────────────────────────
#   - Simple regex over the file's text? Add one `add_regex_check` call in
#     the ANTI-PATTERN LIBRARY section below. Four arguments: a short name,
#     the regex, the mix.lock dependency required for it to fire (or "" for
#     none), and the advisory message. That's the whole change.
#   - Needs to shell out to a tool, walk the AST, or do anything a regex
#     can't? Write one check_<name> function (mix_root and file_path are
#     already in scope; append findings via `findings+=("...")`) and add its
#     name to the EXTRA_CHECKS array. See check_credo below as the template.
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

add_regex_check \
  "ash-repo-bypass" \
  '\b\w*Repo\.(get!?|all|one!?|insert!?|update!?|delete!?)\b' \
  "ash" \
  "Direct Ecto Repo call(s) detected in a project that uses Ash. Calling Repo.get/get!/all/one/one!/insert/insert!/update/update!/delete/delete! directly bypasses Ash's pipeline silently: no authorization/policy checks run, no changeset validations apply, no notifiers fire, no calculations/aggregates are kept in sync. Nothing errors — it just quietly skips everything Ash would have enforced. Prefer the resource's Ash action or code interface instead (e.g. MyApp.Resource.get!/1, Ash.create!/1, Ash.read!/1, a generated code-interface function) unless this is a deliberate, reviewed exception (e.g. inside the Ash data layer/repo module itself, or a migration)."

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

# mix credo --strict, scoped to the single edited file. Only runs when the
# nearest mix.exs's own mix.lock actually depends on credo — a plain Elixir
# project (or one that doesn't use credo) is silently skipped, no noise.
# Bounds a cold/hung compile with whatever timeout binary is available
# (GNU `timeout`, or macOS's `gtimeout` from coreutils); if neither exists,
# falls back to running unwrapped — the hook's own PostToolUse timeout in
# hooks.json still caps the whole thing, this is just belt-and-suspenders.
check_credo() {
  mix_has_dep "credo" || return 0
  local credo_out timeout_bin=""
  if command -v timeout >/dev/null 2>&1; then
    timeout_bin="timeout 25"
  elif command -v gtimeout >/dev/null 2>&1; then
    timeout_bin="gtimeout 25"
  fi
  credo_out="$(cd "$mix_root" && $timeout_bin mix credo --strict "$file_path" 2>&1)"
  [ -n "$credo_out" ] || return 0
  printf '%s' "$credo_out" | grep -q 'found no issues' && return 0
  findings+=("mix credo --strict — $(basename "$file_path"):
$credo_out")
}

# Ash.Query.for_read (or bare for_read/2,3 when Ash.Query is imported/used)
# called without an `actor:` option. This is a real Ash security advisory
# (GHSA-pcxq-fjp3-r752): calling for_read without the actor, then passing the
# actor only to a downstream read/read! call, can let policies evaluate
# without actor context and silently skip authorization.
#
# Only runs in Ash projects. This is regex-based in spirit (same pragmatic
# trade-off as the Repo-bypass check above) but needs to look past the end of
# the line the call starts on — Ash calls are routinely multi-line — so a
# single per-line pattern.search() can't do it. It lives here (not in
# add_regex_check) for the same reason check_credo does: the shared loop only
# matches within one line at a time. Detection: find each `for_read(`, walk
# forward counting balanced parens to find that call's own closing paren
# (falling back to a bounded 400-char lookahead if parens never balance,
# e.g. truncated/malformed code), and flag it if "actor:" doesn't appear
# inside that span. This has known false negatives on unusual formatting
# (e.g. "actor:" split oddly across a string) and will NOT catch every
# multi-line style, but balanced-paren scanning handles the common Ash
# idioms (single-line and multi-line calls) that a naive fixed-line-count
# lookahead would miss.
check_ash_for_read_without_actor() {
  mix_has_dep "ash" || return 0
  local out
  out="$(python3 -c '
import re, sys

path = sys.argv[1]
try:
    content = open(path, "r", errors="replace").read()
except OSError:
    sys.exit(0)

lines = content.splitlines()
line_starts = []
pos = 0
for line in lines:
    line_starts.append(pos)
    pos += len(line) + 1

def line_of(idx):
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= idx:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1

findings = []
for m in re.finditer(r"\bfor_read\(", content):
    start = m.end()
    depth = 1
    i = start
    n = len(content)
    while i < n and depth > 0:
        c = content[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    if depth == 0:
        args_text = content[start:i - 1]
    else:
        args_text = content[start:min(start + 400, n)]
    if "actor:" in args_text:
        continue
    lineno = line_of(m.start())
    snippet = lines[lineno - 1].strip() if lineno - 1 < len(lines) else ""
    findings.append(f"  line {lineno}: {snippet}")

if findings:
    print("\n".join(findings))
' "$file_path")"
  [ -n "$out" ] || return 0
  findings+=("[ash-for-read-without-actor] $(basename "$file_path"):
$out

Ash.Query.for_read (or for_read/2,3) called without an actor: option. This maps to a real, documented Ash security advisory — GHSA-pcxq-fjp3-r752 (github.com/ash-project/ash/security/advisories/GHSA-pcxq-fjp3-r752). Calling for_read without the actor, then passing the actor later (e.g. only on the downstream read/read! call), can let policies evaluate without actor context — silently skipping authorization. The correct form is for_read(:action, %{}, actor: current_user) |> read!() — actor goes on for_read itself, not (only) on the downstream call.")
}

EXTRA_CHECKS=(check_credo check_ash_for_read_without_actor)

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
