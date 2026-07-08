from __future__ import annotations

import json
import sys
import types
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


def _install_fake_scoring(monkeypatch, *, role_result=None, prompt_result=None):
    scoring = types.ModuleType("perspective_gap.scoring")
    calls = {}

    def score_role_assignment(response, reference_need_sets, distractor_id=None):
        calls["role_assignment"] = {
            "response": response,
            "reference_need_sets": reference_need_sets,
            "distractor_id": distractor_id,
        }
        return role_result or {
            "pass": True,
            "metrics": {
                "strict_pass": 1.0,
                "net_match_score": 1.0,
                "required_coverage": 1.0,
                "boundary_precision": 1.0,
                "distractor_leakage": 0.0,
            },
            "counts": {"tp": 1, "fp": 0, "fn": 0, "distractor_leak": 0},
        }

    def score_prompt_writing(
        response,
        fragments,
        reference_need_sets,
        distractor_id=None,
        include_threshold=0.7,
        exclude_threshold=0.3,
    ):
        calls["prompt_writing"] = {
            "response": response,
            "fragments": fragments,
            "reference_need_sets": reference_need_sets,
            "distractor_id": distractor_id,
            "include_threshold": include_threshold,
            "exclude_threshold": exclude_threshold,
        }
        return prompt_result or {
            "pass": True,
            "metrics": {
                "strict_pass": 1.0,
                "net_match_score": 1.0,
                "required_coverage": 1.0,
                "boundary_precision": 1.0,
                "distractor_leakage": 0.0,
            },
            "counts": {"tp": 1, "fp": 0, "fn": 0, "distractor_leak": 0},
        }

    scoring.score_role_assignment = score_role_assignment
    scoring.score_prompt_writing = score_prompt_writing

    package = types.ModuleType("perspective_gap")
    package.scoring = scoring
    monkeypatch.setitem(sys.modules, "perspective_gap", package)
    monkeypatch.setitem(sys.modules, "perspective_gap.scoring", scoring)
    return calls


def test_role_assignment_yaml_registered():
    spec = _load_eval_yaml("function", "perspective_gap_role_assignment")
    assert spec["eval_id"] == 202
    assert spec["name"] == "perspective_gap_role_assignment"
    assert spec["config"]["eval_type_id"] == "CustomCodeEval"
    assert spec["config"]["required_keys"] == ["output", "reference_need_sets"]
    assert "code" in spec["config"]


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
    assert spec["config"]["function_params_schema"]["include_threshold"]["default"] == 0.7
    assert spec["config"]["function_params_schema"]["exclude_threshold"]["default"] == 0.3
    assert "code" in spec["config"]


def test_eval_ids_are_unique():
    eval_ids = []
    for path in SYSTEM_EVALS_DIR.glob("*/*.yaml"):
        spec = yaml.safe_load(path.read_text())
        if spec and "eval_id" in spec:
            eval_ids.append(spec["eval_id"])
    assert len(eval_ids) == len(set(eval_ids))


def test_role_assignment_scorer_dispatch(monkeypatch):
    calls = _install_fake_scoring(monkeypatch)
    spec = _load_eval_yaml("function", "perspective_gap_role_assignment")
    evaluate = _extract_evaluate(spec)
    reference = {"Planner": ["F1"]}

    result = evaluate(
        input=None,
        output='ignored </think> {"Planner": ["F1"]}',
        expected=None,
        context=None,
        reference_need_sets=json.dumps(reference),
        distractor_id="D1",
    )

    assert result["score"] == 1.0
    assert "strict_pass=1.0" in result["reason"]
    assert calls["role_assignment"]["response"] == '{"Planner": ["F1"]}'
    assert calls["role_assignment"]["reference_need_sets"] == reference
    assert calls["role_assignment"]["distractor_id"] == "D1"


def test_role_assignment_surfaces_errors(monkeypatch):
    _install_fake_scoring(
        monkeypatch,
        role_result={
            "pass": False,
            "error": "parse: no JSON object",
            "metrics": {"strict_pass": 0.0},
            "counts": {},
        },
    )
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
    assert result["reason"] == "parse: no JSON object"


def test_prompt_writing_scorer_dispatch(monkeypatch):
    calls = _install_fake_scoring(monkeypatch)
    spec = _load_eval_yaml("function", "perspective_gap_prompt_writing")
    evaluate = _extract_evaluate(spec)
    fragments = [{"id": "F1", "text": "Use the verified benchmark."}]
    reference = {"Planner": ["F1"]}

    result = evaluate(
        input=None,
        output="</think> # Planner\nUse the verified benchmark.",
        expected=None,
        context=None,
        fragments=json.dumps(fragments),
        reference_need_sets=json.dumps(reference),
        distractor_id="D1",
        include_threshold=0.8,
        exclude_threshold=0.2,
    )

    assert result["score"] == 1.0
    assert "PerspectiveGap prompt writing passed" in result["reason"]
    assert calls["prompt_writing"]["response"] == "# Planner\nUse the verified benchmark."
    assert calls["prompt_writing"]["fragments"] == fragments
    assert calls["prompt_writing"]["reference_need_sets"] == reference
    assert calls["prompt_writing"]["distractor_id"] == "D1"
    assert calls["prompt_writing"]["include_threshold"] == 0.8
    assert calls["prompt_writing"]["exclude_threshold"] == 0.2
