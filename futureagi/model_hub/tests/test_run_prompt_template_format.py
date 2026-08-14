"""
Regression tests for template_format normalization (issue #2079).

A run prompt can be previewed with one template engine and executed with
another because template_format was read from two different places depending
on the code path and was never normalized when the prompt was saved:

- ``config.run_prompt_config.template_format`` — current clients
- ``config.configuration.template_format`` — older clients (legacy)

These tests pin the canonical accessor (resolve_template_format) and verify
that Add/Edit persist the public spelling, Edit merges instead of replacing
(so an omitted template_format keeps its stored value), and Preview resolves
the same value that persisted execution will use.

Run with: pytest model_hub/tests/test_run_prompt_template_format.py -v
"""

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import MagicMock, patch

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import RunPrompter
from model_hub.views.run_prompt import (
    TEMPLATE_FORMAT_FSTRING,
    TEMPLATE_FORMAT_JINJA2,
    TEMPLATE_FORMAT_JINJA_PUBLIC,
    TEMPLATE_FORMAT_MUSTACHE,
    normalize_template_format,
    render_template,
    resolve_template_format,
)
from tfc.middleware.workspace_context import set_workspace_context


# ==================== Fixtures ====================


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
    ds = Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    ds.column_order = []
    ds.save()
    return ds


@pytest.fixture
def input_column(db, dataset):
    col = Column.objects.create(
        name="Input Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def row(db, dataset):
    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def cell(db, dataset, input_column, row):
    return Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=row,
        value="Test input value",
    )


@pytest.fixture
def run_prompt_column(db, dataset):
    col = Column.objects.create(
        name="Run Prompt Output",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    dataset.column_order.append(str(col.id))
    dataset.save()
    return col


@pytest.fixture
def run_prompter(db, dataset, organization, workspace):
    return RunPrompter.objects.create(
        name="Test Run Prompter",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.NOT_STARTED.value,
        model="gpt-4",
        messages=[{"role": "user", "content": "Test prompt"}],
        run_prompt_config={},
    )


def valid_run_prompt_config():
    return {
        "model": "gpt-4",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "What is {{Input Column}}?"}],
            }
        ],
        "temperature": 0.7,
        "max_tokens": 100,
        "output_format": "string",
    }


# ==================== Unit tests: accessor ====================


