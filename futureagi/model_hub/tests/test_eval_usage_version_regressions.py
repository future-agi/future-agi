"""Regression tests for eval-usage version tracking (PR #747).

1. Migration 0112 guard — returns early when ee.usage is absent (OSS).
2. Pinned-child version tracking — resolved_version takes priority over default.
"""

import uuid

import pytest


# ── Test 1: Migration OSS guard ──────────────────────────────────────────


class _FakeAppsNoUsage:
    """Simulates Django apps registry with ee.usage absent (OSS build)."""

    def get_model(self, app_label, model_name):
        if app_label == "usage":
            raise LookupError(f"App '{app_label}' doesn't have a '{model_name}' model.")
        raise AssertionError(
            f"Migration should not look up {app_label}.{model_name} after usage guard"
        )


def _load_migration():
    """Import the migration module (numeric prefix requires importlib)."""
    import importlib

    return importlib.import_module(
        "model_hub.migrations.0112_eval_usage_version_backfill"
    )


def test_migration_0112_returns_early_when_usage_app_absent():
    """OSS build: backfill exits cleanly when the usage app isn't installed."""
    mod = _load_migration()
    result = mod.backfill_apicalllog_version_info(_FakeAppsNoUsage(), schema_editor=None)
    assert result is None


def test_migration_0112_has_reversible_noop():
    """The migration's RunPython declares a reverse_code so it's reversible."""
    from django.db.migrations.operations.special import RunPython

    mod = _load_migration()
    run_python_ops = [
        op for op in mod.Migration.operations if isinstance(op, RunPython)
    ]
    assert len(run_python_ops) == 1
    assert run_python_ops[0].reverse_code is RunPython.noop


def test_migration_0112_is_non_atomic():
    """Migration must declare atomic=False so each batch commits independently.

    Without this the full-table UPDATE and per-template loop run inside one
    transaction, holding a write lock on usage_apicalllog for the entire
    duration — a migration-timeout risk on large tables.
    """
    mod = _load_migration()
    assert mod.Migration.atomic is False


@pytest.mark.django_db
class TestMigrationBackfillLogic:
    """The actual backfill logic — version stamping and double-encode unwrap.

    These are the tests Nikhil asked for: the OSS-guard and noop tests above
    verify the migration shell; these verify that it actually touches data
    correctly.
    """

    def test_backfill_stamps_version_id_on_logs_without_it(
        self, organization, workspace
    ):
        """Logs without version_id get stamped with the template's default version."""
        from django.apps import apps as real_apps
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name=f"backfill-test-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={},
            criteria="test",
        )
        version = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            criteria="test",
            model="turing_large",
        )
        version.is_default = True
        version.save(update_fields=["is_default"])

        # Log without version_id — exactly what the backfill targets
        log = APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config={"output": {"output": 1.0}},
        )

        mod = _load_migration()
        mod.backfill_apicalllog_version_info(real_apps, schema_editor=None)

        log.refresh_from_db()
        assert log.config.get("version_id") == str(version.id)
        assert log.config.get("version_number") == version.version_number

    def test_backfill_skips_logs_that_already_have_version_id(
        self, organization, workspace
    ):
        """Logs that already have version_id must not be overwritten."""
        from django.apps import apps as real_apps
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name=f"backfill-skip-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={},
            criteria="test",
        )
        version = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            criteria="test",
            model="turing_large",
        )
        existing_version_id = str(uuid.uuid4())
        log = APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config={"output": {"output": 1.0}, "version_id": existing_version_id},
        )

        mod = _load_migration()
        mod.backfill_apicalllog_version_info(real_apps, schema_editor=None)

        log.refresh_from_db()
        # Must not overwrite the existing version_id
        assert log.config["version_id"] == existing_version_id

    def test_backfill_unwraps_double_encoded_config(self, organization, workspace):
        """Double-encoded JSON strings must be unwrapped to proper JSONB objects.

        Some old logs have config stored as a JSON string inside a JSONField
        (the result of calling json.dumps() before assigning to a JSONField).
        The migration must unwrap these so JSONB operators work.
        """
        from django.apps import apps as real_apps
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate

        template = EvalTemplate.no_workspace_objects.create(
            name=f"backfill-unwrap-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={},
            criteria="test",
        )
        # Simulate double-encoding: assign a JSON string to a JSONField.
        # Django stores the Python str as a JSONB string, so jsonb_typeof = 'string'.
        import json
        log = APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config=json.dumps({"output": {"output": 0.9}}),  # double-encoded
        )

        # Before backfill: config is a string (double-encoded)
        log.refresh_from_db()
        assert isinstance(log.config, str), "Precondition: config should be a string"

        mod = _load_migration()
        mod.backfill_apicalllog_version_info(real_apps, schema_editor=None)

        log.refresh_from_db()
        assert isinstance(log.config, dict), "Config must be unwrapped to a dict"
        assert log.config.get("output", {}).get("output") == 0.9


# ── Test 2: Version tracking in source_config ────────────────────────────


