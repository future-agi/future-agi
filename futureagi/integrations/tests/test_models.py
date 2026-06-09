"""Tests for integrations model invariants.

Covers the platform category map (single source of truth for "data sync"
vs "action only" platforms) and the partial unique constraint that pins
action-only platforms to one live row per (organization, workspace,
platform).
"""

import pytest
from django.db import IntegrityError, transaction

from integrations.models import (
    ACTION_ONLY_PLATFORMS,
    ConnectionStatus,
    IntegrationCategory,
    IntegrationConnection,
    IntegrationPlatform,
    PLATFORM_CATEGORY,
)


# ---------------------------------------------------------------------------
# Platform category map
# ---------------------------------------------------------------------------


class TestPlatformCategoryMap:
    """`PLATFORM_CATEGORY` is the single source of truth — every member of
    `IntegrationPlatform` must have a category. Forgetting to register a
    new platform here is a footgun the sync orchestrator and uniqueness
    rules can't recover from."""

    def test_every_platform_has_a_category(self):
        for platform in IntegrationPlatform:
            assert platform.value in PLATFORM_CATEGORY, (
                f"{platform.value} missing from PLATFORM_CATEGORY — "
                "add an entry when introducing a new platform."
            )

    def test_every_category_value_is_an_enum_member(self):
        valid_categories = {c.value for c in IntegrationCategory}
        for platform, category in PLATFORM_CATEGORY.items():
            assert category.value in valid_categories, (
                f"{platform} -> {category} is not a valid IntegrationCategory."
            )

    def test_linear_is_action_only(self):
        assert (
            PLATFORM_CATEGORY[IntegrationPlatform.LINEAR.value]
            == IntegrationCategory.ACTION_ONLY
        )

    @pytest.mark.parametrize(
        "platform",
        [
            IntegrationPlatform.LANGFUSE,
            IntegrationPlatform.DATADOG,
            IntegrationPlatform.POSTHOG,
            IntegrationPlatform.MIXPANEL,
            IntegrationPlatform.CLOUD_STORAGE,
            IntegrationPlatform.MESSAGE_QUEUE,
        ],
    )
    def test_data_sync_platforms_have_data_sync_category(self, platform):
        assert (
            PLATFORM_CATEGORY[platform.value] == IntegrationCategory.DATA_SYNC
        )

    def test_action_only_platforms_derived_from_map(self):
        """`ACTION_ONLY_PLATFORMS` is derived; it must match the map."""
        expected = frozenset(
            p
            for p, cat in PLATFORM_CATEGORY.items()
            if cat == IntegrationCategory.ACTION_ONLY
        )
        assert ACTION_ONLY_PLATFORMS == expected


# ---------------------------------------------------------------------------
# Partial unique constraint
# ---------------------------------------------------------------------------


def _make_linear(organization, workspace, **overrides):
    """Helper — minimal IntegrationConnection row for Linear."""
    defaults = dict(
        organization=organization,
        workspace=workspace,
        platform=IntegrationPlatform.LINEAR.value,
        display_name="Linear",
        host_url="https://linear.app",
        encrypted_credentials=b"fake",
        external_project_name="Engineering",
        status=ConnectionStatus.ACTIVE,
    )
    defaults.update(overrides)
    return IntegrationConnection.no_workspace_objects.create(**defaults)


@pytest.mark.django_db(transaction=True)
class TestActionOnlyUniqueConstraint:
    """`uq_intconn_org_ws_action_only_active` — partial unique on
    (organization, workspace, platform) WHERE platform in action-only set
    AND deleted=False."""

    def test_second_live_linear_raises_integrity_error(
        self, organization, workspace
    ):
        _make_linear(organization, workspace)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                _make_linear(organization, workspace, display_name="dup")

    def test_soft_deleted_row_does_not_block_new_insert(
        self, organization, workspace
    ):
        first = _make_linear(organization, workspace)
        first.deleted = True
        first.save(update_fields=["deleted"])

        # Should not raise — partial index excludes deleted=True rows.
        second = _make_linear(organization, workspace, display_name="rotated")
        assert second.pk != first.pk

    def test_different_workspaces_can_each_have_linear(
        self, organization, workspace, user
    ):
        """Constraint is per-workspace, not per-org."""
        from accounts.models.workspace import Workspace

        other_workspace = Workspace.objects.create(
            name="Other Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )

        _make_linear(organization, workspace)
        # No raise — different workspace.
        _make_linear(organization, other_workspace, display_name="ws2")
