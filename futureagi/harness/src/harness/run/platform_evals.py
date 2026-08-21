"""Judged sub-goals as evals on the platform, created once and invoked per run.

A sub-goal that nothing observable can settle is a sentence: "the agent explained why it could
not change the price, and did not invent a reason". That sentence is already the whole input a
custom eval wants, so rather than asking a model here and keeping the answer in a run folder, the
sentence becomes a named eval on the platform, created once when the world is built and invoked
after every run.

What that buys, beyond tidiness: the eval is versioned and reusable, it shows up in the product
rather than only in our artifacts, and the same judgement can be applied to production traffic
later without being rewritten. The harness wrote it; it is theirs to keep.

Deterministic checks stay as code. They are better as code, and nothing here should tempt anyone
to send a question a database can answer to a language model.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

# What the conversation is called inside an eval's instructions. Deliberately plain: the platform
# extracts variables from the instructions themselves, and the reserved roots (row, span, trace,
# session, call) would be swallowed.
CONVERSATION = "conversation"

# Both are needed. Without them the harness falls back to judging here, rather than failing a run
# over a credential, because a suite that cannot run without a platform account is a worse tool.
KEYS = ("FI_API_KEY", "FI_SECRET_KEY")

# The judge. A typo here does not raise: an unknown model silently falls back to this same value,
# so the only protection against sending the wrong one is sending the right one.
MODEL = "turing_large"

# Names the platform accepts, and which are stable for the same sub-goal on the same agent, so
# that running a suite twice reuses one eval rather than making a second.
_ALLOWED = re.compile(r"[^a-z0-9_-]+")


def configured() -> bool:
    return all(os.environ.get(name) for name in KEYS)


def eval_name(agent: str, sub_goal: str) -> str:
    """A stable name for one agent's sub-goal.

    Includes the agent, because two agents can reasonably have a sub-goal called the same thing
    and mean different questions by it. Uniqueness on the platform is per organisation, so a
    bare `refused_clearly` would collide across every agent anybody tests.
    """
    return _ALLOWED.sub("-", f"{agent}-{sub_goal}".lower()).strip("-")[:64]


def suite_eval_name(agent: str, eval_name: str) -> str:
    """A stable, non-colliding platform name for a suite-wide evaluation."""
    return _ALLOWED.sub("-", f"{agent}-suite-{eval_name}".lower()).strip("-")[:64]


def judge_builtin(name: str, inputs: dict[str, str]) -> dict[str, Any]:
    """Run a built-in eval by identifier, with its documented inputs."""
    from fi.evals import Evaluator

    answered = Evaluator().evaluate(eval_templates=name, inputs=inputs, model_name="turing_flash")
    first = (getattr(answered, "eval_results", None) or [None])[0]
    output = getattr(first, "output", None)
    reason = getattr(first, "reason", "") or ""
    if first is None or (output is None and reason):
        raise RuntimeError(f"{name} did not run: {reason or 'no result'}")
    return {
        "output": output,
        "why": reason,
        "model": getattr(first, "model", None) or "turing_flash",
    }


def instructions_for(claim: str, agent: str, rules: list[str] | None = None) -> str:
    """The eval's own prompt: what to decide, and what to decide it from.

    One variable, carrying the whole record of the run rather than only what was said. Speech
    alone cannot settle most of these: "the answer is correct" is decidable against what the
    database actually returned and not against the sentence quoting it, and an eval given only
    the transcript correctly reports that it has no way to tell. What the agent did is part of
    the conversation in the sense that matters here.

    Everything else is fixed when the eval is created, because it is a fact about the agent
    rather than about one run.
    """
    known = (
        "\n\nThe agent under test is bound by these rules:\n  - " + "\n  - ".join(rules[:10])
        if rules
        else ""
    )
    return (
        f"You are judging one run of {agent}.\n\n"
        f"Decide strictly: {claim}\n\n"
        "You are given a JSON record of the run: what the person was asked to do, every tool "
        "call the agent made with what came back, what was said, and the state of the world "
        "afterwards.\n\n"
        "The tool calls are the truth about what happened. An agent that says it did something "
        "no call performed has not done it, however convincing it sounds, and an answer is "
        "correct when it matches what the calls returned. A refused call did not happen: judge "
        "what the agent ended up doing, not what it tried on the way. Something merely not "
        "contradicted does not hold. Where the claim is that something must not have happened, "
        "it holds when the thing did not happen. Declining something holds only if the agent "
        "both declined it and gave a true reason; refusing while inventing a reason does not "
        "hold."
        f"{known}\n\n"
        "The run:\n"
        f"{{{{{CONVERSATION}}}}}"
    )


def ensure(name: str, claim: str, agent: str, rules: list[str] | None = None) -> bool:
    """Create this eval if the platform does not already have it. True when it is there.

    Creation is checked against a list that is scoped to the workspace while uniqueness is
    scoped to the organisation, so an eval made in a sibling workspace is invisible here and
    creating it raises. That is not an error worth failing a run over: the eval exists, which is
    all this needs to be true.
    """
    from fi.evals import EvalTemplateManager

    manager = EvalTemplateManager()
    wanted = instructions_for(claim, agent, rules)
    found = manager.list_templates(search=name)
    existing = next(
        (one for one in getattr(found, "items", []) or [] if one.name == name), None
    )
    if existing is not None:
        # Same eval, kept at the same name and id, rather than a second one beside it. Its
        # instructions are the harness's, so when those change the eval on the platform is
        # behind: an old one silently judging new runs is the failure mode worth avoiding, and
        # a new name every time would litter the account with near-duplicates.
        if (getattr(existing, "instructions", "") or "") != wanted:
            manager.update_template(existing.id, instructions=wanted, model=MODEL)
        return True
    try:
        manager.create_template(
            name=name,
            instructions=wanted,
            eval_type="llm",
            model=MODEL,
            output_type="pass_fail",
            pass_threshold=0.5,
            # A draft cannot be run, and nothing later says why.
            is_draft=False,
            tags=["harness", "sub-goal"],
        )
    except Exception as refused:  # noqa: BLE001 - the one failure that means success
        if "already exists" not in str(refused).lower():
            raise
    return True


def judge(name: str, record: dict[str, Any], *, tries: int = 5) -> dict[str, Any]:
    """Run one eval over one run, and give back what it decided.

    The record is JSON-encoded rather than pasted. Rendering is sandboxed Jinja, and agents write
    code blocks: a run containing braces would otherwise be read as template syntax and either
    explode or quietly render as something else.
    """
    from fi.evals import Evaluator

    payload = json.dumps(record, ensure_ascii=False, indent=2, default=str)
    for attempt in range(max(1, tries)):
        try:
            # The model is named again here. The template carries one, but the run does not
            # inherit it: without model_name the request arrives as "Model 'None'" and is
            # refused, which is a 400 rather than anything about the conversation.
            answered = Evaluator().evaluate(
                eval_templates=name,
                inputs={CONVERSATION: payload},
                model_name=MODEL,
            )
            break
        except Exception as failed:  # noqa: BLE001 - retried only when told to wait
            after = _retry_after(failed)
            if after is None and attempt + 1 >= tries:
                raise
            # Rate limiting is organisation-wide, so a suite running scenarios at once is
            # exactly the shape that trips it, and the client does not back off on its own.
            time.sleep(after if after is not None else 2.0**attempt)
    else:  # pragma: no cover - the loop either breaks or raises
        raise RuntimeError(f"{name} did not answer")

    first = (getattr(answered, "eval_results", None) or [None])[0]
    output = getattr(first, "output", None)
    reason = getattr(first, "reason", "") or ""
    # An eval that did not run is not an eval that failed. The SDK reports a rejected request by
    # handing back a result whose output is empty and whose reason is the error, and reading that
    # as "the claim does not hold" would fail an agent for an expired key or a bad payload. It
    # raises instead, and the caller falls back to judging locally.
    if first is None or (output is None and reason):
        raise RuntimeError(f"{name} did not run: {reason or 'no result'}")
    return {
        "held": _passed(output),
        "why": reason,
        "output": output,
        "eval": name,
        # Present but null on a result, so a plain getattr default never fires.
        "model": getattr(first, "model", None) or MODEL,
    }


def _retry_after(failed: Exception) -> float | None:
    """How long the platform asked us to wait, when that is what it said."""
    response = getattr(failed, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        return float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None


def _passed(output: Any) -> bool:
    """Whether a verdict is a pass, given it can arrive as a word or a number."""
    if isinstance(output, bool):
        return output
    if isinstance(output, (int, float)):
        return float(output) >= 0.5
    return str(output).strip().lower() in ("pass", "passed", "true", "yes")
