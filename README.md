# scry

[Claude Code](https://claude.com/claude-code) and
[Codex](https://developers.openai.com/codex/) `SessionStart` checks that give a
fresh agent session — or a fresh you — the state of the world the instant it
starts, instead of making it dig or making you remember to ask. Both clients
run the same shared scripts from this repository; only their package manifests
are client-specific.

A session is told its working directory and a git snapshot, and nothing else.
Not what else is running, not what the last session was doing, not what the
machine is carrying. Independent checks and advisories fill that in.

| | Answers |
|---|---|
| **`architecture.sh`** | What *is* this codebase? |
| **`stack.sh`** | What is it built *on*? |
| **`pins.sh`**, **`pins_edit_advisory.sh`** | Is a version pinned here known-vulnerable, or far behind what is released? One line at startup, once, for a high or critical advisory on a runtime dependency; a count in the status line; a note on the manifest being edited; the rest on demand |
| **`elixir_build_guard.sh`** | Is this command about to cost me 20 minutes? |
| **`health.sh`** | Is this repo in good shape? |
| **`fleet.sh`** | What else is happening *right now*? |
| **`pressure.sh`** | What shape is this machine in? |
| **`main_drift_advisory.sh`** | Is the tree this subagent was handed the current one? |
| **`cache_handoff_monitor.sh`** | Is this session about to lose its prompt cache — and what is still unfinished, or what should be summarised first? |
| **`roster.sh`** | What did *this* session start that has not finished — and is it what I remember? |
| **teammates** (in `fleet.sh`, `roster.sh`) | Which in-process teammates, from any session a live Claude process has held, were never recorded as stopped — with the one tool that can tell, `ListAgents`, named. Said once per process per set of names. |
| **`statusline_setup.sh`** | Is Scry's status bar set up — and if not, why is there no bar? |
| **Markdown trust** | What goes wrong when repository prose is mistaken for authority? |

- **`architecture.sh`** — a map of the codebase. It is a *dispatcher*, not a
  language scanner: it detects the stack and hands off to `scanners/`
  (Elixir/Ash today, others to follow), so one hook works in every repo
  including a polyglot tree. It searches **down from the repo root**, not up
  from the session's directory — an upward walk only finds a project marker at
  or above cwd, so a monorepo whose `mix.exs` files live in `apps/*` reported a
  bare folder list and the domain map went silently missing, in exactly the
  repo that most needed it. Every mix project found is reported, each with its
  domains, resource counts, and `@moduledoc` first sentences.

  The search prunes vendored trees as it walks rather than filtering after —
  a naive repo-wide search of a real monorepo returned **2,512** `mix.exs`
  files, almost all under `deps/`, which would have reported Ash's own domains
  as the application's. Excluding `deps/`, `_build/` and `node_modules/` left
  62; excluding `.worktrees/` (~20 checkouts of the same projects) left the
  real 3. Pruned, it runs in ~240ms where a naive `find` takes ~2s.

  Outside a recognised project it falls back to a directory layout. The
  Elixir/Ash scanner also flags a worktree whose dependencies were never
  fetched (no `deps/` directory) — the fastest way to turn a fresh worktree's
  first `mix compile` from a bare `Mix.Error` into a heads-up instead.
- **`stack.sh`** — the stack the system actually runs on, read from live
  config and never from documentation: managed-database provider, hosting
  units, runtime and frameworks, and the third-party services that are wired
  up. It exists because an instruction file described the app's database as
  Fly.io managed Postgres months after it became Neon, and a session believed
  it. Two rules make it trustworthy where prose was not:
  - **Role-bound, never name-counted.** A provider is reported for a role only
    when the variable owning that role resolves to it. Counting brand names
    reproduces the original bug instead of catching it — in the repo that
    produced it, `.flycast` outnumbered `neon.tech` 25 files to 11 while the
    database was unambiguously Neon; every Fly hit was an unrelated *service*
    address and most Neon hits were migration plans in `docs/`.
  - **Config only, never prose.** `docs/` is excluded outright. Prose is the
    thing that was wrong; reading it back would launder the error.

  It reads `.env` because that is where a database variable's binding is
  actually resolvable, and it emits **no secrets** — connection strings are
  reduced to a provider label and region during parsing, and credentials are
  discarded before any value is retained. A `localhost` binding on a
  non-default port is reported as a tunnel with the provider undetermined,
  rather than as "local", which is literally true and actively misleading.
  Service names come from env var *names*, so they prove a service is *wired*,
  not that it is in use.

  It also correlates the database against the workload and the machine, and
  speaks only when they conflict:
  - **Scale-to-zero that cannot fire.** A Neon/Supabase compute suspends only
    after N seconds with *zero* connections. A scheduler that polls every
    minute never lets the count reach zero, and a machine pinned with
    `min_machines_running` never lets the app go down — so the database's own
    suspend setting is inert while the bill accrues. Reading that setting would
    produce a confidently wrong "this one is fine"; the machine lifecycle is
    what decides.
  - **Migrations over a transaction pooler.** A pooler is right for
    application traffic and wrong for DDL — advisory locks and prepared
    statements do not survive transaction-mode multiplexing — so a pooled
    `DATABASE_URL` with no `DIRECT_*` counterpart is flagged.
- **`pins.sh`** — pinned versions that have a published security advisory, or
  that are far behind the latest release. Born 2026-10-09, from two cases in
  one week in one repo: `config :tailwind, version: "4.1.12"` named a
  standalone binary that cannot run on the current macOS, and nobody noticed
  for months, because a version written in a config file is in no lockfile and
  no `outdated` or `audit` command reads it; and a `mix.lock` carried a batch
  of packages with published advisories that surfaced only when someone ran an
  audit by hand.

  **Who hears what.** The first cut printed every advisory and every
  out-of-date package into the session at every start, resume, clear and
  compact — about 1,300 characters, several times a day, nearly all of it
  already said. It was reworked before it shipped:

  | Where | What | When |
  |---|---|---|
  | session start (`pins.sh`) | **nothing**, or one line: a sentence of at most 160 characters with the count of packages and up to three names, then the command for the full list | `startup` only — never resume, clear or compact — and only for a **high or critical** advisory on a **runtime** dependency that the current branch does not itself change against the default branch. Said **once per repository per advisory set** |
  | status line (`statusline.sh`) | `pins 4!` in red: runtime dependencies with a high or critical advisory. Nothing when clean | always, for the human; read from one small file in the cache |
  | an edited manifest (`pins_edit_advisory.sh`) | one line about **that file's** findings: its advisories, and what in it is behind | `PostToolUse` on Edit/Write of a dependency manifest or pinned-tool config, once per session per file, silent when the file has no findings |
  | on demand | everything, one finding per line | `bash pins.sh --full` (from the plugin directory; `--full <dir>` for another repo) |

  Nothing is said at commit, at deploy, at Stop or at session end.

  *Once per advisory set.* What was said is recorded in the cache, per
  repository (every worktree of it shares the record). Nothing is said again
  until a high or critical advisory id appears that was not in it, or
  `SCRY_PINS_REMIND_DAYS` (1) pass — daily, because a weekly line is said to
  one of the many sessions that start in a repository and missed by the rest. An advisory going away is not news, so a
  shrinking set says nothing.

  *Not what the branch itself changes.* A pin whose version differs from the
  one at the branch's merge base with the default branch — committed or still
  uncommitted — is that branch's own work in progress, and is left out of the
  startup line. Where there is no default branch to compare with, or the
  lockfile is not tracked by git, the pin is kept.

  *Runtime or dev.* A dev/test-only dependency never triggers the startup
  line and is not in the status-line count; `--full` lists it under its own
  heading, marked `[dev]`.

  | Ecosystem | Dev/test-only when |
  |---|---|
  | mix | declared with `only:` naming nothing but `:dev` / `:test`, or reached in `mix.lock` only from such dependencies (the lock's own dependency lists are followed, optional ones excepted). `runtime: false` alone is not dev |
  | npm | the lockfile entry carries `"dev": true` |
  | Python | the requirements file is named for it: `requirements-dev.txt`, `requirements_test.txt`, also `lint`, `ci`, `docs` |

  Anything that cannot be told is runtime: a lock line no `mix.exs`
  declaration reaches, a `tailwind` / `esbuild` config pin, a plain
  `requirements.txt`.

  *Cooldown.* A release counts as the "latest" a pin is behind only once it
  has been published for `SCRY_PINS_COOLDOWN_DAYS` (5): a release a few hours
  old is the one most likely to be yanked, or to be the compromised one. The
  pin is measured against the newest release older than that, and `--full`
  lists what is being held back and how old it is. The registries' publish
  dates are stored with the versions, so changing the setting needs no lookup.
  Advisories are exempt: a fix is named however new it is.

  What it reads, searching down from the repo root with the same pruning as
  the other scanners:

  | Source | Advisories | Behind latest |
  |---|---|---|
  | every `mix.lock` (Hex) | every locked version | packages a `mix.exs` declares |
  | `config :tailwind, version:` / `config :esbuild, version:` in a mix project's `config/*.exs` | as the npm packages `tailwindcss` / `esbuild` | tailwindlabs/tailwindcss GitHub releases (npm if GitHub will not answer) / the `esbuild` npm package |
  | every `package-lock.json` (lockfileVersion 2 or 3) | every locked version | the lockfile root's own dependencies |
  | every `requirements*.txt` | exact `name==version` lines | the same lines |

  Advisories come from [OSV](https://osv.dev) (`querybatch`, one request for
  the whole repo); latest releases and their publish dates from hex.pm, the
  npm registry, PyPI and GitHub. No project tooling is run and no dependency
  has to be fetched or compiled. "Behind" is computed only for packages the
  project declares itself: a transitive package is not a version this repo
  chose, and looking them all up is what would exhaust hex.pm's 100 requests a
  minute.

  **No hook waits on the network.** Session start reads the lockfiles and a
  local cache (`~/.cache/scry/pins.json`) and exits — ~0.15s. When the cache
  lacks an answer or holds one older than a day, it starts a detached refresh
  and returns without waiting; that refresh has a wall-clock budget of its
  own. So the first session in a repo nobody has checked says nothing and the
  next one has the answer, and an offline machine sees what the cache held.
  The edit note and the status line make no network call and start no
  refresh. The cache is keyed by package and version, not by repo, so twenty
  worktrees of one repo share one set of lookups. The lockfiles are re-read
  every time, so a bumped pin stops being reported the moment the file
  changes. The status line reads a per-worktree summary
  (`summary-<id>.json`) that the scanner rewrites at session start, after a
  refresh, after a manifest edit and on `--full`; between those it shows the
  last count, so a `git checkout` alone does not move it. When the cache
  directory does not exist the status line does nothing for this segment.

  What leaves the machine: package names and versions, to OSV; package names,
  to the registry that publishes them. No token is read or sent. A package
  from a private Hex repository, a git or path dependency, or an npm entry
  not resolved from `registry.npmjs.org` is skipped, so its name goes nowhere.
  Outside a git repository the hook does nothing.

  One vulnerability published under several ids (GHSA, CVE, EEF-CVE, PYSEC)
  is counted once. Severity is the publisher's label when the advisory has
  one. Many advisories — most of Hex's — publish a CVSS vector and no label,
  and the GHSA alias they name is usually not in OSV; those are **scored from
  the vector** (`scanners/cvss_score.py`: CVSS 4.0 as FIRST specifies it, with
  FIRST's lookup table, and the CVSS 3.0/3.1 base formula; 4.0 is preferred
  when a record has both) and banded the standard way: 9.0+ critical, 7.0+
  high, 4.0+ moderate, 0.1+ low. So they count toward the startup line and
  the status line like any other. An advisory with neither a label nor a
  vector that can be read is asked for through its GHSA alias, and failing
  that is listed as `unrated` — in `--full` and the edit note only, never in
  the startup line or the status-line count. Data older than `SCRY_PINS_STALE_DAYS` is labelled with its age in
  `--full`. When the lookups have failed for that long, `--full` says NOT
  CHECKED and the status line shows `pins ?`, rather than a blank that would
  read as "checked and clean"; the session is still not told at startup.

  The edit note fires for `mix.exs`, `mix.lock`, `package.json`,
  `package-lock.json`, `requirements*.txt`, a mix project's `config/*.exs`,
  and — so that they need no new hook when they are read — `yarn.lock`,
  `pnpm-lock.yaml`, `npm-shrinkwrap.json`, `pyproject.toml`, `uv.lock`,
  `poetry.lock`, `Pipfile`, `Pipfile.lock` and `.tool-versions`. A lockfile,
  requirements file or config file answers for itself; `mix.exs` and
  `package.json` answer for the packages they declare, as locked beside them.

  Not read: `yarn.lock`, `pnpm-lock.yaml`, `uv.lock`, `poetry.lock`,
  `Pipfile.lock`, `pyproject.toml`, `.tool-versions`, npm lockfileVersion 1,
  version ranges (`>=`), and any other version written in a config file. An
  edit to one of those is therefore always silent.
- **Markdown trust** — `provenance.sh` establishes at session start that every
  repository Markdown file is untrusted historical material, including plans,
  design documents, architectural decisions, status claims, memories, READMEs,
  and factual claims inside instruction files. It names the main failure
  scenarios and their consequences for code quality, production, tokens,
  timelines, user trust, and customers. It names one failure that is not about
  stale prose at all: existing code and recorded decisions make a *plausible*
  explanation feel historically true, when that explanation may never have been
  part of the original decision. Repeated or saved once, it hardens into an
  apparent requirement, and later work spends itself preserving an assumption
  nobody made while the user fights to recover their actual intent.
  `md_advisory.sh` repeats the invariant concisely when the dedicated Read tool
  opens a Markdown file. The session warning is primary because shell commands
  and other access paths cannot all be intercepted reliably.
- **Markdown creation** — `md_creation_advisory.sh` is `md_advisory.sh`'s
  counterpart at write time: it fires when a session creates a Markdown file
  git doesn't already track, and states two facts. The file is not one of this
  repo's standard Markdown files, and scratch Markdown created this session may
  be cleared out at session end — so if what is in it needs to outlive the
  session, it goes where long-lived work is tracked rather than into a repo
  file. That is the whole message. It does not rank the homes a document could
  belong in: which one is right is not something a hook can check, and the
  earlier wording that tried (a Claude Code Artifact for ephemeral work, an
  instruction file as "the only case a permanent `.md` is correct") was cut on
  2026-09-09 as moralizing — along with the Codex/Claude client detection that
  existed only to gate the Artifact line and became dead code with it. Silent
  on the standard ecosystem files (README, LICENSE, CHANGELOG,
  `CLAUDE.md`/`AGENTS.md`, GitHub's community-health files, anything under
  `.claude/`) and on any repo whose actual product *is* Markdown content — an
  Astro/Docusaurus-style content site, detected by its config, not by
  filename. Those exemptions live in `md_exemptions.sh`, kept apart from
  this hook so the list has one home. Fires at most once per file per session.
- **Code-prose trust (atomicity)** — `code_prose_advisory.sh` extends the
  Markdown doctrine to prose embedded in source files (`.ex`, `.exs`, `.py`):
  moduledocs, docstrings, doc comments, CRISP blocks. The doctrine is
  *atomicity*: a file's prose may speak only for its own module. A claim that
  reaches wider — other modules, the pipeline, production behavior, "the
  system always/never" — is **stricken**: invalid as evidence no matter how
  authoritative it reads, usable only as a signal of where to investigate.
  Injected on every Read of a source file. Born 2026-08-27, after a stale
  moduledoc claiming "the pipeline is text-only" was re-asserted by four
  consecutive sessions while the disproving sibling module sat two files away.

  It also says what an *accurate* citation still cannot establish. Reading a
  line settles what that line says; its behavior lives in its callers,
  conditions, and consumers. Skip those and a correct quotation becomes a false
  conclusion — then a test that enshrines it, or code that acts on the wrong
  records — and the next reviewer makes the same leap from the same line.
- **Seed and test data** — `seed_data_advisory.sh` gives an insight when a
  session reads a seed file or test data (fixtures, factories, `test/support`),
  or when a search reaches one — the Grep and Glob tools, or `grep`/`rg`/`ag`/
  `find`/`fd`/`git grep` through Bash, with the files named: seed files usually
  run once against an empty database and are rarely kept current, and test data
  holds values built for tests, so neither is a trustworthy record of what the
  live system holds. A search that finds nothing, in a repo that keeps records
  in a database (tracked migrations, `alembic.ini`, `schema.prisma`,
  `schema.rb`), gets one more: a value absent from the code may still exist in
  the database. Once per file per session for the first two, once per repo per
  session for the empty search; a Bash search piped or chained into anything
  else is never read as empty. Born 2026-09-24, after a session searched the
  code, found a document kind missing from the seed file, and told the user it
  did not exist — it had been in the database all along.
- **Stale-tree advisory (subagents)** — `main_drift_advisory.sh` fires on
  `SubagentStart` and tells the *child* agent that the worktree it was handed
  is behind `origin/main`, by how many commits and how many days, and how to
  read the live tip instead (`git show origin/main:<path>`,
  `git grep <pattern> origin/main`, `git ls-tree -r --name-only origin/main`).
  It exists because none of Scry's other surfaces reach a subagent:
  `SessionStart` output does not propagate into one, and `PreToolUse` /
  `PostToolUse` `additionalContext` on the Agent tool lands in the **parent**.
  `SubagentStart` is the only event whose `additionalContext` is delivered into
  the spawned agent.

  Born 2026-09-09, after subagents dispatched to describe a repo's
  architecture read a checkout whose local `main` was 427 commits and 14 days
  behind, and reported a five-stage pipeline that has six. Nothing errored: the
  sixth stage's files were simply not on disk, `grep` found nothing, and
  **nothing read as non-existence**. That is the sentence the advisory carries,
  because a bare commit count changes no behavior — the identical count was
  already on screen during the incident.

  Narrow on purpose. It speaks only when the agent's worktree is checked out
  on the repo's *default* branch — a feature branch is expected to diverge and
  its author knows what it is based on. It never fetches (`health.sh` already
  fetched at session start; a fetch here would put network latency in front of
  every dispatch) and never mutates: missing refs mean silence, not a fetch.
  Past `SCRY_SUBAGENT_STALE_DAYS` (7) or `SCRY_SUBAGENT_STALE_COMMITS` (50) the
  wording escalates to say plainly that an architecture answer from that tree
  is unreliable — that is the distance at which whole features land. There is
  no `agent_type` exemption: the built-in Explore agent, whose entire output is
  what is and isn't in a tree, is the type *most* exposed to this, not least.
- **Session disposal** — removed 2026-09-27. It reported, once per session
  at `Stop`, the merged worktrees, idle teammates and scratch Markdown a
  session was leaving behind. `Stop` fires at the end of every turn, so the
  note landed mid-conversation and cost the model an extra turn to relay it.
  Merged worktrees and idle teammates are reported at the next session start
  (`health.sh`, `fleet.sh`) instead.

- **Cache deadline** — `cache_handoff_monitor.sh` (Claude only) speaks
  shortly before this session's prompt cache goes cold. Claude Code's prompt
  cache has a time-to-live; every request refreshes it, and an idle session
  lets it run out, after which the next request re-reads the whole context at
  the full rate. While work the session started is unfinished it sends a
  keep-alive with that work's status as Scry can read it; otherwise, at most
  once per user message, it asks the session to write a summary as its reply
  in the conversation. One cached request in the last minutes of the warm
  window leaves that summary at the end of the transcript, where a `/clear`'d
  successor or a new window can find it. No file is written: until 1.31.0 the
  summary went to `~/.claude/scry/handoffs/`, and anything already there is
  left alone (Matt, 2026-09-13: "Why does it need a file, why can't it just
  write it inline").

  Scry reports and the session judges (same decision: "If it's deterministic
  and simple and reliable keep doing it; if not, just tell the orchestrator
  you've got so many agents and here's their status as far as I can tell").
  It stores no progress fingerprints, compares nothing between polls, and
  calls nothing stuck. Its messages name no other system.

  Three pieces, two small files of numbers per session:

  *`cache_deadline_statusline.sh`* records when the cache expires. The
  status-line payload is the only place Claude Code reports it
  (`prompt_cache.expires_at`, verified against the 2.1.267 binary on
  2026-09-10), and no plugin can install a status line, so this is a wrapper
  the user points `statusLine.command` at once (see [Install](#install)).
  It keeps `observed_at`, `warm`, `ttl`, `expires_at`, `requests` and passes
  the payload through to any status line named in `SCRY_STATUSLINE_INNER`,
  appending one segment to the end of the bar (icon, space, value): `♻️ 43m` while warm (reusing what was read), `♻️ 4m ~150k` in yellow with the
  re-read size inside the last ten minutes, and `🗑️ ~150k` in red once it has
  expired. In iTerm2 it colours the session's tab the same way
  (`tab_colour.sh`): nothing while warm, yellow in the last ten minutes, red
  once cold, cleared when the session ends — so a glance at the tab bar shows
  which sessions are going cold. The size is Claude
  Code's own `recache_tokens_if_cold` — what the next request re-reads at the
  full rate if nobody speaks first. Nothing avoids that re-read once the cache
  is cold: a `/compact` sends the same history to write its summary, and
  resuming does too. The only free move is `/clear`, which is why the summary
  is asked for and why the new session is told where it is (see `fleet.sh`).

  *`cache_handoff_arm.sh`* is a `UserPromptSubmit` hook that writes "armed".
  It is the whole re-arm rule: only a user message starts a new cycle. Tool
  calls, cache hits, monitor notifications, and the summary itself never do —
  the summary is a request, a request refreshes the cache, and a monitor that
  re-armed on a warm cache would ask every hour forever with no user in the
  loop (designed with Codex, 2026-09-10). Claude Code runs `UserPromptSubmit`
  for task and monitor notifications as well as user messages, and the payload
  has no field that tells them apart (binary 2.1.270, 2026-09-13), so the hook
  compares the opening of `prompt` against two fixed prefixes —
  `<task-notification>` and `Another Claude session sent a message:` — and a
  match does not arm. That comparison is the only place Scry looks at any part
  of a prompt; nothing of it is stored or printed. The same hook, on
  `SessionStart` and every `UserPromptSubmit`, records which session this
  Claude process is running (`pid/<CLAUDE_PID>`).

  *`cache_handoff_monitor.sh`* is a plugin monitor (`monitors/monitors.json`):
  a background process Claude Code arms at session start whose every stdout
  line reaches the model as a notification — which is what wakes an idle
  session. It polls those files with no model calls and, when the session
  is armed and the deadline is within the lead window, prints one line: the
  expiry time and a request for a summary written as the session's reply in
  the conversation, not to any file. The summary opens with the full session
  id and the line "To continue in a new window, say: pick up from session
  <id>", then covers the objective and latest user direction, decisions and
  open questions, what was done and verified, what remains and the next step,
  the working directory and branch, and any still-unfinished workers from the
  roster below. The cycle is marked `requested` before printing so a restart
  cannot ask twice. A deadline
  that passes while armed is marked `missed` and said on the *next* user turn
  by the arm hook, never by the monitor: a monitor line wakes the model onto a
  cold cache, which is the exact cost this exists to avoid.

  The monitor is started once per Claude process and `/clear` does not restart
  it, so on every poll it works out which session the process is running now:
  Claude Code's `~/.claude/sessions/<pid>.json`, then the arm hook's record,
  the newer of the two, then its own environment only if neither exists. The
  session a `/clear` left is marked `superseded` (keeping `requested:<time>`
  or `armed:<time>`, what it was before) and its deadline is never read again,
  so its request is not fired into the new session; the new session is watched
  from its first user message.

  *Keep-alive.* A subagent, workflow or background shell that finishes wakes
  the session that started it, and on a cold cache that wake re-reads the
  whole context. So when the lead window arrives while this session has
  unfinished work, the line is a keep-alive instead of the summary request. It
  carries the roster — every unfinished worker the session started, with what
  Scry can read deterministically from metadata:

  | Worker | Listed while | Reported as |
  |---|---|---|
  | workflow | its journal has an agent `started` with no `result`, however long it has been quiet | `workflow <name> (<id>): <finished> of <started> agents done, last activity <age> ago` — the name from `workflows/scripts/<name>-<id>.js`, else the id alone |
  | background command | a process still holds its `b*.output` open (one `lsof` call over the files with no exit marker; the shell running a command has the file as stdout until it exits, and nothing holds a finished one) — except a Scry monitor's own notification stream (opens with "Scry — "). Until 1.35.0 the missing marker alone was the test, and a foreground Bash call writes the same file with no marker, so every large-output command a session ever ran read as running forever (2026-09-22: fifty in one session, four days old, kept warm hourly for nothing). Without `lsof`, none is listed | `background command <task id>: a process still holds its output open, last output <age> ago` — what Scry sees, not a claim that it is progressing |
  | subagent | its `agent-*.jsonl` was written within `SCRY_KEEPALIVE_FRESH_SECS` (default 3600, one TTL) | `subagent <id> "<description>": last activity <age> ago` — and in the last quarter of the window, what the silence means: its own cache goes cold at the TTL; finished, nothing happens; still working, its next step re-reads its context once at the full rate; stuck, stop it before that read. The same note rides a tool result while the session is awake (`subagent_cold_advisory.sh`, PostToolUse, once per subagent), so it is never a wake of its own (Matt, 2026-09-22) |

  A plain subagent's metadata has nothing that says it finished, so Scry
  cannot tell a finished one from a quiet one; the one-TTL window is its only
  bound, and one silent longer than that is left out rather than reported as
  running. While something listed has written within one TTL, the keep-alive
  says a one-line status for the user is enough of a reply, that a worker
  whose age reads the same as in the previous status has not moved and is the
  session's own to look at, and how many of the listed workers have been
  silent for a whole TTL. Any reply is a cached request, which moves the
  deadline an hour.

  *Silent work.* When nothing listed has written for a whole TTL
  (`SCRY_KEEPALIVE_FRESH_SECS`, the hour the previous reply bought), the line
  changes: the silent work is the only thing keeping the session warm; how
  long it has been silent; Scry cannot see whether it is working quietly or
  will never finish; work that never finishes never sends the notification
  the session is kept warm for, so waiting does not resolve it; a reply that
  only says "still waiting" pays for another hour and learns nothing; and
  these are the session's own tasks, which nobody else is watching. It asks
  for no status and says nothing about what a command is doing or what to do
  with it. From the second one in a row it says so ("keep-alive 2 in a row to
  find exactly that: nothing changed in between"), and after
  `SCRY_KEEPALIVE_SILENT_MAX` (default 3) of them the summary is requested
  instead, naming the work and saying that whether it is still worth running
  is something the user would act on now. A write by any listed worker ends
  the run and the count starts again. The count is a number in
  `<session>.keepalive`; each pass compares write times with the clock, never
  with an earlier pass. (2026-10-09: three background commands hung on stdin;
  the old line said "still running … when that work finishes, its
  notification wakes this session" and asked for a status, so a session
  answered "still waiting" hourly for eight hours and never looked.)

  One keep-alive per
  deadline; if the deadline has not moved half a lead later, the summary is
  requested instead. `SCRY_KEEPALIVE_MAX` per user message, counted in
  `<session>.keepalive` (counts and times only) before the line is printed;
  a new user message starts the count again, and there is no per-session
  ceiling (Matt, 2026-09-22: "that defeats the purpose" — a new message is
  the signal the session is alive). When nothing is
  unfinished, a cap is reached, or a keep-alive did not take, the summary is
  requested, and any unfinished workers are listed for it to end with.

  Bounded: at most one summary request per user message, none without one,
  and at most `SCRY_KEEPALIVE_MAX` keep-alives before it.
  `SCRY_CACHE_HANDOFF=0` switches it off. It writes the summary nowhere
  itself; the session does, in its own conversation.
- **Roster** — `roster.py` is that same list, made available outside the
  deadline window (2026-09-14). Three readers: the monitor above; `fleet.sh`
  at every SessionStart other than a clear, which names what this session
  started and has not finished (a resume or a compaction is exactly when a
  session answers "is anything running?" from memory); and `roster.sh`, run
  on demand by the `/scry` skill or any Bash call, using the
  `CLAUDE_CODE_SESSION_ID` and `CLAUDE_PID` the Bash tool's environment
  carries. It adds one row to the table above: **plugin monitor** — a
  process under this Claude process running a script from the plugin cache,
  reported with the version in its path, which is the version actually
  running; when that differs from `installed_plugins.json`, the line says so,
  because Claude Code starts a monitor once per process and never restarts it
  (not on a plugin update, not on `/reload-plugins`). Why: on 2026-09-13 a
  session stopped four log-watchers, missed a fifth, and told its user
  "nothing running" twice across a night — the fifth's output file had no
  exit marker the whole time. The same session ran a 1.29.0 monitor all night
  while 1.31.0, which fixed that monitor's hourly re-arm loop, had been
  installed since the afternoon. The session had no list of its own; the file
  system did. Scry reports the list; the session judges and stops nothing.
- **Scale** — `scale_advisory.sh` speaks on first contact with a source file
  that is both large *and* actively worked, whether that contact is a Read or
  an Edit. It reports the file's length, its churn, and — on a Read of a file
  over 2000 lines — exactly how many lines the Read never returned and did not
  mark as missing. Then it names what goes wrong silently at that size: a
  helper you are about to add may already exist hundreds of lines away, and the
  duplicate compiles, passes review, and diverges later in production; and
  behavior you never read is behavior you can still break, because a match,
  guard or default further down may depend on the shape you just changed. The
  remedy asked for is seconds long — grep *this file* for the name before
  introducing it — and the advisory explicitly says to finish the assigned work
  rather than turn the note into a refactor.

  The trigger is deliberately **not** raw line count. Size alone flags the
  wrong files: the largest source file in the codebase this was built for
  (3026 lines) is a flat declarative field registry that must not be split,
  while the file that actually costs the most (2498 lines, 20 commits in six
  months) is well factored and scores as unremarkable on any density measure.
  Size is therefore gated on **churn** — a large file nobody touches costs
  nothing, and warning about it only teaches sessions to skim every advisory.
  The size threshold self-calibrates to the repository (p95 of that language's
  own file-length distribution, clamped to 400–1200) rather than importing a
  folklore constant, so it neither nags a codebase of small files nor goes
  silent as one bloats. Fires at most once per file per session: the risk is
  first contact, not sustained work in a file already being reasoned about.
  Declaration-only files are told they are legitimately long.
- **Prose drift** — a `check_prose_drift` advisory inside `elixir_advisory.sh`:
  when an Edit changes function-level code (`def`/`defp` in the edited region)
  without touching any prose marker, and the file carries a prose block, one
  advisory notes that the prose was left behind and asks for a conscious look.
  Deliberately skippable — pressure toward good behavior, not a gate — and
  quiet by default: full-file Writes, body-only tweaks, prose-touching edits,
  and files with no prose block all stay silent.
- **`agent_model_guard.sh`** — one rule on every surface that dispatches work
  to a model — `Agent`, `Workflow`, and a metered agent CLI run through `Bash`:
  **the model must be chosen.** A blank model is not "the default" — it
  silently inherits whatever the parent session runs on, invisibly at the call
  site, and a fan-out multiplies it. On 2026-08-31 that consumed most of a
  week's premium allowance in a single session: four verifier agents and a
  builder, none naming a model, none needing the model they got. Nobody typed
  the expensive model even once. They typed nothing, and got it anyway.

  It is **not against any model**. A cheaper agent asking the strongest model a
  hard question is legitimate and useful, and a premium model named
  deliberately — including on every agent in a workflow — passes without
  objection. Only the *absence of a decision* is blocked. It also refuses to
  name which model to pick: model rosters age out, the rule does not, so the
  instruction is to choose the cheapest one that can actually do the job and
  leave that judgement where it belongs. A `fork`, which inherits by design and
  ignores the override, gets one speed bump and then proceeds.

  Afterwards, a `PostToolUse` note reports what a premium dispatch costs —
  that it draws on a separate, much smaller allowance, that a single bounded
  question is cheap while duration and breadth are not, and explicitly that
  nothing is wrong, because the choice was deliberate and that was the point.
  Every internal error fails **open**: an unparseable payload, an unwritable
  state dir, a missing `python3` all allow the dispatch, because a cost guard
  that wedges a session costs more than the tokens it saves.
  `SCRY_PREMIUM_MODELS` (default `fable`) sets which models draw the note.

  The same rule reaches one layer further out, to an agent CLI invoked through
  `Bash`. With no model flag the CLI's own config default answers — a value set
  once, months before the call, invisible at the call site, and usually the
  strongest model the account can reach. On 2026-09-06 an overnight session ran
  seventeen design reviews that way, every one on the premium config default,
  draining the month's budget and locking the account out mid-day. A `codex`
  call naming no model is denied; `-m`, `--model`, `--model=` and `-c model=`
  all count as naming one, and management verbs that run no inference
  (`login`, `--help`, `mcp`, `doctor`, …) pass untouched.

  The command is **lexed and split on shell separators**, not grepped, because
  a whole-string search gets it wrong in both directions: `mkdir -m 755 x &&
  codex exec` looks pinned and is not, while `echo "run codex now"` looks like
  a call and is not. Splitting on separators keeps a `-m` from vouching for a
  command it does not belong to, and quoted separators stay inside their token
  so an `&&` in a prompt does not split the call around it. A command shlex
  cannot read fails **open**, like everything else here.

  Which CLIs are metered is configuration, never a hardcoded roster — a tool
  that bills today may not tomorrow, and one this file has never heard of may
  bill the most. `SCRY_METERED_CLIS` (default `codex`) sets the list,
  `SCRY_METERED_CLI_FREE` extends the no-inference verbs, and
  `SCRY_METERED_CLI_GUARD=0` switches the check off.

  One more thing on the same surface, and it is a **speed bump rather than a
  block**: `claude -p` bypasses the subscription login by design and reads
  `ANTHROPIC_API_KEY`, so with a key in the environment the run is metered per
  token against that API account while the subscription pays for none of it.
  Nothing in the command says so and nothing in the session shows it — people
  have found out at $447 and at $1,818, and consumed API credit is not
  refundable. Billing the API deliberately is a legitimate thing to want, so the
  first attempt is refused with that stated and the same command repeated within
  five minutes goes through untouched. `SCRY_API_BILLING_GUARD=0` silences it.

- **`elixir_build_guard.sh`** — a `PreToolUse` speed bump in front of the
  commands that throw away compiled Elixir artifacts: `mix compile --force`,
  `mix deps.compile --force`, `mix clean --deps`, `rm -rf _build`, `rm -rf
  deps`. None of these are destructive — nothing is corrupted and nothing needs
  repairing — but on an Ash project with 140+ dependencies a cold rebuild costs
  tens of minutes and saturates the machine while it runs.

  Because the harm is time rather than damage, it is a **two-strike gate**, not
  a block: the first attempt is denied with the cost stated and the cheaper
  options named; retrying within the window (default 5 minutes) lets it through
  untouched. The window is keyed on the project and the *intent*, never the raw
  command string — an agent's retry is almost never byte-identical, so a string
  key would deny the retry a second time and produce exactly the thrash the
  design exists to avoid. A reflex becomes a decision, and nothing
  is ever truly blocked. A plain `mix compile` — which is already incremental,
  and is the thing we want people running — never fires it.

  Fails open everywhere: not Bash, not an Elixir project, malformed payload or
  an unwritable state directory all allow the command silently. Disable with
  `SCRY_BUILD_GUARD=0`; retune with `SCRY_BUILD_GUARD_WINDOW`.
- **`health.sh`** — the repo's health from this session's perspective:
  - **Primary worktree state:** on main? dirty? stranded on a merged branch?
    How long parked? Files that exist in no commit on any branch?
  - **Primary worktree consequences:** when the session is in the primary
    worktree, projects the mechanical consequences — orphaned edits inherited
    by future sessions, silent ref corruption from concurrent checkouts,
    repo-wide merge blockage if left on a feature branch.
  - **Session worktree health:** when the session is in a linked worktree,
    states unprompted that Claude Code has locked the session to it and gives
    the one working way out (`ExitWorktree`) — the lock is real, not a
    Worktrunk setting, and losing an hour to relearning that live is what this
    line exists to prevent. Then reports the branch's relationship to the
    world — already merged (stranded on finished work), base drift
    (origin/main has moved since the fork point), unpushed commits (work that
    exists only on this disk), branch age (last commit N days ago), and files
    that exist in no commit on any branch (the same unrecoverable-on-cleanup
    check the primary worktree gets, run here too — the file at risk is just
    as often in the linked worktree as in the primary one).
  - **Local main vs origin/main:** divergence in either direction — stranded
    local commits or a stale local tip. Fetches remote state for comparison;
    never advances local branches or changes checked-out files.
  - **Deploy drift:** whether merged work is actually live. Opt-in: configure a
    health URL and it never stays silent again; leave it unconfigured and it
    stays out of the way, because a repo with nothing deployed cannot act on
    the warning (see [Deploy drift](#deploy-drift)).
  - **Worktree hygiene sweep:** merged worktrees safe to remove, abandoned
    branches quietly aging, missing directories, detached HEADs.
  - **Open PRs and CI status:** every open PR with CI rollup (green, failing,
    interrupted, pending, or unknown) and review state (approved, changes
    requested, needs review).
    The session's own branch is marked. Zero config — derived from the git
    remote via `gh`. Silently skipped if `gh` isn't installed.
- **`fleet.sh`** — how many other Claude and Codex sessions are live in this repo *and its
  worktrees*, how old they are, whether one is in your exact directory, which
  subagents are editing here without a session of their own, whether other
  agent CLIs (Gemini, aider, and others) are competing for the same machine, and
  what the last session here was called. After `/clear` it does one more thing.
  Claude Code ends the session and starts a new one (SessionStart fires with
  `source: "clear"`), and nothing in the new session says which one it
  replaced. `clear_record.sh`, a `SessionEnd` hook, writes that down as the old
  session ends — id, transcript path, directory, time — in one small file
  under `$TMPDIR/scry-last-cleared/`; the new session's `fleet.sh` reads the
  record for its directory, deletes it, and states five facts: the session
  left (title, id), its transcript path, what resuming it costs (its prompt
  cache is warm until HH:MM, a full re-read after), whether a summary was
  requested for it — from Scry's own state file for that session: if the
  cache-deadline monitor asked and no user message followed, its last
  message(s) should hold that summary, so read the end of its transcript
  first and send a Sonnet subagent to search the transcript if more detail is
  needed; otherwise no summary was requested and the transcript is the record
  — and **what it left running**. No
  recipe, no action: the reader decides. If the record is missing the newest
  transcript stands in and is labelled a guess, because with ten sessions open
  that guess is wrong exactly when it matters (council + Matt, 2026-09-10).
  Records nobody claims are swept after a day.

  That fifth fact is the one that cost an hour. A subagent, a workflow agent or
  a background job does not stop when the session that launched it is cleared,
  and — verified 2026-09-11, after this hook first shipped — neither does the
  handle: a message addressed to the task id reaches that agent and is
  answered, whether it is still running or finished hours ago. The id is the
  whole handle, though, and it is the hook that has to supply it. A replacement
  conversation is not shown the work: against an agent that has already
  finished — the usual case by the time anyone reads the advisory — `ListAgents`
  does not list it and `TaskOutput` answers `No task found with ID`. The message
  is what re-registers the task; only after it do those two work. So the id is
  not one door among several, it is the one that opens the others. An earlier draft of this paragraph said the result had "nowhere to
  land, because the session id it reports to takes no more turns". That was
  inference stated as mechanism, and the experiment contradicted it: the agent
  was re-parented to the replacement session, wrote its transcript there, and
  its completion notification arrived there. What `/clear` ends is the knowledge
  that the work exists, not the work and not the handle — so the exposure is
  duplication, not loss. Until 1.29.0 this hook
  made that worse rather than better: it *saw* those subagents, found their
  session id was not this session's, and reported them as "from other sessions"
  — which reads as somebody else's work. On 2026-09-11 a session cleared
  mid-build, could not see its own still-running builder, launched a second one
  on the same branch, and the two collided on the same PR. The subagents were
  always visible; they were filed under the wrong owner. So a subagent whose
  session-id directory matches the session just cleared is now reported as this
  seat's own in-flight work, with how long it has been running and the
  consequence — *the same job started again runs twice over the same files and
  the same branch* — and the same line names the tasks registered under that
  session id that have not recorded an exit, **by task id**, which is the
  handle the runtime itself uses for them. A task's file under
  `<task root>/<encoded cwd>/<session id>/tasks/` is a symlink when it belongs
  to a spawned agent, pointing at that agent's transcript, so its liveness is
  that transcript's mtime; a regular file is a shell task, finished once it
  carries a trailing `[exited with code N]`. Emptiness is not the test — an
  empty file is a command that has printed nothing yet, and a first cut of
  this check got that wrong in the direction that invents work. Attribution only ever uses
  the **recorded** id, never the transcript guess: a guess good enough to name a
  conversation for a human to resume is nowhere near good enough to tell a
  session that work is its own. For the same reason the cleared session no
  longer counts as a live neighbour — its transcript is seconds old, so the live
  window read it as an active session competing for this tree, in the same
  report that said it had ended.
- **`pressure.sh`** — load per core, swap in use, disk headroom, and which dev
  servers are already listening.
- **`statusline_setup.sh`** (Claude only) — says when Scry's status bar is not
  set up, and sets it up when asked. A plugin cannot set `statusLine`; it is a
  user setting. So a fresh install has hooks, a monitor and no bar, and until
  2026-10-09 nothing said why: Matt installed Scry on a new Mac, got no status
  bar, and had to find the snippet in [Install](#install). The bar is also
  where the prompt-cache expiry is recorded, so without it the cache-deadline
  monitor has nothing to read and never speaks.

  As a `SessionStart` hook it reads `~/.claude/settings.json` (and the
  project's `.claude/settings.json` and `settings.local.json`, since a status
  line set there is the one in effect) and writes no settings file. With no
  `statusLine` it says so, in one line, every session until it is set up or
  silenced. With a *different* status line it says so once per status line and
  then stays quiet: that one is there because someone chose it. A `statusLine`
  that reaches Scry's `statusline.sh` or `cache_deadline_statusline.sh` is
  silence. An unparseable settings file is silence too.

  The setup is one step, and only ever because a person asked: the
  `/scry-statusline` skill, or `bash statusline_setup.sh --install`. It adds
  the setting documented under [Install](#install), keeps every other key and
  their order, and replaces the file in one rename. When a different
  `statusLine` is already there it changes nothing, prints the one it found,
  and exits 2; `--replace` is the explicit way past that and leaves the file
  as it was beside it as `settings.json.scry-bak`. A settings file that is not
  valid JSON is never rewritten. `--silence` records
  `SCRY_STATUSLINE_CHECK=0` in the file's `env`; `--status` prints what is
  configured and writes nothing.

Nothing boots a build tool, and nothing touches the network except the optional
deploy-drift check, the PR/CI lookup (via `gh`, when installed), and the
detached refresh `pins.sh` starts at most once a day, which no session waits
for. On a busy
8-core laptop — the slow case, not the quiet one — the full suite runs in
~2 seconds.

## Quiet by default

The hard part is not gathering signals — it is not drowning you in them. A
correct warning that prints identically at every session start stops carrying
information: being told the same true thing on day 15 as on day 1 gives you no
reason to act. Six checks reporting healthy state every session would
industrialise that problem.

**So a signal speaks only when it would change a decision.**

| Signal | Silent | Speaks |
|---|---|---|
| Load | 4 on 8 cores | ≥1.5× cores |
| Swap | none | ≥2GB in use |
| Disk | 60% used | ≥90% used, or <20GB free |
| Sessions in this repo | just you | any other live one |
| Last session | none recorded | a title exists |
| Session left by `/clear` | start was not a `/clear` | always: id, title, transcript path, resume cost, whether a summary was requested for it |
| Session worktree location | never silent | states primary-worktree consequences, or the linked-worktree lock + `ExitWorktree` escape hatch — whichever applies |
| Session worktree merged | not merged | content already in main |
| Session worktree drift | up to date | origin/main ahead of fork point |
| Unpushed commits | all pushed | commits only on disk |
| Branch age | recent | ≥3 days since last commit |
| Orphan files (primary or session worktree) | none | a file exists in no commit on any branch |
| Stashes | none | any stash entry exists, with its age |
| Elixir/Ash worktree deps | `deps/` present | `deps/` missing — `mix deps.get` needed |
| Ash domains | no mix project in the repo | every project found, with descriptions |
| Scale-to-zero conflict | machine can actually stop | a polling worker + a pinned machine make it inert |
| Pooler for migrations | direct URL configured | DDL pointed at a transaction pooler |
| Dev tooling | none depended on | Tidewave / Livebook / LiveReload present |
| Build-time config | env reads only in `runtime.exs` | `System.get_env` in `config.exs` |
| Stale migration | migrations newer than any RESOURCE file | a resource changed since the last codegen |
| Ash read without an actor | target resource has no policies, or an explicit bypass | a policy-bearing resource is read with no actor |
| Spark extension edit | ordinary module | a DSL extension / transformer / verifier |
| Elixir build state | `_build` populated | `_build` cold — next compile is a FULL build |
| Force-rebuild command | any ordinary command | first `--force`/`rm -rf _build` attempt |
| Open PRs | none, or `gh` unavailable | any open PR exists |
| Pull request about to open | any command but `gh pr create`; under 10 changed lines; only prose changed; already said for this branch this session | once: lines and files changed, then what the research shows about a second reader and its findings |
| Subagent report comes back | not a subagent hand-back; the report does not read as a review | the kinds of finding a review returns (defect, quality, speculative) and how each is settled |
| Code comment written | not a source file; Markdown; under `$TMPDIR`, `/tmp/` or a `scratchpad/` directory; the edit adds no comment or doc text, whatever the file already holds; already said for this file this session | once per file: what keeps a comment in step with its code, plus a sentence for a comment about another module or file, or a name, a date or a story, in the added text or already in the file |
| Pinned version with an advisory (session start) | none high or critical on a runtime dependency; or dev/test-only; or the branch itself changes that pin; or already said for this advisory set within the last day; or the start is a resume, clear or compact | one line, once: the count and up to three names |
| Pinned version with an advisory (status line) | none high or critical on a runtime dependency | `pins N!` |
| Edited dependency manifest | the file has no advisory and nothing behind; or already said for this file this session | that file's advisories and what in it is behind |
| Pin in Elixir config, declared package behind latest | never said unasked | in `pins.sh --full` and the edit note: ≥1 minor behind, or any major, against a release at least 5 days old |
| Pin data | checked within 7 days, or never checked | status line `pins ?` and NOT CHECKED in `--full` when lookups have failed that long |
| Scry status bar (Claude only) | `statusLine` reaches Scry's status line | no `statusLine`: every session until set up or silenced. A different one: once per status line |
| Stack | no config found | always — see below |

Silence is the default and the feature. A check that reports nothing is
reporting something: *nothing here needs your attention.* Every threshold is
overridable — see [Tuning](#tuning).

Four signals are deliberate exceptions, and the list is meant to be exhaustive —
a doctrine that quietly accumulates unlisted exceptions is just a preference.
Deploy drift never stays silent **once configured** (unconfigured it says
nothing, since a repo with no running service can never satisfy it); the
Markdown trust census reports whenever repository Markdown exists; the stack summary
reports whenever it finds config; and the Ash domain map reports whenever a mix
project exists. The first two were granted exemptions previously; the last two
were added deliberately (Matt, 2026-08-21/22) on the grounds that a stale doc
crosses no threshold and trips no alarm, so orientation facts have to be stated
rather than waited for. So does `stack.sh`: a stale doc crosses no threshold and trips no alarm,
so the stack is stated every session rather than only when something looks
wrong. Markdown trust pays for its fuller session-start insight with a concise
read-time reminder, avoiding a repeated token-heavy explanation.

## Example output

```
Ash domains [apps/engine] (36, 122 resources):
  MyApp.Accounts (3 resources) — Users, sessions, and API tokens.
  MyApp.Billing (5 resources) — Subscriptions, invoices, and usage metering.
  ... every domain, with its @moduledoc first sentence

Ash domains [apps/exhibits] (1, 2 resources):
  Exhibits.Jobs (2 resources)
```

```
Stack (SessionStart, read from live config — fly.toml, .env, manifests; docs
deliberately not consulted):
- Data: Neon (us-east-2) [KB_DATABASE_URL]; tunnel via localhost:5433 —
  provider NOT determinable offline (likely `fly proxy`/ssh to a remote DB)
  [DATABASE_URL]
- Hosting: Fly.io, 7 app(s) (myapp-engine, myapp-ingress, myapp-ocr, myapp-web, +3)
- Runtime: Elixir/Ash/Phoenix (3 mix project(s)); Python; Node; FastAPI; Docker
- Services wired: Anthropic, Hatchet, Logfire, Outseta, Stripe, Stytch, Zep
```

```
Pinned versions: 4 runtime dependencies have a high or critical advisory
(pydantic-ai-slim, hackney, braces, +1 more). Full list: bash
~/.claude/plugins/cache/scry/scry/1.40.0/pins.sh --full
```

That is the whole of what `pins.sh` says at startup (one line; wrapped here),
and it says it once. The full list, on demand:

```
$ bash pins.sh --full
Pinned versions in /Users/you/Dev/myapp (advisories from OSV, releases from the
package registries, read from a local cache checked 3h ago; no project tooling
was run; 326 pinned version(s) read):
Runtime dependencies with a published security advisory: 21 (2 critical, 12 high, 5 moderate, 2 low)
- hackney 1.25.0 (apps/engine/mix.lock): 4 advisories, worst high CVE-2026-47071, fixed in 4.0.1
- braces 3.0.3 (apps/web/package-lock.json): high CVE-2026-93687
- ...
Dev/test-only dependencies with a published security advisory: 4 (1 moderate, 3 low)
- cowlib 2.19.0 (apps/engine/mix.lock) [dev]: 3 advisories, worst moderate CVE-2026-43966, fixed in 2.21.0
- ...
Pinned in Elixir config (a version written in a config file is in no lockfile,
so no outdated or audit command reads it):
- tailwind 4.1.12 in apps/engine/config/config.exs, latest release 4.3.3 (2 minor behind)
Behind the latest release: 43 of 104 directly declared package(s), by at least
1 minor version(s), 10 of them a major version. A release counts once it has
been published 5 day(s).
- typescript 5.9.3 → 7.0.2 (apps/voice-gate/package-lock.json) [dev]
- ash_ai 0.8.2 → 1.1.1 (apps/engine/mix.lock)
- ...
Newer releases not counted yet, published inside the last 5 day(s): ash 3.34.6
(26h ago); fastapi 0.143.0 (38h ago).
```

```
Dev environment health (SessionStart):
Primary worktree: /Users/you/Dev/myapp
- THIS SESSION IS IN THE PRIMARY WORKTREE — the shared checkout for the repo.
  Edits here are not isolated: uncommitted changes are inherited by every
  session that arrives after this one, with no indication of ownership. A
  branch switch from any concurrent session moves the checked-out ref silently
  — commits land on the wrong branch without warning. If this worktree is left
  on a feature branch, the repo-wide merge path stays blocked until someone
  returns it to main.
- On main. Modified: 0, untracked: 0.
- Production is up to date with origin/main (a1b2c3d).
Worktrees: 3 total (2 besides primary).
- 1 worktree(s) already fully merged into origin/main and safe to remove
  (git worktree remove): fix/old-thing
Open PRs: 2
  #42 feat/new-thing -- CI green, approved <- this session
  #43 fix/edge-case -- CI failing (1/4), no reviews
```

```
Dev environment health (SessionStart):
Primary worktree: /Users/you/Dev/myapp
- On main. Modified: 0, untracked: 0.
- Production is up to date with origin/main (a1b2c3d).
This session's worktree: /Users/you/Dev/myapp/.worktrees/feat-new-thing
- THIS SESSION IS LOCKED TO THIS WORKTREE — Claude Code confines a session to
  whatever worktree it started in, so it cannot directly create, enter, or cd
  into another one. The one way out is ExitWorktree, which returns to the
  primary worktree and unlocks the session — the conversation is preserved.
- Branch: feat/new-thing. Modified: 2, untracked: 1.
- origin/main is 5 commit(s) ahead of this branch's fork point — base has
  drifted.
- No remote tracking branch. 3 commit(s) exist only on this disk.
Worktrees: 3 total (2 besides primary).
Open PRs: 1
  #42 feat/new-thing -- CI green, no reviews <- this session
```

```
Session fleet (SessionStart):
- 3 other Claude session(s) active in this repo family in the last 15 min:
  myapp (2), myapp/.worktrees/dependabot-catchall. Oldest has been running
  1h50m.
- COLLISION RISK: one of them is working in this same tree, not just this repo
  family. This tree has 4 file(s) modified or untracked right now — if either
  side commits or resets first, the other's changes are what's exposed. Check
  before editing shared files, and do not assume a clean tree stays clean.
- Last session in this directory: "Fix accessibility issue" (ended 36m ago).
```

```
Machine pressure (SessionStart):
- MACHINE OVERSUBSCRIBED: load 18.7 on 8 cores (2.3x). More parallel agents
  or test runs will slow everything already running rather than finish sooner.
- Local servers already listening: node :3000 postgres :5432. Check before
  starting another — the port may be taken by a session you cannot see.
```

"Repo family" means the repo **and every one of its linked worktrees** — they
are one work stream, so a session in `.worktrees/foo` is a neighbour, not a
stranger. COLLISION RISK is scoped narrower, to the **same working tree**:
two sessions in `/repo` and `/repo/apps/web` are the same checkout even
though they're in different directories, and a `checkout`, `reset` or
`clean` in one is exactly as destructive to the other's uncommitted work. A
session in a different linked worktree doesn't carry that exposure — that's
the entire point of a worktree — so it stays in the family count, not the
collision line.

## Install

### Claude Code

```
/plugin marketplace add matthewmathistrellys/scry
/plugin install scry
```

That is the whole install. The hooks register themselves and run from the
plugin's own directory, so updating is `/plugin update scry` — there are no
copies on your machine to keep in sync.

One thing a plugin cannot do for you: `statusLine` is a user setting. Until it
is set, Scry says so at session start. Run `/scry-statusline` (or
`bash statusline_setup.sh --install` from the plugin directory) to have it
added, or point it at Scry's status line by hand:

```json
"statusLine": {
  "type": "command",
  "command": "f=\"$(ls -d \"$HOME\"/.claude/plugins/cache/scry/scry/*/ 2>/dev/null | sort -V | tail -1)statusline.sh\"; [ -f \"$f\" ] && exec bash \"$f\"; exit 0",
  "refreshInterval": 60
}
```

The resolver picks the newest installed Scry so `/plugin update scry` keeps
working, and prints nothing when Scry is not installed. The bar, left to right,
slow-moving to fast-moving:

```
scry  3 uncommitted  2 unpushed  pins 4!  Fable 5.1  $4.20  👥 3  🗓️ 64% Wed  🌕 42k/200k  ♻️ 39m
```

folder · branch (only when off the default branch) · `N uncommitted` and
`N unpushed` (only when non-zero — together, what would be lost if the
machine died now) · `pins N!` (only when a runtime dependency pinned here has
a high or critical advisory; read from `pins.sh`'s cache, never the network) ·
model · session cost · other live sessions in this
directory (only when any) · the seven-day window with the day it resets,
yellow from 70 %, red from 90 % · context as input tokens over the window,
moon phase by fill · the prompt-cache segment. A segment with nothing to say
is omitted; a segment whose source fails drops alone and the rest renders.
Only metadata is read from the payload — never prompt or response text.

To keep another status line and add only the cache segment, point
`statusLine.command` at `cache_deadline_statusline.sh` instead, with the
other command in `SCRY_STATUSLINE_INNER`.

Claude Code re-runs the status line on events (a new assistant message, a
compaction, and the moment a warm cache reaches `expires_at`), so the 🗑️
flip is on time by itself. The minute count only ticks between events; add
`"refreshInterval": 60` next to `command` to keep it live while you are away
from the keyboard.

### Codex

```sh
codex plugin marketplace add matthewmathistrellys/scry
codex plugin add scry@scry
```

Review and trust the six bundled hooks when Codex asks. Codex deliberately
does not run newly installed, non-managed plugin hooks until their definitions
have been trusted. Updating the marketplace snapshot and reinstalling refreshes
the cached plugin:

```sh
codex plugin marketplace upgrade scry
codex plugin add scry@scry
```

The Codex package uses `.codex-plugin/plugin.json`; Claude uses
`.claude-plugin/plugin.json`. Both discover the same `hooks/hooks.json`, skill,
scripts, and scanners, so there is no copied implementation to drift.

Two of the hooks ride events Claude Code defines — `SubagentStart`
(`main_drift_advisory.sh`) and `UserPromptSubmit` (`cache_handoff_arm.sh`). The cache-deadline monitor itself
(`monitors/monitors.json`) is a Claude Code plugin component with no Codex
equivalent as of 2026-09-10, and its status-line adapter reads a payload only
Claude Code produces; on Codex those two files are inert.
Whether Codex fires those event names has **not** been verified against a
Codex build; if it does not, those two groups simply never run there, which is
the same degradation-to-silence every other check has. The scripts themselves
are shared and client-agnostic — nothing about them is Claude-specific — so
adding Codex's equivalent event names is a manifest change, not a second
implementation.

All six checks are worth having everywhere, not just in the repos you
remembered to wire up: `fleet.sh` and `pressure.sh` are about the machine, and
every check degrades to silence where it doesn't apply. `architecture.sh` falls
back to a directory listing, `health.sh` skips a directory that isn't a git
repo, and `fleet.sh` says nothing when you're the only session.

To try the Claude package without installing:

```
claude --plugin-dir /path/to/scry
```

<details>
<summary>Manual install, without the plugin system</summary>

Copy the scripts plus `scanners/` into `~/.claude/hooks/`, `chmod +x` them
— a non-executable hook is a **silent no-op** — and wire them into the
`SessionStart` block of `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "~/.claude/hooks/architecture.sh" },
          { "type": "command", "command": "~/.claude/hooks/health.sh", "timeout": 60 },
          { "type": "command", "command": "~/.claude/hooks/fleet.sh" },
          { "type": "command", "command": "~/.claude/hooks/pressure.sh" }
        ]
      }
    ]
  }
}
```

This works, but the scripts are copies: a fix in the repo does not reach the
running hook until you copy it again, and nothing reports the drift.
</details>

`architecture.sh` keeps `scanners/` beside it and resolves the scanner relative
to its own location, so the two move together. `health.sh` always resolves to
the **primary** worktree via `git-common-dir`, regardless of which worktree or
subdirectory the session started in, and additionally reports the session's own
worktree health when it differs from the primary. `fleet.sh` reads the common
`SessionStart` JSON payload on stdin to learn its own session id, cwd, and
transcript path, so it never reports itself as a collision. Claude activity is
derived from recent metadata under `~/.claude/projects`; Codex activity is
derived from only the first `session_meta` record and mtime of rollouts under
`${CODEX_HOME:-~/.codex}/sessions`. Scry does not inspect Codex conversation
content.

All six emit the `hookSpecificOutput.additionalContext` envelope supported by
both clients. Each exits `0` unconditionally: a session-start hook that fails,
or that hangs, is worse than one that skips.

## Deploy drift (optional)

`health.sh` can also report whether **merged work is actually live** — the one
thing every other check misses, because they all compare git refs to other git
refs and never ask what production is running.

It is off until configured. Create `.claude/scry.env` in the project:

```
SCRY_HEALTH_URL=https://your-app.example.com/health
SCRY_HEALTH_SHA_FIELD=git_sha        # optional, this is the default
```

The file is parsed as plain `KEY=VALUE` and never sourced, so a config file
cannot execute anything. `SCRY_HEALTH_URL` in the environment wins over the
file. Your app must expose the commit it is serving as JSON at that URL.

**It never stays silent.** No URL configured, offline, endpoint down,
unparseable response, or a commit this repo has never seen: each says so
explicitly as `Deploy state UNKNOWN`, with the reason. A check that quietly
reports nothing teaches you it looked and found nothing wrong, which is worse
than having no check. The request times out after 3 seconds.

## PR and CI status (zero config)

When `gh` (the GitHub CLI) is installed and authenticated, `health.sh` lists
every open PR in the repo with a one-line summary: PR number, branch, CI
rollup, and review state. The session's own branch is marked `<- this session`.

Successful, neutral, and intentionally skipped checks roll up as green. A
known failure, timeout, action-required result, or startup failure takes
precedence over checks still running. Cancelled and stale checks are reported
as interrupted because they ended without a test verdict. Unrecognised or
malformed completed states are unknown rather than silently healthy.

No configuration needed — the git remote provides the repo. If `gh` isn't
installed or authentication fails, the block is silently skipped (fails open).

## Branch point

A branch is created from whatever ref the command names, and from `HEAD` when it
names none. Git raises nothing when that commit is one origin moved past hours
ago: the branch is created, and the work begins on files that are already the
older ones.

A `PreToolUse` advisory speaks when a Bash command creates a branch
(`git checkout -b`, `git switch -c`, `git worktree add`, `git branch <name>`)
**without naming a start point**. It states two facts and no instruction: how far
`HEAD` sits behind `origin/<default>`, and how long ago origin was last fetched —
because a `behind` count measured against a remote-tracking ref last updated
yesterday is itself yesterday's answer, and a zero from a checkout that has never
fetched means "nothing had landed as of the clone", not "nothing has landed".

It is silent when the command chooses its own start point, and when the tree is
current and the fetch is recent enough for that to mean something. It never
fetches, never moves a ref, and never denies.

Observed 2026-09-09 on a freshly built remote box: an agent asked to make one
change ran `git checkout -b <name>` with no start point and no preceding fetch.
`git reflog` recorded "Created from HEAD" and the checkout had no `FETCH_HEAD` at
all. The branch was current only because the clone was minutes old.

## Before a pull request opens

Opening a pull request is the last cheap moment for a second reader. In a
standard flow a person still reviews and a person still deploys after it; where
auto-merge is armed on open and merge deploys, nothing does.

A `PreToolUse` advisory speaks just before `gh pr create`, once per branch per
session. It reads only git's line and file counts against the merge base with
`origin/<default>`. It does not try to know whether a review happened, and it
never denies. It says what the research shows: who makes a good reader and what a useful
review does. What to do with the findings is said when they come back (see
"When a review comes back").
It is silent under 10 changed lines and when every changed file is prose.

Sources (every number was opened at its source on 2026-10-14):

| Claim | Source |
|---|---|
| About 70% of outages come from changes | Google SRE |
| Low review coverage: up to 2–5 more post-release bugs | McIntosh et al., MSR 2014 / EMSE 2016 |
| Same-model self-correction: 91.5% → 88.0% | Huang et al., ICLR 2024 |
| Filtered review 65% addressed (Uber) vs unfiltered 7–8% accepted (Mozilla, Ubisoft) | [Uber uReview](https://www.uber.com/blog/ureview/), [arXiv 2411.07091](https://arxiv.org/abs/2411.07091) |
| Combining review runs raised F1 up to 43.67% | [arXiv 2509.01494](https://arxiv.org/abs/2509.01494) |
| Every model did worse from diff to full context (one preprint) | [arXiv 2603.26130](https://arxiv.org/abs/2603.26130) |
| Reviewers report something even when the work is sound | [Claude Code best practices](https://code.claude.com/docs/en/best-practices) |

Not claimed because the evidence is missing: that persona prompts, specialist
reviewers, checklists, or a different model as reviewer catch more defects.

Observed 2026-10-12 in trellys-app: a session built every finding an adverse
reviewer returned, including a second check against a worry the reviewer itself
called an unconfirmed suspicion. In one week, 44 of 57 pull requests there
opened with no reviewer dispatched first.

## When a review comes back

A subagent's report reaches its parent as a turn of its own, and a
`UserPromptSubmit` hook runs before the session acts on it. When the report
reads as a review (a line-leading "Verdict", "smallest fix", "safe to merge",
"blocker / must-fix / should-fix", a Findings heading, "most serious first"),
Scry says what is known about the three kinds of finding a review returns:
a defect, settled by reproducing it; a quality finding (framework idiom,
project convention, sibling code), settled against the docs or neighbouring
code with no failing test needed; and a speculative risk, a new guard, retry or
check for a danger nobody has shown. It never tells the session what to do.

Detection, measured 2026-10-14 on 239 hand-backs: 20 of 25 defect-hunting
reviews caught, 2 false alarms among 205 other reports. Gemma 3 1B, Qwen3 4B
and Haiku each did worse than the wording.

The note was chosen by a blind test: 169 real findings from 26 real reviews,
each review shown unlabelled to a session that decided fix, verify, ask or
skip, three runs per condition, scored on the 125 findings where two
independent judges agreed. No note: 14% of should-not-build findings built,
83% of should-do findings acted on. This note: 3% and 78%.

| Claim | Source |
|---|---|
| About 75% of review-driven changes are maintainability, 25% functional | Beller et al., MSR 2014; Mäntylä & Lassenius, TSE 2009 |
| 35% of AI functional comments rejected, against 25% of maintainability comments | [arXiv 2607.03316](https://arxiv.org/abs/2607.03316) |

OWNER DECISION (2026-10-14): this hook reads the text of a subagent's report,
never a human message, and keeps nothing.

## When a session writes comments

A `PostToolUse` hook on `Edit`, `Write` and `MultiEdit` reads the lines an edit
added to a source file (`.ex .exs .py .sh .ts .tsx .js .rb .go .heex .yml
.yaml .toml`, tests included). When any of them is comment or doc text, Scry
says what keeps a comment in step with its code for a later reader: a reader,
human or model, relies on a comment as written, misleading comments cost far
more than missing ones, and almost all comment updates ship in the same revision as the code beside them. It
closes with what lasts: the why, an invariant, a unit, a failure mode, a
deliberate choice, and the measured fact behind it. An edit that adds no
comment text is silent, whatever comments the file already holds. It never
tells the session what to do.

Two kinds of comment add a sentence of their own, whether the edit added them
or they were already in the file (read from disk after an `Edit` or
`MultiEdit`; a `Write` adds all of its content). For the file, the lead counts
the comment blocks that carry one.

- **Another module or file.** A dotted module name, a `Module.function/arity`
  or a source file name other than this file's own modules or name, leaving
  out common library modules (`Enum`, `Repo`, `Ecto` and the like). Such a
  comment drifts when that other file changes. On the 300 labelled blocks of
  the profile sample, 39 of the 48 blocks the rule flagged (81%) were labelled
  as describing other modules; it finds 39 of the 105 such blocks.
- **A name, a date or a story.** A name bound to a decision ("per Matt",
  "(Name via the orchestrator, date)"), a date, or the story of how something
  was found ("born from the … incident", "measured … on date"). It reads later
  as a standing rule nobody can check from the code. Doctests, sigil dates and
  dates inside a path are skipped. On one week of transcripts this rule caught
  36 of 38 history comments with no false alarm among the other 47 code edits
  that added comment text; on 12,215 existing blocks it was not tuned on, 54 of
  56 sampled hits were history.

A third kind, a rule that only the comment holds ("must", "MUST", "NEVER",
"shall", "do not"), was measured and left out: of 40 sampled hits in the
engine, 28 stated a rule, but 21 of those were already checked by a test,
validation or compile-time check, so only 7 (18%) were a rule the comment alone
held.

It is silent for Markdown, for paths under `$TMPDIR`, `/tmp/` or a
`scratchpad/` directory, and after the first note for a file in a session.

| Claim | Source |
|---|---|
| Across 17 LLMs, plausible but wrong hint comments cut code-reasoning accuracy by about 33% | CodeCrash, NeurIPS 2025, [arXiv 2504.14119](https://arxiv.org/abs/2504.14119) |
| Missing documentation had no significant effect on LLM code understanding; wrong documentation hurt it | Macke & Doyle, NAACL Findings 2024, [arXiv 2404.03114](https://arxiv.org/abs/2404.03114) |
| 90–97% of comment changes land in the same revision as the code change they describe | Fluri, Würsch & Gall, WCRE 2007 |
| Claims about another module were false 15.7% of the time, against 4.7% for claims about the file's own code; comments that restate the code were stale about 3% of the time | Measured 2026-10-14 on 20,516 comment blocks in trellys-app and Scry |
| Comments should say why and what the code cannot, not restate it or carry its history | Style-guide consensus: Google C++ and Python guides, PEP 8, Go, Rust, Ousterhout |

Live test, 2026-10-15: agents were given a small real task (document the main
function) on copies of six real engine files, 3 runs each with the hook on and
3 with it off. With the hook, 4 of 18 runs also removed names and dates from
comments already in the file and kept the reasons; with it off, 0 of 18. No run
changed code. An earlier wording made agents replace a precise, current reference
to another module with a vague one in 3 of 3 runs on one file; the sentence about a
reference that still matches brought that to 1 of 3.

## When a session writes guidance

A `PostToolUse` hook on `Edit`, `Write` and `MultiEdit` reads the lines an edit
added to an instruction file: `AGENTS.md`, `CLAUDE.md` or `CLAUDE.local.md`
anywhere, in a repo or not (`~/AGENTS.md` counts), and any `.md` under a
`.claude/` directory. Every other path exits on the raw payload. When an added
line opens with a person's name and a date, Scry quotes the line and its line
number and says how it reads later: as one person's note from one day, which a
later session weighs as dated and possibly drifted, not as standing guidance.
It never tells the session what to do.

The lines an edit added are those of the new text that were not lines of the
old text. For a `Write`, they are the lines not in the file's git `HEAD`
version when the file is tracked, otherwise all of the content, so rewriting a
file that already held a dated line is silent. Lines inside a fenced code block
are skipped.

A line matches when, after optional list, quote, dash or emphasis markers
(`-`, `*`, `>`, `_`, `—`, `–`, `**`), it opens with a capitalised name of one
to three words and then a date: `2026-10-18`, `Oct 18, 2026`,
`October 18, 2026` or `18 October 2026`. The shape is a sign-off: after the
date the line ends (an optional `.`, `:`, `)`, `]` or closing emphasis), or goes
on only after a colon or a dash. So `Matt, 2026-10-18.`,
`— Matt, Oct 18, 2026`, `> Claude 2026-10-18: keep it short`,
`Matt, 2026-10-18 — moved the pins check` and `- **Matt Mathis**, 2026-10-18`
speak; `2026-10-18: released 1.40` (no name), `See the 2026-10-18 note`, `The
Oct 18 build`, a date later in a sentence, and a capitalised word, date and
more words (`Verified 2026-10-18 against prod`, `Postgres 2026-10-18 migration
rules`) do not. Month and weekday names,
and a few heading words such as `Updated` or `Released`, are not read as names.

A file changed through a shell command reaches it through the shell-writes
hook below. Once per file per session, and the marker is written only when the
note is said. `SCRY_GUIDANCE_ATTRIBUTION=0` turns it off.

## When a shell command writes files

The write notes above, and the others bound to `Edit` or `Write` in
`hooks/hooks.json` (Elixir, scale, doc currency, pins, new Markdown), see only
the file tools. A `PreToolUse` hook on `Bash` takes a picture of the disk before
each command, and a hook on `PostToolUse` and `PostToolUseFailure` takes it
again after and compares. The command text is never read, so a heredoc, `sed
-i`, `cp`, `mv`, `git show … >` or a script writing a file it does not name all
count the same.

The picture: when the command's folder is in a git work tree, `HEAD` and the
`git --no-optional-locks status --porcelain -z --untracked-files=all` list
(ignored files left out) with each listed file's size and mtime; and, always,
the size, mtime and a copy of `~/AGENTS.md`, `~/CLAUDE.md` and
`~/.claude/CLAUDE.md`. Status never takes `index.lock`. A file changed when it
is new in the list or its size or mtime moved, when a commit made during the
command names it and the file's mtime is no earlier than the first picture
(commits between the two `HEAD`s dated no earlier than the first picture, so a
checkout or fast-forward to older work is not counted, and a commit of files
edited before the command, which does not touch them on disk, is not either),
or when one of the home files moved. Deleted files are left out.

For each changed file, at most 10, Scry builds the payload a file tool would
have sent and runs every `PostToolUse` hook whose matcher names `Edit` or
`Write`, read from `hooks/hooks.json`, with that hook's timeout and a 40-second
budget for the whole step. A file that did not exist before goes as a `Write`
with its content; any other as an `Edit` whose old text is the `HEAD` version
(or the saved copy of a home file) and whose new text is the file now, so the
comment and guidance notes read only the lines that were added. A file over
256 KB goes without its text: the hooks that read the file from disk still
speak, the ones that read added lines stay silent. The notes come back under
one line naming the files, which says they *changed while that command ran*:
the comparison cannot tell which process changed them. When no write hook
speaks, nothing is said. `SCRY_SHELL_WRITES=0` turns both halves off.

Known gaps:

- A change another session makes in the same folder while the command runs is
  counted as if the command made it.
- A background command that writes after it returns is not seen, nor is a
  write to another worktree, or outside the repo and the three home files.
- Shell calls run in parallel take overlapping pictures, so each may name the
  other's files.
- A tracked file already changed before the command is compared with `HEAD`,
  so lines an earlier edit added are read again.
- Codex is not covered: there was no Codex build here to check its hook
  payloads, and Codex writes files through `apply_patch`.

## Tuning

Every threshold is an environment variable. Defaults are set where the number
starts changing a decision.

| Variable | Default | Controls |
|---|---|---|
| `SCRY_FLEET_ACTIVE_MINUTES` | `15` | how recently a session must have written to count as live |
| `SCRY_TASK_STATE_DIR` | `$TMPDIR/claude-$(id -u)` | where task output files live, read after `/clear` to name the tasks a cleared session left without an exit; absent directory means silence. The cache-deadline monitor also looks in `/tmp/claude-$(id -u)` when this is unset |
| `SCRY_LOAD_PER_CORE_WARN` | `1.5` | load-per-core before "oversubscribed" |
| `SCRY_SWAP_USED_MB_WARN` | `2048` | swap in use before it's reported |
| `SCRY_DISK_FREE_GB_WARN` | `20` | free-space floor |
| `SCRY_DISK_USED_PCT_WARN` | `90` | used-percentage ceiling |
| `SCRY_SUBAGENT_STALE_DAYS` | `7` | drift in days before a subagent is told the tree is unreliable |
| `SCRY_SUBAGENT_STALE_COMMITS` | `50` | drift in commits before the same escalation |
| `SCRY_WORKTREE_REMINDER_MINUTES` | `45` | how long a session must run before the disposal reminder speaks |
| `SCRY_WORKTREE_DISK_MB_WARN` | `2048` | worktree disk at which an unmerged pile is worth saying out loud |
| `SCRY_WORKTREE_DU_BUDGET` | `4` | seconds of `du` allowed before the size is reported as a floor |
| `SCRY_SCRATCH_MD_LIST_MAX` | `5` | scratch `.md` files named in the disposal note before the rest become a count |
| `SCRY_BRANCH_POINT_FETCH_HOURS` | `2` | age of the last fetch past which a branch-point count is reported as dated |
| `SCRY_CACHE_HANDOFF_LEAD_SECONDS` | `120` | how long before the prompt cache expires the monitor speaks (keep-alive or summary request) |
| `SCRY_CACHE_HANDOFF_POLL_SECONDS` | `15` | how often the monitor re-reads the deadline (no model calls) |
| `SCRY_CACHE_HANDOFF` | `1` | `0` disables the cache-deadline monitor entirely |
| `SCRY_KEEPALIVE_MAX` | `8` | keep-alives per user message while this session's work is running; `0` turns keep-alives off |
| `SCRY_KEEPALIVE_FRESH_SECS` | `3600` | how recently a subagent must have written to be listed in the keep-alive roster (one TTL); workflows and background commands are listed until they record a finish, however quiet. Also how long everything listed must have been silent for the keep-alive to say the work has gone silent |
| `SCRY_KEEPALIVE_SILENT_MAX` | `3` | keep-alives in a row for work where nothing listed has written for a whole TTL; the next one is the summary request instead. `0` never keeps a session warm for work that silent |
| `SCRY_PINS` | `1` | `0` disables the pinned-version check everywhere: startup line, edit note, status-line segment, background lookups |
| `SCRY_PINS_COOLDOWN_DAYS` | `5` | days a release must have been published before a pin counts as behind it. `0` counts a release at once. Advisories are exempt |
| `SCRY_PINS_REMIND_DAYS` | `1` | days before the startup line is said again for an advisory set already said. `0` says it at every startup |
| `SCRY_PINS_BEHIND_MINORS` | `1` | minor versions behind the latest release before a pin is reported; a major behind is always reported. `0` reports any newer release |
| `SCRY_PINS_BEHIND` | `1` | `0` reports advisories only and looks up no latest releases |
| `SCRY_PINS_LIST_MAX` | `3` | pins named in the edit note before the rest become a count (the startup line never names more than three) |
| `SCRY_PINS_REFRESH_HOURS` | `24` | age at which a cached answer is asked again, in the background |
| `SCRY_PINS_STALE_DAYS` | `7` | age past which pin data is labelled as old in `--full`, and past which failing lookups show as `pins ?` in the status line and NOT CHECKED in `--full` |
| `SCRY_PINS_REFRESH_BUDGET` | `45` | seconds of wall-clock the background refresh may spend; no session waits on it |
| `SCRY_PINS_CACHE_DIR` | `${XDG_CACHE_HOME:-~/.cache}/scry` | where the pin cache lives |
| `SCRY_PINS_OSV_URL`, `_HEX_URL`, `_NPM_URL`, `_PYPI_URL`, `_GITHUB_URL` | the public services | base URL of each source, for a mirror |
| `SCRY_GUIDANCE_ATTRIBUTION` | `1` | `0` stops the note when an edit adds a line opening with a name and a date to `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md` or a `.md` under `.claude/` |
| `SCRY_SHELL_WRITES` | `1` | `0` stops the before-and-after picture around each Bash command, and with it the write notes for files a shell command changed |
| `SCRY_STATUSLINE_CHECK` | `1` | `0` (in the environment or in settings.json `env`) stops the session-start note that Scry's status bar is not set up |

Raising a threshold buys silence. Lowering one buys warning. Neither changes
what is measured.

## Design notes

**Subagents count.** A subagent is not a session, but it *is* a concurrent
writer, and it does not necessarily work where its parent lives — a session in
a repo root routinely dispatches one into a worktree. Counting only top-level
transcripts therefore reports an actively-edited directory as empty. Claude
subagents are attributed using recent cwd metadata from their transcript tail;
Codex subagents are attributed using their first `session_meta` record. The
useful question is *has another writer been active here*, not merely how many
top-level sessions exist.

**Titles, never content.** `fleet.sh` reports what the last session in a
directory was *called*, never what it said or concluded. A title reads as a
label. A summary of a session you cannot see reads as **current** when it may
have been reversed an hour later in another session you also cannot see, and
acting on stale conclusions is worse than having no context. If you want the
detail, ask for it in-session, so it arrives as something you went and got
rather than something you were handed as fact.
The `/clear` case is not an exception to this, only a sharper question —
*which* session was left — answered by a record the ending session wrote,
not by content. Even the summary that session may have written is never
read: Scry says a summary was requested and where to look, from its own state
file. The first cut printed a handoff file's body, and that was content
flowing through Scry, so it went (council + Matt, 2026-09-10); since 1.31.0
there is no file at all (Matt, 2026-09-13).

**Merged means content, not commits.** Whether a branch is merged is decided by
ancestry *and* patch-id equivalence, so a squash or rebase merge — which
rewrites SHAs and hides from `git branch --merged` — is still recognised.
Deciding it on ancestry alone files finished work under "possibly abandoned",
and a review list full of false alarms is a list you learn to skim.

**Pick the event whose output actually lands.** A hook on the semantically
right event that no one ever reads is worse than no hook: it costs a process
per turn and buys the belief that the warning was given. Before wiring an
event, check what its exit-0 contract does with output — Claude Code documents
this per event, and a probe hook returning a unique token settles it in one
run. `SessionEnd` reads like the natural home for an end-of-session reminder
and discards everything it is handed; `Stop` delivers to the model but fires
at the end of every turn, so an end-of-session note there lands
mid-conversation (removed 2026-09-27). Leftovers are said at the next session
start instead. The same question is why
`main_drift_advisory.sh` is on `SubagentStart`: `SessionStart` context never
reaches a child agent, and Agent-tool `additionalContext` lands in the parent.

**Advisory, never blocking.** Blocking an edit is a policy opinion —
trunk-based, work-in-linked-worktrees — that belongs in the project enforcing
it, not in the tool everyone installs to *see* their repo. Scry reports facts
and stops. What to do about four sessions in one directory is yours to decide.

**Projects consequences, never actions.** When signals combine into something
worth a line, that line states the mechanical, verifiable consequence of the
current state — "commits land on the wrong branch without warning" — never a
recommendation like "consider a worktree." A prediction of what *will* happen
is a guess dressed as sight; a statement of what *is* exposed right now is a
fact, and a name is the difference between scrying and giving orders.

**Zero config where possible.** Deploy drift requires configuration because
Scry can't guess where your app lives. PR/CI status requires none — the git
remote is already there. The principle: if the information is derivable from
what's already in the repo, don't ask the user to configure it.

**Fails open.** Every hook exits 0 unconditionally. A session-start hook that
blocks or hangs is worse than one that skips. Optional features (`gh` for
PR/CI, `SCRY_HEALTH_URL` for deploy drift) degrade to silence, never to
errors.

## License

MIT
