"""Tests for the inline simulation eval cutover to agent-learning-kit
(Phase-10A Tier-1 cutover #2; pattern: TH-5642 / test_agent_learning_bridge.py).

Three layers:
- PURE MAPPING (kit mocked): engine routing, evidence construction, and the kit
  payload -> run_eval_func result-shape mapping the shared persistence consumes.
- E2E (kit real, credential-free): a REAL kit evaluation over built evidence
  returning the exact platform result shape for each eval output type.
- PARITY (old vs new): the legacy engine's result (run_eval_func contract; its
  execution needs live LLM credits so the boundary is stubbed with its documented
  shape) side-by-side with a REAL kit result on identical fixtures. Engines differ
  (legacy = LLM judge, kit = local agent-report evaluator) so SCORES ARE NOT
  ASSERTED EQUAL — parity is the persisted contract: identical key set and value
  types in the eval_outputs entry, identical output-type semantics.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.services import agent_learning_bridge as bridge
from simulate.temporal.activities import xl

# The exact eval_outputs entry contract _run_single_evaluation persists for BOTH
# engines: {"output", "reason", "output_type"} from the engine + "name" added at
# persistence time.
ENGINE_RESULT_KEYS = {"output", "reason", "output_type"}


def _eval_config(
    config=None, name="quality_check", output_type="percentage", criteria=None
):
    template = SimpleNamespace(
        name="Quality",
        description="Checks answer quality",
        criteria=criteria,
        output_type_normalized=output_type,
    )
    return SimpleNamespace(
        id="ec-1",
        name=name,
        config=config or {},
        eval_template=template,
    )


def _transcript_data(**overrides):
    data = {
        "transcript": "user: What are your hours?\nassistant: 9-5 Mon-Fri.",
        "user_chat_transcript": "What are your hours?",
        "assistant_chat_transcript": "9-5 Mon-Fri.",
        "voice_recording": "",
        "assistant_recording": "",
        "customer_recording": "",
        "stereo_recording": "",
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# Pure mapping: engine routing                                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_kit_engine_is_default_when_available():
    with patch.object(bridge, "is_available", return_value=True):
        assert xl._eval_engine_uses_kit(_eval_config()) is True


@pytest.mark.unit
def test_legacy_engine_override_per_config():
    with patch.object(bridge, "is_available", return_value=True):
        assert xl._eval_engine_uses_kit(_eval_config({"engine": "legacy"})) is False
        assert (
            xl._eval_engine_uses_kit(_eval_config({"run_config": {"engine": "legacy"}}))
            is False
        )


@pytest.mark.unit
def test_kit_engine_requires_kit_installed():
    with patch.object(bridge, "is_available", return_value=False):
        assert xl._eval_engine_uses_kit(_eval_config()) is False
        assert (
            xl._eval_engine_uses_kit(_eval_config({"engine": "agent_learning_kit"}))
            is False
        )


@pytest.mark.unit
def test_settings_kill_switch_disables_kit_engine(settings):
    settings.SIMULATE_EVALS_VIA_KIT = False
    with patch.object(bridge, "is_available", return_value=True):
        assert xl._eval_engine_uses_kit(_eval_config()) is False
    # Explicit per-config opt-in still wins over the global switch.
    with patch.object(bridge, "is_available", return_value=True):
        assert (
            xl._eval_engine_uses_kit(_eval_config({"engine": "agent_learning_kit"}))
            is True
        )


# --------------------------------------------------------------------------- #
# Pure mapping: evidence construction (bridge)                                 #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_build_eval_evidence_shapes_kit_input():
    evidence = bridge.build_eval_evidence(
        input_text="What are your hours?",
        output_text="9-5 Mon-Fri.",
        transcript="user: ...\nassistant: ...",
        metadata={"agent_name": "acme"},
    )
    assert evidence["input"] == "What are your hours?"
    assert evidence["output"] == "9-5 Mon-Fri."
    assert evidence["metadata"]["agent_name"] == "acme"
    assert evidence["metadata"]["transcript"] == "user: ...\nassistant: ..."


@pytest.mark.unit
def test_run_eval_for_evidence_requires_name_and_task():
    with pytest.raises(ValueError):
        bridge.run_eval_for_evidence(name="", evidence={}, task_description="t")
    with pytest.raises(ValueError):
        bridge.run_eval_for_evidence(name="n", evidence={}, task_description="")


# --------------------------------------------------------------------------- #
# Pure mapping: kit payload -> run_eval_func result shape                      #
# --------------------------------------------------------------------------- #
def _kit_eval_payload(score=0.91, passed=True, findings=None, threshold=0.7):
    return {
        "status": "passed" if passed else "failed",
        "summary": {"score": score, "threshold": threshold},
        "evaluation": {"score": score, "passed": passed},
        "findings": findings
        if findings is not None
        else [{"metric": "task_completion", "reason": "Criteria met.", "score": score}],
    }


@pytest.mark.unit
def test_kit_result_maps_to_score_output():
    out = xl._kit_eval_result_to_platform_output(
        _kit_eval_payload(score=0.9137), output_type="score"
    )
    assert set(out) == ENGINE_RESULT_KEYS
    assert out["output"] == pytest.approx(0.9137)
    assert isinstance(out["output"], float)
    assert out["output_type"] == "score"
    assert "Criteria met." in out["reason"]


@pytest.mark.unit
def test_kit_result_maps_to_pass_fail_bool():
    # Error-localizer semantics depend on isinstance(output, bool) for Pass/Fail.
    out = xl._kit_eval_result_to_platform_output(
        _kit_eval_payload(passed=False, score=0.2), output_type="Pass/Fail"
    )
    assert out["output"] is False
    out = xl._kit_eval_result_to_platform_output(
        _kit_eval_payload(passed=True), output_type="Pass/Fail"
    )
    assert out["output"] is True


@pytest.mark.unit
def test_kit_result_maps_to_choices_string():
    out = xl._kit_eval_result_to_platform_output(
        _kit_eval_payload(passed=True), output_type="choices"
    )
    assert out["output"] == "Passed"


@pytest.mark.unit
def test_kit_result_mapping_handles_missing_fields():
    out = xl._kit_eval_result_to_platform_output({}, output_type="score")
    assert out["output"] == 0.0
    assert out["reason"]  # synthesized reason, never empty
    out = xl._kit_eval_result_to_platform_output(None, output_type="Pass/Fail")
    assert out["output"] is False


@pytest.mark.unit
def test_kit_engine_builds_evidence_from_mapping_and_template(monkeypatch):
    captured = {}

    def fake_run_eval(**kwargs):
        captured.update(kwargs)
        return _kit_eval_payload(score=0.88)

    monkeypatch.setattr(bridge, "run_eval_for_evidence", fake_run_eval)
    eval_config = _eval_config(
        config={"run_config": {"pass_threshold": 0.85}},
        criteria="Answer states the opening hours",
        output_type="percentage",
    )
    out = xl._run_eval_engine_via_kit(
        eval_config,
        eval_config.eval_template,
        # Mapped values: 'hypothesis' is the agent answer under test.
        {"hypothesis": "9-5 Mon-Fri.", "query": "What are your hours?"},
        _transcript_data(),
    )

    assert captured["name"] == "ec-1"
    assert captured["evidence"]["output"] == "9-5 Mon-Fri."  # hypothesis key
    assert captured["evidence"]["input"] == "What are your hours?"  # query key
    assert captured["task_description"] == "Answer states the opening hours"
    assert captured["success_criteria"] == ["Answer states the opening hours"]
    assert captured["threshold"] == 0.85
    assert set(out) == ENGINE_RESULT_KEYS
    assert out["output"] == pytest.approx(0.88)


@pytest.mark.unit
def test_kit_engine_falls_back_to_transcripts(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        bridge,
        "run_eval_for_evidence",
        lambda **kw: captured.update(kw) or _kit_eval_payload(),
    )
    eval_config = _eval_config()
    xl._run_eval_engine_via_kit(
        eval_config, eval_config.eval_template, {}, _transcript_data()
    )
    assert captured["evidence"]["input"] == "What are your hours?"
    assert captured["evidence"]["output"] == "9-5 Mon-Fri."
    assert captured["evidence"]["metadata"]["transcript"].startswith("user:")
    # No criteria on the template -> description is the task.
    assert captured["task_description"] == "Checks answer quality"


# --------------------------------------------------------------------------- #
# E2E — real kit, credential-free                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_e2e_real_kit_eval_produces_platform_contract():
    eval_config = _eval_config(
        criteria="States the opening hours", output_type="percentage"
    )
    out = xl._run_eval_engine_via_kit(
        eval_config,
        eval_config.eval_template,
        {
            "query": "What are your hours?",
            "hypothesis": "Sure! Our hours are 9-5 Mon-Fri.",
        },
        _transcript_data(),
    )
    assert set(out) == ENGINE_RESULT_KEYS
    assert isinstance(out["output"], float)
    assert 0.0 <= out["output"] <= 1.0
    assert isinstance(out["reason"], str) and out["reason"]
    assert out["output_type"] == "score"


@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_e2e_real_kit_eval_pass_fail_template():
    eval_config = _eval_config(
        criteria="States the opening hours", output_type="pass_fail"
    )
    out = xl._run_eval_engine_via_kit(
        eval_config,
        eval_config.eval_template,
        {"hypothesis": "Sure! Our hours are 9-5 Mon-Fri."},
        _transcript_data(),
    )
    assert isinstance(out["output"], bool)
    assert out["output_type"] == "Pass/Fail"


@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_e2e_real_kit_eval_through_bridge_artifact():
    """The bridge produces a REAL kit artifact-evaluation payload end-to-end."""
    result = bridge.run_eval_for_evidence(
        name="inline-eval-smoke",
        evidence=bridge.build_eval_evidence(
            input_text="What are your hours?",
            output_text="Sure! Our hours are 9-5 Mon-Fri.",
            transcript="user: What are your hours?\nassistant: Sure! Our hours are 9-5 Mon-Fri.",
            metadata={"channel": "chat"},
        ),
        task_description="Help the customer with store hours",
        success_criteria=["states the opening hours"],
        threshold=0.5,
    )
    assert result["status"] in {"passed", "failed"}
    assert result["name"] == "inline-eval-smoke"
    assert 0.0 <= float(result["summary"]["score"]) <= 1.0
    assert "evaluation" in result


# --------------------------------------------------------------------------- #
# PARITY — legacy engine contract vs kit engine on identical fixtures          #
# --------------------------------------------------------------------------- #
@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_parity_legacy_vs_kit_eval_engine_contract():
    """Side-by-side on identical fixtures (same eval config, same transcript).

    The LEGACY engine boundary (run_eval_func) is stubbed with its documented
    return contract — executing it for real requires live LLM credits, which the
    Phase-10A test pattern forbids. The KIT engine runs for REAL. Scores may
    differ between engines (LLM judge vs local evaluator — audit guidance);
    parity is asserted on the persisted contract: identical key set, identical
    value types, identical output_type semantics for the same template.
    """
    eval_config = _eval_config(
        config={"run_config": {"pass_threshold": 0.7}},
        criteria="States the opening hours",
        output_type="percentage",
    )
    mapping = {
        "query": "What are your hours?",
        "hypothesis": "Sure! Our hours are 9-5 Mon-Fri.",
    }
    transcript_data = _transcript_data()

    # OLD path: the legacy engine's documented result contract for this template
    # (run_eval_func returns {"output": <0..1 float>, "reason": str,
    # "output_type": ...}); still the shape persisted before the cutover.
    legacy_result = {
        "output": 0.95,
        "reason": "The answer states the opening hours clearly.",
        "output_type": "score",
    }

    # NEW path: REAL kit execution on the identical fixture.
    kit_result = xl._run_eval_engine_via_kit(
        eval_config, eval_config.eval_template, mapping, transcript_data
    )

    # Contract parity: identical key set and value types.
    assert set(kit_result) == set(legacy_result) == ENGINE_RESULT_KEYS
    for key in ENGINE_RESULT_KEYS:
        assert isinstance(kit_result[key], type(legacy_result[key])), (
            f"type mismatch for {key}: "
            f"kit={type(kit_result[key])} legacy={type(legacy_result[key])}"
        )
    # Semantic parity: same output_type for the same template; score in range.
    assert kit_result["output_type"] == legacy_result["output_type"]
    assert 0.0 <= kit_result["output"] <= 1.0
    assert kit_result["reason"]

    # Persistence parity: both engine results produce the SAME eval_outputs
    # entry shape (this is the exact dict _run_single_evaluation persists).
    def persisted_entry(engine_result):
        return {
            "output": engine_result.get("output"),
            "reason": engine_result.get("reason", ""),
            "output_type": engine_result.get("output_type"),
            "name": eval_config.name,
        }

    assert set(persisted_entry(kit_result)) == set(persisted_entry(legacy_result))


@pytest.mark.integration
@pytest.mark.skipif(
    not bridge.is_available(), reason="agent-learning-kit not installed"
)
def test_parity_pass_fail_semantics_match_legacy_bool():
    """For pass_fail templates both engines must persist a BOOL (the error-
    localizer branches on isinstance(output, bool)); values may differ."""
    eval_config = _eval_config(
        criteria="States the opening hours", output_type="pass_fail"
    )
    legacy_result = {"output": True, "reason": "ok", "output_type": "Pass/Fail"}
    kit_result = xl._run_eval_engine_via_kit(
        eval_config,
        eval_config.eval_template,
        {"hypothesis": "Sure! Our hours are 9-5 Mon-Fri."},
        _transcript_data(),
    )
    assert isinstance(kit_result["output"], bool) and isinstance(
        legacy_result["output"], bool
    )
    assert kit_result["output_type"] == legacy_result["output_type"] == "Pass/Fail"
