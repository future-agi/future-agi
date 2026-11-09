"""DB-free request-boundary regressions for test-evaluation input mappings.

Exercise the real serializer, view and config preparation; only the entitlement
check and evaluation dispatch are mocked. Organization instances are unsaved.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from model_hub.views import separate_evals

MAPPING = {"text": "hello world", "reference": "expected answer"}
OTHER_MAPPING = {"text": "different input", "reference": "expected answer"}


@pytest.fixture
def evaluation():
    with (
        patch("tfc.ee_gates.turing_oss_gate_for_template", return_value=None),
        patch.object(separate_evals, "run_eval_func", return_value={"score": 1}) as run,
        patch.object(
            separate_evals,
            "prepare_user_eval_config",
            wraps=separate_evals.prepare_user_eval_config,
        ) as prepare,
    ):
        yield SimpleNamespace(run=run, prepare=prepare)


def _post(mapping_fields):
    request = APIRequestFactory().post(
        "/model-hub/test-evaluation/",
        {
            "name": "mapping-regression",
            "template_type": "Futureagi",
            "output_type": "Pass/Fail",
            "model": "",
            "config": {},
            **mapping_fields,
        },
        format="json",
    )
    force_authenticate(
        request,
        user=SimpleNamespace(
            is_authenticated=True,
            organization=Organization(id=uuid.uuid4(), name="Mapping tests"),
        ),
    )
    return separate_evals.TestEvaluationTemplateAPIView.as_view()(request)


@pytest.mark.parametrize(
    ("mapping_fields", "expected"),
    [
        pytest.param({"mapping": MAPPING}, MAPPING, id="top-level-only"),
        pytest.param({"config": {"mapping": MAPPING}}, MAPPING, id="config-only"),
        pytest.param(
            {
                "mapping": MAPPING,
                "config": {"mapping": dict(reversed(MAPPING.items()))},
            },
            MAPPING,
            id="both-equal",
        ),
        pytest.param({}, {}, id="neither-provided"),
        pytest.param({"mapping": {}}, {}, id="top-level-empty"),
        pytest.param({"config": {"mapping": {}}}, {}, id="config-empty"),
        pytest.param({"mapping": {}, "config": {"mapping": {}}}, {}, id="both-empty"),
    ],
)
def test_evaluation_dispatch_receives_mapping(evaluation, mapping_fields, expected):
    response = _post(mapping_fields)

    assert response.status_code == 200, response.data
    assert response.data["result"] == {"score": 1}
    evaluation.prepare.assert_called_once()
    evaluation.run.assert_called_once()
    assert evaluation.run.call_args.args[1] == expected


@pytest.mark.parametrize(
    ("top_level", "nested"),
    [
        pytest.param(MAPPING, OTHER_MAPPING, id="different-values"),
        pytest.param({}, MAPPING, id="explicit-empty-top-level"),
        pytest.param(MAPPING, {}, id="explicit-empty-config"),
    ],
)
def test_conflicting_mappings_rejected_before_evaluation(evaluation, top_level, nested):
    response = _post({"mapping": top_level, "config": {"mapping": nested}})

    assert response.status_code == 400, response.data
    message = str(response.data).lower()
    assert "mapping" in message
    assert "config.mapping" in message
    assert "conflict" in message
    evaluation.prepare.assert_not_called()
    evaluation.run.assert_not_called()
