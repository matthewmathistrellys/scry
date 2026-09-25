#!/usr/bin/env bash
# seed_data_advisory.sh — PostToolUse hook: answers "is what this session
# just found in the code the system's record, or a stand-in for it?"
#
# Seed files and test data look like the list a system holds. They are not:
# a seed usually runs once against an empty database and is rarely kept
# current as records are edited live, and fixtures and factories hold values
# built for tests. A code search that finds nothing proves nothing about rows
# in a database either. (Born 2026-09-24: a session searched the code, found a
# document kind missing from the seed file, and told the user it did not
# exist. It had been in the database all along.)
#
# Three moments, each an insight, never an instruction:
#   - a Read of a seed or test-data file;
#   - a search (Grep, Glob, or a grep/rg/ag/find/git grep through Bash) whose
#     results include one — named, once per file per session;
#   - a Grep, or a Bash search, that finds nothing in a repo with a database
#     (tracked migrations, alembic.ini, schema.prisma, schema.rb) — once per
#     repo per session.
#
# Advisory only: no blocking, no enforcement, just context injection.
set -uo pipefail

payload="$(cat)"

SCRY_PAYLOAD="$payload" SCRY_STATE="${TMPDIR:-/tmp}/scry-seed-data-advisory" python3 - <<'PY'
import hashlib, json, os, re, shlex, subprocess, sys

try:
    d = json.loads(os.environ["SCRY_PAYLOAD"])
except (json.JSONDecodeError, KeyError):
    sys.exit(0)

tool = d.get("tool_name") or ""
inp = d.get("tool_input") or {}
resp = d.get("tool_response")
cwd = d.get("cwd") or os.getcwd()
session = d.get("session_id") or "nosession"

SEED_WORDS = {"seed", "seeds", "seeder", "seeders", "seeding"}
TEST_WORDS = {"fixture", "fixtures", "__fixtures__", "factory", "factories", "testdata"}


def kind(path):
    # A candidate must look like a path, not a line of matched code.
    if not path or " " in path or not ("/" in path or "." in path):
        return None
    parts = path.lower().split("/")
    # A migration that seeds rows is a change to the database, not a stand-in for it.
    if "migrations" in parts[:-1]:
        return None
    words = set(parts[:-1]) | set(re.split(r"[._-]", parts[-1]))
    if words & SEED_WORDS:
        return "seed"
    if words & TEST_WORDS or "test/support" in path.lower():
        return "test"
    return None


def once(key):
    state = os.environ["SCRY_STATE"]
    try:
        os.makedirs(state, exist_ok=True)
        marker = os.path.join(state, hashlib.sha1(f"{session}|{key}".encode()).hexdigest())
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def git_root():
    try:
        return subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=3).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def has_database(root):
    if not root:
        return False
    try:
        files = subprocess.run(["git", "-C", root, "ls-files"], capture_output=True,
                               text=True, timeout=5).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return False
    for f in files:
        if "/migrations/" in f or f.startswith("migrations/") or f.endswith(("alembic.ini", "schema.prisma", "schema.rb")):
            return True
    return False


def say(text):
    print(json.dumps({
        "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text},
        "suppressOutput": True,
    }))
    sys.exit(0)


SEED_LINE = ("Seed files usually run once against an empty database and are rarely kept current, "
             "so they are untrustworthy as a record of what the database holds now.")
TEST_LINE = ("Fixtures and factories hold values built for tests, "
             "so they are untrustworthy as a record of what the live system holds.")
EMPTY_LINE = ("Insight: no match in this repo's code. This repo keeps records in a database, "
              "so a value absent from the code may still exist there; a code search is "
              "untrustworthy as proof that something does not exist.")

# --- Read of a seed or test-data file -------------------------------------
if tool == "Read":
    fp = inp.get("file_path") or ""
    k = kind(fp)
    if k and once(f"file|{fp}"):
        say("Insight: this is " + ("a seed file. " + SEED_LINE if k == "seed" else "test data. " + TEST_LINE))
    sys.exit(0)

# --- Collect what a search returned ---------------------------------------
paths, empty = [], False
if tool in ("Grep", "Glob") and isinstance(resp, dict):
    paths = list(resp.get("filenames") or [])
    content = resp.get("content") or ""
    if resp.get("mode") in ("content", "count") and content:
        paths += [line.split(":", 1)[0] for line in content.splitlines() if ":" in line]
    empty = tool == "Grep" and not paths and not content.strip()
    # A search aimed at one file returns its lines without the file's name.
    if not empty and inp.get("path"):
        paths.append(inp["path"])
elif tool == "Bash":
    cmd = inp.get("command") or ""
    try:
        words = shlex.split(cmd)
    except ValueError:
        words = cmd.split()
    searches = {"grep", "rg", "ag", "egrep", "fgrep", "find", "fd"}
    if not any(os.path.basename(w) in searches for w in words) and "git grep" not in cmd:
        sys.exit(0)
    out, err = "", ""
    if isinstance(resp, dict):
        out = resp.get("stdout") or ""
        err = resp.get("stderr") or ""
    elif isinstance(resp, str):
        out = resp
    paths = [re.split(r"[:\s]", line.strip(), maxsplit=1)[0] for line in out.splitlines() if line.strip()]
    # Only a pure search can be read as "found nothing": a pipeline or chain may
    # have filtered or replaced its output, and a search that wrote an error
    # (a bad pattern, a missing path) failed rather than found nothing.
    empty = not out.strip() and not err.strip() and not re.search(r"[|;&]", cmd)
else:
    sys.exit(0)

hits = []
for p in dict.fromkeys(paths):
    k = kind(p)
    if k and once(f"file|{p}"):
        hits.append((p, k))
if hits:
    kinds = {k for _, k in hits}
    lines = [SEED_LINE] if kinds == {"seed"} else [TEST_LINE] if kinds == {"test"} else [SEED_LINE, TEST_LINE]
    names = ", ".join(p for p, _ in hits[:5]) + (f", and {len(hits) - 5} more" if len(hits) > 5 else "")
    say(f"Insight: this search reached seed or test data ({names}). " + " ".join(lines))

if empty:
    root = git_root()
    if root and has_database(root) and once(f"empty|{root}"):
        say(EMPTY_LINE)
PY
exit 0
