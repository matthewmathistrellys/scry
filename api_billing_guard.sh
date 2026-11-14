#!/usr/bin/env bash
# api_billing_guard.sh — PreToolUse (Bash): which account a headless Claude
# run bills.
#
# `claude -p` bypasses the subscription login by design and reads
# ANTHROPIC_API_KEY. With a key in the environment, the run is metered per
# token against that API account while the subscription sits unused. Nothing
# in the command says so and nothing in the session shows it; people have
# found out at $447 and at $1,818, and consumed API credit is not refundable.
#
# Billing the API on purpose is a real thing to want, so this is a speed
# bump, not a wall: the first such run is turned down once with the fact
# stated, and the same command again within five minutes proceeds.
# (Split out of agent_model_guard.sh in 1.53.0, when the model-choice guard
# moved to Grimoire: which account pays is the environment; which model to
# choose is the work.)
#
# Fails OPEN on every internal error. SCRY_API_BILLING_GUARD=0 turns it off.
set -uo pipefail

read -r payload

PAYLOAD="$payload" python3 - <<'PY'
import hashlib, json, os, re, sys, time

def allow():
    sys.exit(0)

def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


try:
    d = json.loads(os.environ["PAYLOAD"])
except Exception:
    allow()

tool = d.get("tool_name") or ""
if tool != "Bash":
    allow()

# A payload with no inspectable tool_input is a shape this guard does not
# understand. Denying what it cannot read would wedge a session over a parse
# failure — the one cost that exceeds the tokens this exists to save.
inp = d.get("tool_input")
if not isinstance(inp, dict):
    allow()

event = d.get("hook_event_name") or ""

# A speed bump, not a wall. The first attempt is refused with the cost stated;
# the same call repeated within five minutes is a conscious decision and goes
# through untouched. Used where the action is legitimate but its consequence is
# invisible at the call site — the point is that someone SEES it, not that they
# are prevented. Any state error allows: a guard that wedges a session on an
# unwritable temp dir costs more than the thing it is guarding.
def second_strike(key, window=300):
    state_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                             "scry-api-billing-guard")
    try:
        os.makedirs(state_dir, exist_ok=True)
        marker = os.path.join(state_dir,
                              hashlib.sha256(key.encode()).hexdigest()[:32])
        now = time.time()
        if os.path.exists(marker) and (now - os.path.getmtime(marker)) < window:
            os.remove(marker)
            return True
        with open(marker, "w") as f:
            f.write(str(now))
    except OSError:
        return True
    return False


if tool == "Bash":
    if event and event != "PreToolUse":
        allow()

    def off(name):
        return (os.environ.get(name) or "").strip() == "0"

    command = inp.get("command")
    if not isinstance(command, str) or not command.strip():
        allow()

    ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    PUNCT = set("();<>|&")

    # Strip heredoc bodies before lexing: shlex does not know `<<EOF ... EOF`,
    # so prose inside a commit message or file body (e.g. `...; Codex and pi`)
    # would otherwise be split on `;` and read as a command invocation.
    stripped = re.sub(
        r"(<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?[^\n]*)\n.*?\n[ \t]*\2(?=\n|$)",
        r"\1",
        command,
        flags=re.S,
    )

    try:
        import shlex
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except Exception:
        # An unbalanced quote or a shape shlex will not read. Refusing what
        # cannot be parsed would wedge a session over a lexing failure.
        allow()

    # Split into segments on shell separators, so a `-p` belonging to some
    # OTHER command in the chain is not read as this call's. Quoted
    # separators stay inside their token, so a separator in a prompt does not
    # split the call it belongs to.
    segments, current = [], []
    for t in tokens:
        if t and all(ch in PUNCT for ch in t):
            segments.append(current)
            current = []
        else:
            current.append(t)
    segments.append(current)

    for seg in segments:
        head = 0
        while head < len(seg) and ASSIGNMENT.match(seg[head]):
            head += 1
        if head < len(seg) and os.path.basename(seg[head]) == "env":
            head += 1
            while head < len(seg) and ASSIGNMENT.match(seg[head]):
                head += 1
        if head >= len(seg):
            continue

        cli = os.path.basename(seg[head])
        rest_all = seg[head + 1:]

        # ── Headless Claude bills the API account, never the subscription. ──
        # `claude -p` bypasses OAuth by design and reads ANTHROPIC_API_KEY, so
        # with a key in the environment the run is metered per token against a
        # separate account while the subscription sits unused. Nothing in the
        # command says so and nothing in the session shows it; people have found
        # out at $447 and at $1,818, and those charges are not refundable.
        #
        # This is legitimate — billing the API on purpose is a real thing to
        # want — so it is a speed bump, not a wall. Say what is about to happen
        # once; run it again and it proceeds.
        if cli == "claude" and not off("SCRY_API_BILLING_GUARD"):
            headless = any(t in ("-p", "--print") or t.startswith("--print=")
                           for t in rest_all)
            inline_key = any(t.startswith("ANTHROPIC_API_KEY=")
                             for t in seg[:head])
            if headless and (inline_key or os.environ.get("ANTHROPIC_API_KEY")):
                if not second_strike("api-billing:" + command[:200]):
                    deny(
                        "API BILLING (first pass) — `claude -p` bypasses the "
                        "subscription login by design: it reads "
                        "ANTHROPIC_API_KEY, which is set here, so this run is "
                        "metered per token against that API account. The "
                        "subscription is not touched and pays for none of it.\n\n"
                        "Nothing in this command says that, and nothing in the "
                        "session will show it while it happens — it surfaces on "
                        "an invoice, and consumed API credit is not refundable. "
                        "An unattended or looping headless run is where this "
                        "gets expensive rather than cheap. An interactive "
                        "session, a scheduled task, or the same call without "
                        "ANTHROPIC_API_KEY bills the subscription instead. The "
                        "same command again within five minutes proceeds.\n\n"
                        "Set SCRY_API_BILLING_GUARD=0 to stop saying this."
                    )
                allow()

    allow()

allow()
PY