class TestNormalizeTemplateFormat:
    def test_maps_internal_jinja2_to_public_spelling(self):
        assert normalize_template_format(TEMPLATE_FORMAT_JINJA2) == (
            TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_public_spelling_passes_through(self):
        assert normalize_template_format(TEMPLATE_FORMAT_JINJA_PUBLIC) == (
            TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_other_formats_unchanged(self):
        assert normalize_template_format(TEMPLATE_FORMAT_MUSTACHE) == (
            TEMPLATE_FORMAT_MUSTACHE
        )
        assert normalize_template_format(TEMPLATE_FORMAT_FSTRING) == (
            TEMPLATE_FORMAT_FSTRING
        )


class TestResolveTemplateFormat:
    def test_current_key_wins_over_legacy_key(self):
        config = {
            "run_prompt_config": {"template_format": TEMPLATE_FORMAT_MUSTACHE},
            "configuration": {"template_format": TEMPLATE_FORMAT_JINJA2},
        }
        assert resolve_template_format(config) == TEMPLATE_FORMAT_MUSTACHE

    def test_falls_back_to_legacy_configuration_key(self):
        config = {
            "run_prompt_config": {},
            "configuration": {"template_format": TEMPLATE_FORMAT_FSTRING},
        }
        assert resolve_template_format(config) == TEMPLATE_FORMAT_FSTRING

    def test_normalizes_jinja2_to_public_spelling(self):
        config = {"run_prompt_config": {"template_format": TEMPLATE_FORMAT_JINJA2}}
        # The accessor returns the PUBLIC spelling, not the raw constant.
        assert resolve_template_format(config) == TEMPLATE_FORMAT_JINJA_PUBLIC

    def test_missing_value_returns_normalized_default(self):
        # The default is the normalized public spelling ("jinja"), NOT the raw
        # DEFAULT_TEMPLATE_FORMAT constant ("jinja2").
        assert resolve_template_format({}) == TEMPLATE_FORMAT_JINJA_PUBLIC
        assert resolve_template_format(None) == TEMPLATE_FORMAT_JINJA_PUBLIC

    def test_bare_persisted_config_normalizes_jinja2(self):
        # The persisted run_prompt_config is a bare dict (no nested keys).
        assert (
            resolve_template_format({"template_format": TEMPLATE_FORMAT_JINJA2})
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_bare_persisted_config_passes_other_formats(self):
        assert (
            resolve_template_format({"template_format": TEMPLATE_FORMAT_MUSTACHE})
            == TEMPLATE_FORMAT_MUSTACHE
        )


class TestRenderTemplatePublicSpelling:
    def test_public_jinja_spelling_renders_like_jinja2(self):
        template = "Hello {{ name }}!"
        context = {"name": "World"}
        assert (
            render_template(template, context, template_format=TEMPLATE_FORMAT_JINJA_PUBLIC)
            == "Hello World!"
        )
        assert (
            render_template(template, context, template_format=TEMPLATE_FORMAT_JINJA2)
            == "Hello World!"
        )

    def test_mustache_still_renders(self):
        assert (
            render_template(
                "Hello {{ name }}!",
                {"name": "World"},
                template_format=TEMPLATE_FORMAT_MUSTACHE,
            )
            == "Hello World!"
        )


# ==================== API tests: Add persists normalized ====================


@pytest.mark.django_db
class TestAddRunPromptTemplateFormat:
    def test_add_normalizes_jinja2_to_public_spelling(
        self, auth_client, dataset, input_column
    ):
        config = valid_run_prompt_config()
        config["run_prompt_config"] = {
            "model_name": "gpt-4o-mini",
            "model_type": "llm",
            "template_format": TEMPLATE_FORMAT_JINJA2,
        }
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Normalized Add",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ):
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="Normalized Add")
        assert (
            run_prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_add_persists_normalized_default_when_omitted(
        self, auth_client, dataset, input_column
    ):
        config = valid_run_prompt_config()
        config["run_prompt_config"] = {"model_name": "gpt-4o-mini"}
        payload = {
            "dataset_id": str(dataset.id),
            "name": "Defaulted Add",
            "config": config,
        }

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ):
            response = auth_client.post(
                "/model-hub/develops/add_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter = RunPrompter.objects.get(name="Defaulted Add")
        assert (
            run_prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )


# ==================== API tests: Edit merges instead of replacing ====================


@pytest.mark.django_db
class TestEditRunPromptTemplateFormat:
    def _link_and_payload(self, run_prompter, run_prompt_column, dataset, config):
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()
        return {
            "dataset_id": str(dataset.id),
            "column_id": str(run_prompt_column.id),
            "name": "Edited",
            "config": config,
        }

    def test_edit_omitted_template_format_keeps_stored_value(
        self,
        auth_client,
        dataset,
        run_prompt_column,
        run_prompter,
    ):
        # Stored config carries a non-default format; the edit payload omits it.
        run_prompter.run_prompt_config = {
            "model_name": "gpt-4o-mini",
            "template_format": TEMPLATE_FORMAT_MUSTACHE,
        }
        run_prompter.save()

        config = valid_run_prompt_config()
        config["run_prompt_config"] = {"model_name": "gpt-4o-mini"}
        payload = self._link_and_payload(
            run_prompter, run_prompt_column, dataset, config
        )

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ):
            response = auth_client.post(
                "/model-hub/develops/edit_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter.refresh_from_db()
        assert (
            run_prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_MUSTACHE
        )

    def test_edit_incoming_template_format_wins_and_is_normalized(
        self,
        auth_client,
        dataset,
        run_prompt_column,
        run_prompter,
    ):
        run_prompter.run_prompt_config = {
            "model_name": "gpt-4o-mini",
            "template_format": TEMPLATE_FORMAT_MUSTACHE,
        }
        run_prompter.save()

        config = valid_run_prompt_config()
        config["run_prompt_config"] = {
            "model_name": "gpt-4o-mini",
            "template_format": TEMPLATE_FORMAT_JINJA2,
        }
        payload = self._link_and_payload(
            run_prompter, run_prompt_column, dataset, config
        )

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ):
            response = auth_client.post(
                "/model-hub/develops/edit_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter.refresh_from_db()
        assert (
            run_prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_edit_normalizes_stored_jinja2_on_any_edit(
        self,
        auth_client,
        dataset,
        run_prompt_column,
        run_prompter,
    ):
        # Legacy stored "jinja2" is normalized to the public spelling even when
        # the payload does not mention template_format.
        run_prompter.run_prompt_config = {
            "model_name": "gpt-4o-mini",
            "template_format": TEMPLATE_FORMAT_JINJA2,
        }
        run_prompter.save()

        config = valid_run_prompt_config()
        config["run_prompt_config"] = {"model_name": "gpt-4o-mini"}
        payload = self._link_and_payload(
            run_prompter, run_prompt_column, dataset, config
        )

        with patch(
            "model_hub.tasks.run_prompt.process_prompts_single.apply_async"
        ):
            response = auth_client.post(
                "/model-hub/develops/edit_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run_prompter.refresh_from_db()
        assert (
            run_prompter.run_prompt_config["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )


# ==================== API tests: Retrieve returns public spelling ====================


@pytest.mark.django_db
class TestRetrieveRunPromptTemplateFormat:
    def test_retrieve_normalizes_stored_jinja2(
        self, auth_client, run_prompt_column, run_prompter
    ):
        run_prompter.run_prompt_config = {
            "model_name": "gpt-4o-mini",
            "template_format": TEMPLATE_FORMAT_JINJA2,
        }
        run_prompter.save()
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        response = auth_client.get(
            "/model-hub/develops/retrieve_run_prompt_column_config/",
            {"column_id": str(run_prompt_column.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        config = response.json()["result"]["config"]
        assert (
            config["run_prompt_config"]["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )

    def test_retrieve_defaults_missing_format_to_public_spelling(
        self, auth_client, run_prompt_column, run_prompter
    ):
        run_prompter.run_prompt_config = {"model_name": "gpt-4o-mini"}
        run_prompter.save()
        run_prompt_column.source_id = run_prompter.id
        run_prompt_column.save()

        response = auth_client.get(
            "/model-hub/develops/retrieve_run_prompt_column_config/",
            {"column_id": str(run_prompt_column.id)},
        )

        assert response.status_code == status.HTTP_200_OK
        config = response.json()["result"]["config"]
        assert (
            config["run_prompt_config"]["template_format"]
            == TEMPLATE_FORMAT_JINJA_PUBLIC
        )


# ==================== API tests: Preview resolves the same format ====================


@pytest.mark.django_db
class TestPreviewRunPromptTemplateFormat:
    def _preview_payload(self, dataset, config):
        return {
            "dataset_id": str(dataset.id),
            "name": "Preview column",
            "row_indices": [1],
            "config": config,
        }

    def test_preview_resolves_current_key(
        self, auth_client, dataset, input_column, row, cell
    ):
        config = valid_run_prompt_config()
        config["run_prompt_config"] = {
            "model_name": "gpt-4o-mini",
            "template_format": TEMPLATE_FORMAT_MUSTACHE,
        }
        payload = self._preview_payload(dataset, config)

        with patch(
            "model_hub.views.run_prompt.populate_placeholders",
            return_value=[
                {"role": "user", "content": [{"type": "text", "text": "hi"}]}
            ],
        ) as mock_populate, patch(
            "model_hub.views.run_prompt.RunPrompt.litellm_response",
            return_value=(
                "Preview response",
                {
                    "metadata": {"usage": {}, "cost": {}},
                    "data": {"response": "Preview response"},
                },
            ),
        ):
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert mock_populate.call_count == 1
        kwargs = mock_populate.call_args.kwargs
        assert kwargs["template_format"] == TEMPLATE_FORMAT_MUSTACHE

    def test_preview_uses_normalized_default_when_omitted(
        self, auth_client, dataset, input_column, row, cell
    ):
        config = valid_run_prompt_config()
        config["run_prompt_config"] = {"model_name": "gpt-4o-mini"}
        payload = self._preview_payload(dataset, config)

        with patch(
            "model_hub.views.run_prompt.populate_placeholders",
            return_value=[
                {"role": "user", "content": [{"type": "text", "text": "hi"}]}
            ],
        ) as mock_populate, patch(
            "model_hub.views.run_prompt.RunPrompt.litellm_response",
            return_value=(
                "Preview response",
                {
                    "metadata": {"usage": {}, "cost": {}},
                    "data": {"response": "Preview response"},
                },
            ),
        ):
            response = auth_client.post(
                "/model-hub/develops/preview_run_prompt_column/",
                payload,
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        assert mock_populate.call_count == 1
        kwargs = mock_populate.call_args.kwargs
        assert kwargs["template_format"] == TEMPLATE_FORMAT_JINJA_PUBLIC
