#!/usr/bin/env python3
"""
Stale pins — the scanner behind pins.sh.

Answers "is a version this repo pins known-vulnerable, or far behind what is
released?" for the pins nothing else watches.

WHY THIS EXISTS (2026-10-09). Two cases in one week, same repo:

  * `config :tailwind, version: "4.1.12"` in an Elixir config file. That
    standalone binary cannot run on the current macOS, and nobody noticed for
    months: a version written in a config file is in no lockfile, so no
    `outdated` or `audit` command ever reads it.
  * A mix.lock carrying a batch of packages with published security
    advisories, found only when someone ran an audit by hand.

WHO HEARS WHAT (reworked 2026-10-09, before this shipped). The first cut
printed every advisory and every out-of-date package into the session at
every SessionStart source -- about 1,300 characters, several times a day,
most of it unchanged since the last time. Now:

  startup    (`--startup`; SessionStart, source `startup` only) is silent
             unless a RUNTIME dependency has a HIGH or CRITICAL advisory that
             this branch does not itself change against the default branch.
             Then it is one line, said once per repository per advisory set:
             what was said is recorded in the cache, and nothing is said
             again until an advisory id appears that was not in it, or
             SCRY_PINS_REMIND_DAYS pass.
  edit note  (`--edited`; PostToolUse on Edit/Write) speaks when the edited
             file is a dependency manifest or a pinned-tool config, about
             that file's findings only, once per session per file.
  status     statusline.sh reads the small summary file this scanner leaves
             in the cache (`pins 4!`). That is the human's view.
  full       (`--full`) prints everything, for whoever asks.
  refresh    (`--refresh`; the detached child) is the ONLY mode that touches
             the network. It asks the registries inside a wall-clock budget
             and rewrites the cache. Startup starts it detached when the
             cache lacks answers or they are over a day old, and never waits.

So a session never waits on the network, an offline machine costs one failed
background process, and the first session in a repo nobody has checked says
nothing -- the second one has the answer.

RUNTIME OR DEV. A dev/test-only dependency never triggers the startup line.
mix: `only:` naming just :dev/:test in mix.exs, carried through the lock's
own dependency lists, so a package reached only from dev deps is dev. npm:
the lockfile's `"dev": true`. Python: a requirements file named for dev,
test, lint, ci or docs. Anything that cannot be told is runtime.

COOLDOWN. A release only counts as the "latest" a pin is behind once it has
been published for SCRY_PINS_COOLDOWN_DAYS (5): a release hours old is the
one most likely to be yanked, or to be the compromised one. The registries'
publish dates are cached with the versions and the cooldown is applied when
reporting. Advisories are exempt -- a fix is named however new it is.

The cache is keyed by PACKAGE, not by repo: `plug 1.18.1 has these
advisories` is the same fact in every worktree, and a repo with twenty
worktrees would otherwise spend twenty refreshes (and hex.pm's 100
requests/minute) learning it twenty times. The lockfiles are re-read on every
report, so a bumped pin stops being reported the moment the file changes,
with no network involved.

WHAT LEAVES THE MACHINE: package names and versions, to OSV; package names,
to the registry that publishes them. Nothing else, and no credentials -- no
token is read from the environment. Packages that do not come from the public
registry (a private Hex repo, a git or file dependency, a non-registry
`resolved` URL) are skipped, so their names are never sent anywhere.

SOURCES. OSV's querybatch covers Hex, npm and PyPI advisories in one request.
Latest releases and their publish dates come from hex.pm, the npm registry,
PyPI and GitHub releases. None of it needs the project's dependencies fetched
or compiled, which is why `mix hex.outdated` / `mix deps.audit` are not used.

"Behind" is only computed for DIRECTLY DECLARED packages (mix.exs deps, the
lockfile root's dependencies, requirement lines). A transitive package behind
its latest release is not something this repo chose, and looking them all up
is what would exhaust the registry's rate limit. Advisories are checked for
every locked version, transitive included.

Usage:
    python3 scanners/pins.py --root . --full       # everything, plain text
    python3 scanners/pins.py --root . --refresh    # ask the registries now
    python3 scanners/pins.py --root . --startup    # the SessionStart line
    python3 scanners/pins.py --edited < payload    # the PostToolUse note
"""

import calendar
import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects  # noqa: E402  (path set above so hooks can run from anywhere)

USER_AGENT = "scry-pins/1 (+https://github.com/matthewmathistrellys/scry)"
CACHE_SCHEMA = 2
MAX_DEPTH = 4
REQUEST_TIMEOUT = 6
# An npm packument carries every version ever published; a few are megabytes.
PACKUMENT_TIMEOUT = 20
# Releases kept per package, newest first: enough to step back past a burst
# of releases inside the cooldown.
RELEASES_KEPT = 8
URGENT = 3  # SEVERITY_RANK at which a runtime advisory is said at startup
# A cache entry no repo has asked about for this long is dropped.
PRUNE_SECONDS = 30 * 86400
# After an attempt for this repo, wait this long before starting another --
# an offline machine otherwise spawns a doomed process at every session start.
RETRY_SECONDS = 600

# Elixir packages that download a standalone binary at the version written in
# config. `advisory` is the OSV package the binary is built from; `latest` is
# where its releases are published.
CONFIG_PINS = {
    "tailwind": {"advisory": ("npm", "tailwindcss"),
                 "latest": "gh|tailwindlabs/tailwindcss"},
    "esbuild": {"advisory": ("npm", "esbuild"), "latest": "npm|esbuild"},
}
# When GitHub's unauthenticated API will not answer (60 requests/hour per
# address), the same release is on npm under this name.
GITHUB_NPM_FALLBACK = {"tailwindlabs/tailwindcss": "tailwindcss"}

SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1}
SEVERITY_NAME = {4: "critical", 3: "high", 2: "moderate", 1: "low", 0: "unrated"}


# ── settings ──────────────────────────────────────────────────────────────

