"""optional_keys forwarding in prepare_run_params (tracer/standalone path).

Regression: AgentEvaluator computes truly_required = required_keys - optional_keys
from run kwargs. optional_keys was never injected, so templates dual-listing a key
(action_confirmation_gating's transcript) died 'Missing required input(s)' when the
optional key was unmapped. Mirrors EvaluationRunner.map_fields policy.
"""

from types import SimpleNamespace

from evaluations.engine.params import prepare_run_params


def _t(config, owner="system"):
    return SimpleNamespace(name="t", criteria="c", config=config, owner=owner)


def test_system_template_forwards_declared_optional_keys():
    t = _t({"eval_type_id": "AgentEvaluator",
            "required_keys": ["agent_prompt", "conversation", "transcript"],
            "optional_keys": ["agent_prompt", "transcript"]})
    p = prepare_run_params(inputs={"conversation": "x"}, eval_template=t)
    assert p["optional_keys"] == ["agent_prompt", "transcript"]


def test_user_template_marks_all_required_keys_optional():
    t = _t({"eval_type_id": "AgentEvaluator", "required_keys": ["input", "output"]},
           owner="user")
    p = prepare_run_params(inputs={"input": "q", "output": "a"}, eval_template=t)
    assert p["optional_keys"] == ["input", "output"]
