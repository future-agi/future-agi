"""Prompt-optimisation SEARCH via the agent-learning-kit (Phase-10A Tier-1 cutover #1).

Replaces the platform-native search engine (``ee.agenthub.fix_your_agent.FixYourAgent``
candidate-generation + trial loop) with the kit's ``optimize.build_task_optimization_manifest``
/ ``optimize_manifest`` search, per the Phase-10A adoption audit (internal-docs/agent-trinity/
v1-program/phase10-platform-adoption/ADOPTION-AUDIT.md, mapping row #2).

Responsibility split (audit section 6, "Optimizer apply semantics"):
- KIT  = the search engine: scores candidates, returns winner + per-candidate history.
- PLATFORM = everything around it, unchanged: PromptTrial/TrialItemResult persistence
  (``store_optimization_results``), apply semantics (``optimizer_apply.py`` →
  PromptVersion), and the API/serializer result contract
  (``AgentPromptOptimiserRawResultSerializer``: history[]/best_prompt/final_score/...).

``map_kit_result_to_search_contract`` is therefore the heart of this module: it folds the
kit's optimization payload into the exact dict shape the legacy engine
(``ee.agent_opt.types.OptimizationResult.dict()``) produced, so every downstream consumer
(result storage, trial table, apply_trial) keeps working byte-compatibly.

The legacy engine stays callable behind ``run_agent_prompt_optimiser(..., _legacy=True)``
(or ``configuration.use_legacy_search``) until parity sign-off; deletion happens in a
later pass.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import structlog

from . import agent_learning_bridge as bridge

logger = structlog.get_logger(__name__)

# Credential-free default: the kit's local agent-report evaluator scores candidates
# without provider keys (verified by dry-run-style local execution in tests).
DEFAULT_EVALUATION_CONFIG: dict[str, Any] = {"metrics": [{"name": "task_completion"}]}


# --------------------------------------------------------------------------- #
# Base-agent resolution (moved from simulate.utils.agent_prompt_optimiser)     #
# --------------------------------------------------------------------------- #
def resolve_base_prompt(prompt_optimiser_run: Any) -> str:
    """Best-effort current system prompt for the agent under optimisation."""
    te = prompt_optimiser_run.test_execution
    agent_def = getattr(te, "agent_definition", None) or getattr(
        getattr(te, "run_test", None), "agent_definition", None
    )
    for attr in ("system_prompt", "prompt", "instructions"):
        val = getattr(agent_def, attr, None)
        if val:
            return str(val)
    return "You are a helpful assistant."


def resolve_base_agent(prompt_optimiser_run: Any) -> dict[str, Any]:
    """Build the WHOLE-agent base config from the run's agent definition.

    The optimisation searches the full agent config (model, provider identity,
    instructions, ...), not just the prompt; this is the seed every search-space
    patch applies to. Provider/assistant_id ride along so the winning config can
    be applied back to the provider-side agent.
    """
    te = prompt_optimiser_run.test_execution
    agent_def = getattr(te, "agent_definition", None) or getattr(
        getattr(te, "run_test", None), "agent_definition", None
    )
    base: dict[str, Any] = {
        "type": "llm",
        "name": getattr(agent_def, "agent_name", None) or "agent-under-optimisation",
        "model": (getattr(prompt_optimiser_run, "model", None) or "gpt-4o-mini"),
        "instructions": resolve_base_prompt(prompt_optimiser_run),
    }
    if agent_def is not None:
        if getattr(agent_def, "provider", None):
            base["provider"] = str(agent_def.provider)
        if getattr(agent_def, "assistant_id", None):
            base["assistant_id"] = str(agent_def.assistant_id)
    return base


# --------------------------------------------------------------------------- #
# Search-input resolution (pure)                                               #
# --------------------------------------------------------------------------- #
def build_search_inputs(prompt_optimiser_run: Any) -> dict[str, Any]:
    """Resolve kit search inputs from the run's configuration. Pure (no kit I/O).

    Configuration keys honoured (all optional):
    - ``base_agent``: full agent config the patches apply to (else resolved from
      the run's agent definition).
    - ``agent_candidates``: explicit full candidate configs (passed straight through).
    - ``candidate_prompts``: list of prompt strings → becomes a
      ``search_space["agent.instructions"]`` over baseline + candidates, i.e. the
      legacy "prompt variations" input expressed as a kit search space.
    - ``search_space``: dot-path search space over the whole agent (wins over
      candidate_prompts when both are present, merged with prompts if disjoint).
    - ``evaluation_config``: kit evaluation config (defaults to task_completion).
    """
    config = prompt_optimiser_run.configuration or {}
    base_agent = dict(
        config.get("base_agent") or resolve_base_agent(prompt_optimiser_run)
    )

    candidates = [
        dict(c)
        for c in (config.get("agent_candidates") or [])
        if isinstance(c, Mapping)
    ]
    if not candidates:
        candidates = [dict(base_agent)]

    search_space: dict[str, list[Any]] = {
        str(k): list(v) for k, v in (config.get("search_space") or {}).items()
    }
    candidate_prompts = [str(p) for p in (config.get("candidate_prompts") or []) if p]
    if candidate_prompts and "agent.instructions" not in search_space:
        baseline_prompt = str(base_agent.get("instructions") or "")
        prompts: list[str] = []
        for prompt in [baseline_prompt, *candidate_prompts]:
            if prompt and prompt not in prompts:
                prompts.append(prompt)
        search_space["agent.instructions"] = prompts

    return {
        "base_agent": base_agent,
        "agent_candidates": candidates,
        "search_space": search_space or None,
        "evaluation_config": dict(
            config.get("evaluation_config") or DEFAULT_EVALUATION_CONFIG
        ),
        "threshold": float(config.get("threshold", 0.9)),
        # The kit's local engine executes candidates credential-free; dry_run=True
        # only validates the manifest and returns NO history (no trials → nothing
        # to apply), so the search path defaults to a real local run.
        "dry_run": bool(config.get("dry_run", False)),
    }


# --------------------------------------------------------------------------- #
# Kit result → legacy search contract (pure)                                   #
# --------------------------------------------------------------------------- #
def map_kit_result_to_search_contract(
    kit_result: Mapping[str, Any] | None,
    base_agent: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fold the kit's optimization payload into the legacy engine's result contract.

    Output shape == ``ee.agent_opt.types.OptimizationResult.dict()`` as consumed by
    ``store_optimization_results`` and ``AgentPromptOptimiserRawResultSerializer``:
    ``{best_prompt, history[{prompt, average_score, is_baseline, individual_results,
    metadata}], final_score, best_index, stepper_state}``.

    Semantics:
    - history preserves the kit's candidate order; the entry with an empty
      ``candidate_patch`` is the baseline (legacy contract: baseline first when present).
    - ``individual_results`` is ``{}``: the kit searches at candidate level, not per
      platform CallExecution; per-call rows remain a platform concern (TrialItemResult
      stays empty for kit-run searches, exactly like the explicit kit optimiser type).
    - ``metadata.candidate_config`` carries the WHOLE winning/candidate agent config
      (baseline overlaid with the patch) so apply can push more than the prompt.
    """
    result = dict(kit_result or {})
    optimization = dict(result.get("optimization") or {})
    history_in = list(optimization.get("history") or [])
    base = dict(base_agent or {})
    baseline_prompt = str(base.get("instructions") or "")

    best_id = optimization.get("best_candidate_id") or (
        (result.get("summary") or {}).get("best_candidate_id")
    )

    history_out: list[dict[str, Any]] = []
    best_index = 0
    best_score_seen = float("-inf")
    best_by_id_found = False

    for idx, entry in enumerate(history_in):
        entry = dict(entry or {})
        patch = dict(entry.get("candidate_patch") or entry.get("patch") or {})
        patch_agent = dict(patch.get("agent") or {})
        prompt = str(patch_agent.get("instructions") or "") or baseline_prompt
        is_baseline = not patch
        score = float(entry.get("score") or 0.0)
        candidate_config = {**base, **patch_agent}

        history_out.append(
            {
                "prompt": prompt,
                "average_score": score,
                "is_baseline": is_baseline,
                "individual_results": {},
                "metadata": {
                    "source": "agent_learning_kit",
                    "candidate_id": entry.get("candidate_id"),
                    "selected": bool(best_id) and entry.get("candidate_id") == best_id,
                    "metrics": dict(entry.get("metrics") or {}),
                    "candidate_config": candidate_config,
                },
            }
        )

        if best_id and entry.get("candidate_id") == best_id:
            best_index = idx
            best_by_id_found = True
        elif not best_by_id_found and score > best_score_seen:
            best_score_seen = score
            best_index = idx

    best_prompt = history_out[best_index]["prompt"] if history_out else baseline_prompt
    final_score = (
        float(optimization.get("final_score") or 0.0)
        if optimization.get("final_score") is not None
        else (history_out[best_index]["average_score"] if history_out else 0.0)
    )

    return {
        "best_prompt": best_prompt,
        "history": history_out,
        "final_score": final_score,
        "best_index": best_index,
        "stepper_state": None,  # kit searches are single-shot; no resume stepper
        # Additive (serializer-declared / DB-only) keys — never read by legacy code:
        "trials_run": len(history_out),
        "engine": "agent_learning_kit",
        "kit_status": result.get("status"),
        "kit_summary": dict(result.get("summary") or {}),
        "kit_best_config": dict(optimization.get("best_config") or {}),
    }


# --------------------------------------------------------------------------- #
# Orchestrator                                                                 #
# --------------------------------------------------------------------------- #
def run_prompt_optimisation_search(prompt_optimiser_run: Any) -> dict[str, Any]:
    """Execute the prompt-optimisation SEARCH via the kit.

    Returns the legacy-contract result dict (see map_kit_result_to_search_contract).
    Persistence (trials, run.result/status) and apply stay with the caller — the
    platform. Raises ``AgentLearningUnavailable`` if the kit is not installed.
    """
    inputs = build_search_inputs(prompt_optimiser_run)
    kit_result = bridge.optimize_and_apply_for_agent(
        name=str(prompt_optimiser_run.id),
        agent_candidates=inputs["agent_candidates"],
        evaluation_config=inputs["evaluation_config"],
        search_space=inputs["search_space"],
        base_agent=inputs["base_agent"],
        threshold=inputs["threshold"],
        dry_run=inputs["dry_run"],
    )
    contract = map_kit_result_to_search_contract(kit_result, inputs["base_agent"])
    logger.info(
        "prompt_optimisation_search_via_kit_completed",
        run_id=str(prompt_optimiser_run.id),
        kit_status=contract.get("kit_status"),
        trials_run=contract.get("trials_run"),
        final_score=contract.get("final_score"),
    )
    return contract
