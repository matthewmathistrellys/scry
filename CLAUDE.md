# CLAUDE.md — scry

SessionStart hooks that give a fresh session the state of the world instead
of making it dig. Full behavior lives in `README.md` — this is the design
contract for anyone, human or agent, editing the hooks themselves.

## Principles

- **Quiet by default.** A signal speaks only when it would change a
  decision. A check that reports nothing is reporting something: nothing
  here needs attention. Don't lower a threshold "to be safe" — that trades
  silence for noise, and noise is what gets skimmed.
- **Advisory, never blocking.** Scry reports facts and stops. What to do
  about them is the reader's call, not the tool's. No hook should ever
  exit non-zero to block a session.
- **Projects consequences, never actions.** When facts combine into
  something worth flagging, state the mechanical, verifiable consequence of
  the current state ("N files are exposed if X happens first") — never a
  recommendation ("you should branch," "consider a worktree"). A prediction
  of what *will* happen is a guess; a statement of what *is* true right now
  is a fact.
- **Never silently wrong.** Where a check depends on something outside its
  control (network, an optional config), report unknown as unknown,
  explicitly — never let it read as "checked, and fine."
- **Fails open.** Every hook exits 0 unconditionally. A session-start hook
  that blocks or hangs is worse than one that skips.
- **One concern per script.** `architecture.sh` / `health.sh` / `fleet.sh` /
  `pressure.sh` don't share state or read each other's output — each
  computes what it needs from scratch, even if that duplicates a cheap git
  call. Adding a new signal means picking which one of these it's a fact
  about, not inventing a fifth script for one line.

Change a threshold in the README's Tuning table, not in code — every
threshold is an env var for a reason.
