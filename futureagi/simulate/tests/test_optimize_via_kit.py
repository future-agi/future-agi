"""Tests for the prompt-optimisation search cutover to agent-learning-kit
(Phase-10A Tier-1 cutover #1; pattern: TH-5642 / test_agent_learning_bridge.py).

Three layers:
- PURE MAPPING (kit mocked): platform run config -> kit search inputs, and kit
  optimization payload -> the legacy ``OptimizationResult.dict()`` contract.
- E2E (kit real, credential-free): the full ``run_agent_prompt_optimiser`` flow
  executes a REAL local kit optimization over scripted candidates and persists the
  legacy-contract result — including REAL PromptTrial rows when the test DB is up.
- PARITY (old vs new): the legacy engine's genuine result artifact
  (``ee.agent_opt.types.OptimizationResult``) side-by-side with the kit-mapped result
  on the same fixture intent. The engines differ by design (legacy = LLM-judge trial
  loop, kit = local agent-report evaluator), so scores are NOT asserted equal —
  parity is contract SHAPE + semantics, per the Phase-10A audit guidance: same
  consumer-visible keys/types, baseline-first semantics, best-selection invariant,
  and both results accepted by the same serializer + persistence path.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models.agent_prompt_optimiser_run import AgentPromptOptimiserRun
from simulate.services import agent_learning_bridge as bridge
from simulate.services import optimize_via_kit as ovk
from simulate.utils import agent_prompt_optimiser as apo

# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                           #
# --------------------------------------------------------------------------- #

# The exact history-entry keys the legacy engine (ee.agent_opt.types.
# IterationHistory) emitted and the platform consumes (store_optimization_results
# + AgentPromptOptimiserRawTrialResultSerializer).
LEGACY_TRIAL_KEYS = {"prompt", "average_score", "individual_results"}
# Top-level legacy contract keys (OptimizationResult.dict()).
LEGACY_RESULT_KEYS = {
    "best_prompt",
    "history",
    "final_score",
    "best_index",
    "stepper_state",
}


def _fake_run(optimiser_type, configuration=None):
    run = SimpleNamespace(
        id="run-1",
        optimiser_type=optimiser_type,
        configuration=configuration or {},
        model="gpt-4o-mini",
        result=None,
        status=None,
        test_execution=SimpleNamespace(agent_definition=None, run_test=None),
    )
    run.save = lambda **kw: None
    run.mark_as_failed = lambda msg=None: setattr(run, "_failed", msg)
    return run


def _kit_payload(history, best_id=None, final_score=None, status="passed"):
    return {
        "status": status,
        "summary": {"optimization_score": final_score, "best_candidate_id": best_id},
        "optimization": {
            "history": history,
            "best_candidate_id": best_id,
            "final_score": final_score,
            "best_config": {"agent": {"type": "llm"}},
        },
    }


def _assert_legacy_contract(result, *, source):
    """Assert a result dict satisfies the legacy search-result contract."""
    missing = LEGACY_RESULT_KEYS - set(result)
    assert not missing, f"{source} result missing legacy keys: {missing}"
    assert isinstance(result["best_prompt"], str)
    assert isinstance(result["history"], list)
    assert isinstance(result["final_score"], (int, float))
    assert isinstance(result["best_index"], int)
    for entry in result["history"]:
        missing_entry = LEGACY_TRIAL_KEYS - set(entry)
        assert not missing_entry, f"{source} trial missing keys: {missing_entry}"
        assert isinstance(entry["prompt"], str)
        assert isinstance(entry["average_score"], (int, float))
        assert 0.0 <= float(entry["average_score"]) <= 1.0
        assert isinstance(entry["individual_results"], dict)
    if result["history"]:
        # Best-selection invariant: best_prompt is the prompt at best_index.
        assert (
            result["best_prompt"] == result["history"][result["best_index"]]["prompt"]
        )


# --------------------------------------------------------------------------- #
# Pure mapping: run config -> kit search inputs                                #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_candidate_prompts_become_instruction_search_space():
    run = _fake_run(
        AgentPromptOptimiserRun.OptimiserType.RANDOM_SEARCH,
        {
            "base_agent": {"type": "llm", "instructions": "Base prompt."},
            "candidate_prompts": ["Better prompt.", "Base prompt.", "Best prompt."],
        },
    )
    inputs = ovk.build_search_inputs(run)
    # Baseline first, deduped against the candidates.
    assert inputs["search_space"]["agent.instructions"] == [
        "Base prompt.",
        "Better prompt.",
        "Best prompt.",
    ]
    assert inputs["agent_candidates"] == [
        {"type": "llm", "instructions": "Base prompt."}
    ]
    assert inputs["base_agent"]["instructions"] == "Base prompt."
    # The search path runs the engine for real (dry_run only validates).
    assert inputs["dry_run"] is False


@pytest.mark.unit
def test_explicit_search_space_wins_over_candidate_prompts():
    run = _fake_run(
        AgentPromptOptimiserRun.OptimiserType.GEPA,
        {
            "base_agent": {"type": "llm", "instructions": "Base."},
            "candidate_prompts": ["ignored"],
            "search_space": {"agent.instructions": ["A", "B"], "agent.model": ["m1"]},
        },
    )
    inputs = ovk.build_search_inputs(run)
    assert inputs["search_space"]["agent.instructions"] == ["A", "B"]
    assert inputs["search_space"]["agent.model"] == ["m1"]


@pytest.mark.unit
def test_base_agent_resolved_from_run_when_not_configured():
    run = _fake_run(AgentPromptOptimiserRun.OptimiserType.GEPA, {})
    inputs = ovk.build_search_inputs(run)
    base = inputs["base_agent"]
    assert base["type"] == "llm"
    assert base["model"] == "gpt-4o-mini"
    assert base["instructions"]  # falls back to the default system prompt
    assert inputs["evaluation_config"] == ovk.DEFAULT_EVALUATION_CONFIG


# --------------------------------------------------------------------------- #
# Pure mapping: kit payload -> legacy search contract                          #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_kit_history_maps_to_legacy_contract():
    base_agent = {"type": "llm", "instructions": "Base prompt.", "model": "gpt-4o-mini"}
    payload = _kit_payload(
        history=[
            {"candidate_id": "c-base", "score": 0.61, "candidate_patch": {}},
            {
                "candidate_id": "c-win",
                "score": 0.94,
                "candidate_patch": {"agent": {"instructions": "Winning prompt."}},
                "metrics": {"task_completion": 1.0},
            },
        ],
        best_id="c-win",
        final_score=0.94,
    )
    result = ovk.map_kit_result_to_search_contract(payload, base_agent)

    _assert_legacy_contract(result, source="kit-mapped")
    assert [e["is_baseline"] for e in result["history"]] == [True, False]
    # Baseline (empty patch) inherits the base prompt.
    assert result["history"][0]["prompt"] == "Base prompt."
    assert result["best_index"] == 1
    assert result["best_prompt"] == "Winning prompt."
    assert result["final_score"] == 0.94
    # Kit searches at candidate level — per-call rows stay a platform concern.
    assert all(e["individual_results"] == {} for e in result["history"])
    # Whole-agent apply: candidate_config = base overlaid with the patch.
    win_meta = result["history"][1]["metadata"]
    assert win_meta["candidate_config"]["instructions"] == "Winning prompt."
    assert win_meta["candidate_config"]["model"] == "gpt-4o-mini"
    assert win_meta["selected"] is True
    assert result["history"][0]["metadata"]["selected"] is False
    # Kit searches are single-shot — no resume stepper (legacy key still present).
    assert result["stepper_state"] is None


@pytest.mark.unit
def test_best_falls_back_to_max_score_without_best_id():
    base_agent = {"type": "llm", "instructions": "Base."}
    payload = _kit_payload(
        history=[
            {"candidate_id": "a", "score": 0.5, "candidate_patch": {}},
            {
                "candidate_id": "b",
                "score": 0.8,
                "candidate_patch": {"agent": {"instructions": "B"}},
            },
            {
                "candidate_id": "c",
                "score": 0.7,
                "candidate_patch": {"agent": {"instructions": "C"}},
            },
        ],
        best_id=None,
        final_score=None,
    )
    result = ovk.map_kit_result_to_search_contract(payload, base_agent)
    assert result["best_index"] == 1
    assert result["best_prompt"] == "B"
    assert result["final_score"] == 0.8


@pytest.mark.unit
def test_empty_kit_history_maps_to_empty_contract():
    base_agent = {"type": "llm", "instructions": "Base."}
    result = ovk.map_kit_result_to_search_contract({"status": "passed"}, base_agent)
    _assert_legacy_contract(result, source="kit-mapped-empty")
    assert result["history"] == []
    assert result["best_prompt"] == "Base."
    assert result["trials_run"] == 0


@pytest.mark.unit
def test_orchestrator_passes_inputs_to_bridge(monkeypatch):
    run = _fake_run(
        AgentPromptOptimiserRun.OptimiserType.RANDOM_SEARCH,
        {
            "base_agent": {"type": "llm", "instructions": "Base."},
            "candidate_prompts": ["Alt."],
            "threshold": 0.8,
        },
    )
    captured = {}

    def fake_optimize(**kwargs):
        captured.update(kwargs)
        return _kit_payload(
            history=[{"candidate_id": "x", "score": 0.9, "candidate_patch": {}}],
            best_id="x",
            final_score=0.9,
        )

    monkeypatch.setattr(ovk.bridge, "optimize_and_apply_for_agent", fake_optimize)
    result = ovk.run_prompt_optimisation_search(run)

    assert captured["name"] == "run-1"
    assert captured["search_space"] == {"agent.instructions": ["Base.", "Alt."]}
    assert captured["base_agent"]["instructions"] == "Base."
    assert captured["threshold"] == 0.8
    assert captured["dry_run"] is False
    _assert_legacy_contract(result, source="orchestrator")


# --------------------------------------------------------------------------- #
# API contract: kit-mapped results satisfy the response serializer             #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_kit_mapped_result_satisfies_raw_result_serializer():
    from simulate.serializers.agent_prompt_optimiser import (
        AgentPromptOptimiserRawResultSerializer,
    )

    base_agent = {"type": "llm", "instructions": "Base."}
    payload = _kit_payload(
        history=[
            {"candidate_id": "a", "score": 0.4, "candidate_patch": {}},
            {
                "candidate_id": "b",
                "score": 0.9,
                "candidate_patch": {"agent": {"instructions": "Win"}},
            },
        ],
        best_id="b",
        final_score=0.9,
    )
    result = ovk.map_kit_result_to_search_contract(payload, base_agent)
    ser = AgentPromptOptimiserRawResultSerializer(data=result)
    assert ser.is_valid(), ser.errors
    rendered = ser.validated_data
    assert rendered["best_prompt"] == "Win"
    assert len(rendered["history"]) == 2


# --------------------------------------------------------------------------- #
# E2E — real kit, credential-free (scripted agents, local engine)              #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_e2e_kit_search_through_platform_flow_produces_legacy_contract():
    """run_agent_prompt_optimiser (GEPA type → kit cutover path) executes a REAL
    local kit optimization and stores the legacy-contract result + trials."""
    run = _fake_run(
        AgentPromptOptimiserRun.OptimiserType.GEPA,
        {
            "agent_candidates": [
                {"type": "scripted", "responses": [{"content": "I don't know."}]},
            ],
            "base_agent": {
                "type": "scripted",
                "instructions": "Base prompt.",
                "responses": [{"content": "I don't know."}],
            },
            "search_space": {
                "agent.responses": [
                    [{"content": "I don't know."}],
                    [{"content": "Sure! Our hours are 9-5 Mon-Fri."}],
                ]
            },
            "evaluation_config": {"metrics": [{"name": "task_completion"}]},
        },
    )
    stored = {}

    def capture_store(prompt_optimiser_run, result_dict, *, starting_trial_number=0):
        stored["result"] = result_dict
        stored["starting_trial_number"] = starting_trial_number

    with (
        patch.object(apo.AgentPromptOptimiserRun.objects, "get", return_value=run),
        patch.object(apo, "store_optimization_results", side_effect=capture_store),
    ):
        apo.run_agent_prompt_optimiser("run-1")

    assert run.status == AgentPromptOptimiserRun.Status.COMPLETED, getattr(
        run, "_failed", None
    )
    _assert_legacy_contract(run.result, source="e2e-kit")
    # Real kit artifacts: a non-empty candidate history with kit identifiers.
    assert run.result["history"], "real kit run must produce candidate history"
    assert all(
        e["metadata"]["source"] == "agent_learning_kit" for e in run.result["history"]
    )
    assert run.result["engine"] == "agent_learning_kit"
    assert run.result["kit_status"] in ("passed", "failed")
    # Trials flow through the SAME persistence entry point the legacy engine used.
    assert stored["result"] is run.result
    assert stored["starting_trial_number"] == 0


@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_e2e_kit_search_persists_prompt_trials_in_db(db, organization, workspace, user):
    """Full DB-verified E2E (TH-5642 pattern): REAL kit search through the REAL
    platform flow persists PromptTrial rows and the run result in PostgreSQL."""
    from simulate.models import (
        AgentDefinition,
        AgentOptimiser,
        AgentOptimiserRun,
        PromptTrial,
        RunTest,
        SimulatorAgent,
        TestExecution,
    )

    agent_definition = AgentDefinition.objects.create(
        agent_name="Kit Cutover Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+1234567890",
        inbound=True,
        organization=organization,
        workspace=workspace,
    )
    simulator_agent = SimulatorAgent.objects.create(
        name="Kit Cutover Simulator",
        prompt="You are a test simulator",
        organization=organization,
        workspace=workspace,
    )
    run_test = RunTest.objects.create(
        name="Kit Cutover Run",
        agent_definition=agent_definition,
        simulator_agent=simulator_agent,
        organization=organization,
        workspace=workspace,
    )
    test_execution = TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.COMPLETED,
        simulator_agent=simulator_agent,
        agent_definition=agent_definition,
    )
    agent_optimiser = AgentOptimiser.objects.create(
        name="Kit Cutover Optimiser", description="", configuration={}
    )
    agent_optimiser_run = AgentOptimiserRun.objects.create(
        agent_optimiser=agent_optimiser,
        status=AgentOptimiserRun.OptimiserStatus.COMPLETED,
        input_data={},
    )
    optimiser_run = AgentPromptOptimiserRun.objects.create(
        name="Kit Cutover Prompt Run",
        agent_optimiser=agent_optimiser,
        agent_optimiser_run=agent_optimiser_run,
        test_execution=test_execution,
        optimiser_type=AgentPromptOptimiserRun.OptimiserType.RANDOM_SEARCH,
        model="gpt-4o-mini",
        configuration={
            "base_agent": {
                "type": "scripted",
                "instructions": "Base prompt.",
                "responses": [{"content": "I don't know."}],
            },
            "candidate_prompts": ["Always state our opening hours clearly."],
            "evaluation_config": {"metrics": [{"name": "task_completion"}]},
        },
    )

    apo.run_agent_prompt_optimiser(str(optimiser_run.id))

    optimiser_run.refresh_from_db()
    assert optimiser_run.status == AgentPromptOptimiserRun.Status.COMPLETED, (
        optimiser_run.error_message
    )
    _assert_legacy_contract(optimiser_run.result, source="e2e-db")

    trials = list(
        PromptTrial.objects.filter(agent_prompt_optimiser_run=optimiser_run).order_by(
            "trial_number"
        )
    )
    assert len(trials) == len(optimiser_run.result["history"])
    assert trials, "kit search must persist PromptTrial rows"
    assert trials[0].is_baseline is True
    for trial, entry in zip(trials, optimiser_run.result["history"], strict=True):
        assert trial.prompt == entry["prompt"]
        assert trial.average_score == pytest.approx(entry["average_score"])
        assert trial.metadata.get("source") == "agent_learning_kit"
        assert "candidate_config" in trial.metadata


# --------------------------------------------------------------------------- #
# PARITY — legacy engine contract vs kit-mapped contract, identical fixtures   #
# --------------------------------------------------------------------------- #
def _legacy_result_dict():
    """A GENUINE legacy-engine result artifact built with the legacy engine's own
    types (ee.agent_opt.types.OptimizationResult) — the exact .dict() shape
    run_agent_prompt_optimiser stored before the cutover. The trial loop itself
    needs live LLM credentials, so the artifact is constructed, not searched;
    the contract (the part the platform consumes) is identical either way.
    """
    try:
        from ee.agent_opt.types import (
            EvaluationResult,
            IterationHistory,
            OptimizationResult,
        )
    except ImportError:
        pytest.skip("ee.agent_opt.types not importable (OSS checkout)")

    return OptimizationResult(
        best_prompt="Winning prompt.",
        history=[
            IterationHistory(
                prompt="Base prompt.",
                average_score=0.61,
                individual_results={
                    "ce-1": EvaluationResult(score=0.61, reason="baseline", metadata={})
                },
            ),
            IterationHistory(
                prompt="Winning prompt.",
                average_score=0.94,
                individual_results={
                    "ce-1": EvaluationResult(score=0.94, reason="better", metadata={})
                },
            ),
        ],
        final_score=0.94,
        best_index=1,
    ).dict()


@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_parity_legacy_vs_kit_search_result_contract():
    """Side-by-side: OLD engine result vs NEW kit path on the same fixture intent
    (baseline 'Base prompt.' vs candidate 'Winning prompt.').

    Scores are NOT asserted equal: the legacy engine scores trials with the
    platform's LLM-judge eval engine while the kit scores candidates with its
    local agent-report evaluator — different engines, values may differ (audit
    guidance). Parity = the result CONTRACT: same consumer-visible keys/types,
    same semantics (baseline-first, best-selection invariant, scores in [0,1]),
    and both results pass the same serializer + persistence entry point.
    """
    from simulate.serializers.agent_prompt_optimiser import (
        AgentPromptOptimiserRawResultSerializer,
    )

    legacy = _legacy_result_dict()

    run = _fake_run(
        AgentPromptOptimiserRun.OptimiserType.RANDOM_SEARCH,
        {
            "base_agent": {"type": "llm", "instructions": "Base prompt."},
            "candidate_prompts": ["Winning prompt."],
            "evaluation_config": {"metrics": [{"name": "task_completion"}]},
        },
    )
    new = ovk.run_prompt_optimisation_search(run)  # REAL kit execution

    # 1) Both satisfy the legacy contract shape + semantics.
    _assert_legacy_contract(legacy, source="legacy")
    _assert_legacy_contract(new, source="kit")

    # 2) Every key the legacy contract exposes exists in the new result
    #    (the new result may ADD keys; it must not remove any).
    assert LEGACY_RESULT_KEYS <= set(new)
    for legacy_entry, new_entry in zip(legacy["history"], new["history"], strict=True):
        assert LEGACY_TRIAL_KEYS <= set(new_entry)
        assert type(new_entry["prompt"]) is type(legacy_entry["prompt"])
        assert isinstance(
            new_entry["average_score"], type(legacy_entry["average_score"])
        )

    # 3) Same fixture intent resolves to the same trials/prompt semantics:
    #    both searched exactly [baseline, candidate] in order.
    assert (
        [e["prompt"] for e in new["history"]]
        == [
            "Base prompt.",
            "Winning prompt.",
        ]
        == [e["prompt"] for e in legacy["history"]]
    )

    # 4) Both validate against the SAME response serializer (API contract).
    for label, payload in (("legacy", legacy), ("kit", new)):
        ser = AgentPromptOptimiserRawResultSerializer(data=payload)
        assert ser.is_valid(), f"{label}: {ser.errors}"

    # 5) Both flow through the SAME persistence entry point without error.
    #    (__wrapped__ bypasses @transaction.atomic so no DB is needed here.)
    with (
        patch.object(apo, "_build_lookup_maps", return_value=({}, {})),
        patch.object(apo, "PromptTrial") as trial_model,
    ):
        fake_run = SimpleNamespace(id="parity-run")
        apo.store_optimization_results.__wrapped__(fake_run, legacy)
        legacy_calls = trial_model.objects.create.call_count
        trial_model.reset_mock()
        apo.store_optimization_results.__wrapped__(fake_run, new)
        kit_calls = trial_model.objects.create.call_count
    assert legacy_calls == kit_calls == 2
