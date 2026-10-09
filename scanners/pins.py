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

TWO MODES, AND THE SPLIT IS THE DESIGN.

  report   (default; what the SessionStart hook runs) reads the lockfiles on
           disk and a local cache, prints, and exits. It makes NO network
           call. If the cache is missing answers or they are older than a
           day, it starts `--refresh` detached and does not wait for it.
  refresh  (`--refresh`; the detached child) asks the registries, inside a
           wall-clock budget, and rewrites the cache.

So a session never waits on the network, an offline machine costs one failed
background process, and the first session in a repo nobody has checked says
nothing -- the second one has the answer.

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
Latest releases come from hex.pm, the npm registry, PyPI and GitHub releases.
None of it needs the project's dependencies fetched or compiled, which is why
`mix hex.outdated` / `mix deps.audit` are not used.

"Behind" is only computed for DIRECTLY DECLARED packages (mix.exs deps, the
lockfile root's dependencies, requirement lines). A transitive package behind
its latest release is not something this repo chose, and looking them all up
is what would exhaust the registry's rate limit. Advisories are checked for
every locked version, transitive included.

Usage:
    python3 scanners/pins.py [--root .]            # report from cache
    python3 scanners/pins.py --root . --refresh    # ask the registries now
    python3 scanners/pins.py --root . --all        # full list, plain text
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import projects  # noqa: E402  (path set above so hooks can run from anywhere)

USER_AGENT = "scry-pins/1 (+https://github.com/matthewmathistrellys/scry)"
CACHE_SCHEMA = 1
MAX_DEPTH = 4
REQUEST_TIMEOUT = 6
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
_MIX_DEP_RE = re.compile(r"\{\s*:([a-z][a-z0-9_]*)\s*,")


def mix_declared(lock_dir):
    """Package names declared in the mix.exs beside a lock, plus an umbrella's
    apps/*/mix.exs, which share the one lock above them."""
    paths = [os.path.join(lock_dir, "mix.exs")]
    apps = os.path.join(lock_dir, "apps")
    try:
        paths += [os.path.join(apps, d, "mix.exs") for d in sorted(os.listdir(apps))]
    except OSError:
        pass
    names, read_any = set(), False
    for path in paths:
        body = read_text(path)
        if body:
            read_any = True
            names.update(_MIX_DEP_RE.findall(body))
    return names if read_any else None


def mix_lock_pins(path, root):
    declared = mix_declared(os.path.dirname(path))
    src = os.path.relpath(path, root)
    for line in read_text(path).splitlines():
        match = _MIX_LOCK_RE.match(line)
        # Only the public registry: a private repo's package names stay here.
        if not match or match.group(3) != "hexpm":
            continue
        name, version = match.group(1), match.group(2)
        direct = declared is None or name in declared
        yield {"eco": "Hex", "name": name, "version": version, "src": src,
               "direct": direct, "latest": "Hex|" + name if direct else None}


def _strip_exs_comments(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


_EXS_BLOCK_RE = re.compile(
    r"^[ \t]*config\s+:(tailwind|esbuild)\s*,(.*?)(?=^[ \t]*(?:config|import_config|if|import)\b|\Z)",
    re.M | re.S)
_EXS_VERSION_RE = re.compile(r"""(?:\bversion:|:version\s*,)\s*["']([^"']+)["']""")


def elixir_config_pins(mix_exs, root):
    config_dir = os.path.join(os.path.dirname(mix_exs), "config")
    try:
        names = sorted(n for n in os.listdir(config_dir) if n.endswith(".exs"))
    except OSError:
        return
    for name in names:
        path = os.path.join(config_dir, name)
        body = _strip_exs_comments(read_text(path))
        for match in _EXS_BLOCK_RE.finditer(body):
            version = _EXS_VERSION_RE.search(match.group(2))
            if not version or not parse_version(version.group(1)):
                continue
            spec = CONFIG_PINS[match.group(1)]
            eco, package = spec["advisory"]
            yield {"eco": eco, "name": package, "version": version.group(1),
                   "src": os.path.relpath(path, root), "direct": True,
                   "latest": spec["latest"], "config": match.group(1)}


def npm_lock_pins(path, root):
    try:
        lock = json.loads(read_text(path) or "{}")
    except ValueError:
        return
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        return  # lockfileVersion 1 has no `packages` map; not read.
    declared = set()
    for key, entry in packages.items():
        if "node_modules/" in key or not isinstance(entry, dict):
            continue
        for field in ("dependencies", "devDependencies", "optionalDependencies"):
            declared.update((entry.get(field) or {}).keys())
    src = os.path.relpath(path, root)
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
        yield {"eco": "npm", "name": name, "version": version, "src": src,
               "direct": direct, "latest": "npm|" + name if direct else None}


_REQ_RE = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)\s*$")


def requirements_pins(path, root):
    """Exact `name==version` lines only. A range is not a pin, and option
    lines (`--index-url ...`, which can carry credentials) are never parsed."""
    src = os.path.relpath(path, root)
    for line in read_text(path).splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        match = _REQ_RE.match(line)
        if not match:
            continue
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        yield {"eco": "PyPI", "name": name, "version": match.group(2),
               "src": src, "direct": True, "latest": "PyPI|" + name}


def collect(root):
    """Every pin under `root`, one record per (ecosystem, name, version)."""
    files = find_files(root)
    merged = {}
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
            if pin["direct"] and not seen["direct"]:
                seen["direct"], seen["latest"] = True, pin["latest"]
            if pin.get("config") and not seen.get("config"):
                # The config pin is the one nothing else tracks: it wins.
                seen["config"], seen["latest"] = pin["config"], pin["latest"]
                seen["srcs"].remove(src)
                seen["srcs"].insert(0, src)
    for path in files["mix.exs"]:
        add(elixir_config_pins(path, root))
    for path in files["mix.lock"]:
        add(mix_lock_pins(path, root))
    for path in files["package-lock.json"]:
        add(npm_lock_pins(path, root))
    for path in files["requirements"]:
        add(requirements_pins(path, root))
    return list(merged.values())


def adv_key(pin):
    return "%s|%s|%s" % (pin["eco"], pin["name"], pin["version"])


# ── cache ─────────────────────────────────────────────────────────────────

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
    os.makedirs(cache_dir(), exist_ok=True)
    tmp = "%s.%d.tmp" % (cache_path(), os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, cache_path())


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


def http_json(url, body=None):
    """GET (or POST `body`) and decode JSON. Returns (data, headers)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8", "replace")), response.headers


def quote(name):
    return urllib.parse.quote(name, safe="@")


def fetch_latest(key, conf, hex_gate):
    """Latest stable release for one latest-key, or None if the registry
    answered and does not know the package. Raises on a failed lookup."""
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
            return data.get("latest_stable_version")
        if kind == "npm":
            data, _ = http_json("%s/%s/latest" % (conf["npm"], quote(name)))
            return data.get("version")
        if kind == "PyPI":
            data, _ = http_json("%s/pypi/%s/json" % (conf["pypi"], quote(name)))
            return (data.get("info") or {}).get("version")
        if kind == "gh":
            try:
                data, _ = http_json("%s/repos/%s/releases/latest" % (conf["github"], name))
                return (data.get("tag_name") or "").lstrip("vV") or None
            except (urllib.error.URLError, OSError, ValueError):
                fallback = GITHUB_NPM_FALLBACK.get(name)
                if not fallback:
                    raise
                data, _ = http_json("%s/%s/latest" % (conf["npm"], quote(fallback)))
                return data.get("version")
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
        except (urllib.error.URLError, OSError, ValueError):
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
        for key, status, version in latest_jobs:
            if status == "ok":
                succeeded += 1
                cache["latest"][key] = {"at": time.time(), "v": version}
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
    return 0


def attempt_marker(root):
    tag = hashlib.sha1(os.path.abspath(root).encode()).hexdigest()[:12]
    return os.path.join(cache_dir(), "attempt-" + tag)


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


# ── report ────────────────────────────────────────────────────────────────

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


def analyse(pins, cache, conf, now):
    vulnerable, config_behind, lock_behind = [], [], []
    unknown = direct_known = 0
    oldest = now
    for pin in pins:
        entry = cache["adv"].get(adv_key(pin))
        if entry is None:
            unknown += 1
        else:
            oldest = min(oldest, entry.get("at", now))
            groups = advisory_groups(pin, cache)
            if groups:
                fixes = [f for g in groups for f in g["fixed"]]
                vulnerable.append({
                    "pin": pin, "count": len(groups),
                    "sev": max(g["sev"] for g in groups),
                    "id": group_id(max(groups, key=lambda g: g["sev"])),
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
        gap = behind(pin["version"], latest["v"], conf["minors"])
        if gap:
            row = {"pin": pin, "latest": latest["v"], "gap": gap}
            (config_behind if pin.get("config") else lock_behind).append(row)
    vulnerable.sort(key=lambda v: (-v["sev"], -v["count"], v["pin"]["name"]))
    order = lambda r: (r["gap"][0] != "major", -r["gap"][1], r["pin"]["name"])
    config_behind.sort(key=order)
    lock_behind.sort(key=order)
    return {"vulnerable": vulnerable, "config_behind": config_behind,
            "lock_behind": lock_behind, "unknown": unknown,
            "direct_known": direct_known, "oldest": oldest, "total": len(pins)}


def gap_text(gap):
    return "%d %s behind" % (gap[1], gap[0])


def vulnerable_text(row):
    pin = row["pin"]
    text = "%s (%s): " % (display(pin), pin["srcs"][0])
    text += ("%d advisories, worst " % row["count"]) if row["count"] > 1 else ""
    text += "%s %s" % (SEVERITY_NAME[row["sev"]], row["id"])
    if row["fixed"]:
        text += ", fixed in %s" % row["fixed"]
    return text


def behind_text(row):
    pin = row["pin"]
    return "%s → %s (%s)" % (display(pin), row["latest"], pin["srcs"][0])


def listed(rows, render_row, limit):
    shown = "; ".join(render_row(r) for r in rows[:limit])
    return shown + ("; +%d more" % (len(rows) - limit) if len(rows) > limit else "")


def render(root, pins, cache, conf, now, full=False):
    found = analyse(pins, cache, conf, now)
    vulnerable, config_behind, lock_behind = (
        found["vulnerable"], found["config_behind"], found["lock_behind"])
    age = now - found["oldest"]
    too_old = conf["stale_secs"] and age > conf["stale_secs"]
    limit = 10 ** 6 if full else conf["list_max"]

    if not (vulnerable or config_behind or lock_behind):
        # Silence means "checked, nothing crossed a threshold". When the
        # check has not been able to run for a week, silence would say that
        # falsely -- so this one case speaks.
        failing = cache["meta"].get("fail_since")
        dark = failing and conf["stale_secs"] and now - failing > conf["stale_secs"]
        if pins and dark and (found["unknown"] or too_old):
            return ("Pinned versions (SessionStart): NOT CHECKED. Advisory and release "
                    "lookups have failed for %s, so the %d version(s) pinned in this repo "
                    "have no current data. This is not a finding that they are clean."
                    % (age_text(now - failing), found["total"]))
        return ""

    when = "checked %s ago" % age_text(age)
    if too_old:
        when = ("LAST CHECKED %s AGO, the registries have not answered since, so "
                "anything published later is missing" % age_text(age).upper())
    lines = ["Pinned versions (SessionStart; advisories from OSV, releases from the "
             "package registries, read from a local cache %s; no project tooling "
             "was run):" % when]

    if vulnerable:
        tally = {}
        for row in vulnerable:
            tally[row["sev"]] = tally.get(row["sev"], 0) + 1
        breakdown = ", ".join("%d %s" % (tally[s], SEVERITY_NAME[s])
                              for s in sorted(tally, reverse=True))
        lines.append(
            "- %d pinned version(s) have a published security advisory (%s). %s."
            % (len(vulnerable), breakdown, listed(vulnerable, vulnerable_text, limit)))

    if config_behind:
        parts = ["%s in %s, latest release %s (%s)"
                 % (display(r["pin"]), r["pin"]["srcs"][0], r["latest"], gap_text(r["gap"]))
                 for r in config_behind]
        lines.append(
            "- Pinned in Elixir config: %s. A version written in a config file is in "
            "no lockfile, so no outdated or audit command reads it." % "; ".join(parts))

    if lock_behind:
        majors = sum(1 for r in lock_behind if r["gap"][0] == "major")
        threshold = ("%d minor version(s)" % conf["minors"]) if conf["minors"] else "any release"
        extra = (", %d of them a major version" % majors) if majors else ""
        lines.append(
            "- %d of %d directly declared package(s) are behind their latest release "
            "by at least %s%s. Furthest: %s."
            % (len(lock_behind), found["direct_known"], threshold, extra,
               listed(lock_behind, behind_text, limit)))

    if found["unknown"]:
        lines.append("- %d lookup(s) for this repo have no answer in the cache yet "
                     "and are not counted above." % found["unknown"])
    if not full and max(len(vulnerable), len(lock_behind)) > limit:
        lines.append("Full list: python3 %s --root %s --all"
                     % (os.path.abspath(__file__), root))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Stale pins")
    parser.add_argument("--root", default=".", help="Repo root to scan")
    parser.add_argument("--refresh", action="store_true",
                        help="Ask the registries now and rewrite the cache")
    parser.add_argument("--all", action="store_true",
                        help="Print every finding as plain text (no hook envelope)")
    args = parser.parse_args()
    if os.environ.get("SCRY_PINS", "1") == "0":
        return 0
    root = os.path.abspath(args.root)
    conf = settings()

    if args.refresh:
        return refresh(root, conf)

    pins = collect(root)
    if not pins:
        return 0
    cache = load_cache()
    now = time.time()
    if not args.all and any(needed(pins, cache, conf, now)):
        spawn_refresh(root)
    body = render(root, pins, cache, conf, now, full=args.all)
    if not body:
        return 0
    if args.all:
        print(body)
    else:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": body}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook that fails is worse than one that skips
        sys.exit(0)
