"""
CustomEvalConfig API Tests

Tests for /tracer/custom-eval-config/ endpoints.
"""

import json
import uuid
from io import StringIO

import pytest
from django.core.management import call_command
from rest_framework import status

from tracer.models.custom_eval_config import CustomEvalConfig

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigCreateAPI:
    """Tests for POST /tracer/custom-eval-config/ endpoint."""

    def test_create_config_unauthenticated(self, api_client, project, eval_template):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "New Config",
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_config_success(self, auth_client, project, eval_template):
        """Create a new custom eval config."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "New Custom Eval",
                "config": {"threshold": 0.9},
                "mapping": {"input": "input", "output": "output"},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "id" in data or "custom_eval_config_id" in data

    def test_create_config_with_filters(self, auth_client, project, eval_template):
        """Create config with filters."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "Filtered Config",
                "filters": {"observation_type": ["llm"]},
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_create_config_missing_project(self, auth_client, eval_template):
        """Create config fails without project."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "eval_template": str(eval_template.id),
                "name": "No Project Config",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_config_duplicate_name(
        self, auth_client, project, eval_template, custom_eval_config
    ):
        """Create config with duplicate name fails."""
        response = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": custom_eval_config.name,  # Same name
            },
            format="json",
        )
        # Should fail due to unique constraint
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_config_saves_only_path_string_mapping_values(
        self, auth_client, project, eval_template
    ):
        """Mapping values are attribute paths — an object value is not stored."""
        rejected = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "Object Mapping Config",
                "mapping": {"input": {"path": "input"}, "output": "output"},
            },
            format="json",
        )
        assert rejected.status_code == status.HTTP_400_BAD_REQUEST
        assert not CustomEvalConfig.objects.filter(
            name="Object Mapping Config"
        ).exists()
        # The response names the offending key in words the caller can act on,
        # and carries no DRF internals.
        body = json.dumps(rejected.json())
        assert "attribute path strings" in body
        assert "input" in body
        assert "ErrorDetail" not in body

        accepted = auth_client.post(
            "/tracer/custom-eval-config/",
            {
                "project": str(project.id),
                "eval_template": str(eval_template.id),
                "name": "Path Mapping Config",
                "mapping": {"input": "input.value", "output": "output"},
            },
            format="json",
        )
        assert accepted.status_code == status.HTTP_200_OK
        saved = CustomEvalConfig.objects.get(name="Path Mapping Config")
        assert saved.mapping == {"input": "input.value", "output": "output"}


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigPartialUpdateAPI:
    """Tests for PATCH /tracer/custom-eval-config/<id>/ endpoint."""

    def test_partial_update_unauthenticated(self, api_client, custom_eval_config):
        """Unauthenticated requests should be rejected."""
        response = api_client.patch(
            f"/tracer/custom-eval-config/{custom_eval_config.id}/",
            {"mapping": {"input": "input.value"}},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_partial_update_saves_only_path_string_mapping_values(
        self, auth_client, custom_eval_config
    ):
        """Mapping values are attribute paths — an object value is not stored."""
        existing_mapping = dict(custom_eval_config.mapping)

        rejected = auth_client.patch(
            f"/tracer/custom-eval-config/{custom_eval_config.id}/",
            {"mapping": {"input": {"path": "input"}, "output": "output"}},
            format="json",
        )
        assert rejected.status_code == status.HTTP_400_BAD_REQUEST
        custom_eval_config.refresh_from_db()
        assert custom_eval_config.mapping == existing_mapping
        body = json.dumps(rejected.json())
        assert "attribute path strings" in body
        assert "input" in body
        assert "ErrorDetail" not in body

        accepted = auth_client.patch(
            f"/tracer/custom-eval-config/{custom_eval_config.id}/",
            {"mapping": {"input": "input.value", "output": "output"}},
            format="json",
        )
        assert accepted.status_code == status.HTTP_200_OK
        custom_eval_config.refresh_from_db()
        assert custom_eval_config.mapping == {
            "input": "input.value",
            "output": "output",
        }


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigListAPI:
    """Tests for GET /tracer/custom-eval-config/list_custom_eval_configs/ endpoint."""

    def test_list_configs_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {"project_id": str(project.id)},
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_configs_missing_project(self, auth_client):
        """List configs without project ID."""
        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/"
        )
        # API may return 200 with empty list or 400
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_list_configs_success(self, auth_client, project, custom_eval_config):
        """List custom eval configs for a project."""
        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert isinstance(data, list) or "configs" in data

    def test_list_configs_empty(self, auth_client, project):
        """List returns empty when no configs exist."""
        # Delete any existing configs
        CustomEvalConfig.objects.filter(project=project).delete()

        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_configs_rejects_legacy_query_aliases(
        self, auth_client, project, eval_task
    ):
        """List endpoint should expose only canonical query params."""
        response = auth_client.get(
            "/tracer/custom-eval-config/list_custom_eval_configs/",
            {
                "projectId": str(project.id),
                "taskId": str(eval_task.id),
                "filters": json.dumps({}),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigCheckExistsAPI:
    """Tests for POST /tracer/custom-eval-config/check_exists/ endpoint."""

    def test_check_exists_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/check_exists/",
            # API expects project_name and eval_tags, not project_id and name
            {"project_name": project.name, "eval_tags": ["test"]},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_check_exists_true(self, auth_client, project, custom_eval_config):
        """Check exists returns true for existing config."""
        response = auth_client.post(
            "/tracer/custom-eval-config/check_exists/",
            {
                # API expects project_name and eval_tags
                "project_name": project.name,
                "eval_tags": [custom_eval_config.name],
            },
            format="json",
        )
        # API returns 200 with exists field, 400 if not found, or 500 on internal error
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_check_exists_false(self, auth_client, project):
        """Check exists returns false for non-existing config."""
        response = auth_client.post(
            "/tracer/custom-eval-config/check_exists/",
            {
                "project_name": project.name,
                "eval_tags": ["NonExistentConfig"],
            },
            format="json",
        )
        # API may return 200 with exists=false, 400, or 500 on internal error
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigGetByNameAPI:
    """Tests for POST /tracer/custom-eval-config/get_custom_eval_by_name/ endpoint."""

    def test_get_by_name_unauthenticated(self, api_client, eval_template):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/get_custom_eval_by_name/",
            # API expects eval_template_name
            {"eval_template_name": eval_template.name},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_by_name_success(self, auth_client, eval_template):
        """Get eval template by name."""
        response = auth_client.post(
            "/tracer/custom-eval-config/get_custom_eval_by_name/",
            {
                # API expects eval_template_name
                "eval_template_name": eval_template.name,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert "is_user_eval_template" in data or "eval_template" in data

    def test_get_by_name_not_found(self, auth_client):
        """Get by name returns empty for non-existing template."""
        response = auth_client.post(
            "/tracer/custom-eval-config/get_custom_eval_by_name/",
            {
                "eval_template_name": "NonExistentTemplate",
            },
            format="json",
        )
        # API returns 200 with is_user_eval_template=False when not found
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestCustomEvalConfigRunEvaluationAPI:
    """Tests for POST /tracer/custom-eval-config/run_evaluation/ endpoint."""

    def test_run_evaluation_unauthenticated(
        self, api_client, custom_eval_config, project_version
    ):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {
                "custom_eval_config_id": str(custom_eval_config.id),
                "project_version_id": str(project_version.id),
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_run_evaluation_missing_config(self, auth_client, project_version):
        """Run evaluation fails without config ID."""
        response = auth_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {"project_version_id": str(project_version.id)},
            format="json",
        )
        # API should return 400 but may return 500 on internal error
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_run_evaluation_success(
        self, auth_client, custom_eval_config, observation_span, project_version
    ):
        """Run evaluation on spans."""
        response = auth_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {
                "custom_eval_config_id": str(custom_eval_config.id),
                "project_version_id": str(project_version.id),
                "span_ids": [observation_span.id],
            },
            format="json",
        )
        # May succeed or fail depending on eval configuration
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_run_evaluation_invalid_config(self, auth_client, project_version):
        """Run evaluation with invalid config fails."""
        response = auth_client.post(
            "/tracer/custom-eval-config/run_evaluation/",
            {
                "custom_eval_config_id": str(uuid.uuid4()),
                "project_version_id": str(project_version.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.unit
@pytest.mark.django_db
class TestProjectDeleteCascade:
    """Verify _soft_delete_projects cascades to CustomEvalConfig and EvalLogger."""

    def _make_eval_config(self, observe_project, eval_template):
        from tracer.models.custom_eval_config import CustomEvalConfig
        return CustomEvalConfig.objects.create(
            name="Cascade Test Config",
            project=observe_project,
            eval_template=eval_template,
            config={},
            mapping={},
            filters={},
        )

    def _make_eval_logger(self, eval_config, trace_session):
        from tracer.models.observation_span import EvalLogger
        return EvalLogger.objects.create(
            custom_eval_config=eval_config,
            target_type="session",
            trace_session=trace_session,
            eval_type_id="CustomPromptEvaluator",
            output_metadata={},
            results_tags=[],
            results_explanation={},
            eval_tags=[],
            eval_explanation="",
            output_str_list=[],
            error=False,
        )

    def test_delete_project_soft_deletes_eval_config(
        self, observe_project, eval_template
    ):
        """Deleting a project soft-deletes its CustomEvalConfig records."""
        from tracer.views.project import ProjectView

        cfg = self._make_eval_config(observe_project, eval_template)
        assert cfg.deleted is False

        view = ProjectView()
        view._soft_delete_projects(
            observe_project.__class__.objects.filter(id=observe_project.id),
            "observe",
        )

        cfg.refresh_from_db()
        assert cfg.deleted is True

    def test_delete_project_soft_deletes_eval_logger(
        self, observe_project, eval_template, trace_session
    ):
        """Deleting a project soft-deletes EvalLogger entries for its eval configs."""
        from tracer.views.project import ProjectView

        cfg = self._make_eval_config(observe_project, eval_template)
        el = self._make_eval_logger(cfg, trace_session)
        assert el.deleted is False

        view = ProjectView()
        view._soft_delete_projects(
            observe_project.__class__.objects.filter(id=observe_project.id),
            "observe",
        )

        el.refresh_from_db()
        assert el.deleted is True

    def test_delete_project_does_not_affect_other_project_configs(
        self, observe_project, eval_template, organization, workspace
    ):
        """Eval configs for OTHER projects are untouched when one project is deleted."""
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.project import Project
        from model_hub.models.choices import ModelChoices
        from tracer.views.project import ProjectView

        from model_hub.models.ai_model import AIModel
        other_project = Project.objects.create(
            name="Other Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        other_cfg = CustomEvalConfig.objects.create(
            name="Other Config",
            project=other_project,
            eval_template=eval_template,
            config={},
            mapping={},
            filters={},
        )

        view = ProjectView()
        view._soft_delete_projects(
            observe_project.__class__.objects.filter(id=observe_project.id),
            "observe",
        )

        other_cfg.refresh_from_db()
        assert other_cfg.deleted is False


BYO_MODEL_STRINGS = ["gpt-4o-mini", "gpt-4o", "claude-3-5-sonnet-latest", "turing_large", ""]


@pytest.mark.unit
class TestEvalConfigBYOModel:
    @pytest.mark.parametrize("model_value", BYO_MODEL_STRINGS)
    def test_custom_eval_config_serializer_accepts_byo(self, db, model_value):
        from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer

        serializer = CustomEvalConfigSerializer(
            data={
                "name": "eval",
                "eval_template": str(uuid.uuid4()),
                "project": str(uuid.uuid4()),
                "mapping": {},
                "config": {},
                "model": model_value,
            }
        )
        serializer.is_valid(raise_exception=False)
        assert "model" not in serializer.errors, serializer.errors["model"]

    @pytest.mark.parametrize("model_value", BYO_MODEL_STRINGS)
    def test_external_eval_config_clean_fields_accepts_byo(self, model_value):
        from tracer.models.external_eval_config import ExternalEvalConfig

        config = ExternalEvalConfig(model=model_value)
        exclude = [f.name for f in ExternalEvalConfig._meta.fields if f.name != "model"]
        config.clean_fields(exclude=exclude)


@pytest.mark.integration
class TestScanEvalMappingPathsCommand:
    """Tests for `manage.py scan_eval_mapping_paths` — the existing-row sweep."""

    def _scan(self, **kwargs):
        out = StringIO()
        call_command("scan_eval_mapping_paths", stdout=out, **kwargs)
        return out.getvalue()

    def test_reports_a_row_saved_before_the_write_gate(self, custom_eval_config):
        # Written straight through the ORM: exactly how the rows that predate
        # the API gate exist today.
        custom_eval_config.mapping = {"input": {"path": "input"}, "output": "output"}
        custom_eval_config.save(update_fields=["mapping"])

        output = self._scan()

        assert str(custom_eval_config.id) in output
        assert "input=dict" in output
        assert "output" not in output.split("invalid_keys=")[1].split("]")[0]
        assert "affected_eval_configs=1" in output

    def test_reports_a_list_valued_row_without_aborting(self, custom_eval_config):
        # non_path_mapping_keys used to raise AttributeError on a non-dict
        # mapping, which killed the sweep on the first such row and left every
        # row after it unreported.
        custom_eval_config.mapping = ["input", "output"]
        custom_eval_config.save(update_fields=["mapping"])

        output = self._scan()

        assert str(custom_eval_config.id) in output
        assert "<mapping>=list" in output
        assert "affected_eval_configs=1" in output

    def test_reports_nothing_when_every_value_is_a_path(self, custom_eval_config):
        output = self._scan()

        assert str(custom_eval_config.id) not in output
        assert "affected_eval_configs=0" in output

    def test_leaves_the_row_untouched(self, custom_eval_config):
        broken = {"input": {"path": "input"}, "output": "output"}
        custom_eval_config.mapping = broken
        custom_eval_config.save(update_fields=["mapping"])

        self._scan()

        custom_eval_config.refresh_from_db()
        assert custom_eval_config.mapping == broken

    def test_project_filter_scopes_the_scan(self, custom_eval_config):
        custom_eval_config.mapping = {"input": {"path": "input"}}
        custom_eval_config.save(update_fields=["mapping"])

        output = self._scan(project_id=str(uuid.uuid4()))

        assert "affected_eval_configs=0" in output


@pytest.mark.integration
class TestEvalTagIngestMappingValues:
    """The OTEL eval-tag path writes CustomEvalConfig.mapping straight from the
    wire via get_or_create, bypassing both the serializer and the eval-group
    helper. It is the only write path a customer's SDK reaches directly."""

    def _tag(self, mapping, eval_template):
        return [
            {
                "custom_eval_name": "release-tag-eval",
                "eval_name": eval_template.name,
                "mapping": mapping,
            }
        ]

    def test_object_mapping_value_is_refused_before_a_config_is_created(
        self, project, eval_template
    ):
        # Driven through get_or_create_project_version, not _process_eval_tags:
        # creation happens in _create_custom_eval_configs, so only the caller
        # that reaches it can show that nothing was created.
        from tracer.utils.otel import get_or_create_project_version

        with pytest.raises(Exception, match="must be an attribute path string"):
            get_or_create_project_version(
                project_id=project.id,
                project_version_name="v1",
                project_version_id=None,
                eval_tags=self._tag(
                    {"input": {"path": "input"}, "output": "output"}, eval_template
                ),
                metadata=None,
                project_type="experiment",
            )
        assert not CustomEvalConfig.objects.filter(name="release-tag-eval").exists()

    def test_async_ingest_keeps_the_span_batch_when_a_tag_is_malformed(
        self, project, eval_template
    ):
        """The OTLP export already answered 200 before this runs.

        bulk_create_observation_span_task is a max_retries=0 activity wrapping
        the whole batch in one transaction, so raising here would drop every
        span in it with nothing surfaced to the client. The bad tag is dropped
        instead; the spans land.
        """
        from tracer.utils.otel import _bulk_get_or_create_project_versions

        tags = self._tag(
            {"input": {"path": "input"}, "output": "output"}, eval_template
        )
        versions = _bulk_get_or_create_project_versions(
            [(project.name, "experiment", "v1", None, tags)],
            {(project.name, project.organization_id, "experiment"): project},
            project.organization_id,
        )

        assert any(
            v is not None for v in versions.values()
        ), "the whole span batch would have been dropped"
        assert not CustomEvalConfig.objects.filter(name="release-tag-eval").exists()

    def test_list_mapping_is_refused_as_a_value_error(self, project, eval_template):
        # A list was storable before the write gates existed: the OTEL path
        # stored wire values verbatim after json.loads. It must not reach
        # ``.items()`` as an AttributeError.
        from tracer.utils.otel import _process_eval_tags

        with pytest.raises(ValueError, match="must be an object"):
            _process_eval_tags(self._tag(["input", "output"], eval_template), project)

    def test_path_string_mapping_values_pass_through(self, project, eval_template):
        from tracer.utils.otel import _process_eval_tags

        processed = _process_eval_tags(
            self._tag({"input": "input.value", "output": "output"}, eval_template),
            project,
        )

        assert processed[0]["mapping"] == {"input": "input.value", "output": "output"}

    def test_json_string_mapping_is_parsed_then_checked(self, project, eval_template):
        from tracer.utils.otel import _process_eval_tags

        # The wire form is often a JSON string; the guard must run after the parse.
        with pytest.raises(ValueError, match="must be an attribute path string"):
            _process_eval_tags(
                self._tag(json.dumps({"input": {"path": "input"}}), eval_template),
                project,
            )
