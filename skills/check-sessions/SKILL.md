---
name: check-sessions
description: Use when one session must see what all the other sessions are doing, on a schedule (every 30 minutes or longer via ScheduleWakeup) or on demand, when the user types /check-sessions, or when the user has lost track of the sessions. Reads the other sessions without messaging or waking them and writes a short note on each. For one long thread, use executive instead.
---

# Check sessions

An orchestrator is one herdr tab coordinating many build and run tabs. This is its heartbeat. It reads every tab's state and sends nothing to any tab, because a message to a tab is a paid turn: a tab idle past cache expiry rewrites 300-900k tokens ($3-7) to answer one line.

`<this skill's folder>` is the folder this SKILL.md sits in (resolve it from the skill file's own path). The gather script reads through Scry's shared reader, `peek.py`, two folders up, the same one the automatic peek on a quiet subagent uses.

Inputs from your own session: your **workspace** id (for example `w0`), any **named extras** (such as the owner's paired design sessions), your **own tab name**, and your **sessions file** (open and parked tabs; the path is per project). If one is missing, ask once and keep it in the wake-up prompt.

## The check-in, in order

1. **Gather.** Run `python3 <this skill's folder>/scrum-gather.py --workspace <workspace> --name <extras> --skip <own tab name> --evidence-dir <a scratch folder>` and read its output once. It reads herdr, the session registry, transcripts and git only. It prints a count line (every tab counted, so none drops out), one block per tab that changed, and flags: BLOCKED, WAITING, NOT RUNNING, SILENT, OVER 250k context, DO NOT WAKE. For each changed tab it writes an evidence file with everything that tab said and was told since the last check-in. Without herdr (iTerm, Terminal, tmux), add `--no-herdr`: the script then lists every live Claude session on the machine from the session registry and filters with `--name` instead of `--workspace`. Screen reads (recap, unsent input, background shells, the menu a blocked tab shows) are not available in that mode, so say so in the report. Never read a tab yourself and never `herdr agent prompt` or `send-keys`.
2. **Summarize with a model.** Give each changed tab's evidence file to a Sonnet subagent (one subagent for up to 5 tabs, all in one message), with the instructions in `<this skill's folder>/summarizer-prompt.md`. It returns one compact scrum note per tab. You do not read evidence files or long reports yourself.
3. **Act on flags only.**
   - **BLOCKED** (for example a session limit): list the tab and what it is blocked on.
   - **Must act**: message a tab only when it is blocked on something you can answer or has work only it can do. One tab, one message, never a broadcast, never "where are you" or "status".
   - **DO NOT WAKE** (cache cold with a big context, or over 600k context): never message it. If its work must continue, propose a fresh tab started from its handoff, and open it only with the owner's yes.
   - **OVER 250k context**: list for the owner with its size. Do not act on it.
4. **Park.** For a finished or idle tab, confirm its note or handoff file exists on disk. Then `herdr tab close <tab_id>` and record in the sessions file which tab, what it was for, and where its state lives. No file on disk, no close; tell the owner that tab has no handoff yet. Do not message a DO NOT WAKE tab to ask for one.
5. **Report to the owner.** Start with the count line from the script, so the tab total always adds up. Then only what needs their attention, in the executive scrum form: **Just done**, **Doing now**, **Next**, **Parked for the owner**. Plain words, every number saying what it counts, no commit hashes or tab ids. Facts and observations only; never a better or worse verdict on a production run. If nothing needs him, one line saying so. This feeds the `executive` skill; it does not replace it.
6. **Schedule.** `ScheduleWakeup` at 1800 seconds (never shorter), or 3600 when the script reported no tab changed. Pass the prompt below back unchanged.

## Wake-up prompt

Fill in the values and pass this exact text to `ScheduleWakeup` each time:

```
/check-sessions Workspace: <workspace>. Extras: <names>. Own tab: <own tab name>. Sessions file: <sessions file>. Run the check-in: run <this skill's folder>/scrum-gather.py once and read its output, message a tab only if it is blocked or must act, never wake a DO NOT WAKE tab, send long reads to a Sonnet subagent, park finished tabs whose state is on disk, tell the owner only what needs their attention, then schedule the next check-in with this same prompt.
```

## What this skill does not do

- It does not message or wake a tab to ask how it is doing, and never broadcasts.
- It does not open new feature or fix tabs. That needs the owner's explicit yes.
- It does not write to production.
- It does not make business decisions. Those go on the owner's list, one at a time.
