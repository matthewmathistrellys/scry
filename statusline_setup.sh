#!/usr/bin/env bash
# statusline_setup.sh — says when Scry's status bar is not set up, and sets it
# up when asked.
#
# A plugin cannot install a status line: `statusLine` is a user setting, and
# Claude Code reads it from settings.json, not from any plugin. So a fresh
# install of Scry has hooks, a monitor, and no bar — and until this existed,
# nothing said why. On 2026-10-09 Matt installed Scry on a new Mac, got no
# status bar, and had to find the snippet in the README's Install section.
# The missing bar is also not only cosmetic: cache_deadline_statusline.sh is
# the one place the prompt-cache expiry is recorded, so without it the
# cache-deadline monitor has nothing to read and never speaks.
#
# Two modes, and the default is the one that cannot write:
#
#   (no arguments)  SessionStart hook. READS the settings files and prints one
#                   line when the status line in effect is not Scry's. Silent
#                   once it is. With no status line at all it speaks every
#                   session; with a different one it speaks once per status
#                   line, because that one is there on purpose. Never writes
#                   a settings file.
#   --install       Adds the documented statusLine to the user's settings.json,
#                   keeping every other key. Run by the /scry-statusline skill
#                   or by hand — only ever because a person asked. Refuses
#                   when a different statusLine is already there, and says
#                   what it found; --replace is the explicit way past that and
#                   keeps a copy of the file first.
#   --silence       Records SCRY_STATUSLINE_CHECK=0 in settings.json `env`,
#                   for someone who has chosen another status line and does
#                   not want to hear about this one.
#   --status        Prints what is configured. Writes nothing.
#
# Which file: ${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json, or --settings
# PATH. The hook also reads the project's .claude/settings.json and
# .claude/settings.local.json, because a status line set there is the one in
# effect.
#
# Claude Code only. Codex has no status line setting, so the hook speaks only
# when CLAUDE_PLUGIN_ROOT is set, which Claude Code does for plugin hooks.
#
# Opt-out: SCRY_STATUSLINE_CHECK=0, in the environment or in settings.json
# `env`. Fails open: an unreadable or unparseable settings file is silence in
# hook mode and a refusal to write in --install.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mode="hook"
settings=""
replace=0
while [ $# -gt 0 ]; do
  case "$1" in
    --install) mode="install" ;;
    --replace) mode="install"; replace=1 ;;
    --silence) mode="silence" ;;
    --status) mode="status" ;;
    --settings) shift; settings="${1:-}" ;;
    *) echo "statusline_setup.sh: unknown argument: $1" >&2; exit 64 ;;
  esac
  shift
done

if [ "$mode" = "hook" ]; then
  [ "${SCRY_STATUSLINE_CHECK:-1}" = "0" ] && exit 0
  [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] || exit 0
  payload="$(cat 2>/dev/null || true)"
else
  payload=""
fi

[ -n "$settings" ] || settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"

SCRY_MODE="$mode" SCRY_SETTINGS="$settings" SCRY_REPLACE="$replace" \
SCRY_HERE="$here" SCRY_PAYLOAD="$payload" python3 - <<'PY'
import hashlib, json, os, re, shutil, sys

mode = os.environ["SCRY_MODE"]
path = os.environ["SCRY_SETTINGS"]
here = os.environ["SCRY_HERE"]

# The snippet from README "Install". The resolver picks the newest installed
# Scry, so `/plugin update scry` keeps working, and prints nothing when Scry
# is not installed.
COMMAND = ('f="$(ls -d "$HOME"/.claude/plugins/cache/scry/scry/*/ 2>/dev/null '
           '| sort -V | tail -1)statusline.sh"; [ -f "$f" ] && exec bash "$f"; exit 0')
SNIPPET = {"type": "command", "command": COMMAND, "refreshInterval": 60}

# Scry's own: the wrapper by name, or statusline.sh reached through a path
# that names scry.
OURS = re.compile(r"cache_deadline_statusline\.sh|scry.*statusline\.sh", re.I | re.S)


