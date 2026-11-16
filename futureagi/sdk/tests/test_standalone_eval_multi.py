"""
POST /sdk/api/v1/eval/ with several eval_ids in ``config``.

The agentcc gateway guardrail sends every configured eval_id in one request
(``config: {"<eval_id>": {...}, ...}``) and blocks the request if any returned
evaluation failed. The view must therefore run every requested eval and return
each result, and an error in any one of them must fail the whole request
instead of being dropped.

Only ``evaluations.engine.run_eval`` (the evaluator itself) is stubbed; the
view, ``_run_protect``, template lookup and API key authentication are real.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import status

URL = "/sdk/api/v1/eval/"

# Synthetic eval ids, far from the seeded system eval ids.
EVAL_ID_A = 990101
EVAL_ID_B = 990102
EVAL_ID_C = 990103


@pytest.fixture
def api_key(organization, user, db):
    from accounts.models import OrgApiKey

    key, _ = OrgApiKey.objects.get_or_create(
        organization=organization,
        user=user,
        defaults={
            "api_key": "synthetic_multi_eval_api_key",
            "secret_key": "synthetic_multi_eval_secret_key",
            "name": "Synthetic multi-eval key",
            "enabled": True,
            "type": "user",
        },
    )
    return key


@pytest.fixture
def sdk_client(api_client, api_key):
    api_client.credentials(
        HTTP_X_API_KEY=api_key.api_key,
        HTTP_X_SECRET_KEY=api_key.secret_key,
    )
    return api_client


def _make_template(name, eval_id):
    from model_hub.models.evals_metric import EvalTemplate

    template, _ = EvalTemplate.objects.get_or_create(
        name=name,
        defaults={
            "description": f"Synthetic guardrail {name}",
            "eval_id": eval_id,
            "eval_tags": ["test"],
            "config": {
                "eval_type_id": "CustomCodeEval",
                "required_keys": ["text"],
                "output": "Pass/Fail",
            },
            "owner": "system",
            "organization": None,
        },
    )
    return template


@pytest.fixture
def guardrail_templates(user, db):
    return {
        EVAL_ID_A: _make_template("synthetic_guardrail_a", EVAL_ID_A),
        EVAL_ID_B: _make_template("synthetic_guardrail_b", EVAL_ID_B),
        EVAL_ID_C: _make_template("synthetic_guardrail_c", EVAL_ID_C),
    }


def _result(value, reason):
    return SimpleNamespace(
        value=value,
        reason=reason,
        runtime=1,
        output_type="Pass/Fail",
        data={},
        metadata={},
    )


class _FakeRunEval:
    """Records which templates ran; returns a verdict per template name."""

    def __init__(self, verdicts, errors=()):
        self.verdicts = verdicts
        self.errors = set(errors)
        self.calls = []

    def __call__(self, request):
        name = request.eval_template.name
        self.calls.append(
            {
                "name": name,
                "config_overrides": request.config_overrides,
                "text": request.inputs.get("text"),
                "call_type": request.inputs.get("call_type"),
                "organization_id": request.organization_id,
            }
        )
        if name in self.errors:
            raise RuntimeError(f"synthetic evaluator outage in {name}")
        return _result(self.verdicts[name], f"{name} verdict")


def _post(client, config, text="synthetic input"):
    return client.post(
        URL,
        {"inputs": [{"input": text, "call_type": "protect"}], "config": config},
        format="json",
    )


@pytest.mark.integration
@pytest.mark.api
class TestStandaloneEvalSeveralEvalIds:
    def test_every_requested_eval_runs_and_is_returned(
        self, sdk_client, guardrail_templates, organization
    ):
        fake = _FakeRunEval(
            {
                "synthetic_guardrail_a": "Failed",
                "synthetic_guardrail_b": "Passed",
            }
        )
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(
                sdk_client,
                {
                    str(EVAL_ID_A): {"call_type": "protect"},
                    str(EVAL_ID_B): {"call_type": "protect"},
                },
            )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert [c["name"] for c in fake.calls] == [
            "synthetic_guardrail_a",
            "synthetic_guardrail_b",
        ]
        evaluations = response.json()["result"][0]["evaluations"]
        assert [(e["name"], e["output"]) for e in evaluations] == [
            ("synthetic_guardrail_a", "Failed"),
            ("synthetic_guardrail_b", "Passed"),
        ]
        # Every eval ran for the caller's org on the caller's text.
        assert {c["organization_id"] for c in fake.calls} == {str(organization.id)}
        assert {c["text"] for c in fake.calls} == {"synthetic input"}

    def test_each_eval_gets_its_own_config(self, sdk_client, guardrail_templates):
        fake = _FakeRunEval(
            {
                "synthetic_guardrail_a": "Passed",
                "synthetic_guardrail_b": "Passed",
                "synthetic_guardrail_c": "Passed",
            }
        )
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(
                sdk_client,
                {
                    str(EVAL_ID_A): {"marker": "a"},
                    str(EVAL_ID_B): {"marker": "b"},
                    str(EVAL_ID_C): {},
                },
            )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert [(c["name"], c["config_overrides"]) for c in fake.calls] == [
            ("synthetic_guardrail_a", {"marker": "a"}),
            ("synthetic_guardrail_b", {"marker": "b"}),
            ("synthetic_guardrail_c", {}),
        ]
        assert len(response.json()["result"][0]["evaluations"]) == 3

    def test_error_in_an_earlier_eval_fails_the_request(
        self, sdk_client, guardrail_templates
    ):
        """An evaluator error must not be dropped while later evals pass."""
        fake = _FakeRunEval(
            {"synthetic_guardrail_b": "Passed"},
            errors={"synthetic_guardrail_a"},
        )
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(
                sdk_client,
                {
                    str(EVAL_ID_A): {"call_type": "protect"},
                    str(EVAL_ID_B): {"call_type": "protect"},
                },
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        body = response.json()
        assert body["status"] is False
        # No partial result list that a client could read as "B passed".
        assert "evaluations" not in response.content.decode()
        assert [c["name"] for c in fake.calls] == ["synthetic_guardrail_a"]

    def test_error_in_a_later_eval_fails_the_request(
        self, sdk_client, guardrail_templates
    ):
        fake = _FakeRunEval(
            {"synthetic_guardrail_a": "Passed"},
            errors={"synthetic_guardrail_b"},
        )
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(
                sdk_client,
                {
                    str(EVAL_ID_A): {"call_type": "protect"},
                    str(EVAL_ID_B): {"call_type": "protect"},
                },
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json()["status"] is False

    @pytest.mark.parametrize("unknown_position", ["first", "last"])
    def test_unknown_eval_id_rejected_before_any_eval_runs(
        self, sdk_client, guardrail_templates, unknown_position
    ):
        """An unknown id fails the request before any eval has run or been billed."""
        fake = _FakeRunEval(
            {"synthetic_guardrail_a": "Passed", "synthetic_guardrail_b": "Passed"}
        )
        known = [(str(EVAL_ID_A), {}), (str(EVAL_ID_B), {})]
        unknown = [("990199", {})]
        items = unknown + known if unknown_position == "first" else known + unknown
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(sdk_client, dict(items))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["status"] is False
        assert "990199" in response.json()["result"]
        assert fake.calls == []

    def test_ambiguous_eval_id_rejected_before_any_eval_runs(
        self, sdk_client, guardrail_templates
    ):
        """Two templates sharing an eval_id cannot be resolved; nothing runs."""
        _make_template("synthetic_guardrail_b_twin", EVAL_ID_B)
        fake = _FakeRunEval({"synthetic_guardrail_a": "Passed"})
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(sdk_client, {str(EVAL_ID_A): {}, str(EVAL_ID_B): {}})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert str(EVAL_ID_B) in response.json()["result"]
        assert fake.calls == []

    def test_duplicate_eval_id_rejected_before_any_eval_runs(
        self, sdk_client, guardrail_templates
    ):
        """ "990101" and "0990101" name the same eval; it must not run twice."""
        fake = _FakeRunEval({"synthetic_guardrail_a": "Passed"})
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(sdk_client, {str(EVAL_ID_A): {}, "0" + str(EVAL_ID_A): {}})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert fake.calls == []

    def test_non_numeric_eval_id_rejected_before_any_eval_runs(
        self, sdk_client, guardrail_templates
    ):
        fake = _FakeRunEval({"synthetic_guardrail_a": "Passed"})
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(
                sdk_client,
                {
                    str(EVAL_ID_A): {"call_type": "protect"},
                    "not_a_number": {},
                },
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert fake.calls == []

    def test_too_many_eval_ids_rejected_before_any_eval_runs(
        self, sdk_client, guardrail_templates
    ):
        from sdk.views.evaluations import MAX_STANDALONE_EVALS_PER_REQUEST

        fake = _FakeRunEval({"synthetic_guardrail_a": "Passed"})
        config = {
            str(EVAL_ID_A + 1000 + i): {}
            for i in range(MAX_STANDALONE_EVALS_PER_REQUEST + 1)
        }
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(sdk_client, config)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert fake.calls == []

    def test_single_eval_id_unchanged(self, sdk_client, guardrail_templates):
        """Positive control: the one-eval request shape still works."""
        fake = _FakeRunEval({"synthetic_guardrail_c": "Passed"})
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(sdk_client, {str(EVAL_ID_C): {"call_type": "protect"}})

        assert response.status_code == status.HTTP_200_OK, response.content
        body = response.json()
        assert body["status"] is True
        evaluations = body["result"][0]["evaluations"]
        assert len(evaluations) == 1
        assert evaluations[0]["name"] == "synthetic_guardrail_c"
        assert evaluations[0]["output"] == "Passed"
        assert evaluations[0]["output_type"] == "Pass/Fail"
        assert fake.calls[0]["call_type"] == "protect"

    def test_unauthenticated_request_runs_nothing(
        self, api_client, guardrail_templates
    ):
        fake = _FakeRunEval({"synthetic_guardrail_a": "Passed"})
        with patch("evaluations.engine.run_eval", side_effect=fake):
            response = _post(
                api_client,
                {str(EVAL_ID_A): {}, str(EVAL_ID_B): {}},
            )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert fake.calls == []
