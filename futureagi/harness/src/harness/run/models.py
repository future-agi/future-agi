"""Which model plays which part.

Three different jobs, and one setting for all of them was wrong for every one. The agent under
test and the person talking to it run on every turn of every scenario; the judge runs once per
scenario and is where a wrong answer costs the most; the harness itself writes contracts, worlds
and checks and is a different job again.

The harness's own model is deliberately not here. It is set by ``ALK_HARNESS_MODEL`` and belongs
to the conversation you have with the harness, not to the simulation it runs.

**On Gemini.** The obvious thing to want is Flash for the agent and the simulated user: they are
the two roles that run constantly, and Vertex is already configured. It does not work yet, and
the reason is worth writing down rather than rediscovering. The reconstructed agent runs on the
Claude Agent SDK, which is pointed at Vertex by ``CLAUDE_CODE_USE_VERTEX`` and speaks to
Anthropic models only. Handed a Gemini name it produced a session that said nothing at all: no
turns, no calls, every check red, and a result that read as an agent ignoring the person.

Running the agent on Gemini means giving the spec one of ALK's own endpoint adapters as the
target — ``system_prompt`` resolves an LLM target from a prompt, which is exactly what the
reconstruction is — instead of the harness's own. That also moves tool execution to ALK, which
is a real change and not a configuration one. Until then these stay on what can actually be
driven, and the guard in ``targets.py`` refuses the rest loudly.
"""

from __future__ import annotations

import os

# What the reconstructed agent and the simulated user run on today. Both roles run constantly, so
# this is the setting worth revisiting first once the target can be handed to ALK.
AGENT = "claude-sonnet-4-6"
USER = "claude-sonnet-4-6"
# Kept separate and stronger. A judged sub-goal is the one place a cheap wrong answer is
# expensive: it decides a pass, it runs once per scenario, and nobody re-reads it.
JUDGE = "claude-opus-4-7"


def for_roles(override: str | None = None) -> dict[str, str]:
    """The model each part runs on.

    ``override`` names one model for every role, which is what a caller comparing two models end
    to end is asking for: same suite, same world, one thing changed.
    """
    if override:
        return {"agent": override, "user": override, "judge": override}
    return {
        "agent": os.environ.get("ALK_AGENT_MODEL", AGENT),
        "user": os.environ.get("ALK_USER_MODEL", USER),
        "judge": os.environ.get("ALK_JUDGE_MODEL", JUDGE),
    }
