"""Version responses must describe the saved inputs used by versioned runs."""

import pytest

from model_hub.models.run_prompt import PromptVersion
from model_hub.serializers.prompt_template import PromptHistoryExecutionSerializer

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def saved_versions(prompt_template, prompt_version):
    prompt_version.prompt_config_snapshot = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Reply with {{country}}"}],
            }
        ],
        "configuration": {},
    }
    prompt_version.variable_names = {"country": ["France"]}
    prompt_version.save(update_fields=["prompt_config_snapshot", "variable_names"])
    latest = PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v2",
        prompt_config_snapshot=prompt_version.prompt_config_snapshot,
        variable_names={"country": ["Japan"]},
    )
    prompt_template.variable_names = {"country": ["Japan"]}
    prompt_template.save(update_fields=["variable_names"])
    return prompt_template, prompt_version, latest


@pytest.mark.parametrize("endpoint", ["history", "versions"])
def test_history_returns_each_versions_saved_variables(
    auth_client, saved_versions, endpoint
):
    template, original, latest = saved_versions
    if endpoint == "history":
        response = auth_client.get(
            "/model-hub/prompt-history-executions/",
            {"template_id": str(template.id)},
        )
    else:
        response = auth_client.get(
            f"/model-hub/prompt-templates/{template.id}/versions/"
        )

    assert response.status_code == 200
    variables = {
        row["template_version"]: row["variable_names"]
        for row in response.json()["results"]
    }
    assert variables == {
        "v1": {"country": ["France"]},
        "v2": {"country": ["Japan"]},
    }
    original.refresh_from_db()
    latest.refresh_from_db()
    template.refresh_from_db()
    assert original.variable_names == {"country": ["France"]}
    assert latest.variable_names == template.variable_names == {"country": ["Japan"]}


def test_compare_returns_each_versions_saved_variables(auth_client, saved_versions):
    template, _, _ = saved_versions
    response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/compare-versions/",
        {"versions": ["v1", "v2"]},
        format="json",
    )

    assert response.status_code == 200
    assert [row["variable_names"] for row in response.json()["result"]["data"]] == [
        {"country": ["France"]},
        {"country": ["Japan"]},
    ]


def test_current_draft_editor_loads_and_saves_its_own_variables(
    auth_client, saved_versions
):
    template, original, draft = saved_versions
    draft.is_draft = True
    draft.save(update_fields=["is_draft"])

    response = auth_client.get(f"/model-hub/prompt-templates/{template.id}/")
    assert response.status_code == 200
    assert response.json()["version"] == "v2"
    assert response.json()["variable_names"] == {"country": ["Japan"]}

    response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/run_template/",
        {
            "version": "v2",
            "is_run": False,
            "prompt_config": [draft.prompt_config_snapshot],
            "variable_names": {"country": ["Canada"]},
        },
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["result"]["variable_names"] == {"country": ["Canada"]}
    response = auth_client.get(f"/model-hub/prompt-templates/{template.id}/")
    assert response.status_code == 200
    assert response.json()["variable_names"] == {"country": ["Canada"]}
    draft.refresh_from_db()
    template.refresh_from_db()
    original.refresh_from_db()
    assert draft.variable_names == template.variable_names == {"country": ["Canada"]}
    assert original.variable_names == {"country": ["France"]}


def test_new_draft_returns_its_saved_inputs_not_the_parent_values(
    auth_client, saved_versions
):
    template, _, latest = saved_versions
    response = auth_client.post(
        f"/model-hub/prompt-templates/{template.id}/add-new-draft/",
        {
            "new_prompts": [
                {
                    "prompt_config": [latest.prompt_config_snapshot],
                    "variable_names": {"country": ["Canada"]},
                    "evaluation_configs": [],
                }
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    row = response.json()["result"][0]
    assert row["is_draft"] is True
    assert row["variable_names"] == {"country": ["Canada"]}
    draft = PromptVersion.no_workspace_objects.get(id=row["id"])
    assert draft.variable_names == {"country": ["Canada"]}
    template.refresh_from_db()
    assert template.variable_names == {"country": ["Japan"]}


def test_version_serialization_does_not_pad_or_mutate_saved_inputs(prompt_version):
    saved = {"country": ["France", "Japan"], "tone": ["formal"]}
    prompt_version.variable_names = saved
    prompt_version.save(update_fields=["variable_names"])

    data = PromptHistoryExecutionSerializer(prompt_version).data

    assert data["variable_names"] == {
        "country": ["France", "Japan"],
        "tone": ["formal"],
    }
    assert prompt_version.variable_names == {
        "country": ["France", "Japan"],
        "tone": ["formal"],
    }


@pytest.mark.parametrize("variables", [None, [], ["country"], {}])
def test_legacy_empty_variables_do_not_inherit_parent_values(prompt_version, variables):
    prompt_version.original_template.variable_names = {"country": ["Japan"]}
    prompt_version.variable_names = variables

    assert PromptHistoryExecutionSerializer(prompt_version).data["variable_names"] == {}
