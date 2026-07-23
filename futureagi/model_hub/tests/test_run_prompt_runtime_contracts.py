import uuid

import pytest
from rest_framework import status


def assert_unknown_field(response, field_name):
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"][field_name] == ["Unknown field."]


def prompt_config():
    return {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Answer {{input}}"}],
        "output_format": "string",
    }


# ────────────────────────────────────────────────────────────────────────
# Endpoint × (canonical field, legacy camel-alias) matrix.
#
# All entries share the same shape assertion: POST the endpoint with a
# valid payload + one legacy camelCase alias field → the serializer must
# reject the alias with an "unknown field" error. The base serializer's
# ``extra = "forbid"`` guard is a single library behaviour; testing it
# once per endpoint (previously 5 nearly-identical tests) is what this
# parametrization replaces. Mirrors the pattern applied to
# ``test_dataset_runtime_contracts.py`` under TH-7128.
#
# Each case: (id, url_factory, payload, legacy_alias)
# url_factory is a callable so per-test UUIDs are fresh (evaluating
# ``uuid.uuid4()`` at import time would produce a shared value).
# ────────────────────────────────────────────────────────────────────────
_REJECT_UNKNOWN_FIELD_CASES = [
    (
        "litellm_run_prompt",
        lambda: "/model-hub/run-prompt/",
        {
            "dataset_id": str(uuid.uuid4()),
            "model": "gpt-4o",
            "name": "prompt-column",
            "messages": [{"role": "user", "content": "Answer {{input}}"}],
            "modelName": "legacy camel alias",
        },
        "modelName",
    ),
    (
        "add_run_prompt_column",
        lambda: "/model-hub/develops/add_run_prompt_column/",
        {
            "dataset_id": str(uuid.uuid4()),
            "name": "prompt-column",
            "config": prompt_config(),
            "datasetId": "legacy camel alias",
        },
        "datasetId",
    ),
    (
        "preview_run_prompt_column",
        lambda: "/model-hub/develops/preview_run_prompt_column/",
        {
            "dataset_id": str(uuid.uuid4()),
            "name": "prompt-column",
            "config": prompt_config(),
            "first_n_rows": 1,
            "firstNRows": 1,
        },
        "firstNRows",
    ),
    (
        "edit_run_prompt_column",
        lambda: "/model-hub/develops/edit_run_prompt_column/",
        {
            "dataset_id": str(uuid.uuid4()),
            "column_id": str(uuid.uuid4()),
            "name": "prompt-column",
            "config": prompt_config(),
            "columnId": "legacy camel alias",
        },
        "columnId",
    ),
    (
        "run_prompt_for_rows",
        lambda: "/model-hub/run-prompt-for-rows/",
        {
            "run_prompt_ids": [str(uuid.uuid4())],
            "row_ids": [str(uuid.uuid4())],
            "selected_all_rows": False,
            "selectedAllRows": True,
        },
        "selectedAllRows",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_factory,payload,legacy_alias",
    [case[1:] for case in _REJECT_UNKNOWN_FIELD_CASES],
    ids=[case[0] for case in _REJECT_UNKNOWN_FIELD_CASES],
)
def test_endpoint_rejects_unknown_request_field(
    auth_client, url_factory, payload, legacy_alias
):
    response = auth_client.post(url_factory(), payload, format="json")
    assert_unknown_field(response, legacy_alias)
