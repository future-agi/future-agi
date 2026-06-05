"""Bridge from the platform's red-team layer to the agent-learning-kit SDK (TH-5642).

The user asked us to use ``agent-learning-kit`` as the common engine for simulation
and optimization rather than maintaining a parallel implementation. This module wires
the ONE flow where the kit is genuinely the engine and we can verify it end-to-end in
this runtime: **red-team**. Our platform-native adversarial scenario catalogue
(``simulate.services.adversarial_scenarios``) is mapped onto the kit's
``build_redteam_manifest`` / ``redteam_manifest`` campaign path, so the categories we
generate become real attack campaigns scored by the kit's agent-report evaluator.

Design notes:
- The kit is an optional dependency. ``is_available()`` reports whether it imports; the
  build/run helpers raise a clear ``AgentLearningUnavailable`` if it is missing rather
  than failing deep inside an import. This keeps the platform importable even where the
  kit is not installed.
- ``build_redteam_manifest_for_agent`` is pure (no I/O) and returns the manifest dict —
  cheap to unit-test and to inspect.
- ``run_redteam_for_agent`` executes the campaign. ``dry_run=True`` (the default)
  materializes + scores the campaign without needing live LLM credentials, which is the
  verifiable path; pass ``dry_run=False`` once provider credentials are configured.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from .adversarial_scenarios import (
    adversarial_category_keys,
    generate_adversarial_scenarios,
)

# Map our adversarial category keys -> the kit's canonical attack identifiers. The kit
# accepts open-ended attack strings and normalises them, but using its canonical ids
# (prompt_injection / jailbreak / data_exfiltration / secret_exfiltration) keeps the
# generated campaigns aligned with its attack-pack taxonomy.
_CATEGORY_TO_ATTACK: dict[str, str] = {
    "jailbreak": "jailbreak",
    "prompt_injection": "prompt_injection",
    "pii_extraction": "data_exfiltration",
    "toxicity_bait": "toxicity",
    "out_of_scope": "out_of_scope",
    "hallucination_pressure": "hallucination",
}

# Channel -> surfaces the campaign should probe. Voice agents are still driven over the
# same text campaign engine (local_text); the surface set is what differs.
_CHANNEL_SURFACES: dict[str, tuple[str, ...]] = {
    "chat": ("instruction", "tool"),
    "voice": ("instruction", "tool", "memory"),
}


class AgentLearningUnavailable(RuntimeError):
    """Raised when an agent-learning-kit flow is requested but the kit is not installed."""


def is_available() -> bool:
    """True if ``agent_learning`` can be imported in this runtime."""
    try:
        import agent_learning  # noqa: F401

        return True
    except Exception:
        return False


def _kit_submodule(attr: str) -> Any:
    try:
        import agent_learning

        return getattr(agent_learning, attr)
    except Exception as exc:  # pragma: no cover - exercised via is_available() in tests
        raise AgentLearningUnavailable(
            "agent-learning-kit is not installed; install it to run agent-learning flows "
            "(pip install -e <agent-learning-kit> --no-deps)."
        ) from exc


def _redteam_module() -> Any:
    return _kit_submodule("redteam")


def _simulate_module() -> Any:
    return _kit_submodule("simulate")


def _optimize_module() -> Any:
    return _kit_submodule("optimize")


def _attacks_for_categories(categories: Sequence[str]) -> list[str]:
    out: list[str] = []
    for key in categories:
        attack = _CATEGORY_TO_ATTACK.get(key, key)
        if attack not in out:
            out.append(attack)
    return out


def _scenario_from_specs(
    name: str, agent_description: str, specs: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Fold our adversarial specs into a kit ``scenario`` mapping.

    The persona prompts describe how the adversary attacks; the success_criteria are the
    safe behaviours the agent must hold. We carry both so the campaign and its evaluator
    have the platform's intent, not just generic defaults.
    """
    return {
        "name": name,
        "description": f"Adversarial red-team of: {agent_description}".strip(),
        "objectives": [s["success_criteria"] for s in specs],
        "adversary_personas": [
            {
                "category": s["category"],
                "name": s["name"],
                "prompt": s["persona_prompt"],
                "success_criteria": s["success_criteria"],
            }
            for s in specs
        ],
    }


