---
name: scry-statusline
description: Set up Scry's status line in Claude Code — adds the documented statusLine setting to the user's settings.json, keeping every other key, and refuses to overwrite a different status line unasked. Use when the user asks for Scry's status bar, says the bar is missing after installing Scry, or accepts the offer after Scry's session-start note that the status bar is not set up.
---

# Scry status line — one-step setup

A plugin cannot set `statusLine`; it is a user setting in
`~/.claude/settings.json`. This skill adds it by running the script Scry
ships. Do not edit the settings file by hand: the script keeps every other
key, writes the file in one rename, and refuses when it should.

Run it only because the user asked for the status line, or said yes when it
was offered. Claude Code only; Codex has no status line setting.

## Steps

1. Find the plugin root. The scripts live two directories above this skill
   file:

```bash
SCRY_ROOT="$(cd "$(dirname "SKILL_PATH")/../.." && pwd)"
```

Use the actual resolved path of this skill file to compute `SCRY_ROOT`.

2. Run the setup and read what it prints:

```bash
bash "$SCRY_ROOT/statusline_setup.sh" --install
```

3. Relay the result. It is one of:

   - **Set** — the setting was added. Tell the user the bar appears on the
     next status refresh and that a new session may be needed.
   - **Already set** — nothing changed.
   - **Not changed: a different statusLine** (exit code 2) — the script
     prints the status line that is there. Show it to the user and ask which
     they want; do not choose for them:
     - replace it: `bash "$SCRY_ROOT/statusline_setup.sh" --replace` (the file
       as it was is kept beside it as `settings.json.scry-bak`);
     - keep it and stop Scry's session-start note:
       `bash "$SCRY_ROOT/statusline_setup.sh" --silence`;
     - keep it and add only Scry's cache segment: point `statusLine.command`
       at `cache_deadline_statusline.sh` with the existing command in
       `SCRY_STATUSLINE_INNER` (README, Install). The script does not do this
       one; it is a hand edit the user makes or asks for explicitly.
   - **Not changed: could not be read** (exit code 1) — the settings file is
     not valid JSON. Say so; do not rewrite it.

`bash "$SCRY_ROOT/statusline_setup.sh" --status` prints what is configured and
writes nothing.
