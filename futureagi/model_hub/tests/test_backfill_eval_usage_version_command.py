"""Tests for the standalone backfill_eval_usage_version management command.

This command is the "run once, on a single pod, after deploy" replacement
for the expensive part of migration 0115 — it must produce the same
stamping result without being wired into any migration.
"""

import json
import uuid

import pytest

from model_hub.management.commands.backfill_eval_usage_version import (
    backfill_usage_logs,
)

pytestmark = pytest.mark.requires_ee


@pytest.mark.django_db(databases=["default", "default_direct"])
class TestBackfillUsageLogsCommand:
    def test_stamps_version_id_on_logs_without_it(self, organization, workspace):
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name=f"cmd-backfill-{uuid.uuid4().hex[:6]}",
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

        log = APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config={"output": {"output": 1.0}},
        )

        result = backfill_usage_logs(only_template=str(template.id))

        log.refresh_from_db()
        assert log.config.get("version_id") == str(version.id)
        assert log.config.get("version_number") == version.version_number
        assert result["updated"] == 1

    def test_skips_logs_that_already_have_version_id(self, organization, workspace):
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name=f"cmd-skip-{uuid.uuid4().hex[:6]}",
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

        backfill_usage_logs(only_template=str(template.id))

        log.refresh_from_db()
        assert log.config["version_id"] == existing_version_id

    def test_unwraps_double_encoded_config(self, organization, workspace):
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate

        template = EvalTemplate.no_workspace_objects.create(
            name=f"cmd-unwrap-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={},
            criteria="test",
        )
        log = APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config=json.dumps({"output": {"output": 0.9}}),
        )
        log.refresh_from_db()
        assert isinstance(log.config, str), "Precondition: config should be a string"

        backfill_usage_logs(only_template=str(template.id))

        log.refresh_from_db()
        assert isinstance(log.config, dict)
        assert log.config.get("output", {}).get("output") == 0.9

    def test_creates_v1_for_versionless_template(self, organization, workspace):
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name=f"cmd-v1-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            config={"output": "Pass/Fail"},
            criteria="test",
        )
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

        backfill_usage_logs(only_template=str(template.id))

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

    def test_single_pass_stamps_multiple_templates(self, organization, workspace):
        """One table pass stamps logs of several templates with their own versions."""
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        expected = {}
        logs = []
        for n in range(3):
            template = EvalTemplate.no_workspace_objects.create(
                name=f"cmd-multi-{n}-{uuid.uuid4().hex[:6]}",
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
            expected[str(template.id)] = (str(version.id), version.version_number)
            logs.append(
                APICallLog.objects.create(
                    organization=organization,
                    workspace=workspace,
                    status=APICallStatusChoices.SUCCESS.value,
                    cost=0,
                    source=SourceChoices.EVAL_PLAYGROUND.value,
                    source_id=str(template.id),
                    config={"output": {"output": 1.0}},
                )
            )

        result = backfill_usage_logs()

        assert result["updated"] >= 3
        for log in logs:
            log.refresh_from_db()
            version_id, version_number = expected[log.source_id]
            assert log.config["version_id"] == version_id
            assert log.config["version_number"] == version_number

    def test_unwraps_and_stamps_in_single_pass(self, organization, workspace):
        """A double-encoded config is unwrapped AND stamped in the same run,
        while a string config from an unmapped source is only unwrapped."""
        from ee.usage.models.usage import APICallLog, APICallStatusChoices
        from model_hub.models.choices import OwnerChoices, SourceChoices
        from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion

        template = EvalTemplate.no_workspace_objects.create(
            name=f"cmd-onepass-{uuid.uuid4().hex[:6]}",
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

        stamped_log = APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config=json.dumps({"output": {"output": 0.5}}),  # double-encoded
        )
        # String config whose source is not a user eval template — must be
        # unwrapped but never stamped.
        unmapped_log = APICallLog.objects.create(
            organization=organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(uuid.uuid4()),
            config=json.dumps({"output": {"output": 0.7}}),  # double-encoded
        )

        backfill_usage_logs()

        stamped_log.refresh_from_db()
        assert isinstance(stamped_log.config, dict)
        assert stamped_log.config["output"] == {"output": 0.5}
        assert stamped_log.config["version_id"] == str(version.id)
        assert stamped_log.config["version_number"] == version.version_number

        unmapped_log.refresh_from_db()
        assert isinstance(unmapped_log.config, dict)
        assert unmapped_log.config["output"] == {"output": 0.7}
        assert "version_id" not in unmapped_log.config

    def test_command_is_callable_via_manage_py(self, organization, workspace):
        """The Command wrapper (used by `manage.py`) delegates correctly."""
        from django.core.management import call_command

        call_command("backfill_eval_usage_version", "--dry-run")