def _agent_mapping(
    *,
    instructions: str | None,
    scripted_responses: Sequence[str] | None,
    agent_spec: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Resolve the agent-under-test mapping for the manifest.

    Precedence: an explicit ``agent_spec`` (passed straight through) > scripted responses
    (no LLM, the credential-free verifiable path) > an ``llm`` agent built from the
    system ``instructions`` (used for real runs once credentials are present).
    """
    if agent_spec is not None:
        return dict(agent_spec)
    if scripted_responses:
        return {
            "type": "scripted",
            "responses": [{"content": r} for r in scripted_responses],
        }
    return {
        "type": "llm",
        "instructions": instructions or "You are a helpful assistant.",
    }


def build_redteam_manifest_for_agent(
    *,
    name: str,
    agent_description: str,
    categories: Sequence[str] | None = None,
    channel: str = "chat",
    domain: str | None = None,
    instructions: str | None = None,
    scripted_responses: Sequence[str] | None = None,
    agent_spec: Mapping[str, Any] | None = None,
    threshold: float = 0.9,
    min_turns: int = 3,
    max_turns: int = 3,
) -> dict[str, Any]:
    """Build a kit red-team manifest from our adversarial catalogue. Pure (no I/O)."""
    if not name:
        raise ValueError("name is required")
    cats = list(categories or adversarial_category_keys())
    specs = generate_adversarial_scenarios(
        agent_description, categories=cats, domain=domain
    )
    # generate_adversarial_scenarios drops unknown keys; reconcile so attacks line up.
    present = [s["category"] for s in specs]
    if not present:
        raise ValueError("no valid adversarial categories were resolved")
    attacks = _attacks_for_categories(present)
    surfaces = _CHANNEL_SURFACES.get(channel, _CHANNEL_SURFACES["chat"])

    rt = _redteam_module()
    return rt.build_redteam_manifest(
        name=name,
        attacks=attacks,
        surfaces=list(surfaces),
        channels=[channel],
        scenario=_scenario_from_specs(name, agent_description, specs),
        agent=_agent_mapping(
            instructions=instructions,
            scripted_responses=scripted_responses,
            agent_spec=agent_spec,
        ),
        threshold=threshold,
        min_turns=min_turns,
        max_turns=max_turns,
    )


def _run_async(coro: Any) -> Any:
    """Run a coroutine from sync code, erroring clearly inside a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "run_redteam_for_agent() called inside a running event loop; await "
        "redteam_manifest(...) directly from async code instead."
    )


