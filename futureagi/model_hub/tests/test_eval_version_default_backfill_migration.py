"""Tests for migration 0113_backfill_eval_version_is_default.

Data-migration guarantees under test:
  * A template with zero is_default=True versions gets its highest-numbered
    non-deleted version promoted to default.
  * A template that already has a flagged version is left untouched.
  * A template with only soft-deleted versions is left untouched (no orphaned
    template to fix).
"""

import importlib

import pytest
from django.apps import apps as global_apps

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion


backfill_module = importlib.import_module(
    "model_hub.migrations.0113_backfill_eval_version_is_default"
)
backfill_is_default = backfill_module.backfill_is_default


def _make_template(organization, workspace, name):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


def _make_version(template, user, organization, workspace, number, is_default=False):
    # Bypass EvalTemplateVersionManager.create_version so we can seed the
    # exact "orphan" state (all is_default=False) that the migration exists
    # to repair.
    return EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=number,
        prompt_messages=[],
        config_snapshot={},
        criteria="",
        model="turing_large",
        is_default=is_default,
        created_by=user,
        organization=organization,
        workspace=workspace,
    )


@pytest.mark.unit
@pytest.mark.django_db
class TestBackfillIsDefault:
    def test_promotes_highest_version_when_no_default_flagged(
        self, organization, workspace, user
    ):
        template = _make_template(organization, workspace, "orphan-template")
        v1 = _make_version(template, user, organization, workspace, 1, is_default=False)
        v2 = _make_version(template, user, organization, workspace, 2, is_default=False)
        v3 = _make_version(template, user, organization, workspace, 3, is_default=False)

        backfill_is_default(global_apps, None)

        v1.refresh_from_db()
        v2.refresh_from_db()
        v3.refresh_from_db()
        assert v1.is_default is False
        assert v2.is_default is False
        assert v3.is_default is True

    def test_leaves_already_flagged_template_untouched(
        self, organization, workspace, user
    ):
        """User may have deliberately set an older version as default (e.g. v1
        stayed default while v2 was added). Backfill must never override that.
        """
        template = _make_template(organization, workspace, "already-flagged")
        v1 = _make_version(template, user, organization, workspace, 1, is_default=True)
        v2 = _make_version(template, user, organization, workspace, 2, is_default=False)

        backfill_is_default(global_apps, None)

        v1.refresh_from_db()
        v2.refresh_from_db()
        assert v1.is_default is True
        assert v2.is_default is False

    def test_leaves_template_with_only_soft_deleted_versions_untouched(
        self, organization, workspace, user
    ):
        template = _make_template(organization, workspace, "all-deleted")
        v1 = _make_version(template, user, organization, workspace, 1, is_default=False)
        v1.deleted = True
        v1.save(update_fields=["deleted"])

        backfill_is_default(global_apps, None)

        v1.refresh_from_db()
        assert v1.is_default is False

    def test_is_idempotent(self, organization, workspace, user):
        template = _make_template(organization, workspace, "idempotent")
        _make_version(template, user, organization, workspace, 1, is_default=False)
        v2 = _make_version(template, user, organization, workspace, 2, is_default=False)

        backfill_is_default(global_apps, None)
        backfill_is_default(global_apps, None)

        defaults = EvalTemplateVersion.objects.filter(
            eval_template=template, is_default=True, deleted=False
        )
        assert defaults.count() == 1
        assert defaults.first().id == v2.id
