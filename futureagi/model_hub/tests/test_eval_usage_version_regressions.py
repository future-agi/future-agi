"""Regression tests for the eval-usage version backfill migration (0115).

The OSS-guard and reverse-noop shell tests live in
test_eval_version_tracking.py; this file verifies the migration actually
touches data correctly: stamping, skip-if-already-stamped, and the
double-encoded-config unwrap.
"""

import importlib
import json
import uuid

import pytest

pytest.importorskip("ee.usage.models.usage")


def _load_migration():
    """Import the migration module (numeric prefix requires importlib)."""
    return importlib.import_module(
        "model_hub.migrations.0115_eval_usage_version_backfill"
    )


def test_migration_is_non_atomic():
    """Migration must declare atomic=False so each batch commits independently.

    Without this the full-table UPDATE and per-template loop run inside one
    transaction, holding a write lock on usage_apicalllog for the entire
    duration — a migration-timeout risk on large tables.
    """
    mod = _load_migration()
    assert mod.Migration.atomic is False


@pytest.mark.django_db
class TestMigrationBackfillLogic:
    """The actual backfill logic — version stamping and double-encode unwrap."""

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
        EvalTemplateVersion.objects.create_version(
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

    def test_backfill_creates_v1_for_versionless_template(
        self, organization, workspace
    ):
        """A user template with zero versions gets v1 created and stamped."""
        from django.apps import apps as real_apps
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name=f"backfill-v1-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "Pass/Fail"},
            criteria="test",
        )
        # Templates created through the API get a version automatically; a
        # legacy/raw-created template may have none. Simulate that.
        EvalTemplateVersion.objects.filter(eval_template=template).delete()

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

        created = EvalTemplateVersion.objects.filter(
            eval_template=template, deleted=False
        )
        assert created.count() == 1
        v1 = created.first()
        assert v1.version_number == 1
        assert v1.is_default is True

        log.refresh_from_db()
        assert log.config.get("version_id") == str(v1.id)
        assert log.config.get("version_number") == 1
