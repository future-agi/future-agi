"""The prompt that drives the simulated person, and filling it in for one scenario.

Written once for a conversational agent with its slots left open, so a scenario supplies only
what differs: who this person is this time and what they are trying to do. What a good one says
is judgement and lives in the build skill; what is here is only saving it, reading it back, and
substituting a scenario's values into it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SIMULATOR = "simulator_prompt.md"


def save_simulator_prompt(prompt: str, destination: Path) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / SIMULATOR
    path.write_text(prompt, encoding="utf-8")
    return path


def load_simulator_prompt(destination: Path) -> str:
    path = Path(destination) / SIMULATOR
    return path.read_text(encoding="utf-8") if path.exists() else ""


def variables_in(prompt: str) -> set[str]:
    """The slots a scenario has to fill.

    Written ``{{ name }}``, so the prompt stays readable as prose and a missing value is caught
    before a call is placed rather than appearing verbatim in what the simulated caller says.
    """
    import re

    return set(re.findall(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", prompt))


def fill(prompt: str, values: dict[str, Any]) -> tuple[str, list[str]]:
    """The simulator prompt for one scenario, and anything it left unfilled."""
    import re

    missing = sorted(variables_in(prompt) - set(values))

    def swap(match: re.Match[str]) -> str:
        return str(values.get(match.group(1), match.group(0)))

    filled = re.sub(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}", swap, prompt)
    return filled, missing


def validate_simulator_prompt(prompt: str, *, require_persona: bool = False) -> list[str]:
    """Problems that make a simulator prompt unusable.

    Deliberately thin. What a good simulator prompt says is judgement, and belongs in the skill;
    what can be checked here is that it exists and that a scenario has somewhere to put its
    instruction, since a prompt with no variables is the same prompt for every scenario.
    """
    problems: list[str] = []
    if len(prompt.strip()) < 80:
        problems.append("too short to be a simulator prompt")
    if not variables_in(prompt):
        problems.append(
            "no variables: without a slot for the scenario's instruction, every scenario would "
            "run the same conversation. Write them as {{ instruction }}"
        )
    if require_persona and "persona" not in variables_in(prompt):
        problems.append(
            "no persona slot: conversational scenarios need {{ persona }} so each caller's "
            "identity and communication profile is explicit"
        )
    return problems
