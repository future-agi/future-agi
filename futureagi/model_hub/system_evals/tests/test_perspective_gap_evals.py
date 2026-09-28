from __future__ import annotations

import json
from pathlib import Path

import yaml

SYSTEM_EVALS_DIR = Path(__file__).resolve().parent.parent


def _load_eval_yaml(track: str, name: str):
    return yaml.safe_load((SYSTEM_EVALS_DIR / track / f"{name}.yaml").read_text())


def _extract_evaluate(yaml_spec):
    code = yaml_spec["config"]["code"]
    namespace = {}
    exec(code, namespace)
    return namespace["evaluate"]


def _run_in_production_sandbox(yaml_spec, input_data, monkeypatch):
    from agentic_eval.core_evals.fi_utils import sandbox

    monkeypatch.setattr(sandbox, "_call_executor_service", lambda *args, **kwargs: None)
    monkeypatch.setattr(sandbox, "SAFE_MODULES", ["json", "re", "difflib"])
    result = sandbox.execute_sandboxed_python(
        yaml_spec["config"]["code"],
        input_data,
        timeout=10,
    )
    assert result["status"] == "success", result
    return result["data"]


def test_role_assignment_yaml_registered():
    spec = _load_eval_yaml("function", "perspective_gap_role_assignment")
    assert spec["eval_id"] == 202
    assert spec["name"] == "perspective_gap_role_assignment"
    assert spec["config"]["eval_type_id"] == "CustomCodeEval"
    assert spec["config"]["required_keys"] == ["output", "reference_need_sets"]
    assert "from perspective_gap" not in spec["config"]["code"]


def test_prompt_writing_yaml_registered():
    spec = _load_eval_yaml("function", "perspective_gap_prompt_writing")
    assert spec["eval_id"] == 203
    assert spec["name"] == "perspective_gap_prompt_writing"
    assert spec["config"]["eval_type_id"] == "CustomCodeEval"
    assert spec["config"]["required_keys"] == [
        "output",
        "fragments",
        "reference_need_sets",
    ]
    assert (
        spec["config"]["function_params_schema"]["include_threshold"]["default"] == 0.7
    )
    assert (
        spec["config"]["function_params_schema"]["exclude_threshold"]["default"] == 0.3
    )
    assert "from perspective_gap" not in spec["config"]["code"]


def test_eval_ids_are_unique():
    eval_ids = []
    for path in SYSTEM_EVALS_DIR.glob("*/*.yaml"):
        spec = yaml.safe_load(path.read_text())
        if spec and "eval_id" in spec:
            eval_ids.append(spec["eval_id"])
    assert len(eval_ids) == len(set(eval_ids))


def test_role_assignment_exact_match_passes():
    spec = _load_eval_yaml("function", "perspective_gap_role_assignment")
    evaluate = _extract_evaluate(spec)
    reference = {"Planner": ["F1", "F3"], "Reviewer": ["F2"]}

    result = evaluate(
        input=None,
        output='ignored </think> {"Planner": ["F3", "F1"], "Reviewer": ["F2"]}',
        expected=None,
        context=None,
        reference_need_sets=json.dumps(reference),
        distractor_id="D1",
    )

    assert result["score"] == 1.0
    assert "PerspectiveGap role assignment passed" in result["reason"]
    assert (
        "counts={'tp': 3, 'fp': 0, 'fn': 0, 'distractor_leak': 0}" in result["reason"]
    )


def test_role_assignment_extra_distractor_fails():
    spec = _load_eval_yaml("function", "perspective_gap_role_assignment")
    evaluate = _extract_evaluate(spec)

    result = evaluate(
        input=None,
        output='{"Planner": ["F1", "D1"]}',
        expected=None,
        context=None,
        reference_need_sets={"Planner": ["F1"]},
        distractor_id="D1",
    )

    assert result["score"] == 0.0
    assert "PerspectiveGap role assignment failed" in result["reason"]
    assert "leakage=1.0" in result["reason"]


def test_role_assignment_surfaces_parse_error():
    spec = _load_eval_yaml("function", "perspective_gap_role_assignment")
    evaluate = _extract_evaluate(spec)

    result = evaluate(
        input=None,
        output="not json",
        expected=None,
        context=None,
        reference_need_sets={"Planner": ["F1"]},
    )

    assert result["score"] == 0.0
    assert result["reason"].startswith("parse:")


def test_prompt_writing_exact_fragments_pass():
    spec = _load_eval_yaml("function", "perspective_gap_prompt_writing")
    evaluate = _extract_evaluate(spec)
    fragments = [{"id": "F1", "text": "Use the verified benchmark evidence."}]

    result = evaluate(
        input=None,
        output="</think> # Planner\nUse the verified benchmark evidence.",
        expected=None,
        context=None,
        fragments=json.dumps(fragments),
        reference_need_sets=json.dumps({"Planner": ["F1"]}),
        distractor_id="D1",
    )

    assert result["score"] == 1.0
    assert "PerspectiveGap prompt writing passed" in result["reason"]


def test_prompt_writing_distractor_leak_fails():
    spec = _load_eval_yaml("function", "perspective_gap_prompt_writing")
    evaluate = _extract_evaluate(spec)
    fragments = [
        {"id": "F1", "text": "alpha planner evidence"},
        {"id": "F2", "text": "beta reviewer evidence"},
        {"id": "D1", "text": "gamma confidential distractor"},
    ]
    response = (
        "# Planner\nalpha planner evidence gamma confidential distractor\n\n"
        "# Reviewer\nbeta reviewer evidence"
    )

    result = evaluate(
        input=None,
        output=response,
        expected=None,
        context=None,
        fragments=fragments,
        reference_need_sets={"Planner": ["F1"], "Reviewer": ["F2"]},
        distractor_id="D1",
    )

    assert result["score"] == 0.0
    assert "PerspectiveGap prompt writing failed" in result["reason"]
    assert "leakage=1.0" in result["reason"]


def test_role_assignment_runs_in_production_sandbox(monkeypatch):
    spec = _load_eval_yaml("function", "perspective_gap_role_assignment")
    result = _run_in_production_sandbox(
        spec,
        {
            "output": '{"Planner": ["F1"]}',
            "reference_need_sets": {"Planner": ["F1"]},
            "distractor_id": "D1",
        },
        monkeypatch,
    )

    assert result["result"] == 1.0
    assert "PerspectiveGap role assignment passed" in result["reason"]


def test_prompt_writing_runs_in_production_sandbox(monkeypatch):
    spec = _load_eval_yaml("function", "perspective_gap_prompt_writing")
    result = _run_in_production_sandbox(
        spec,
        {
            "output": "# Planner\nUse the verified benchmark evidence.",
            "fragments": [{"id": "F1", "text": "Use the verified benchmark evidence."}],
            "reference_need_sets": {"Planner": ["F1"]},
            "distractor_id": "D1",
        },
        monkeypatch,
    )

    assert result["result"] == 1.0
    assert "PerspectiveGap prompt writing passed" in result["reason"]
