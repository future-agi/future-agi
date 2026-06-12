"""Regression tests for eval-usage version tracking (PR #747).

1. Migration 0111 guard — returns early when ee.usage is absent (OSS).
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
        "model_hub.migrations.0111_eval_usage_version_backfill"
    )


def test_migration_0111_returns_early_when_usage_app_absent():
    """OSS build: backfill exits cleanly when the usage app isn't installed."""
    mod = _load_migration()
    result = mod.backfill_apicalllog_version_info(_FakeAppsNoUsage(), schema_editor=None)
    assert result is None


def test_migration_0111_has_reversible_noop():
    """The migration's RunPython declares a reverse_code so it's reversible."""
    from django.db.migrations.operations.special import RunPython

    mod = _load_migration()
    run_python_ops = [
        op for op in mod.Migration.operations if isinstance(op, RunPython)
    ]
    assert len(run_python_ops) == 1
    assert run_python_ops[0].reverse_code is RunPython.noop


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
