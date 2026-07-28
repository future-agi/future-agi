from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.models import RunTest, Scenarios

ANONYMOUS_PROMPT_TEMPLATE_ID = "00000000-0000-4000-8000-000000001024"
ANONYMOUS_PROMPT_RUN_TEST_ID = "00000000-0000-4000-8000-000000001025"


@pytest.fixture
def prompt_template(db, organization, workspace):
    return PromptTemplate.objects.create(
        name="Contract Prompt",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_version(db, prompt_template):
    return PromptVersion.objects.create(
        original_template=prompt_template,
        template_version="v1",
        is_default=True,
        commit_message="Initial version",
    )


@pytest.fixture
def prompt_simulation(db, organization, workspace, prompt_template, prompt_version):
    return RunTest.objects.create(
        name="Contract Simulation",
        source_type="prompt",
        prompt_template=prompt_template,
        prompt_version=prompt_version,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def scenario(db, organization, workspace):
    return Scenarios.objects.create(
        name="Contract Scenario",
        source="Contract source",
        organization=organization,
        workspace=workspace,
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/simulate/prompt-simulations/scenarios/", None),
        (
            "get",
            f"/simulate/prompt-templates/{ANONYMOUS_PROMPT_TEMPLATE_ID}/simulations/",
            None,
        ),
        (
            "post",
            f"/simulate/prompt-templates/{ANONYMOUS_PROMPT_TEMPLATE_ID}/simulations/",
            {},
        ),
        (
            "get",
            f"/simulate/prompt-templates/{ANONYMOUS_PROMPT_TEMPLATE_ID}/simulations/{ANONYMOUS_PROMPT_RUN_TEST_ID}/",
            None,
        ),
        (
            "patch",
            f"/simulate/prompt-templates/{ANONYMOUS_PROMPT_TEMPLATE_ID}/simulations/{ANONYMOUS_PROMPT_RUN_TEST_ID}/",
            {},
        ),
        (
            "delete",
            f"/simulate/prompt-templates/{ANONYMOUS_PROMPT_TEMPLATE_ID}/simulations/{ANONYMOUS_PROMPT_RUN_TEST_ID}/",
            None,
        ),
        (
            "post",
            f"/simulate/prompt-templates/{ANONYMOUS_PROMPT_TEMPLATE_ID}/simulations/{ANONYMOUS_PROMPT_RUN_TEST_ID}/execute/",
            {},
        ),
        (
            "get",
            f"/simulate/export/{ANONYMOUS_PROMPT_RUN_TEST_ID}/?type=runtest",
            None,
        ),
    ],
)
def test_prompt_simulation_and_export_routes_reject_anonymous_before_work(
    api_client, method, path, body
):
    request = getattr(api_client, method)

    if body is None:
        response = request(path)
    else:
        response = request(path, body, format="json")

    assert response.status_code in {
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    }
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.integration
@pytest.mark.api
class TestPromptSimulationRuntimeContracts:
    def test_create_rejects_unknown_body_field(
        self, auth_client, prompt_template, prompt_version, scenario
    ):
        response = auth_client.post(
            f"/simulate/prompt-templates/{prompt_template.id}/simulations/",
            {
                "name": "New Simulation",
                "prompt_version_id": str(prompt_version.id),
                "scenario_ids": [str(scenario.id)],
                "prompt_template_id": str(prompt_template.id),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["prompt_template_id"] == ["Unknown field."]

    def test_update_rejects_unknown_body_field(
        self, auth_client, prompt_template, prompt_simulation
    ):
        response = auth_client.patch(
            f"/simulate/prompt-templates/{prompt_template.id}/simulations/{prompt_simulation.id}/",
            {"name": "Updated Simulation", "legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    def test_execute_rejects_unknown_body_field(
        self, auth_client, prompt_template, prompt_simulation
    ):
        response = auth_client.post(
            f"/simulate/prompt-templates/{prompt_template.id}/simulations/{prompt_simulation.id}/execute/",
            {"legacy_extra": True},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["details"]["legacy_extra"] == ["Unknown field."]

    @patch(
        "simulate.views.prompt_simulation.check_scenarios_incomplete", return_value=None
    )
    @patch("simulate.views.prompt_simulation.TestExecutor")
    def test_execute_uses_validated_scenario_ids(
        self,
        mock_executor_cls,
        _mock_check_scenarios,
        auth_client,
        prompt_template,
        prompt_simulation,
        scenario,
    ):
        prompt_simulation.scenarios.add(scenario)
        mock_executor = mock_executor_cls.return_value
        mock_executor.execute_test.return_value = {
            "success": True,
            "execution_id": scenario.id,
            "run_test_id": str(prompt_simulation.id),
            "status": "pending",
            "total_scenarios": 1,
            "total_calls": 1,
        }

        response = auth_client.post(
            f"/simulate/prompt-templates/{prompt_template.id}/simulations/{prompt_simulation.id}/execute/",
            {"scenario_ids": [str(scenario.id)]},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        mock_executor.execute_test.assert_called_once()
        assert mock_executor.execute_test.call_args.kwargs["scenario_ids"] == [
            str(scenario.id)
        ]
