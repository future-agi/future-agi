"""Tests for template_format normalization (issue #2079).

A run prompt can be previewed with one template engine and executed with
another because template_format is read from two different places depending
on the code path (run_prompt_config.template_format vs the legacy
configuration.template_format) and was not normalized when the prompt was
saved.

These tests pin the canonical accessor (resolve_template_format) and the
persistence behavior so preview and persisted execution always agree.
"""

import uuid
from unittest.mock import patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import SourceChoices, StatusType
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.run_prompt import RunPrompter
from model_hub.views.run_prompt import (
    DEFAULT_TEMPLATE_FORMAT,
    TEMPLATE_FORMAT_JINJA_PUBLIC,
    TEMPLATE_FORMAT_MUSTACHE,
    normalize_template_format,
    resolve_template_format,
)
from tfc.middleware.workspace_context import set_workspace_context


# ==================== Canonical accessor unit tests ====================


class TestNormalizeTemplateFormat:
    def test_jinja2_alias_maps_to_public_spelling(self):
        assert normalize_template_format("jinja2") == TEMPLATE_FORMAT_JINJA_PUBLIC

    def test_public_spelling_is_preserved(self):
        assert normalize_template_format("jinja") == TEMPLATE_FORMAT_JINJA_PUBLIC

    def test_mustache_is_preserved(self):
        assert normalize_template_format("mustache") == "mustache"


class TestResolveTemplateFormat:
    def test_reads_current_key_first(self):
        config = {
            "run_prompt_config": {"template_format": "mustache"},
            "configuration": {"template_format": "jinja"},
        }
        assert resolve_template_format(config) == TEMPLATE_FORMAT_MUSTACHE

    def test_falls_back_to_legacy_configuration_key(self):
        config = {"configuration": {"template_format": "jinja"}}
        assert resolve_template_format(config) == TEMPLATE_FORMAT_JINJA_PUBLIC

    def test_normalizes_legacy_jinja2_alias(self):
        config = {"run_prompt_config": {"template_format": "jinja2"}}
        assert resolve_template_format(config) == TEMPLATE_FORMAT_JINJA_PUBLIC

    def test_missing_format_resolves_to_consistent_default(self):
        assert resolve_template_format({}) == DEFAULT_TEMPLATE_FORMAT
        assert resolve_template_format(None) == DEFAULT_TEMPLATE_FORMAT

    def test_accepts_bare_run_prompt_config_dict(self):
        # Execution path passes the persisted run_prompt_config directly.
        assert (
            resolve_template_format({"template_format": "jinja2"})
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_bare_dict_without_format_resolves_to_default(self):
        assert resolve_template_format({}) == DEFAULT_TEMPLATE_FORMAT


# ==================== Persistence behavior (API) ====================


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="test@example.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = APIClient()
    client.force_authenticate(user=user)
    set_workspace_context(workspace=workspace, organization=user.organization)
    return client


@pytest.fixture
def dataset(db, organization, workspace):
    ds = Dataset.objects.create(name="Test Dataset", organization=organization)
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def row(db, dataset):
    from model_hub.models.develop_dataset import Row

    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def valid_config():
    return {
        "model": "gpt-4",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "What is {{col}}?"}],
            }
        ],
        "output_format": "string",
        "run_prompt_config": {"model_name": "gpt-4"},
    }


