#!/usr/bin/env python3
"""
Stack overview — the infrastructure scanner behind architecture.sh.

Answers "what is this system actually built ON?" from operational config
alone, so a session never has to take an instruction file's word for it.

WHY THIS EXISTS (2026-08-21). A CLAUDE.md described the app's database as
Fly.io managed Postgres. It had been Neon since the 08-06 migration. An
agent read the doc, believed it, and reasoned about the wrong database.

The tempting fix -- grep the tree for provider names and report the
winner -- reproduces the bug exactly. Counted across trellys-app, `.flycast`
beat `neon.tech` 25 files to 11. Every Fly hit was the OCR *service*
address (`http://trellys-ocr.flycast`); most Neon hits were migration
plans in docs/ describing the move. Brand-name frequency is not evidence
of what anything uses.

So this scanner obeys two rules:

  1. ROLE-BOUND, NEVER NAME-COUNTED. A provider is only reported for a
     role when the variable that OWNS that role resolves to it. Postgres
     is whatever DATABASE_URL's host says it is -- a `.flycast` string
     somewhere else in the tree is a different service and is not a vote.
  2. CONFIG ONLY, NEVER PROSE. docs/ is excluded outright. Prose is the
     thing that was wrong; reading it back would launder the error.

It reads .env because that is where the role binding is actually
resolvable, and it emits NO SECRETS: a connection string is reduced to a
provider label and region before it ever reaches output. Passwords, keys
and endpoint ids are parsed past, never stored, never printed.

Usage:
    python3 scanners/stack.py [--root .]
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects  # noqa: E402  (path set above so hooks can run from anywhere)

# Shared prune list, plus docs/ -- excluded on purpose and not as
# noise-trimming: see rule 2 above. Prose is the thing that was wrong.
SKIP_DIRS = projects.PRUNE_DIRS | {"docs"}

# Host fingerprint -> provider. Matched against the HOST of a role variable,
# never against free text anywhere in the repo.
#
# Entries are tried IN ORDER and most-specific must come first, because a
# substring match is greedy: `internal` sitting above `host.docker.internal`
# made the docker entry dead code and labelled ANY host containing the
# substring -- `db.internal.mycorp.com` -- as Fly. A confidently wrong
# provider label is the exact failure this scanner was built to kill, so the
# two ambiguous fingerprints now match as SUFFIXES (leading ".") rather than
# as substrings. Found by Fable review, 2026-08-22.
DB_HOSTS = [
    ("neon.tech", "Neon"),
    ("supabase.co", "Supabase"),
    ("supabase.com", "Supabase"),
    ("rds.amazonaws.com", "AWS RDS"),
    ("cockroachlabs.cloud", "CockroachDB"),
    ("psdb.cloud", "PlanetScale"),
    ("planetscale", "PlanetScale"),
    ("ondigitalocean.com", "DigitalOcean"),
    ("timescale", "Timescale"),
    ("render.com", "Render"),
    ("railway", "Railway"),
    ("mongodb.net", "MongoDB Atlas"),
    ("upstash.io", "Upstash"),
    ("azure.com", "Azure"),
    ("host.docker.internal", "local (docker)"),   # before .internal
    (".flycast", "Fly.io (internal)"),             # suffix
    (".internal", "Fly.io (internal)"),            # suffix
    ("localhost", "local"),
    ("127.0.0.1", "local"),
]

# Env var NAMES that bind a provider to a role. Value never read.
SERVICE_VARS = [
    (r"^ANTHROPIC_", "Anthropic"),
    (r"^OPENAI_", "OpenAI"),
    (r"^GEMINI_|^GOOGLE_AI", "Google AI"),
    (r"^VOYAGE_", "Voyage"),
    (r"^HATCHET_", "Hatchet"),
    (r"^LOGFIRE_", "Logfire"),
    (r"^SENTRY_", "Sentry"),
    (r"^HONEYBADGER_", "Honeybadger"),
    (r"^ZEP_", "Zep"),
    (r"^STYTCH_", "Stytch"),
    (r"^OUTSETA_", "Outseta"),
    (r"^SUPERTOKENS_", "SuperTokens"),
    (r"^CLERK_", "Clerk"),
    (r"^AUTH0_", "Auth0"),
    (r"^STRIPE_", "Stripe"),
    (r"^TELNYX_", "Telnyx"),
    (r"^TWILIO_", "Twilio"),
    (r"^LETTERSTREAM_", "Letterstream"),
    (r"^RESEND_|^POSTMARK_|^SENDGRID_", "Email delivery"),
    (r"^MISSIVE_", "Missive"),
    (r"^FILEVINE_", "Filevine"),
    (r"^LEADDOCKET_", "LeadDocket"),
    (r"^CLICKUP_", "ClickUp"),
    (r"^SLACK_", "Slack"),
    (r"^AWS_", "AWS"),
    (r"^CLOUDFLARE_|^R2_", "Cloudflare"),
    (r"^REDIS_", "Redis"),
    (r"^ELASTIC_|^OPENSEARCH_", "Elasticsearch"),
]

# Hosts that bill by ACTIVITY rather than by the hour -- the providers where a
# compute suspends itself once connections drop to zero.
SCALE_TO_ZERO_PROVIDERS = {"Neon", "Supabase"}

# Libraries that hold a database connection open and poll continuously. Their
# presence is what makes a scale-to-zero setting inert: the compute only
# suspends after N seconds with ZERO connections, and a scheduler that wakes
# every minute never lets the count reach zero.
POLLING_WORKERS = [
    ("oban", "Oban"), ("quantum", "Quantum"), ("broadway", "Broadway"),
    ("celery", "Celery"), ("apscheduler", "APScheduler"),
]

# Transaction-mode pooler fingerprints. A pooler is the right default for app
# traffic and the wrong one for migrations: DDL locking and prepared
# statements do not survive transaction-mode multiplexing.
POOLER_FINGERPRINTS = ["-pooler.", "pooler.supabase.com"]
POOLER_PORTS = {"6543"}

# A role variable is one whose NAME declares it points at a database.
DB_VAR_RE = re.compile(r"^[A-Z0-9_]*(DATABASE_URL|DATABASE_DSN|DB_URL|DB_DSN|POSTGRES_URL)$")
ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")
REGION_RE = re.compile(r"\b((?:us|eu|ap|sa|ca|me|af)-[a-z]+-\d)\b")


def host_of(value: str) -> tuple[str, str]:
    """Extract the HOST from a connection string, discarding credentials.

    Deliberately parses past the userinfo segment rather than capturing
    it -- the password never enters a variable, so it cannot leak into
    output by a later formatting mistake.
    """
    value = value.strip().strip('"').strip("'")
    if not value:
        return "", ""
    # Strip scheme, then everything through the last '@' (userinfo).
    value = re.sub(r"^[a-zA-Z0-9+]+://", "", value)
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    # Host ends at path or query; the port is kept -- it carries meaning.
    hostport = re.split(r"[/?]", value, maxsplit=1)[0].lower()
    if ":" in hostport:
        host, _, port = hostport.rpartition(":")
        return host, port
    return hostport, ""


def provider_for(host: str) -> str:
    """First matching fingerprint wins, so DB_HOSTS order is load-bearing.

    A fingerprint starting with "." matches only as a suffix; anything else
    matches as a substring. Suffix mode exists because `.internal` and
    `.flycast` are ordinary words that appear inside unrelated hostnames.
    """
    for fingerprint, name in DB_HOSTS:
        if fingerprint.startswith("."):
            if host == fingerprint[1:] or host.endswith(fingerprint):
                return name
        elif fingerprint in host:
            return name
    return ""


def env_files(root: str) -> list[str]:
    """Root-level and one-level-down .env files. Not a recursive sweep."""
    found = []
    for base in [root] + [
        os.path.join(root, d)
        for d in ("apps", "services", "packages")
        if os.path.isdir(os.path.join(root, d))
    ]:
        try:
            entries = os.listdir(base)
        except OSError:
            continue
        for entry in sorted(entries):
            path = os.path.join(base, entry)
            if os.path.isfile(path) and entry.startswith(".env"):
                found.append(path)
            elif os.path.isdir(path) and base != root and entry not in SKIP_DIRS:
                for sub in sorted(os.listdir(path)):
                    if sub.startswith(".env") and os.path.isfile(os.path.join(path, sub)):
                        found.append(os.path.join(path, sub))
    return found


def read_env(paths: list[str]) -> tuple[dict, set]:
    """Return (db role -> (host, port), set of env var names).

    Credentials are discarded during parsing and never retained.
    """
    db_roles, names = {}, set()
    for path in paths:
        # .env.example holds placeholders, not the real binding.
        if path.endswith((".example", ".sample", ".template")):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            match = ENV_LINE_RE.match(line)
            if not match:
                continue
            name, value = match.group(1), match.group(2)
            names.add(name)
            if DB_VAR_RE.match(name):
                host, port = host_of(value)
                if host:
                    db_roles.setdefault(name, (host, port))
    return db_roles, names


def walk_config(root: str) -> dict:
    """Collect operational config markers. Never descends into SKIP_DIRS."""
    facts = {"fly_apps": [], "always_on_apps": [], "mix": 0, "python": False,
             "node": False, "docker": False, "ash": False, "phoenix": False,
             "frameworks": set(), "ci": False, "workers": set()}
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".venv")]
        # Depth guard: config lives near the top, not 8 levels down.
        if cur[len(root):].count(os.sep) > 3:
            dirs[:] = []
            continue
        for name in files:
            path = os.path.join(cur, name)
            if name == "fly.toml":
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        body = f.read()
                except OSError:
                    continue
                m = re.search(r"""^\s*app\s*=\s*['"]([^'"]+)['"]""", body, re.M)
                if not m:
                    continue
                app = m.group(1)
                facts["fly_apps"].append(app)
                # The machine lifecycle is what actually bills. A machine
                # pinned up keeps a connection pool alive, which keeps a
                # scale-to-zero database awake -- the cost follows the
                # machine, not the database setting.
                mmr = re.search(r"^\s*min_machines_running\s*=\s*(\d+)", body, re.M)
                asm = re.search(r"""^\s*auto_stop_machines\s*=\s*['"]?(\w+)""", body, re.M)
                if (mmr and int(mmr.group(1)) > 0) or (asm and asm.group(1) == "off"):
                    facts["always_on_apps"].append(app)
            elif name == "mix.lock":
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        lock = f.read().lower()
                    for dep, label in POLLING_WORKERS:
                        if f'"{dep}"' in lock:
                            facts["workers"].add(label)
                except OSError:
                    pass
            elif name == "mix.exs":
                facts["mix"] += 1
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        body = f.read()
                    if re.search(r"{:ash[,_ }]|{:ash\b", body):
                        facts["ash"] = True
                    if ":phoenix" in body:
                        facts["phoenix"] = True
                except OSError:
                    pass
            elif name in ("pyproject.toml", "requirements.txt", "setup.py"):
                facts["python"] = True
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        body = f.read().lower()
                    for lib in ("fastapi", "django", "flask", "litestar"):
                        if lib in body:
                            facts["frameworks"].add(lib.capitalize() if lib != "fastapi" else "FastAPI")
                    for dep, label in POLLING_WORKERS:
                        if dep in body:
                            facts["workers"].add(label)
                except OSError:
                    pass
            elif name == "package.json":
                facts["node"] = True
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        body = f.read().lower()
                    for lib, label in (("\"next\"", "Next.js"), ("\"astro\"", "Astro"),
                                       ("\"svelte\"", "Svelte"), ("\"vue\"", "Vue"),
                                       ("\"react\"", "React")):
                        if lib in body:
                            facts["frameworks"].add(label)
                except OSError:
                    pass
            elif name.startswith("docker-compose") or name == "Dockerfile":
                facts["docker"] = True
    if os.path.isdir(os.path.join(root, ".github", "workflows")):
        facts["ci"] = True
    return facts


def is_pooled(host: str, port: str) -> bool:
    return any(f in host for f in POOLER_FINGERPRINTS) or port in POOLER_PORTS


def cost_and_correctness_warnings(db_roles, env_names, facts) -> list[str]:
    """Conflicts between the database, the workload, and the machine lifecycle.

    Both checks below are genuinely thresholded -- they say nothing unless the
    conflicting combination is actually present -- so they cost nothing on the
    repos where they do not apply.
    """
    warnings = []
    providers = {provider_for(h) for h, _ in db_roles.values()}

    # 1. Scale-to-zero that cannot ever fire.
    #
    # A Neon/Supabase compute suspends only after N seconds with ZERO
    # connections. A scheduler that polls every minute never lets the count
    # reach zero, so the suspend timeout is inert for as long as the app is
    # up -- and a machine pinned with min_machines_running keeps it up
    # forever. The database setting looks like thrift while the machine
    # lifecycle quietly buys always-on Postgres. Reading the database's own
    # config here would produce a confidently wrong "this one is fine";
    # the machine is the thing that decides.
    scale_to_zero = providers & SCALE_TO_ZERO_PROVIDERS
    if scale_to_zero and facts["workers"] and facts["always_on_apps"]:
        apps = sorted(set(facts["always_on_apps"]))
        pinned = ", ".join(apps[:4]) + (f", +{len(apps) - 4}" if len(apps) > 4 else "")
        verb = "is" if len(apps) == 1 else "are"
        warnings.append(
            f"COST: {'/'.join(sorted(scale_to_zero))} scale-to-zero cannot fire here. "
            f"{', '.join(sorted(facts['workers']))} holds a connection and polls "
            f"continuously, and {pinned} {verb} pinned up (min_machines_running > 0 or "
            "auto_stop_machines off) — so the compute never sees zero connections "
            "and never suspends. This is always-on Postgres whether or not that was "
            "the intent. The fix is the machine lifecycle, not the database timeout."
        )

    # 2. Migrations over a transaction pooler.
    #
    # A pooler is the right default for application traffic and the wrong one
    # for DDL: transaction-mode multiplexing does not preserve the advisory
    # locks and prepared statements migrations rely on.
    pooled = [v for v, (h, port) in db_roles.items() if is_pooled(h, port)]
    if pooled and not any(n.startswith("DIRECT_") or "DIRECT_DATABASE" in n
                          for n in env_names):
        warnings.append(
            f"MIGRATIONS: {', '.join(sorted(pooled))} points at a transaction pooler and "
            "no DIRECT_* URL is set. Application traffic belongs on the pooler; DDL does "
            "not — advisory locks and prepared statements do not survive transaction-mode "
            "multiplexing, so migrations run over it can hang or half-apply. Point "
            "migrations at the direct (non-pooler) endpoint."
        )
    return warnings


def render(db_roles: dict, env_names: set, facts: dict) -> str:
    lines = []

    # DATA -- the role-bound line this scanner exists for.
    if db_roles:
        by_provider = {}
        for var, (host, port) in db_roles.items():
            provider = provider_for(host) or "unrecognized host"
            region = REGION_RE.search(host)
            if provider.startswith("local") and port and port != "5432":
                label = (f"tunnel via {host}:{port} — provider NOT determinable "
                         f"offline (likely `fly proxy`/ssh to a remote DB)")
            elif region and not provider.startswith("local"):
                label = f"{provider} ({region.group(1)})"
            else:
                label = provider
            by_provider.setdefault(label, []).append(var)
        parts = []
        for label, vars_ in sorted(by_provider.items()):
            count = f" x{len(vars_)}" if len(vars_) > 1 else ""
            parts.append(f"{label}{count} [{', '.join(sorted(vars_))}]")
        lines.append("- Data: " + "; ".join(parts))

    # HOSTING.
    if facts["fly_apps"]:
        apps = sorted(set(facts["fly_apps"]))
        shown = ", ".join(apps[:4])
        more = f", +{len(apps) - 4}" if len(apps) > 4 else ""
        lines.append(f"- Hosting: Fly.io, {len(apps)} app(s) ({shown}{more})")

    # RUNTIME.
    runtime = []
    if facts["mix"]:
        tag = "Elixir"
        if facts["ash"]:
            tag += "/Ash"
        if facts["phoenix"]:
            tag += "/Phoenix"
        runtime.append(f"{tag} ({facts['mix']} mix project(s))")
    if facts["python"]:
        runtime.append("Python")
    if facts["node"]:
        runtime.append("Node")
    if facts["frameworks"]:
        runtime.append(", ".join(sorted(facts["frameworks"])))
    if facts["docker"]:
        runtime.append("Docker")
    if runtime:
        lines.append("- Runtime: " + "; ".join(runtime))

    # SERVICES -- from env var NAMES. Wired, which is not the same as live.
    services = set()
    for name in env_names:
        for pattern, label in SERVICE_VARS:
            if re.search(pattern, name):
                services.add(label)
    if services:
        ordered = sorted(services)
        shown = ", ".join(ordered[:12])
        more = f", +{len(ordered) - 12}" if len(ordered) > 12 else ""
        lines.append(f"- Services wired: {shown}{more}")

    lines.extend(cost_and_correctness_warnings(db_roles, env_names, facts))

    if not lines:
        return ""

    header = ("Stack (SessionStart, read from live config — fly.toml, .env, manifests; "
              "docs deliberately not consulted):")
    footer = ("Database provider is bound to the variable that owns the role — this is what THIS "
              "MACHINE connects to, read from local .env; production's binding lives in the deploy "
              "platform's secrets and is not readable offline. Service list comes from env var "
              "NAMES — proof a service is WIRED, not that it is in use.")
    return "\n".join([header] + lines + [footer])


def main():
    parser = argparse.ArgumentParser(description="Stack overview")
    parser.add_argument("--root", default=".", help="Repo root to scan")
    args = parser.parse_args()
    root = os.path.abspath(args.root)

    db_roles, env_names = read_env(env_files(root))
    facts = walk_config(root)
    body = render(db_roles, env_names, facts)
    if not body:
        return

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": body,
        }
    }))


if __name__ == "__main__":
    main()