def run_redteam_for_agent(
    *,
    name: str,
    agent_description: str,
    categories: Sequence[str] | None = None,
    channel: str = "chat",
    domain: str | None = None,
    instructions: str | None = None,
    scripted_responses: Sequence[str] | None = None,
    agent_spec: Mapping[str, Any] | None = None,
    threshold: float = 0.9,
    min_turns: int = 3,
    max_turns: int = 3,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Build and execute a red-team campaign for an agent via the kit.

    Returns the kit's public red-team payload (``status``/``summary``/``redteam``/...).
    ``dry_run=True`` materializes + scores the campaign with no live LLM credentials and
    is the path verified in CI; set ``dry_run=False`` for a real provider-backed run.
    """
    manifest = build_redteam_manifest_for_agent(
        name=name,
        agent_description=agent_description,
        categories=categories,
        channel=channel,
        domain=domain,
        instructions=instructions,
        scripted_responses=scripted_responses,
        agent_spec=agent_spec,
        threshold=threshold,
        min_turns=min_turns,
        max_turns=max_turns,
    )
    rt = _redteam_module()
    return _run_async(rt.redteam_manifest(manifest, dry_run=dry_run))


# --------------------------------------------------------------------------- #
# Simulation — drive an agent through a task/scenario via the kit             #
# --------------------------------------------------------------------------- #
def build_simulation_manifest_for_agent(
    *,
    name: str,
    task_description: str,
    success_criteria: Sequence[str] = (),
    channel: str = "chat",
    instructions: str | None = None,
    scripted_responses: Sequence[str] | None = None,
    agent_spec: Mapping[str, Any] | None = None,
    scenario: Mapping[str, Any] | None = None,
    persona: Mapping[str, Any] | None = None,
    evaluation_config: Mapping[str, Any] | None = None,
    threshold: float = 0.7,
    min_turns: int = 1,
    max_turns: int = 1,
) -> dict[str, Any]:
    """Build a kit simulation manifest for an agent. Pure (no I/O).

    ``channel`` maps to the kit's ``modality`` (chat/voice); the agent-under-test is
    resolved with the same precedence as red-team (spec > scripted > llm).
    """
    if not name:
        raise ValueError("name is required")
    sim = _simulate_module()
    return sim.build_task_run_manifest(
        name=name,
        agent=_agent_mapping(
            instructions=instructions,
            scripted_responses=scripted_responses,
            agent_spec=agent_spec,
        ),
        task_description=task_description,
        success_criteria=list(success_criteria),
        scenario=dict(scenario) if scenario is not None else None,
        persona=dict(persona) if persona is not None else None,
        evaluation_config=dict(evaluation_config)
        if evaluation_config is not None
        else None,
        modality=channel,
        threshold=threshold,
        min_turns=min_turns,
        max_turns=max_turns,
    )


def run_simulation_for_agent(
    *,
    name: str,
    task_description: str,
    success_criteria: Sequence[str] = (),
    channel: str = "chat",
    instructions: str | None = None,
    scripted_responses: Sequence[str] | None = None,
    agent_spec: Mapping[str, Any] | None = None,
    scenario: Mapping[str, Any] | None = None,
    persona: Mapping[str, Any] | None = None,
    evaluation_config: Mapping[str, Any] | None = None,
    threshold: float = 0.7,
    min_turns: int = 1,
    max_turns: int = 1,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Build and execute a simulation for an agent via the kit.

    Returns the kit's public run payload (``status``/``summary``/...). ``dry_run=True``
    is the credential-free path verified in CI.
    """
    manifest = build_simulation_manifest_for_agent(
        name=name,
        task_description=task_description,
        success_criteria=success_criteria,
        channel=channel,
        instructions=instructions,
        scripted_responses=scripted_responses,
        agent_spec=agent_spec,
        scenario=scenario,
        persona=persona,
        evaluation_config=evaluation_config,
        threshold=threshold,
        min_turns=min_turns,
        max_turns=max_turns,
    )
    sim = _simulate_module()
    return _run_async(sim.run_manifest(manifest, dry_run=dry_run))


# --------------------------------------------------------------------------- #
# Optimization — search candidates and surface the winning fix                #
# --------------------------------------------------------------------------- #
def build_optimization_manifest_for_agent(
    *,
    name: str,
    agent_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    scenario: Mapping[str, Any] | None = None,
    threshold: float = 0.9,
    min_turns: int = 1,
    max_turns: int | None = None,
) -> dict[str, Any]:
    """Build a kit optimization manifest. Pure (no I/O).

    ``agent_candidates`` are the variants to search over; the kit scores each against
    ``evaluation_config`` and surfaces the best.
    """
    if not name:
        raise ValueError("name is required")
    if not agent_candidates:
        raise ValueError("agent_candidates must contain at least one candidate")
    opt = _optimize_module()
    return opt.build_task_optimization_manifest(
        name=name,
        agent_candidates=[dict(c) for c in agent_candidates],
        evaluation_config=dict(evaluation_config),
        scenario=dict(scenario) if scenario is not None else None,
        threshold=threshold,
        min_turns=min_turns,
        max_turns=max_turns,
    )


def optimize_and_apply_for_agent(
    *,
    name: str,
    agent_candidates: Sequence[Mapping[str, Any]],
    evaluation_config: Mapping[str, Any],
    scenario: Mapping[str, Any] | None = None,
    threshold: float = 0.9,
    min_turns: int = 1,
    max_turns: int | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Run optimization over agent candidates via the kit and return the result.

    The user's intent is "in optimization we directly apply the fix": the kit selects the
    best-scoring candidate; the returned payload (``status``/``summary``/...) carries the
    winning configuration for the platform to apply back onto the agent. ``dry_run=True``
    is the credential-free path verified in CI; ``optimize_manifest`` is synchronous.
    """
    manifest = build_optimization_manifest_for_agent(
        name=name,
        agent_candidates=agent_candidates,
        evaluation_config=evaluation_config,
        scenario=scenario,
        threshold=threshold,
        min_turns=min_turns,
        max_turns=max_turns,
    )
    opt = _optimize_module()
    return opt.optimize_manifest(manifest, dry_run=dry_run)