@pytest.mark.django_db
class TestAddRunPromptColumnNormalizesTemplateFormat:
    def test_persists_public_spelling_when_saved_as_jinja2(
        self, auth_client, dataset, valid_config
    ):
        config = {
            **valid_config,
            "run_prompt_config": {
                **valid_config["run_prompt_config"],
                "template_format": "jinja2",
            },
        }
        payload = {"dataset_id": str(dataset.id), "name": "AI Response", "config": config}

        with patch("model_hub.tasks.run_prompt.process_prompts_single.apply_async"):
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        prompter = RunPrompter.objects.get(name="AI Response")
        assert (
            prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_migrates_legacy_configuration_key_on_save(
        self, auth_client, dataset, valid_config
    ):
        # Older clients send template_format under configuration.* — it must
        # be migrated into run_prompt_config so execution sees it.
        config = {
            **valid_config,
            "configuration": {"template_format": "mustache"},
        }
        payload = {"dataset_id": str(dataset.id), "name": "AI Response", "config": config}

        with patch("model_hub.tasks.run_prompt.process_prompts_single.apply_async"):
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        prompter = RunPrompter.objects.get(name="AI Response")
        assert (
            prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_MUSTACHE
        )


@pytest.mark.django_db
class TestEditRunPromptColumnPreservesTemplateFormat:
    def _make_prompter(self, dataset, run_prompt_config=None):
        return RunPrompter.objects.create(
            name="Test Run Prompter",
            dataset=dataset,
            organization=dataset.organization,
            status=StatusType.NOT_STARTED.value,
            model="gpt-4",
            messages=[{"role": "user", "content": "Test prompt"}],
            run_prompt_config=run_prompt_config or {},
        )

    def _make_column(self, dataset, prompter):
        column = Column.objects.create(
            name="Run Prompt Output",
            dataset=dataset,
            data_type="text",
            source=SourceChoices.RUN_PROMPT.value,
        )
        column.source_id = prompter.id
        column.save()
        return column

    def test_edit_without_template_format_keeps_stored_value(
        self, auth_client, dataset, valid_config
    ):
        prompter = self._make_prompter(
            dataset, run_prompt_config={"template_format": "mustache"}
        )
        column = self._make_column(dataset, prompter)

        # Payload omits template_format entirely (e.g. partial edit from an
        # older client) — the stored value must survive.
        payload = {
            "dataset_id": str(dataset.id),
            "column_id": str(column.id),
            "name": "Updated",
            "config": valid_config,
        }

        with patch("model_hub.tasks.run_prompt.process_prompts_single.apply_async"):
            response = auth_client.post(
                "/model-hub/develops/edit_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        prompter.refresh_from_db()
        assert prompter.run_prompt_config["template_format"] == "mustache"

    def test_edit_with_jinja2_normalizes_to_public_spelling(
        self, auth_client, dataset, valid_config
    ):
        prompter = self._make_prompter(dataset)
        column = self._make_column(dataset, prompter)

        config = {
            **valid_config,
            "run_prompt_config": {
                **valid_config["run_prompt_config"],
                "template_format": "jinja2",
            },
        }
        payload = {
            "dataset_id": str(dataset.id),
            "column_id": str(column.id),
            "name": "Updated",
            "config": config,
        }

        with patch("model_hub.tasks.run_prompt.process_prompts_single.apply_async"):
            response = auth_client.post(
                "/model-hub/develops/edit_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        prompter.refresh_from_db()
        assert (
            prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )


@pytest.mark.django_db
class TestPreviewRunPromptColumnUsesResolvedFormat:
    def _post_preview(self, auth_client, dataset, config):
        from unittest.mock import MagicMock

        payload = {
            "dataset_id": str(dataset.id),
            "row_indices": [1],
            "config": config,
        }

        with patch(
            "model_hub.views.run_prompt.populate_placeholders",
            return_value=[],
        ) as mock_populate, patch(
            "model_hub.views.run_prompt.litellm.completion"
        ) as mock_completion:
            mock_completion.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="Preview response"))],
                usage=MagicMock(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            )
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                payload,
                format="json",
            )
        return response, mock_populate

    def test_preview_passes_template_format_from_config(
        self, auth_client, dataset, row, valid_config
    ):
        config = {
            **valid_config,
            "run_prompt_config": {
                **valid_config["run_prompt_config"],
                "template_format": "mustache",
            },
        }

        response, mock_populate = self._post_preview(auth_client, dataset, config)

        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]
        assert mock_populate.call_args.kwargs["template_format"] == "mustache"

    def test_preview_falls_back_to_legacy_configuration_key(
        self, auth_client, dataset, row, valid_config
    ):
        config = {
            **valid_config,
            "configuration": {"template_format": "jinja2"},
        }

        response, mock_populate = self._post_preview(auth_client, dataset, config)

        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]
        assert (
            mock_populate.call_args.kwargs["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )
