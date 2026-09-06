#!/usr/bin/env bash
# agent_model_guard.sh — model-selection guard on every surface that dispatches
# work to a model: Agent, Workflow, and a metered agent CLI run through Bash.
#
# ONE rule: a dispatch must CHOOSE a model. Leaving it blank is the defect,
# because a blank model is not "the default" — it silently inherits whatever
# the parent session runs on, invisibly at the call site, and a fan-out
# multiplies that. On 2026-08-31 that consumed most of a week's premium quota
# in a single session: four verifier agents and a builder, none naming a
# model, none needing the model they got. Nobody typed the expensive model
# even once; they typed nothing, and got it anyway.
#
# An external agent CLI fails the identical way, one layer out: with no model
# flag the CLI's own config default answers, and a config default is set once
# and then forgotten. On 2026-09-06 an overnight session ran seventeen design
# reviews through such a CLI, every one on the premium config default because
# no run named a model. It drained the month's budget and locked the account
# out mid-day. Same defect, same fix — name the model at the call site.
#
# This guard is NOT against any particular model. A cheaper agent asking the
# strongest model one hard question is a legitimate, useful thing to do, and
# an expensive model named deliberately — even inside a workflow — passes
# without objection. What is blocked is the ABSENCE of a decision.
#
# It also does not name which model to pick. Model names age out; the rule
# does not. The instruction is to choose, and to choose the cheapest one that
# can actually do the job — never a roster that will be wrong in six months.
#
# Two events:
#   PreToolUse  — deny a dispatch that names no model, so the call is fixed
#                 before it runs. A fork, which inherits by design and ignores
#                 the override, gets one speed bump and then proceeds.
#   PostToolUse — when a premium model WAS chosen, note what it costs. Guidance
#                 after a legitimate choice, never a block.
#
# Fails OPEN on every internal error: an unparseable payload, an unwritable
# state directory, a missing python3 — all allow the dispatch. A cost guard
# that wedges a session costs more than the tokens it saves.
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

def note(text):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": text,
        },
        "suppressOutput": True,
    }))
    sys.exit(0)

try:
    d = json.loads(os.environ["PAYLOAD"])
except Exception:
    allow()

tool = d.get("tool_name") or ""
if tool not in ("Agent", "Workflow", "Bash"):
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
                             "scry-agent-model-guard")
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


# The instruction every surface shares. It names no model: model names age out,
# the rule does not, and a roster here would be wrong within months.
CHOOSE = (
    "Choose the cheapest model that can actually do this job — that judgement "
    "is yours to make per task, and there is no model here that is off-limits "
    "when it is the right one. What is not allowed is leaving the choice "
    "unmade, because unmade does not mean cheap: it means whatever default is "
    "already in place, for the whole run, multiplied by however many are "
    "launched."
)

# ── Bash: the same rule where the dispatch leaves this process entirely. ────
# A metered agent CLI invoked with no model flag falls back to its own config
# default. That default is chosen once, months before the call, and is usually
# the strongest model the account can reach — so the cheapest possible call
# site, the one that names nothing, buys the most expensive possible run.
#
# Only the ABSENCE of a choice is refused, exactly as above. Which CLIs are
# metered is configuration, never a hardcoded roster: a tool that bills today
# may not tomorrow, and a tool this file has never heard of may bill the most.
if tool == "Bash":
    if event and event != "PreToolUse":
        allow()

    def off(name):
        return (os.environ.get(name) or "").strip() == "0"

    METERED = set() if off("SCRY_METERED_CLI_GUARD") else {
        c.strip().lower()
        for c in (os.environ.get("SCRY_METERED_CLIS") or "codex").split(",")
        if c.strip()
    }

    command = inp.get("command")
    if not isinstance(command, str) or not command.strip():
        allow()

    # Management verbs that run no inference and so bill nothing. This IS a
    # roster and it will rot — but the two failure directions are not equal.
    # A verb that ages off this list costs one clearable speed bump; a spending
    # verb wrongly on it costs a budget, silently. So it stays short and
    # errs toward guarding, and SCRY_METERED_CLI_FREE extends it locally.
    FREE = {
        "help", "--help", "-h", "--version", "-v", "-V", "login", "logout",
        "completion", "update", "doctor", "mcp", "mcp-server", "plugin",
        "app", "app-server", "remote-control", "features", "apply",
        "archive", "unarchive", "delete", "sandbox", "debug", "agents",
    }
    FREE |= {
        v.strip()
        for v in (os.environ.get("SCRY_METERED_CLI_FREE") or "").split(",")
        if v.strip()
    }

    ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
    PUNCT = set("();<>|&")

    def names_a_model(seg):
        for i, t in enumerate(seg):
            if t in ("-m", "--model") and i + 1 < len(seg):
                return True
            if t.startswith("--model=") or t.startswith("-m="):
                return True
            # `-c model=...` / `--config model=...` is just as explicit.
            if t in ("-c", "--config") and i + 1 < len(seg) \
                    and seg[i + 1].startswith("model="):
                return True
        return False

    try:
        import shlex
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except Exception:
        # An unbalanced quote or a shape shlex will not read. Refusing what
        # cannot be parsed would wedge a session over a lexing failure.
        allow()

    # Split into segments on shell separators, so a `-m` belonging to some
    # OTHER command in the chain cannot vouch for this one — `mkdir -m 755 x
    # && codex exec` names no model for codex, whatever a whole-string grep
    # would conclude. Quoted separators stay inside their token, so a
    # separator in a prompt does not split the call it belongs to.
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

        cli = os.path.basename(seg[head]).lower()
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
                        "gets expensive rather than cheap.\n\nIf that is what "
                        "you meant, run it again and it proceeds. If you meant "
                        "the work to come out of the subscription, use an "
                        "interactive session or a scheduled task instead of "
                        "`-p`, or unset ANTHROPIC_API_KEY for this call.\n\n"
                        "Set SCRY_API_BILLING_GUARD=0 to stop saying this."
                    )
                allow()

        if cli not in METERED:
            continue
        rest = seg[head + 1:]
        if any(t in FREE for t in rest):
            continue
        if names_a_model(rest):
            continue

        deny(
            "MODEL CHOICE — this `%s` call names no model, so the CLI's own "
            "config default answers it. That default was set once and is not "
            "visible here; when it is the premium model, every unflagged run "
            "buys the most expensive answer available and nothing at the call "
            "site says so.\n\nPass the model explicitly (`-m` / `--model`). "
            "%s\n\nA metered CLI also spends real money rather than a session "
            "allowance, so the run itself is worth a deliberate go — not just "
            "the model on it.\n\nSet SCRY_METERED_CLI_GUARD=0 to disable this "
            "check, or SCRY_METERED_CLIS to change which CLIs it covers."
            % (cli, CHOOSE)
        )

    allow()

