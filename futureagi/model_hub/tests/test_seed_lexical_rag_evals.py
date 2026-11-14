"""Exercise the persisted catalog, its detail API, and actual code dispatch."""

import pytest
from django.core.cache import cache

from agentic_eval.core_evals.fi_utils import sandbox
from evaluations.engine.runner import EvalRequest, run_eval
from model_hub.management.commands.seed_system_evals import (
    SYSTEM_EVALS_VERSION,
    seed_evals,
)
from model_hub.models.evals_metric import EvalTemplate

NAMES = (
    "context_precision_score",
    "context_recall_score",
    "faithfulness_score",
    "noise_sensitivity",
)


@pytest.mark.django_db
@pytest.mark.parametrize("previous_version", [30, 31])
def test_previous_catalog_receives_distinct_lexical_templates(
    auth_client, previous_version
):
    previous = cache.get("system_evals_version")
    try:
        seed_evals(force=True)
        existing = dict(
            EvalTemplate.no_workspace_objects.filter(
                name__in=["groundedness", "context_relevance", "dead_air_detection"]
            ).values_list("name", "id")
        )
        EvalTemplate.no_workspace_objects.filter(name__in=NAMES).delete()
        cache.set("system_evals_version", previous_version, timeout=None)
        seed_evals()
        assert cache.get("system_evals_version") == SYSTEM_EVALS_VERSION
        assert SYSTEM_EVALS_VERSION > previous_version
        for name, eval_id in zip(NAMES, range(221, 225), strict=True):
            template = EvalTemplate.no_workspace_objects.get(name=name, owner="system")
            assert template.eval_id == eval_id
            assert template.visible_ui
            assert template.eval_type == "code"
            assert template.config["eval_type_id"] == "CustomCodeEval"
            response = auth_client.get(
                f"/model-hub/eval-templates/{template.id}/detail/"
            )
            assert response.status_code == 200
            assert response.data["result"]["name"] == name
            assert "lexical" in response.data["result"]["description"].lower()
        assert existing == dict(
            EvalTemplate.no_workspace_objects.filter(name__in=existing).values_list(
                "name", "id"
            )
        )
        assert seed_evals() == (0, 0, 0)
    finally:
        if previous is None:
            cache.delete("system_evals_version")
        else:
            cache.set("system_evals_version", previous, timeout=None)


@pytest.mark.django_db
def test_seeded_recall_executes_default_and_configured_threshold(settings, monkeypatch):
    seed_evals(force=True)
    template = EvalTemplate.no_workspace_objects.get(
        name="context_recall_score", owner="system"
    )
    monkeypatch.setattr(sandbox, "_call_executor_service", lambda *args, **kwargs: None)
    settings.CODE_EXECUTOR_LOCAL_FALLBACK = True
    settings.CLOUD_DEPLOYMENT = ""
    inputs = {
        "hypothesis": ["Berlin is capital"],
        "reference": ["Paris is capital"],
    }
    result = run_eval(EvalRequest(eval_template=template, inputs=inputs))
    assert result.value == 0.0
    result = run_eval(
        EvalRequest(
            eval_template=template,
            inputs=inputs,
            runtime_config={"params": {"min_token_overlap": 0.6}},
        )
    )
    assert result.value == 1.0
    assert "lexical" in result.reason.lower()