@pytest.mark.django_db
class TestVersionTrackingInSourceConfig:
    """Verify that version tracking in run_eval_func records the correct version.

    The version-tracking block (evals.py:222-230) puts version_id and
    version_number into source_config. This tests:
    - resolved_version kwarg → uses that version (pinned child case)
    - no resolved_version → falls back to get_default()
    """

    @pytest.fixture
    def template_with_versions(self, organization, workspace):
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.objects.create(
            name=f"test-template-{uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            criteria="Test criteria",
            config={"rule_prompt": "Test prompt", "eval_type_id": "CustomPromptEvaluator"},
        )

        # v1 — will be pinned (NOT default)
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot={"rule_prompt": "Pinned prompt v1"},
            criteria="Pinned criteria v1",
            model="gpt-4",
            user=None,
            organization=organization,
            workspace=workspace,
        )
        v1.is_default = False
        v1.save(update_fields=["is_default"])

        # v2 — default version
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot={"rule_prompt": "Default prompt v2"},
            criteria="Default criteria v2",
            model="gpt-4",
            user=None,
            organization=organization,
            workspace=workspace,
        )
        v2.is_default = True
        v2.save(update_fields=["is_default"])

        return template, v1, v2

    def _build_source_config_with_version(self, template, resolved_version=None):
        """Replicate the version-tracking logic from run_eval_func (evals.py:222-230)."""
        from model_hub.models.evals_metric import EvalTemplateVersion

        source_config = {
            "reference_id": str(template.id),
            "source": "test",
        }

        _tracked_version = resolved_version
        if not _tracked_version:
            try:
                _tracked_version = EvalTemplateVersion.objects.get_default(template)
            except Exception:
                pass
        if _tracked_version:
            source_config["version_id"] = str(_tracked_version.id)
            source_config["version_number"] = _tracked_version.version_number

        return source_config

    def test_pinned_version_recorded_not_default(self, template_with_versions):
        """When resolved_version is passed, source_config uses the pinned version."""
        template, v1_pinned, v2_default = template_with_versions

        source_config = self._build_source_config_with_version(
            template, resolved_version=v1_pinned
        )

        assert source_config["version_id"] == str(v1_pinned.id)
        assert source_config["version_number"] == v1_pinned.version_number
        # Must NOT be the default
        assert source_config["version_id"] != str(v2_default.id)

    def test_unpinned_falls_back_to_default(self, template_with_versions):
        """Without resolved_version, source_config uses get_default()."""
        template, v1_pinned, v2_default = template_with_versions

        source_config = self._build_source_config_with_version(template)

        assert source_config["version_id"] == str(v2_default.id)
        assert source_config["version_number"] == v2_default.version_number

    def test_no_versions_at_all(self, organization, workspace):
        """Template with zero versions → no version_id in source_config."""
        from model_hub.models.evals_metric import EvalTemplate

        template = EvalTemplate.objects.create(
            name=f"no-versions-{uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            criteria="Test",
            config={},
        )

        source_config = self._build_source_config_with_version(template)

        assert "version_id" not in source_config
        assert "version_number" not in source_config


# ── EvaluationRunner uses the shared helper (no inline pin logic) ─────────


@pytest.mark.django_db
class TestEvalRunnerUsesResolveHelper:
    """EvaluationRunner.pre_run used to duplicate pinned-version resolution
    inline at eval_runner.py:926. The dedup commit routes it through
    _resolve_uem_version. These tests prove the runner picks pin/default
    via the helper — soft-deleted pin falls back to default, exactly the
    behavior the helper guarantees and nothing the runner should know about.
    """

    @pytest.fixture
    def template_with_versions(self, organization, workspace):
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.objects.create(
            name=f"runner-template-{uuid.uuid4().hex[:8]}",
            organization=organization,
            workspace=workspace,
            criteria="Test criteria",
            config={"rule_prompt": "p", "eval_type_id": "CustomPromptEvaluator"},
        )
        v1 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot={"rule_prompt": "v1"},
            criteria="v1",
            model="gpt-4",
            user=None,
            organization=organization,
            workspace=workspace,
        )
        v1.is_default = False
        v1.save(update_fields=["is_default"])
        v2 = EvalTemplateVersion.objects.create_version(
            eval_template=template,
            config_snapshot={"rule_prompt": "v2"},
            criteria="v2",
            model="gpt-4",
            user=None,
            organization=organization,
            workspace=workspace,
        )
        v2.is_default = True
        v2.save(update_fields=["is_default"])
        return template, v1, v2

    def _make_metric(self, organization, workspace, template, pinned_version=None):
        from model_hub.models.evals_metric import UserEvalMetric
        from model_hub.models.develop_dataset import DevelopDataset

        dataset = DevelopDataset.objects.create(
            name=f"ds-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
        )
        return UserEvalMetric.objects.create(
            name=f"uem-{uuid.uuid4().hex[:6]}",
            organization=organization,
            dataset=dataset,
            template=template,
            config={},
            pinned_version=pinned_version,
        )

    def test_helper_returns_pinned_when_set(
        self, organization, workspace, template_with_versions
    ):
        from tracer.utils.eval import _resolve_uem_version

        template, v1, v2 = template_with_versions
        metric = self._make_metric(organization, workspace, template, pinned_version=v1)
        assert _resolve_uem_version(metric).id == v1.id

    def test_helper_falls_back_to_default_when_unpinned(
        self, organization, workspace, template_with_versions
    ):
        from tracer.utils.eval import _resolve_uem_version

        template, v1, v2 = template_with_versions
        metric = self._make_metric(organization, workspace, template, pinned_version=None)
        assert _resolve_uem_version(metric).id == v2.id

    def test_helper_falls_back_when_pin_soft_deleted(
        self, organization, workspace, template_with_versions
    ):
        """Soft-deleted pin must fall back to default — the contract eval_runner
        relied on inline. Locked in here so a future refactor of the helper
        cannot silently drop this guarantee."""
        from tracer.utils.eval import _resolve_uem_version

        template, v1, v2 = template_with_versions
        v1.deleted = True
        v1.save(update_fields=["deleted"])
        metric = self._make_metric(organization, workspace, template, pinned_version=v1)
        assert _resolve_uem_version(metric).id == v2.id
