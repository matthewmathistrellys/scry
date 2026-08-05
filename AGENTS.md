# Scry contributor contract

Scry provides the same `SessionStart` checks to Claude Code and Codex. Full
behavior lives in `README.md`; these rules govern changes to the shared hooks.

## Principles

- **One implementation for both clients.** Client-specific files package or
  register Scry; behavioral checks stay in the shared scripts. Do not fork a
  Claude and Codex copy of a check.
- **Quiet by default.** A signal speaks only when it would change a decision.
  A check that reports nothing is reporting that nothing needs attention.
- **Advisory, never blocking.** Every hook exits zero. Scry reports facts and
  leaves the response to the reader.
- **Projects consequences, never actions.** State mechanical, verifiable
  exposure, not workflow recommendations or predictions.
- **Never silently wrong.** Report an external dependency as unknown when it
  cannot be checked; do not imply it was checked and found healthy.
- **One concern per script.** Architecture, repository health, session fleet,
  and machine pressure remain independent checks.
- **Metadata, not conversation content.** Fleet detection may read stable or
  minimal session metadata needed for identity, cwd, activity, and title. It
  must not ingest prompts, responses, or summaries.

Keep thresholds documented in the README tuning table. Before handing off a
change, run `python3 -m unittest discover -s tests -v`, validate shell and JSON
syntax, and validate the Codex plugin manifest with the plugin validator.
