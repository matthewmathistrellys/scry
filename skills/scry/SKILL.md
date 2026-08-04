---
name: scry
description: Run Scry's health, fleet, pressure, and architecture checks on demand — the same signals that fire at session start, fresh right now. Use when something feels off, when you suspect machine pressure, when checking repo state mid-session, or when asked to "check the state of things."
---

# Scry — on-demand state of the world

Run the same four checks that fire at SessionStart, right now. Each script
lives in the plugin root and emits JSON — extract the `additionalContext`
field for the human-readable report.

## Steps

1. Find the plugin root. The scripts live beside this skill file:

```bash
SCRY_ROOT="$(cd "$(dirname "SKILL_PATH")/../.." && pwd)"
```

Use the actual resolved path of this skill file to compute `SCRY_ROOT`.

2. Run all four scripts in parallel via Bash. Each needs the current working
   directory passed as a JSON payload on stdin (the same shape SessionStart
   provides). Construct it from the session's current directory:

```bash
PAYLOAD='{"cwd":"'"$PWD"'","session_id":"on-demand"}'
```

3. Run each script and capture its output:

```bash
echo "$PAYLOAD" | bash "$SCRY_ROOT/architecture.sh" 2>/dev/null
echo "$PAYLOAD" | bash "$SCRY_ROOT/health.sh" 2>/dev/null
echo "$PAYLOAD" | bash "$SCRY_ROOT/fleet.sh" 2>/dev/null
echo "$PAYLOAD" | bash "$SCRY_ROOT/pressure.sh" 2>/dev/null
```

4. Each script emits a JSON object. Extract the readable report from
   `hookSpecificOutput.additionalContext` (and `systemMessage` if present).
   Parse with:

```python
import json
data = json.loads(output)
ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
msg = data.get("systemMessage", "")
```

5. Present the combined output to the user. Lead with anything that would
   change a decision — the same rule as SessionStart. If everything is
   clean, say so in one line.

## Important

- These are the SAME scripts that run at session start. The output format
  is identical. No special handling needed.
- health.sh makes network calls (git fetch, deploy check, gh pr list) so
  it may take 2-3 seconds. The others are local and fast.
- Every script exits 0 unconditionally. A missing script or a failed parse
  means that check is unavailable, not that something is wrong.
- Do NOT skip any of the four scripts. The value is the complete picture.