def load(p):
    """(settings dict, state): state is 'ok', 'missing' or 'unreadable'."""
    try:
        with open(p, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return {}, "missing"
    except OSError:
        return {}, "unreadable"
    if not text.strip():
        return {}, "ok"
    try:
        data = json.loads(text)
    except ValueError:
        return {}, "unreadable"
    return (data, "ok") if isinstance(data, dict) else ({}, "unreadable")


def command_of(data):
    line = data.get("statusLine")
    if isinstance(line, dict):
        return str(line.get("command") or "")
    return "" if line is None else str(line)


def write(p, data):
    """Replace the file in one rename, keeping its mode."""
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = "%s.scry-%d.tmp" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    try:
        shutil.copymode(p, tmp)
    except OSError:
        pass
    os.replace(tmp, p)


def hook():
    try:
        payload = json.loads(os.environ.get("SCRY_PAYLOAD") or "{}")
    except ValueError:
        payload = {}
    cwd = payload.get("cwd") or os.getcwd()
    user, state = load(path)
    if state == "unreadable":
        return
    if str((user.get("env") or {}).get("SCRY_STATUSLINE_CHECK", "1")) == "0":
        return
    # The status line in effect: project-local, then project, then user.
    root = cwd
    probe = os.path.abspath(cwd)
    while probe != "/":
        if os.path.exists(os.path.join(probe, ".git")):
            root = probe
            break
        probe = os.path.dirname(probe)
    current, where = "", path
    for candidate in (os.path.join(root, ".claude", "settings.local.json"),
                      os.path.join(root, ".claude", "settings.json")):
        data, cstate = load(candidate)
        if cstate == "unreadable":
            return
        if str((data.get("env") or {}).get("SCRY_STATUSLINE_CHECK", "1")) == "0":
            return
        if "statusLine" in data:
            current, where = command_of(data), candidate
            break
    else:
        current = command_of(user)
    if OURS.search(current):
        return
    setup = "bash %s/statusline_setup.sh" % here
    if current:
        # Another status line is there because someone put it there. That is
        # said ONCE per status line, not at every session start: a note that
        # repeats against a deliberate choice is the wallpaper Scry exists to
        # avoid. No status line at all is different -- that is said until it
        # is set up or silenced.
        state_dir = os.environ.get("SCRY_STATUSLINE_STATE_DIR") or os.path.join(
            os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
            "scry")
        marker = os.path.join(
            state_dir, "statusline-told-" + hashlib.sha1(current.encode()).hexdigest()[:12])
        try:
            os.makedirs(state_dir, exist_ok=True)
            os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except OSError:
            return  # already said, or nowhere to remember having said it
        text = (
            "Scry status bar (SessionStart): not in use. %s sets a different "
            "statusLine, so Scry's bar is not shown and its cache-deadline monitor "
            "has no expiry to read. Nothing was changed. `%s --replace` switches to "
            "Scry's bar and keeps a copy of the file; README (Install) shows how to "
            "keep the current one and add only the cache segment. Said once per "
            "status line." % (where, setup))
    else:
        text = (
            "Scry status bar (SessionStart): not set up. %s has no statusLine, and "
            "a plugin cannot set one (it is a user setting), so there is no bar and "
            "Scry's cache-deadline monitor has no expiry to read. The user has not "
            "been asked. The /scry-statusline skill (`%s --install`) adds it and "
            "keeps every other key; `%s --silence` stops this note."
            % (path, setup, setup))
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart", "additionalContext": text}}))


def install():
    data, state = load(path)
    if state == "unreadable":
        print("Not changed: %s could not be read as a JSON object. Fix the file "
              "first; Scry will not rewrite a settings file it cannot parse." % path)
        return 1
    current = command_of(data)
    if "statusLine" in data and OURS.search(current):
        print("Already set: statusLine in %s points at Scry. Nothing changed." % path)
        return 0
    if "statusLine" in data and os.environ["SCRY_REPLACE"] != "1":
        print("Not changed: %s already has a different statusLine:\n  %s\n"
              "Scry will not overwrite it unasked. To replace it (a copy of the "
              "file is kept beside it): %s/statusline_setup.sh --replace\n"
              "To keep it and stop the session-start note: "
              "%s/statusline_setup.sh --silence\n"
              "To keep it and add only Scry's cache segment, see README, Install."
              % (path, current or json.dumps(data["statusLine"]), here, here))
        return 2
    replaced = ""
    if "statusLine" in data:
        backup = path + ".scry-bak"
        shutil.copy2(path, backup)
        replaced = (" The previous statusLine was:\n  %s\nThe file as it was is at %s."
                    % (current or json.dumps(data["statusLine"]), backup))
    data["statusLine"] = dict(SNIPPET)
    write(path, data)
    print("Set: statusLine in %s now runs Scry's status line; every other key is "
          "unchanged.%s The bar appears on Claude Code's next status refresh; a "
          "new session may be needed." % (path, replaced))
    return 0


def silence():
    data, state = load(path)
    if state == "unreadable":
        print("Not changed: %s could not be read as a JSON object." % path)
        return 1
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    env["SCRY_STATUSLINE_CHECK"] = "0"
    data["env"] = env
    write(path, data)
    print("Set: env.SCRY_STATUSLINE_CHECK=0 in %s. Scry will not mention the "
          "status line at session start; statusLine itself is unchanged." % path)
    return 0


def status():
    data, state = load(path)
    if state == "unreadable":
        print("%s could not be read as a JSON object." % path)
    elif "statusLine" not in data:
        print("%s has no statusLine%s." % (path, " (file does not exist)" if state == "missing" else ""))
    elif OURS.search(command_of(data)):
        print("statusLine in %s points at Scry." % path)
    else:
        print("%s has a different statusLine:\n  %s" % (path, command_of(data)))
    return 0


try:
    if mode == "hook":
        hook()
        code = 0
    else:
        code = {"install": install, "silence": silence, "status": status}[mode]()
except Exception as error:  # a hook never fails; a setup says why it did
    if mode == "hook":
        code = 0
    else:
        print("Not changed: %s" % error)
        code = 1
sys.exit(code)
PY
code=$?
[ "$mode" = "hook" ] && exit 0
exit "$code"
