"""CustomEvalConfig accepts BYO model names (TH-6710)."""

import pytest

from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer


@pytest.mark.parametrize(
    "model_value",
    [
        "gpt-4o-mini",
        "gpt-4o",
        "claude-3-5-sonnet-latest",
        "turing_large",  # still valid alongside BYO
        "",              # blank accepted; model is optional on the config
    ],
)
def test_serializer_accepts_arbitrary_model_string(
    db, project, eval_template, model_value
):
    serializer = CustomEvalConfigSerializer(
        data={
            "name": f"eval-{model_value or 'blank'}",
            "eval_template": str(eval_template.id),
            "project": str(project.id),
            "mapping": {"output": "output"},
            "config": {},
            "model": model_value,
        }
    )
    assert serializer.is_valid(raise_exception=False), serializer.errors
    assert serializer.validated_data.get("model") == model_value