def env_int(name, default):
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def env_float(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def settings():
    return {
        "behind": os.environ.get("SCRY_PINS_BEHIND", "1") != "0",
        "minors": max(0, env_int("SCRY_PINS_BEHIND_MINORS", 1)),
        "list_max": max(1, env_int("SCRY_PINS_LIST_MAX", 3)),
        "cooldown_secs": max(0.0, env_float("SCRY_PINS_COOLDOWN_DAYS", 5)) * 86400,
        "remind_secs": max(0.0, env_float("SCRY_PINS_REMIND_DAYS", 7)) * 86400,
        "refresh_secs": max(0.0, env_float("SCRY_PINS_REFRESH_HOURS", 24)) * 3600,
        "stale_secs": max(0.0, env_float("SCRY_PINS_STALE_DAYS", 7)) * 86400,
        "budget": max(1.0, env_float("SCRY_PINS_REFRESH_BUDGET", 45)),
        "osv": os.environ.get("SCRY_PINS_OSV_URL") or "https://api.osv.dev",
        "hex": os.environ.get("SCRY_PINS_HEX_URL") or "https://hex.pm",
        "npm": os.environ.get("SCRY_PINS_NPM_URL") or "https://registry.npmjs.org",
        "pypi": os.environ.get("SCRY_PINS_PYPI_URL") or "https://pypi.org",
        "github": os.environ.get("SCRY_PINS_GITHUB_URL") or "https://api.github.com",
    }


def cache_dir():
    explicit = os.environ.get("SCRY_PINS_CACHE_DIR")
    if explicit:
        return explicit
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    return os.path.join(base, "scry")


# ── versions ──────────────────────────────────────────────────────────────

_VERSION_RE = re.compile(r"^[vV=]?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$")


def parse_version(text):
    """(major, minor, patch, is_release) or None. Pre-releases sort below
    the release they lead up to, and that is all the precision needed here."""
    match = _VERSION_RE.match((text or "").strip())
    if not match:
        return None
    rest = match.group(4) or ""
    # ".4" (a fourth component), "+build" and ".post1" are still releases.
    pre = bool(re.match(r"^(-|\.?(a|b|rc|dev|alpha|beta|pre)\d*)", rest, re.I))
    return (int(match.group(1)), int(match.group(2) or 0),
            int(match.group(3) or 0), 0 if pre else 1)


def behind(pinned, latest, min_minors):
    """("major", n) / ("minor", n) when `pinned` trails `latest` by at least
    the threshold, else None. A major is always enough."""
    have, want = parse_version(pinned), parse_version(latest)
    if not have or not want or want <= have:
        return None
    if want[0] > have[0]:
        return ("major", want[0] - have[0])
    minors = want[1] - have[1]
    if minors >= min_minors:
        return ("minor", minors)
    return None


# ── reading pins from disk ────────────────────────────────────────────────
#
# Every reader takes TEXT, not a path, so the same code reads a file on disk
# and the default branch's copy of it (`git show`), which is how "this branch
# already changes that pin" is told.

def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def find_files(root):
    """One pruned walk for every marker, rather than one walk per marker."""
    found = {"mix.lock": [], "mix.exs": [], "package-lock.json": [],
             "requirements": []}
    root = os.path.abspath(root)
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in projects.prune(dirs) if not d.startswith(".venv")]
        if cur[len(root):].count(os.sep) > MAX_DEPTH:
            dirs[:] = []
            continue
        for name in sorted(files):
            if name in ("mix.lock", "mix.exs", "package-lock.json"):
                found[name].append(os.path.join(cur, name))
            elif fnmatch.fnmatch(name, "requirements*.txt"):
                found["requirements"].append(os.path.join(cur, name))
    return found


# "name": {:hex, :pkg, "1.2.3", "<checksum>", [managers], [deps], "<repo>", ...}
_MIX_LOCK_RE = re.compile(
    r'^\s*"[^"]+":\s*\{:hex,\s*:"?([A-Za-z0-9_]+)"?,\s*"([^"]+)",.*\],\s*"([^"]+)"'
    r'(?:,\s*"[0-9a-fA-F]+")?\s*\},?\s*$')
_MIX_LOCK_KEY_RE = re.compile(r'^\s*"([^"]+)":\s*\{')
# One entry of a lock line's own dependency list: {:mime, "~> 2.0", [hex: ...]}
_MIX_LOCK_DEP_RE = re.compile(
    r'\{:"?([A-Za-z0-9_]+)"?,\s*(?:"[^"]*"|nil),\s*\[(hex:[^\]]*)\]')
_MIX_DEP_RE = re.compile(r"\{\s*:([a-z][a-z0-9_]*)\s*,")
_MIX_ONLY_RE = re.compile(r"\bonly:\s*(\[[^\]]*\]|:[a-z_]+)")
DEV_ENVS = {"dev", "test"}


