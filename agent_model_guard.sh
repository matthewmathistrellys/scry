#!/usr/bin/env bash
# agent_model_guard.sh — model-selection guard on Agent and Workflow dispatch.
#
# ONE rule: a dispatch must CHOOSE a model. Leaving it blank is the defect,
# because a blank model is not "the default" — it silently inherits whatever
# the parent session runs on, invisibly at the call site, and a fan-out
# multiplies that. On 2026-08-31 that consumed most of a week's premium quota
# in a single session: four verifier agents and a builder, none naming a
# model, none needing the model they got. Nobody typed the expensive model
# even once; they typed nothing, and got it anyway.
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
if tool not in ("Agent", "Workflow"):
    allow()

# A payload with no inspectable tool_input is a shape this guard does not
# understand. Denying what it cannot read would wedge a session over a parse
# failure — the one cost that exceeds the tokens this exists to save.
inp = d.get("tool_input")
if not isinstance(inp, dict):
    allow()

event = d.get("hook_event_name") or ""

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
CHOOSE = (
    "Name the model on the dispatch itself. Choose the cheapest model that can "
    "actually do this job — that judgement is yours to make per task, and there "
    "is no model here that is off-limits when it is the right one. What is not "
    "allowed is leaving the choice unmade, because unmade does not mean cheap: "
    "it means whatever this session happens to be running, for the agent's "
    "entire life, multiplied by however many are launched."
)

if tool == "Agent":
    subagent = (inp.get("subagent_type") or "").strip().lower()

    if subagent == "fork":
        # A fork inherits the parent model by definition and ignores `model`,
        # so the choice cannot be expressed — only made consciously.
        state_dir = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                 "scry-agent-model-guard")
        key = "fork:" + (inp.get("description") or "")[:120]
        try:
            os.makedirs(state_dir, exist_ok=True)
            marker = os.path.join(state_dir,
                                  hashlib.sha256(key.encode()).hexdigest()[:32])
            now = time.time()
            if os.path.exists(marker) and (now - os.path.getmtime(marker)) < 300:
                os.remove(marker)      # second strike — a conscious choice
                allow()
            with open(marker, "w") as f:
                f.write(str(now))
        except OSError:
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
             "session's for its entire run.\n\n" + CHOOSE)
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
        "inherits this session's model, for its whole run.\n\n%s\n\nIf a helper "
        "builds the options object, put the model there so the choice is visible "
        "at the call site." % (agent_calls, modelled, CHOOSE)
    )

allow()
PY

exit 0
