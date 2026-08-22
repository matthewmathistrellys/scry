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

# Directories that never carry authoritative config. docs/ is excluded on
# purpose and not as noise-trimming: see rule 2 above.
SKIP_DIRS = {
    ".git", "node_modules", "deps", "_build", "dist", "build", "target",
    ".venv", "venv", "__pycache__", ".next", "coverage", ".worktrees",
    ".pytest_cache", "docs", ".terraform", "vendor", "site-packages",
}

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
    facts = {"fly_apps": [], "mix": 0, "python": False, "node": False,
             "docker": False, "ash": False, "phoenix": False,
             "frameworks": set(), "ci": False}
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
                        for line in f:
                            m = re.match(r"""^\s*app\s*=\s*['"]([^'"]+)['"]""", line)
                            if m:
                                facts["fly_apps"].append(m.group(1))
                                break
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
