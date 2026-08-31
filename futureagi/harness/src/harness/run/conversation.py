"""Two parties talking: a simulated customer with a goal, and the agent under test.

The customer is a separate session that can only talk. It has no tools and no view of the world,
which is the point: it knows what it wants and how it behaves, and everything it learns about
what is possible it learns from what the agent tells it. An agent that lies to it gets away with
it here exactly as it would with a person, and that is what makes the transcript worth grading.

The conversation ends when the customer is done, when it gives up, or when it runs out of turns.
All three are recorded, because how a conversation ended is often the finding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions

from ..config import chosen_model, provider_env
from ..contract import AgentContract
from ..scenario import Scenario
from ..session import Stage
from ..world.runtime import Call
from .targets import Target

DONE = "[DONE]"
STUCK = "[STUCK]"

FINISHED = "finished"
GAVE_UP = "gave-up"
RAN_OUT = "ran-out-of-turns"


@dataclass
class Exchange:
    speaker: str
    text: str


@dataclass
class Transcript:
    """What happened, in the two forms grading needs: what was said and what was done."""

    exchanges: list[Exchange] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    ended: str = ""
    spent_usd: float = 0.0

    def spoken(self) -> str:
        return "\n".join(f"{turn.speaker}: {turn.text}" for turn in self.exchanges)

    def actions(self) -> str:
        if not self.calls:
            return "(the agent called no tools at all)"
        lines = []
        for call in self.calls:
            outcome = (
                "refused" if call.refused else ("crashed" if not call.ok else "ok")
            )
            lines.append(
                f"{call.name}({call.arguments}) -> {outcome}: {call.error or call.result}"
            )
        return "\n".join(lines)

    def crashed(self) -> list[Call]:
        """Calls that failed for our reasons rather than the world's.

        Reported separately and never counted against the agent. A run over a world that fell
        over says nothing about the agent, and scoring it as a failure is how a harness invents
        findings.
        """
        return [call for call in self.calls if not call.ok and not call.refused]


# What the simulated person is asked for on the turn that has no conversation behind it. It says
# which part they are playing, because that is exactly what is ambiguous here: a model handed a
# system prompt about an agent, and asked to speak with nothing preceding it, will sometimes reply
# as the agent instead of to it.
OPENING = (
    "The conversation is starting and you speak first. You are the person making contact, not "
    "the agent being contacted. Say what you came to say, in your own words, and nothing else. "
    "Do not offer to look anything up, do not answer on their behalf, and do not greet them and "
    "wait: say the thing you actually want."
)

# How an opening turn reads when the part has been swapped. Offers of help, not requests for it.
_AS_THE_AGENT = (
    "let me ",
    "i'll look",
    "i will look",
    "i'd be happy to look",
    "i can help you with that",
    "how can i help",
    "how may i help",
    "what would you like to know",
    "i'll check",
    "i will check",
    "let me check",
    "sure! let me",
)


def _answered_as_the_agent(said: str) -> bool:
    """Whether the opening line is the agent's part rather than the person's."""
    opening = said.strip().lower()
    return any(mark in opening for mark in _AS_THE_AGENT)


def customer_prompt(
    scenario: Scenario, contract: AgentContract, written: str = ""
) -> str:
    """The simulated person, from the prompt the harness wrote for this agent.

    The prompt belongs to the environment, not to this loop: it is written once for the agent and
    each scenario fills its slots. Only the ending convention is added here, because it is how
    this particular loop knows a conversation is over.
    """
    from ..simulator import fill

    if written:
        filled, _missing = fill(written, scenario.slots())
    else:
        # No simulator prompt was written, which the environment gate refuses for a
        # conversational agent. Kept minimal rather than inventing a character.
        filled = (
            f"You are contacting {contract.agent}, which is: {contract.one_liner}\n\n"
            f"WHAT YOU ARE HERE TO DO:\n{scenario.instruction}"
        )
    return (
        filled
        + "\n\nWhen you have got what you came for, or accepted that you cannot, say the one "
        f"line you would actually say to end it, then {DONE} on a line of its own. If the agent "
        f"is going in circles and you would give up, do the same with {STUCK}.\n"
        "Do not end while the agent is waiting on you. A refusal that offers you two "
        "alternatives, or asks you a question, is not the end of the conversation: answer it, "
        "and end after that."
    )


async def converse(
    target: Target,
    scenario: Scenario,
    contract: AgentContract,
    *,
    world_root: Any = None,
    model: str | None = None,
    on_exchange: Callable[[Exchange], Any] | None = None,
) -> Transcript:
    """Run one scenario as a conversation and return what happened."""
    transcript = Transcript()
    from ..simulator import load_simulator_prompt

    customer = Stage(
        ClaudeAgentOptions(
            system_prompt=customer_prompt(
                scenario,
                contract,
                load_simulator_prompt(world_root) if world_root else "",
            ),
            allowed_tools=[],
            setting_sources=[],
            max_turns=1,
            model=chosen_model(model),
            env=provider_env(model),
        ),
        name="customer",
    )

    def record(speaker: str, text: str) -> None:
        exchange = Exchange(speaker, text)
        transcript.exchanges.append(exchange)
        if on_exchange:
            on_exchange(exchange)

    await target.open()
    await customer.__aenter__()
    try:
        # The customer opens, in its own words. The scenario's instruction is written *about*
        # the caller ("orders two burgers and asks for..."), so speaking it verbatim would hand
        # the agent a stage direction instead of a person.
        opening = await customer.say(OPENING)
        said = opening.text.strip() or scenario.instruction
        if _answered_as_the_agent(said):
            # The opening turn is the one with no conversation behind it, and a model asked to
            # speak into that gap will sometimes take the other part: it offers to look something
            # up, the agent replies that no question was asked, and the run fails for a reason
            # that has nothing to do with the agent. The instruction is the fallback, because a
            # blunt version of the right question tests more than a fluent version of the wrong
            # one.
            said = scenario.instruction
        record("customer", said)
        for _turn in range(max(1, scenario.max_turns)):
            reply = await target.say(said)
            record("agent", reply or "(said nothing)")

            turn = await customer.say(reply or "(no response)")
            said = turn.text.strip()
            if DONE in said or STUCK in said:
                transcript.ended = GAVE_UP if STUCK in said else FINISHED
                # The closing line comes with the sentinel, and is kept. Breaking on the marker
                # alone threw it away, so every conversation ended on the agent's turn with
                # nothing after it: a transcript that reads as cut off rather than finished,
                # and no way to tell a person who left satisfied from one who was still waiting.
                closing = said.replace(DONE, "").replace(STUCK, "").strip()
                if closing:
                    record("customer", closing)
                break
            record("customer", said)
        else:
            transcript.ended = RAN_OUT
    finally:
        await customer.__aexit__(None, None, None)
        await target.close()

    transcript.calls = list(target.world.calls) if hasattr(target, "world") else []
    transcript.spent_usd = target.spent_usd + customer.spent_usd
    return transcript
