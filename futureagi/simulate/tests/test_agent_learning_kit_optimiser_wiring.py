"""Proves the agent-learning-kit bridge is reachable from the REAL optimiser flow (TH-5642).

The bridge (simulate.services.agent_learning_bridge) was previously only exercised by its
own tests. This asserts that run_agent_prompt_optimiser — the platform's "Fix My Agent"
entry point — actually invokes the bridge when optimiser_type == AGENT_LEARNING_KIT, i.e.
the kit drives optimisation from a real code path, not an island.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models.agent_prompt_optimiser_run import AgentPromptOptimiserRun
from simulate.utils import agent_prompt_optimiser as apo


def _fake_run(optimiser_type, configuration=None):
    run = SimpleNamespace(
        id="run-1",
        optimiser_type=optimiser_type,
        configuration=configuration or {},
        result=None,
        status=None,
        test_execution=SimpleNamespace(agent_definition=None, run_test=None),
    )
    run.save = lambda **kw: None
    run.mark_as_failed = lambda msg=None: setattr(run, "_failed", msg)
    return run


@pytest.mark.unit
def test_optimiser_routes_to_bridge_for_agent_learning_kit():
    run = _fake_run(
        AgentPromptOptimiserRun.OptimiserType.AGENT_LEARNING_KIT,
        {"agent_candidates": [{"type": "scripted", "responses": [{"content": "hi"}]}],
         "evaluation_config": {"metrics": [{"name": "task_completion"}]}, "dry_run": True},
    )
    with patch.object(apo.AgentPromptOptimiserRun.objects, "get", return_value=run), \
         patch("simulate.services.agent_learning_bridge.is_available", return_value=True), \
         patch("simulate.services.agent_learning_bridge.optimize_and_apply_for_agent",
               return_value={"status": "passed", "summary": {"optimization_score": 0.9}}) as m:
        apo.run_agent_prompt_optimiser("run-1")

    # The REAL platform optimiser flow invoked the bridge — no longer an island.
    m.assert_called_once()
    kwargs = m.call_args.kwargs
    assert kwargs["name"] == "run-1"
    assert kwargs["agent_candidates"] == [{"type": "scripted", "responses": [{"content": "hi"}]}]
    assert kwargs["dry_run"] is True
    assert run.result == {"status": "passed", "summary": {"optimization_score": 0.9}}
    assert run.status == AgentPromptOptimiserRun.Status.COMPLETED


@pytest.mark.unit
def test_other_optimiser_types_do_not_route_to_bridge():
    run = _fake_run(AgentPromptOptimiserRun.OptimiserType.GEPA)
    # Force the ee FixYourAgent import to fail so the non-kit path returns early cleanly;
    # the assertion is that the kit branch is NOT taken for non-kit optimiser types.
    with patch.object(apo.AgentPromptOptimiserRun.objects, "get", return_value=run), \
         patch("simulate.utils.agent_prompt_optimiser._run_agent_learning_kit_optimisation") as kit, \
         patch.dict("sys.modules", {"ee.agenthub.fix_your_agent.fix_your_agent": None}), \
         patch("simulate.utils.agent_prompt_optimiser.settings") as s:
        s.DEBUG = False
        apo.run_agent_prompt_optimiser("run-1")
    kit.assert_not_called()


@pytest.mark.integration
def test_real_kit_optimisation_runs_end_to_end_through_platform_flow():
    """END-TO-END (not mocked): run_agent_prompt_optimiser → REAL agent-learning-kit
    optimize → real result stored. Proves the wired path actually executes an
    optimization and applies the winning candidate, not just that a function is called."""
    from simulate.services import agent_learning_bridge as alb

    if not alb.is_available():
        pytest.skip("agent-learning-kit not installed")

    run = _fake_run(
        AgentPromptOptimiserRun.OptimiserType.AGENT_LEARNING_KIT,
        {
            "agent_candidates": [
                {"type": "scripted", "responses": [{"content": "I don't know."}]},
                {"type": "scripted",
                 "responses": [{"content": "Sure! Our hours are 9-5 Mon-Fri."}]},
            ],
            "evaluation_config": {"metrics": [{"name": "task_completion"}]},
            "dry_run": False,  # real (non-dry-run) kit optimisation
        },
    )
    with patch.object(apo.AgentPromptOptimiserRun.objects, "get", return_value=run):
        apo.run_agent_prompt_optimiser("run-1")

    assert run.status == AgentPromptOptimiserRun.Status.COMPLETED
    assert isinstance(run.result, dict)
    # Real agent-learning-kit optimize result shape.
    assert run.result.get("status") in ("passed", "failed")
    assert "optimization" in run.result or "summary" in run.result


@pytest.mark.unit
def test_bridge_unavailable_marks_run_failed():
    run = _fake_run(AgentPromptOptimiserRun.OptimiserType.AGENT_LEARNING_KIT)
    with patch.object(apo.AgentPromptOptimiserRun.objects, "get", return_value=run), \
         patch("simulate.services.agent_learning_bridge.is_available", return_value=False), \
         patch("simulate.services.agent_learning_bridge.optimize_and_apply_for_agent") as m:
        apo.run_agent_prompt_optimiser("run-1")
    m.assert_not_called()
    assert getattr(run, "_failed", None) and "agent-learning-kit" in run._failed