# Models whose use is worth a word afterwards. Configurable so this survives a
# rename without a code change, and so it is never a hardcoded roster.
PREMIUM = {
    m.strip().lower()
    for m in (os.environ.get("SCRY_PREMIUM_MODELS") or "fable").split(",")
    if m.strip()
}

MODEL_RE = re.compile(r"""model\s*:\s*['"]([A-Za-z0-9._\-\[\]]+)['"]""")

def workflow_script():
    script = inp.get("script") or ""
    if not script:
        sp = inp.get("scriptPath")
        if sp and os.path.isfile(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    script = f.read()
            except OSError:
                return ""
    # Strip comments so prose about a model is not mistaken for a call site.
    return re.sub(r"//.*?$|/\*.*?\*/", "", script, flags=re.S | re.M)

# ── PostToolUse: a premium model was deliberately chosen. Say what it costs. ──
if event == "PostToolUse":
    if tool == "Agent":
        chosen = {(inp.get("model") or "").strip().lower()}
    else:
        chosen = {m.group(1).lower() for m in MODEL_RE.finditer(workflow_script())}
    hit = sorted(chosen & PREMIUM)
    if not hit:
        allow()
    note(
        "COST — this dispatch runs on %s, which draws on a separate and much "
        "smaller usage allowance than the everyday models. A single bounded "
        "question is cheap and often worth it. What is expensive is duration and "
        "breadth: an agent that runs long, carries a large working context, or is "
        "one of several launched together can consume a meaningful share of a "
        "week's allowance by itself, and that is invisible until the allowance is "
        "gone.\n\n"
        "Nothing is wrong here — the model was chosen deliberately, which is the "
        "whole point. Just keep the scope of this one tight, and if more work "
        "follows it, ask whether that work needs the same model or only this one "
        "answer did." % ", ".join(hit)
    )

# ── PreToolUse: the only defect is an absent choice. ────────────────────────
if tool == "Agent":
    subagent = (inp.get("subagent_type") or "").strip().lower()

    if subagent == "fork":
        # A fork inherits the parent model by definition and ignores `model`,
        # so the choice cannot be expressed — only made consciously.
        if second_strike("fork:" + (inp.get("description") or "")[:120]):
            allow()
        deny(
            "MODEL CHOICE (first pass) — a fork inherits THIS session's model and "
            "ignores any override, so the choice cannot be expressed on the call. "
            "If you need this session's context carried over, dispatch it again "
            "and it proceeds. If you only need the task done, a regular agent lets "
            "you name the model — which is usually what was meant."
        )

    if not (inp.get("model") or "").strip():
        deny("MODEL CHOICE — this Agent names no model, so it inherits this "
             "session's for its entire run.\n\nName the model on the dispatch "
             "itself. " + CHOOSE)
    allow()

# ── Workflow: the multiplier. Every agent() must have made the choice. ─────
code = workflow_script()
if not code.strip():
    allow()   # a saved/named workflow cannot be inspected from here

agent_calls = len(re.findall(r"\bagent\s*\(", code))
modelled = len(MODEL_RE.findall(code))
if agent_calls > modelled:
    deny(
        "MODEL CHOICE — this workflow has %d agent() call(s) but only %d name a "
        "model. A workflow is the multiplier: each agent() that names none "
        "inherits this session's model, for its whole run.\n\nName the model on "
        "each dispatch itself. %s\n\nIf a helper "
        "builds the options object, put the model there so the choice is visible "
        "at the call site." % (agent_calls, modelled, CHOOSE)
    )

allow()
PY

exit 0
