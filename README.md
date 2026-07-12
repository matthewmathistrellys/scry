# ash_scry

A [Claude Code](https://claude.com/claude-code) `SessionStart` hook for
Ash projects. The moment a session starts, it prints a one-line
architectural map of your Ash domains — so a fresh agent (or a fresh
you) orients instantly instead of re-discovering the codebase's shape
from scratch.

No `mix`/BEAM boot required — it's pure text parsing (grep + AST-free
regex over your `.ex` files), so it runs in well under a second.

## Example output

```
Ash domains (4):
  MyApp.Accounts (3 resources) — Users, sessions, and API tokens.
  MyApp.Billing (5 resources) — Subscriptions, invoices, and usage metering.
  MyApp.Catalog (2 resources) — Products and pricing tiers.
  MyApp.Support (1 resource) — Support tickets.
```

One line per `use Ash.Domain` module: the module name, its resource
count, and the first sentence of its `@moduledoc`.

## Install

1. Copy `scry.sh` and `scry_domains.py` into your project, e.g.
   `.claude/hooks/`.
2. Make the hook executable: `chmod +x .claude/hooks/scry.sh` — a
   non-executable hook is a silent no-op, so don't skip this.
3. Wire it into `.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": ".claude/hooks/scry.sh"
          }
        ]
      }
    ]
  }
}
```

That's it. `scry.sh` walks up from wherever the session starts looking
for the nearest `mix.exs`, and scans that project's `lib/`. If no
`mix.exs` is found — e.g. a session started outside any Elixir app in a
polyglot monorepo — it's a silent no-op.

## Why

Ash's `@moduledoc` convention already documents domain intent well; the
gap is that nothing surfaces it automatically. `mix docs` is too heavy
for a session-start glance, and asking an agent to `grep` around for
domain boundaries burns a round-trip every single session. This closes
that gap with something that costs nothing to run.

## License

MIT