def _strip_exs_comments(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def _tuple_at(body, start):
    """The `{...}` that opens at `start`, brackets balanced, strings skipped."""
    depth, i, in_string = 0, start, False
    end = min(len(body), start + 2000)
    while i < end:
        ch = body[i]
        if in_string:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
            if depth == 0:
                return body[start:i + 1]
        i += 1
    return body[start:end]


def mix_exs_deps(text):
    """{app: "dev" | "runtime"} for every `{:app, ...}` in a mix.exs.

    Dev is `only:` naming nothing but :dev and :test. `runtime: false` is not
    dev: it says the app is not started, and it still ships in a release."""
    deps = {}
    body = _strip_exs_comments(text)
    for match in _MIX_DEP_RE.finditer(body):
        only = _MIX_ONLY_RE.search(_tuple_at(body, match.start()))
        envs = set(re.findall(r":([a-z_]+)", only.group(1))) if only else set()
        scope = "dev" if envs and envs <= DEV_ENVS else "runtime"
        if deps.get(match.group(1)) != "runtime":
            deps[match.group(1)] = scope
    return deps


def mix_declared(lock_dir):
    """{app: scope} declared in the mix.exs beside a lock, plus an umbrella's
    apps/*/mix.exs, which share the one lock above them. Runtime anywhere is
    runtime. None when no mix.exs could be read."""
    paths = [os.path.join(lock_dir, "mix.exs")]
    apps = os.path.join(lock_dir, "apps")
    try:
        paths += [os.path.join(apps, d, "mix.exs") for d in sorted(os.listdir(apps))]
    except OSError:
        pass
    declared, read_any = {}, False
    for path in paths:
        body = read_text(path)
        if not body:
            continue
        read_any = True
        for app, scope in mix_exs_deps(body).items():
            if declared.get(app) != "runtime":
                declared[app] = scope
    return declared if read_any else None


def mix_lock_entries(text):
    """Every lock line: its app, what it depends on, and -- only when it is a
    package from the public registry -- its name and version."""
    for line in text.splitlines():
        key = _MIX_LOCK_KEY_RE.match(line)
        if not key:
            continue
        match = _MIX_LOCK_RE.match(line)
        # Only the public registry: a private repo's package names stay here.
        public = bool(match) and match.group(3) == "hexpm"
        # An optional dependency is only present when something else asks
        # for it outright, and that something has its own line and edge.
        deps = [app for app, opts in _MIX_LOCK_DEP_RE.findall(line)
                if not re.search(r"\boptional:\s*true\b", opts)]
        yield {"app": key.group(1), "deps": deps,
               "name": match.group(1) if public else None,
               "version": match.group(2) if public else None}


def _reachable(graph, roots):
    seen, todo = set(), list(roots)
    while todo:
        app = todo.pop()
        if app in seen:
            continue
        seen.add(app)
        todo.extend(graph.get(app, ()))
    return seen


def mix_lock_pins(text, src, declared):
    entries = list(mix_lock_entries(text))
    graph = {e["app"]: e["deps"] for e in entries}
    runtime = dev = set()
    if declared is not None:
        runtime = _reachable(graph, [a for a, s in declared.items() if s == "runtime"])
        dev = _reachable(graph, [a for a, s in declared.items() if s == "dev"])
    for entry in entries:
        if not entry["name"]:
            continue
        app, name = entry["app"], entry["name"]
        direct = declared is None or app in declared or name in declared
        # Dev only when a dev dependency reaches it and no runtime one does.
        # A line nothing declared reaches cannot be told, so it is runtime.
        scope = "dev" if app in dev and app not in runtime else "runtime"
        yield {"eco": "Hex", "name": name, "version": entry["version"], "src": src,
               "direct": direct, "scope": scope,
               "latest": "Hex|" + name if direct else None}


_EXS_BLOCK_RE = re.compile(
    r"^[ \t]*config\s+:(tailwind|esbuild)\s*,(.*?)(?=^[ \t]*(?:config|import_config|if|import)\b|\Z)",
    re.M | re.S)
_EXS_VERSION_RE = re.compile(r"""(?:\bversion:|:version\s*,)\s*["']([^"']+)["']""")


def elixir_config_pins(text, src):
    body = _strip_exs_comments(text)
    for match in _EXS_BLOCK_RE.finditer(body):
        version = _EXS_VERSION_RE.search(match.group(2))
        if not version or not parse_version(version.group(1)):
            continue
        spec = CONFIG_PINS[match.group(1)]
        eco, package = spec["advisory"]
        # A build tool, but its output ships: not provably dev, so runtime.
        yield {"eco": eco, "name": package, "version": version.group(1),
               "src": src, "direct": True, "scope": "runtime",
               "latest": spec["latest"], "config": match.group(1)}


def elixir_config_files(mix_exs):
    config_dir = os.path.join(os.path.dirname(mix_exs), "config")
    try:
        return [os.path.join(config_dir, n) for n in sorted(os.listdir(config_dir))
                if n.endswith(".exs")]
    except OSError:
        return []


def npm_lock_pins(text, src):
    try:
        lock = json.loads(text or "{}")
    except ValueError:
        return
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        return  # lockfileVersion 1 has no `packages` map; not read.
    declared = set()
    for key, entry in packages.items():
        if "node_modules/" in key or not isinstance(entry, dict):
            continue
        for field in ("dependencies", "devDependencies", "optionalDependencies"):
            declared.update((entry.get(field) or {}).keys())
    for key, entry in packages.items():
        if "node_modules/" not in key or not isinstance(entry, dict):
            continue
        version = entry.get("version")
        resolved = entry.get("resolved") or ""
        if (not version or entry.get("link")
                or not resolved.startswith("https://registry.npmjs.org/")):
            continue
        # An aliased install keeps the real package name in `name`.
        name = entry.get("name") or key.rsplit("node_modules/", 1)[1]
        direct = key == "node_modules/" + name and name in declared
        # npm marks a package reached only through devDependencies itself.
        scope = "dev" if entry.get("dev") is True else "runtime"
        yield {"eco": "npm", "name": name, "version": version, "src": src,
               "direct": direct, "scope": scope,
               "latest": "npm|" + name if direct else None}


_REQ_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)\s*$")
_REQ_DEV_RE = re.compile(
    r"[-_.](dev|develop|development|test|tests|testing|lint|ci|docs?)[-_.]", re.I)


def requirements_pins(text, src):
    """Exact `name==version` lines only. A range is not a pin, and option
    lines (`--index-url ...`, which can carry credentials) are never parsed.
    The file's name is the only thing that says dev: requirements-dev.txt."""
    scope = "dev" if _REQ_DEV_RE.search(os.path.basename(src)) else "runtime"
    for line in text.splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        match = _REQ_RE.match(line)
        if not match:
            continue
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        yield {"eco": "PyPI", "name": name, "version": match.group(2),
               "src": src, "direct": True, "scope": scope, "latest": "PyPI|" + name}


def pins_in(text, src):
    """The pins one file's text holds, by what kind of file `src` names.
    Used for the default branch's copy, where only the keys matter."""
    name = os.path.basename(src)
    if name == "mix.lock":
        return mix_lock_pins(text, src, None)
    if name == "package-lock.json":
        return npm_lock_pins(text, src)
    if fnmatch.fnmatch(name, "requirements*.txt"):
        return requirements_pins(text, src)
    if name.endswith(".exs"):
        return elixir_config_pins(text, src)
    return ()


def collect(root):
    """Every pin under `root`, one record per (ecosystem, name, version)."""
    files = find_files(root)
    merged = {}
    rel = lambda path: os.path.relpath(path, root)
    def add(pins):
        for pin in pins:
            key = adv_key(pin)
            seen = merged.get(key)
            if not seen:
                pin["srcs"] = [pin.pop("src")]
                merged[key] = pin
                continue
            src = pin["src"]
            if src not in seen["srcs"]:
                seen["srcs"].append(src)
            if pin["scope"] == "runtime":
                seen["scope"] = "runtime"  # runtime anywhere is runtime
            if pin["direct"] and not seen["direct"]:
                seen["direct"], seen["latest"] = True, pin["latest"]
            if pin.get("config") and not seen.get("config"):
                # The config pin is the one nothing else tracks: it wins.
                seen["config"], seen["latest"] = pin["config"], pin["latest"]
                seen["srcs"].remove(src)
                seen["srcs"].insert(0, src)
    for path in files["mix.exs"]:
        for config in elixir_config_files(path):
            add(elixir_config_pins(read_text(config), rel(config)))
    for path in files["mix.lock"]:
        add(mix_lock_pins(read_text(path), rel(path),
                          mix_declared(os.path.dirname(path))))
    for path in files["package-lock.json"]:
        add(npm_lock_pins(read_text(path), rel(path)))
    for path in files["requirements"]:
        add(requirements_pins(read_text(path), rel(path)))
    return list(merged.values())


def adv_key(pin):
    return "%s|%s|%s" % (pin["eco"], pin["name"], pin["version"])


# ── cache ─────────────────────────────────────────────────────────────────

def path_tag(path):
    """A short stable name for a directory. statusline.sh computes the same
    one for the worktree it is drawn in, to find that worktree's summary."""
    return hashlib.sha1(os.path.realpath(path).encode()).hexdigest()[:12]


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cache_path():
    return os.path.join(cache_dir(), "pins.json")


def load_cache():
    try:
        with open(cache_path(), encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("schema") != CACHE_SCHEMA:
            raise ValueError
    except (OSError, ValueError, AttributeError):
        cache = {"schema": CACHE_SCHEMA}
    for section in ("adv", "vuln", "latest", "meta"):
        if not isinstance(cache.get(section), dict):
            cache[section] = {}
    return cache


def save_cache(cache, now):
    for section in ("adv", "vuln", "latest"):
        cache[section] = {k: v for k, v in cache[section].items()
                          if now - v.get("at", 0) < PRUNE_SECONDS}
    write_json(cache_path(), cache)


def needed(pins, cache, conf, now):
    """What a refresh would have to ask: (advisory keys, latest keys)."""
    cutoff = now - conf["refresh_secs"]
    adv = sorted({adv_key(p) for p in pins
                  if cache["adv"].get(adv_key(p), {}).get("at", 0) <= cutoff})
    latest = []
    if conf["behind"]:
        latest = sorted({p["latest"] for p in pins if p["latest"]
                         and cache["latest"].get(p["latest"], {}).get("at", 0) <= cutoff})
    return adv, latest


# ── refresh (the only code that touches the network) ──────────────────────

class RateLimited(Exception):
    pass


def http_json(url, body=None, timeout=REQUEST_TIMEOUT):
    """GET (or POST `body`) and decode JSON. Returns (data, headers)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace")), response.headers


def quote(name):
    return urllib.parse.quote(name, safe="@")


def parse_time(text):
    """Epoch seconds for a registry's ISO-8601 UTC timestamp, or None."""
    match = re.match(r"(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)", text or "")
    if not match:
        return None
    try:
        return calendar.timegm(time.strptime(
            match.group(1) + " " + match.group(2), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def releases(pairs, latest):
    """{"v": latest, "rel": [[version, published], ...]} from a registry's
    (version, timestamp) pairs: stable releases only, none above the one the
    registry calls latest, newest first, the newest few. The cooldown is
    applied when reporting, so it can be re-tuned without asking again."""
    top = parse_version(latest)
    kept = []
    for version, stamp in pairs:
        parsed = parse_version(version)
        if not parsed or not parsed[3] or (top and parsed > top):
            continue
        kept.append((parsed, version, parse_time(stamp) if isinstance(stamp, str) else None))
    kept.sort(reverse=True)
    if not latest and kept:
        latest = kept[0][1]
    if not latest:
        return None
    return {"v": latest, "rel": [[v, at] for _, v, at in kept[:RELEASES_KEPT]]}


def npm_releases(name, conf):
    """The packument has the publish dates and can be very large; when it
    will not come, the small `latest` document still names the version, with
    no date -- and a release with no date cannot be held back by the cooldown."""
    try:
        data, _ = http_json("%s/%s" % (conf["npm"], quote(name)),
                            timeout=PACKUMENT_TIMEOUT)
        times = data.get("time") or {}
        return releases(times.items(), (data.get("dist-tags") or {}).get("latest"))
    except urllib.error.HTTPError as error:
        if error.code in (404, 429):
            raise
    except Exception:  # timed out, cut short, or not the shape expected
        pass
    data, _ = http_json("%s/%s/latest" % (conf["npm"], quote(name)))
    return releases([], data.get("version"))


def fetch_latest(key, conf, hex_gate):
    """Recent stable releases for one latest-key (see `releases`), or None if
    the registry answered and does not know the package. Raises on a failed
    lookup."""
    kind, name = key.split("|", 1)
    try:
        if kind == "Hex":
            if hex_gate.is_set():
                raise RateLimited()
            data, headers = http_json("%s/api/packages/%s" % (conf["hex"], quote(name)))
            # hex.pm allows 100 unauthenticated requests a minute per address.
            # Stop short of the limit; what is left is asked next time.
            remaining = headers.get("x-ratelimit-remaining")
            if remaining is not None and remaining.isdigit() and int(remaining) <= 2:
                hex_gate.set()
            return releases(
                [(r.get("version"), r.get("inserted_at"))
                 for r in data.get("releases") or [] if isinstance(r, dict)],
                data.get("latest_stable_version"))
        if kind == "npm":
            return npm_releases(name, conf)
        if kind == "PyPI":
            data, _ = http_json("%s/pypi/%s/json" % (conf["pypi"], quote(name)))
            pairs = []
            for version, files in (data.get("releases") or {}).items():
                stamps = [f.get("upload_time_iso_8601") or f.get("upload_time")
                          for f in files or [] if isinstance(f, dict)
                          and not f.get("yanked")]
                stamps = [s for s in stamps if s]
                if stamps:
                    pairs.append((version, min(stamps)))
            return releases(pairs, (data.get("info") or {}).get("version"))
        if kind == "gh":
            try:
                data, _ = http_json("%s/repos/%s/releases?per_page=20"
                                    % (conf["github"], name))
                return releases(
                    [((r.get("tag_name") or "").lstrip("vV"), r.get("published_at"))
                     for r in data if isinstance(r, dict)
                     and not r.get("draft") and not r.get("prerelease")], None)
            except (urllib.error.URLError, OSError, ValueError, TypeError):
                fallback = GITHUB_NPM_FALLBACK.get(name)
                if not fallback:
                    raise
                return npm_releases(fallback, conf)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code == 429:
            if kind == "Hex":
                hex_gate.set()
            raise RateLimited()
        raise
    return None


def digest_vuln(record):
    """Keep only what the report prints: severity, aliases, fixed ranges."""
    severity = ((record.get("database_specific") or {}).get("severity") or "").upper()
    fixed = {}
    for affected in record.get("affected") or []:
        package = affected.get("package") or {}
        eco, name = package.get("ecosystem"), package.get("name")
        if not eco or not name:
            continue
        if eco == "PyPI":
            name = re.sub(r"[-_.]+", "-", name).lower()
        for rng in affected.get("ranges") or []:
            if rng.get("type") not in ("SEMVER", "ECOSYSTEM"):
                continue
            introduced = "0"
            for event in rng.get("events") or []:
                if "introduced" in event:
                    introduced = event["introduced"]
                elif "fixed" in event:
                    fixed.setdefault("%s|%s" % (eco, name), []).append(
                        [introduced, event["fixed"]])
    return {"sev": SEVERITY_RANK.get(severity, 0),
            "aliases": sorted(set(record.get("aliases") or [])),
            "fixed": fixed}


def refresh(root, conf):
    """Ask the registries for whatever the cache lacks. Returns an exit code."""
    os.makedirs(cache_dir(), exist_ok=True)
    lock = os.path.join(cache_dir(), "pins.lock")
    try:
        if time.time() - os.path.getmtime(lock) > conf["budget"] * 2 + 60:
            os.unlink(lock)  # left by a refresh that was killed
    except OSError:
        pass
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except OSError:
        return 0  # another refresh holds it
    try:
        return _refresh_locked(root, conf)
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass


def _refresh_locked(root, conf):
    started = time.time()
    deadline = started + conf["budget"]
    pins = collect(root)
    cache = load_cache()
    want_adv, want_latest = needed(pins, cache, conf, started)
    succeeded = failed = 0

    # 1. Advisories: one querybatch per 1000 (name, version) pairs.
    by_key = {adv_key(p): p for p in pins}
    for start in range(0, len(want_adv), 1000):
        chunk = want_adv[start:start + 1000]
        queries = [{"package": {"name": by_key[k]["name"], "ecosystem": by_key[k]["eco"]},
                    "version": by_key[k]["version"]} for k in chunk]
        try:
            data, _ = http_json(conf["osv"] + "/v1/querybatch", {"queries": queries})
            results = data.get("results") or []
            if len(results) != len(chunk):
                raise ValueError("querybatch answered %d of %d" % (len(results), len(chunk)))
        except (urllib.error.URLError, OSError, ValueError):
            failed += 1
            continue
        succeeded += 1
        now = time.time()
        for key, result in zip(chunk, results):
            vulns = (result or {}).get("vulns") or []
            cache["adv"][key] = {
                "at": now,
                "ids": sorted(v["id"] for v in vulns if v.get("id")),
                "mod": {v["id"]: v.get("modified", "") for v in vulns if v.get("id")},
            }

    # 2. Details (severity, fixed version) for advisories not yet held, or
    #    modified since they were.
    want_vulns = set()
    for pin in pins:
        entry = cache["adv"].get(adv_key(pin)) or {}
        for vid in entry.get("ids", []):
            held = cache["vuln"].get(vid)
            if not held or held.get("mod") != entry.get("mod", {}).get(vid, ""):
                want_vulns.add(vid)
            else:
                held["at"] = time.time()  # still in use: keep it from pruning

    def one_vuln(vid):
        if time.time() > deadline:
            return vid, None
        try:
            data, _ = http_json("%s/v1/vulns/%s" % (conf["osv"], quote(vid)))
            digest = digest_vuln(data)
        except (urllib.error.URLError, OSError, ValueError):
            return vid, None
        # A record from a database that publishes only a CVSS vector (most
        # Hex advisories) has no severity label; the GitHub advisory it names
        # as an alias does. Without this, a lockfile's worst offenders are
        # all "unrated" and cannot be ranked.
        if not digest["sev"] and time.time() < deadline:
            for alias in digest["aliases"]:
                if not alias.startswith("GHSA-"):
                    continue
                try:
                    data, _ = http_json("%s/v1/vulns/%s" % (conf["osv"], quote(alias)))
                    digest["sev"] = digest_vuln(data)["sev"]
                except (urllib.error.URLError, OSError, ValueError):
                    pass
                break
        return vid, digest

    hex_gate = threading.Event()

    def one_latest(key):
        if time.time() > deadline:
            return key, "skip", None
        try:
            return key, "ok", fetch_latest(key, conf, hex_gate)
        except RateLimited:
            return key, "skip", None
        except Exception:  # an answer in a shape nobody expected is a failure
            return key, "fail", None

    with ThreadPoolExecutor(max_workers=8) as pool:
        vuln_jobs = pool.map(one_vuln, sorted(want_vulns))
        latest_jobs = pool.map(one_latest, want_latest)
        mods = {}
        for pin in pins:
            mods.update((cache["adv"].get(adv_key(pin)) or {}).get("mod", {}))
        for vid, digest in vuln_jobs:
            if digest is None:
                failed += 1
                continue
            succeeded += 1
            digest.update(at=time.time(), mod=mods.get(vid, ""))
            cache["vuln"][vid] = digest
        for key, status, found in latest_jobs:
            if status == "ok":
                succeeded += 1
                entry = {"at": time.time(), "v": None, "rel": []}
                entry.update(found or {})
                cache["latest"][key] = entry
            elif status == "fail":
                failed += 1

    # Pruning runs on age, so anything this repo still pins is re-stamped
    # only when it was re-asked; entries are re-asked daily, well inside it.
    meta = cache["meta"]
    now = time.time()
    meta["last_attempt"] = now
    if succeeded:
        meta["last_ok"] = now
        meta.pop("fail_since", None)
    elif failed:
        meta.setdefault("fail_since", now)
    save_cache(cache, now)
    # The status line reads this, so it follows the refresh without a session.
    write_summary(root, analyse(pins, cache, conf, now), cache, conf, now)
    return 0


def attempt_marker(root):
    return os.path.join(cache_dir(), "attempt-" + path_tag(root))


def spawn_refresh(root):
    """Start `--refresh` detached and return at once.

    The child gets /dev/null for all three streams and its own session: a
    hook's caller waits for the hook's stdout to close, so a child that
    inherited it would make the session wait on the network after all.
    """
    marker = attempt_marker(root)
    try:
        if time.time() - os.path.getmtime(marker) < RETRY_SECONDS:
            return False
    except OSError:
        pass
    try:
        os.makedirs(cache_dir(), exist_ok=True)
        with open(marker, "w"):
            pass
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--root", root, "--refresh"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
            cwd="/")
        return True
    except OSError:
        return False


# ── findings ──────────────────────────────────────────────────────────────

def age_text(seconds):
    seconds = max(0, int(seconds))
    if seconds < 5400:
        return "%dm" % max(1, seconds // 60)
    if seconds < 2 * 86400:
        return "%dh" % (seconds // 3600)
    return "%d days" % (seconds // 86400)


def advisory_groups(pin, cache):
    """The distinct vulnerabilities behind a pin's advisory ids.

    One vulnerability is published by several databases under different ids
    (GHSA-…, EEF-CVE-…, PYSEC-…) that name each other as aliases; counted
    raw, one flaw reads as three.
    """
    ids = (cache["adv"].get(adv_key(pin)) or {}).get("ids", [])
    groups = []  # each: {"names": set, "sev": int, "fixed": [..]}
    for vid in ids:
        record = cache["vuln"].get(vid) or {}
        names = {vid} | set(record.get("aliases", []))
        fixed = fixed_for(pin, record)
        group = {"names": names, "sev": record.get("sev", 0),
                 "fixed": [fixed] if fixed else []}
        for other in [g for g in groups if g["names"] & names]:
            groups.remove(other)
            group["names"] |= other["names"]
            group["sev"] = max(group["sev"], other["sev"])
            group["fixed"] += other["fixed"]
        groups.append(group)
    return groups


def fixed_for(pin, record):
    """The release that clears this advisory for this pinned version."""
    have = parse_version(pin["version"])
    ranges = (record.get("fixed") or {}).get("%s|%s" % (pin["eco"], pin["name"]), [])
    if not have:
        return None
    inside, above = [], []
    for introduced, fixed in ranges:
        low, high = parse_version(introduced) or (0, 0, 0, 0), parse_version(fixed)
        if not high or high <= have:
            continue
        (inside if low <= have else above).append((high, fixed))
    chosen = inside or above
    return min(chosen)[1] if chosen else None


def group_id(group):
    names = sorted(group["names"])
    for prefix in ("CVE-", "GHSA-"):
        for name in names:
            if name.startswith(prefix):
                return name
    return names[0]


def display(pin):
    return "%s %s" % (pin.get("config") or pin["name"], pin["version"])


def effective_latest(entry, conf, now):
    """(the release a pin is measured against, the newest one held back).

    The first is the newest release published at least the cooldown ago; the
    second is [version, published] for a newer one still inside it, or None.
    A release whose publish date the registry did not give cannot be held."""
    rel = entry.get("rel") or []
    if not rel:
        return entry.get("v"), None
    cutoff = now - conf["cooldown_secs"]
    held = None
    for version, at in rel:
        if at is None or at <= cutoff:
            return version, held
        held = held or [version, at]
    return None, held


def analyse(pins, cache, conf, now):
    vulnerable, config_behind, lock_behind, held_back = [], [], [], []
    unknown = direct_known = answered = 0
    oldest = now
    for pin in pins:
        entry = cache["adv"].get(adv_key(pin))
        if entry is None:
            unknown += 1
        else:
            answered += 1
            oldest = min(oldest, entry.get("at", now))
            groups = advisory_groups(pin, cache)
            if groups:
                fixes = [f for g in groups for f in g["fixed"]]
                vulnerable.append({
                    "pin": pin, "count": len(groups),
                    "sev": max(g["sev"] for g in groups),
                    "id": group_id(max(groups, key=lambda g: g["sev"])),
                    "urgent_ids": sorted(group_id(g) for g in groups
                                         if g["sev"] >= URGENT),
                    "fixed": max(fixes, key=lambda f: parse_version(f) or (0,)) if fixes else None,
                })
        if not conf["behind"] or not pin["latest"]:
            continue
        latest = cache["latest"].get(pin["latest"])
        if latest is None:
            unknown += 1
            continue
        oldest = min(oldest, latest.get("at", now))
        if not latest.get("v"):
            continue
        direct_known += 0 if pin.get("config") else 1
        version, held = effective_latest(latest, conf, now)
        if held and behind(pin["version"], held[0], conf["minors"]):
            held_back.append({"pin": pin, "latest": held[0], "at": held[1]})
        gap = behind(pin["version"], version, conf["minors"]) if version else None
        if gap:
            row = {"pin": pin, "latest": version, "gap": gap}
            (config_behind if pin.get("config") else lock_behind).append(row)
    vulnerable.sort(key=lambda v: (-v["sev"], -v["count"], v["pin"]["name"]))
    order = lambda r: (r["gap"][0] != "major", -r["gap"][1], r["pin"]["name"])
    config_behind.sort(key=order)
    lock_behind.sort(key=order)
    held_back.sort(key=lambda r: r["pin"]["name"])
    return {"vulnerable": vulnerable, "config_behind": config_behind,
            "lock_behind": lock_behind, "held_back": held_back, "unknown": unknown,
            "answered": answered,
            "direct_known": direct_known, "oldest": oldest, "total": len(pins)}


def urgent_rows(found):
    """What the startup line and the status line count: a high or critical
    advisory on something that is not dev/test-only."""
    return [r for r in found["vulnerable"]
            if r["sev"] >= URGENT and r["pin"]["scope"] == "runtime"]


def not_checked(found, cache, conf, now):
    """Seconds the lookups have been failing, when that is long enough that
    this repo's answers are missing or old; else 0. Silence must not read as
    "checked and clean" when the check has not been able to run."""
    failing = cache["meta"].get("fail_since")
    if not failing or not conf["stale_secs"] or now - failing <= conf["stale_secs"]:
        return 0
    too_old = now - found["oldest"] > conf["stale_secs"]
    return now - failing if found["total"] and (found["unknown"] or too_old) else 0


def summary_path(root):
    return os.path.join(cache_dir(), "summary-%s.json" % path_tag(root))


def write_summary(root, found, cache, conf, now):
    """The few numbers statusline.sh shows, one small file per worktree, so
    the bar never walks the tree or opens the package cache."""
    try:
        write_json(summary_path(root), {
            "at": now, "root": root, "total": found["total"],
            "urgent": len(urgent_rows(found)),
            "advisories": len(found["vulnerable"]),
            "dark": bool(not_checked(found, cache, conf, now))})
    except OSError:
        pass


# ── git: what this branch itself changes ──────────────────────────────────

def git(root, *args):
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True,
                              text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def branch_base(root):
    """Where this branch left the default branch, or None if there is no
    default branch to measure against."""
    head = (git(root, "symbolic-ref", "--quiet", "--short",
                "refs/remotes/origin/HEAD") or "").strip()
    for ref in ([head] if head else []) + ["origin/main", "origin/master",
                                           "main", "master"]:
        if git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}") is None:
            continue
        return (git(root, "merge-base", "HEAD", ref) or "").strip() or ref
    return None


def unchanged_on_branch(root, rows):
    """The rows whose pin is the same on the default branch.

    A pin this branch adds or moves is work in progress on exactly that pin;
    saying so at startup would be telling the branch about itself. Compared
    with the working tree, so an uncommitted bump counts. Whatever cannot be
    compared (no default branch, a lockfile git does not track) is kept."""
    base = branch_base(root)
    if not base:
        return rows
    held = {}

    def at_base(src):
        if src not in held:
            text = git(root, "show", "%s:%s" % (base, src))
            if text is None and not (git(root, "ls-files", "--", src) or "").strip():
                held[src] = None  # untracked: there is no branch change to see
            else:
                held[src] = {adv_key(p) for p in pins_in(text or "", src)}
        return held[src]

    kept = []
    for row in rows:
        key = adv_key(row["pin"])
        if any(at_base(s) is None or key in at_base(s) for s in row["pin"]["srcs"]):
            kept.append(row)
    return kept


def repo_tag(root):
    """One name for a repository across all of its worktrees."""
    common = (git(root, "rev-parse", "--git-common-dir") or "").strip()
    return path_tag(os.path.join(root, common) if common else root)


# ── what is said ──────────────────────────────────────────────────────────

def pins_command(flag):
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "pins.sh")
    home = os.path.expanduser("~")
    if script.startswith(home + os.sep):
        script = "~" + script[len(home):]
    return "bash %s %s" % (script, flag)


def full_command():
    return pins_command("--full")


STARTUP_MAX_CHARS = 200


def startup_line(root, found, conf, now):
    """One line, or nothing. See the module docstring for when."""
    rows = urgent_rows(found)
    rows = unchanged_on_branch(root, rows) if rows else rows
    if not rows:
        return ""
    ids = {i for row in rows for i in row["urgent_ids"]}
    said_path = os.path.join(cache_dir(), "said-%s.json" % repo_tag(root))
    said = read_json(said_path)
    try:
        said_at = float(said.get("at") or 0)
    except (TypeError, ValueError):
        said_at = 0
    due = now - said_at >= conf["remind_secs"]
    already = set() if due else {i for i in said.get("ids") or [] if isinstance(i, str)}
    if not due and ids <= already:
        return ""
    try:
        # Kept as a union inside the window: a branch that fixes one advisory
        # and the default branch that still has it are not two new sets.
        write_json(said_path, {"at": now if due else said_at,
                               "ids": sorted(ids | already), "root": root})
    except OSError:
        pass

    names = []
    for row in rows:
        name = row["pin"].get("config") or row["pin"]["name"]
        if name not in names:
            names.append(name)
    noun = "dependency has" if len(rows) == 1 else "dependencies have"
    for shown in range(min(3, len(names)), -1, -1):
        listed_names = names[:shown]
        if len(names) > shown and shown:
            listed_names.append("+%d more" % (len(names) - shown))
        text = "Pinned versions: %d runtime %s a high or critical advisory%s. Full list: %s" % (
            len(rows), noun,
            " (%s)" % ", ".join(listed_names) if listed_names else "", full_command())
        if len(text) <= STARTUP_MAX_CHARS:
            break
    return text


def vulnerable_text(row, src=True):
    pin = row["pin"]
    text = display(pin)
    text += " (%s)" % pin["srcs"][0] if src else ""
    text += " [dev]" if pin["scope"] == "dev" and src else ""
    text += ": "
    text += ("%d advisories, worst " % row["count"]) if row["count"] > 1 else ""
    text += "%s %s" % (SEVERITY_NAME[row["sev"]], row["id"])
    if row["fixed"]:
        text += ", fixed in %s" % row["fixed"]
    return text


def behind_text(row, src=True):
    pin = row["pin"]
    text = "%s → %s" % (display(pin), row["latest"])
    if src:
        text += " (%s)" % pin["srcs"][0]
        text += " [dev]" if pin["scope"] == "dev" else ""
    return text


def listed(rows, render_row, limit):
    shown = "; ".join(render_row(r) for r in rows[:limit])
    return shown + ("; +%d more" % (len(rows) - limit) if len(rows) > limit else "")


def tally_text(rows):
    tally = {}
    for row in rows:
        tally[row["sev"]] = tally.get(row["sev"], 0) + 1
    return ", ".join("%d %s" % (tally[s], SEVERITY_NAME[s])
                     for s in sorted(tally, reverse=True))


def days_text(conf):
    return "%g" % (conf["cooldown_secs"] / 86400)


def render_full(root, found, cache, conf, now):
    """Everything, one finding per line. Never empty: whoever asked for the
    full list is told what was read even when nothing was found."""
    vulnerable, config_behind, lock_behind = (
        found["vulnerable"], found["config_behind"], found["lock_behind"])
    age = now - found["oldest"]
    too_old = conf["stale_secs"] and age > conf["stale_secs"]
    when = "checked %s ago" % age_text(age)
    if not found["answered"]:
        when = "that holds no answer for this repo yet"
    elif too_old:
        when = ("LAST CHECKED %s AGO, the registries have not answered since, so "
                "anything published later is missing" % age_text(age).upper())
    lines = ["Pinned versions in %s (advisories from OSV, releases from the package "
             "registries, read from a local cache %s; no project tooling was run; "
             "%d pinned version(s) read):" % (root, when, found["total"])]

    dark = not_checked(found, cache, conf, now)
    if dark:
        lines.append("NOT CHECKED: advisory and release lookups have failed for %s, so "
                     "what follows is not current. This is not a finding that the "
                     "rest are clean." % age_text(dark))

    runtime = [r for r in vulnerable if r["pin"]["scope"] == "runtime"]
    dev = [r for r in vulnerable if r["pin"]["scope"] == "dev"]
    for title, rows in (("Runtime dependencies with a published security advisory", runtime),
                        ("Dev/test-only dependencies with a published security advisory", dev)):
        if rows:
            lines.append("%s: %d (%s)" % (title, len(rows), tally_text(rows)))
            lines += ["- " + vulnerable_text(r) for r in rows]

    if config_behind:
        lines.append("Pinned in Elixir config (a version written in a config file is in "
                     "no lockfile, so no outdated or audit command reads it):")
        lines += ["- %s in %s, latest release %s (%s)"
                  % (display(r["pin"]), r["pin"]["srcs"][0], r["latest"], gap_text(r["gap"]))
                  for r in config_behind]

    if lock_behind:
        majors = sum(1 for r in lock_behind if r["gap"][0] == "major")
        threshold = ("%d minor version(s)" % conf["minors"]) if conf["minors"] else "any release"
        extra = (", %d of them a major version" % majors) if majors else ""
        lines.append(
            "Behind the latest release: %d of %d directly declared package(s), by at "
            "least %s%s. A release counts once it has been published %s day(s)."
            % (len(lock_behind), found["direct_known"], threshold, extra, days_text(conf)))
        lines += ["- " + behind_text(r) for r in lock_behind]

    if found["held_back"]:
        held = []  # one package pinned at two versions is still one release
        for row in found["held_back"]:
            text = "%s %s (%s ago)" % (row["pin"].get("config") or row["pin"]["name"],
                                       row["latest"], age_text(now - row["at"]))
            if text not in held:
                held.append(text)
        lines.append("Newer releases not counted yet, published inside the last %s day(s): %s."
                     % (days_text(conf), "; ".join(held)))

    if not (vulnerable or config_behind or lock_behind):
        lines.append("No published advisory, and nothing behind the threshold, among "
                     "the lookups that have an answer.")
    if found["unknown"]:
        lines.append("%d lookup(s) for this repo have no answer in the cache yet and are "
                     "not counted above. To ask the registries now: %s"
                     % (found["unknown"], pins_command("--refresh")))
    return "\n".join(lines)


def gap_text(gap):
    return "%d %s behind" % (gap[1], gap[0])


# ── the note on an edited manifest ────────────────────────────────────────

# Files whose edit is a dependency or pinned-tool change. Several are named
# here that the scanner does not read pins from yet (see README, "Not read"):
# an edit to one of those has no findings and is silent.
MANIFEST_NAMES = {
    "mix.exs", "mix.lock", "package.json", "package-lock.json",
    "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "pyproject.toml",
    "uv.lock", "poetry.lock", "Pipfile", "Pipfile.lock", ".tool-versions"}


def is_manifest(path):
    name = os.path.basename(path)
    return (name in MANIFEST_NAMES or fnmatch.fnmatch(name, "requirements*.txt")
            or (name.endswith(".exs")
                and os.path.basename(os.path.dirname(path)) == "config"))


def file_matcher(root, path):
    """(predicate over pins, the lockfile the findings are read from).

    A lockfile, a requirements file or a config file answers for itself. A
    manifest answers through the lock beside it, for the packages the
    manifest itself declares."""
    name = os.path.basename(path)
    src, wanted = os.path.relpath(path, root), None
    if name == "mix.exs":
        wanted = set(mix_exs_deps(read_text(path)))
        folder, lock = os.path.dirname(path), None
        while folder.startswith(root):
            if os.path.isfile(os.path.join(folder, "mix.lock")):
                lock = os.path.join(folder, "mix.lock")
                break
            if folder == root:
                break
            folder = os.path.dirname(folder)
        if not lock:
            return None, None
        src = os.path.relpath(lock, root)
    elif name == "package.json":
        try:
            manifest = json.loads(read_text(path) or "{}")
        except ValueError:
            return None, None
        wanted = set()
        for field in ("dependencies", "devDependencies", "optionalDependencies"):
            section = manifest.get(field) if isinstance(manifest, dict) else None
            wanted.update(section.keys() if isinstance(section, dict) else ())
        src = os.path.relpath(os.path.join(os.path.dirname(path), "package-lock.json"), root)
    return (lambda pin: src in pin["srcs"]
            and (wanted is None or pin["name"] in wanted)), src


def edit_marker(session, path):
    folder = os.path.join(tempfile.gettempdir(), "scry-pins-edit-%s"
                          % re.sub(r"[^A-Za-z0-9_-]", "_", str(session))[:80])
    return os.path.join(folder, hashlib.sha1(path.encode()).hexdigest()[:16])


def edited(conf):
    """PostToolUse on Edit/Write: this file's findings, from the cache, once
    per session per file. Makes no network call and starts no refresh."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Edit", "Write"):
        return 0
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path or not is_manifest(path):
        return 0
    path = os.path.realpath(path)
    marker = edit_marker(payload.get("session_id") or os.getppid(), path)
    if os.path.exists(marker):
        return 0
    root = (git(os.path.dirname(path), "rev-parse", "--show-toplevel") or "").strip()
    if not root:
        return 0
    root = os.path.realpath(root)
    pins = collect(root)
    if not pins:
        return 0
    cache, now = load_cache(), time.time()
    found = analyse(pins, cache, conf, now)
    write_summary(root, found, cache, conf, now)  # the bar follows a bump
    match, src = file_matcher(root, path)
    if not match:
        return 0
    vulnerable = [r for r in found["vulnerable"] if match(r["pin"])]
    behind_rows = [r for r in found["config_behind"] + found["lock_behind"]
                   if match(r["pin"])]
    if not vulnerable and not behind_rows:
        return 0
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        return 0  # a parallel tool call said it
    except OSError:
        pass

    rel = os.path.relpath(path, root)
    where = rel if src == rel else "%s (as locked in %s)" % (rel, src)
    parts = ["Pinned versions in %s, from Scry's local cache checked %s ago (nothing "
             "was looked up now):" % (where, age_text(now - found["oldest"]))]
    limit = conf["list_max"]
    if vulnerable:
        dev = sum(1 for r in vulnerable if r["pin"]["scope"] == "dev")
        parts.append("%d with a published security advisory (%s%s): %s." % (
            len(vulnerable), tally_text(vulnerable),
            "; %d dev/test-only" % dev if dev else "",
            listed(vulnerable, lambda r: vulnerable_text(r, src=False), limit)))
    if behind_rows:
        parts.append("%d behind the latest release: %s." % (
            len(behind_rows), listed(behind_rows, lambda r: behind_text(r, src=False), limit)))
    parts.append("Full list: %s" % full_command())
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostToolUse", "additionalContext": " ".join(parts)},
        "suppressOutput": True}))
    return 0


# ── entry ─────────────────────────────────────────────────────────────────

def startup_source():
    """The SessionStart payload's `source`, or "" when there is none to read.
    hooks.json already limits the hook to `startup`; this holds the line for
    a client or an install that does not apply the matcher."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        payload = json.loads(sys.stdin.read() or "{}")
        return str(payload.get("source") or "") if isinstance(payload, dict) else ""
    except (OSError, ValueError):
        return ""


def main():
    parser = argparse.ArgumentParser(description="Stale pins")
    parser.add_argument("--root", default=".", help="Repo root to scan")
    parser.add_argument("--refresh", action="store_true",
                        help="Ask the registries now and rewrite the cache")
    parser.add_argument("--full", "--all", action="store_true", dest="full",
                        help="Print every finding as plain text")
    parser.add_argument("--startup", action="store_true",
                        help="SessionStart: one line or nothing (the default)")
    parser.add_argument("--edited", action="store_true",
                        help="PostToolUse: a note on the edited manifest (payload on stdin)")
    args = parser.parse_args()
    if os.environ.get("SCRY_PINS", "1") == "0":
        return 0
    conf = settings()
    if args.edited:
        return edited(conf)
    root = os.path.realpath(args.root)
    if args.refresh:
        return refresh(root, conf)

    if not args.full and startup_source() not in ("", "startup"):
        return 0
    pins = collect(root)
    if not pins:
        if args.full:
            print("Pinned versions in %s: no lockfile, requirements file or config "
                  "pin that Scry reads was found." % root)
        return 0
    cache = load_cache()
    now = time.time()
    found = analyse(pins, cache, conf, now)
    if args.full:
        print(render_full(root, found, cache, conf, now))
        write_summary(root, found, cache, conf, now)
        return 0
    if any(needed(pins, cache, conf, now)):
        spawn_refresh(root)
    write_summary(root, found, cache, conf, now)
    line = startup_line(root, found, conf, now)
    if line:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": line}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook that fails is worse than one that skips
        sys.exit(0)
