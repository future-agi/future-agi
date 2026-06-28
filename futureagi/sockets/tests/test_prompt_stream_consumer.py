"""Tests for PromptStreamConsumer — focussed on the permission-check path.

Covers the fix for TH-5944: validate_template_access must check the
template's org against ALL of the user's active org memberships, not
just the single org resolved at WS-connect time.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sockets.prompt_stream_consumer import (
    PromptStreamConsumer,
    WS_CLOSE_CODE_NOT_FOUND,
    WS_CLOSE_CODE_PERMISSION_DENIED,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_consumer():
    consumer = PromptStreamConsumer()
    consumer.scope = {"type": "websocket", "query_string": b""}
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer.channel_name = "test-channel"
    consumer.channel_layer = MagicMock()
    consumer.session_uuid = "test-session"
    return consumer


def _make_user(org_id=None):
    user = MagicMock(is_authenticated=True)
    user.organization_id = org_id
    user.organization = MagicMock(id=org_id) if org_id else None
    return user


def _make_template(org_id):
    tpl = MagicMock()
    tpl.organization_id = org_id
    return tpl


# ── user_can_access_template selector ────────────────────────────────────────

def _patch_memberships(active_org_ids, has_any_membership_for_fk=False):
    """Return a context manager that mocks OrganizationMembership.objects.

    get_user_org_ids calls .filter() twice:
      1. filter(user=…, is_active=True).values_list(…) → active org ids
      2. filter(user=…, organization_id=fk_id).exists() → inactive check
    """
    mock_mgr = MagicMock()
    active_qs = MagicMock()
    active_qs.values_list.return_value = active_org_ids
    inactive_qs = MagicMock()
    inactive_qs.exists.return_value = has_any_membership_for_fk

    def _filter(**kwargs):
        if "is_active" in kwargs:
            return active_qs
        return inactive_qs

    mock_mgr.filter.side_effect = _filter
    return patch("accounts.services.template_access.OrganizationMembership.objects", mock_mgr)


class TestUserCanAccessTemplate:
    """Pure unit tests — no DB, no consumer, just the selector function."""

    def test_user_with_matching_primary_org_no_memberships(self):
        """FK org is added when user has no membership row for it at all."""
        from accounts.services.template_access import user_can_access_template

        user = _make_user(org_id="org-a")
        template = _make_template(org_id="org-a")

        with _patch_memberships(active_org_ids=[], has_any_membership_for_fk=False):
            assert user_can_access_template(user, template) is True

    def test_user_with_membership_in_template_org(self):
        from accounts.services.template_access import user_can_access_template

        user = _make_user(org_id="org-a")
        template = _make_template(org_id="org-b")

        with _patch_memberships(active_org_ids=["org-b"]):
            assert user_can_access_template(user, template) is True

    def test_user_without_access_to_template_org(self):
        from accounts.services.template_access import user_can_access_template

        user = _make_user(org_id="org-a")
        template = _make_template(org_id="org-c")

        with _patch_memberships(active_org_ids=["org-a"], has_any_membership_for_fk=True):
            assert user_can_access_template(user, template) is False

    def test_multi_org_user_can_access_any_of_their_orgs(self):
        from accounts.services.template_access import user_can_access_template

        user = _make_user(org_id="org-a")
        template = _make_template(org_id="org-c")

        with _patch_memberships(active_org_ids=["org-a", "org-b", "org-c"]):
            assert user_can_access_template(user, template) is True

    def test_removed_user_fk_org_not_re_granted(self):
        """FK org is NOT added when user has an inactive membership row for it."""
        from accounts.services.template_access import user_can_access_template

        user = _make_user(org_id="org-a")
        template = _make_template(org_id="org-a")

        # Active memberships empty, but an inactive row exists for org-a
        with _patch_memberships(active_org_ids=[], has_any_membership_for_fk=True):
            assert user_can_access_template(user, template) is False


# ── validate_template_access consumer method ─────────────────────────────────

@pytest.mark.asyncio
class TestValidateTemplateAccess:

    async def test_valid_access_sends_no_error(self):
        consumer = _make_consumer()
        consumer.user = _make_user(org_id="org-a")

        mock_template = _make_template("org-a")

        with patch(
            "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
            return_value=mock_template,
        ), _patch_memberships(active_org_ids=[], has_any_membership_for_fk=False):
            result = await consumer.validate_template_access("tpl-id")

        assert result is True
        consumer.send_json.assert_not_called()
        consumer.close.assert_not_called()

    async def test_no_permission_sends_error_and_closes_4003(self):
        consumer = _make_consumer()
        consumer.user = _make_user(org_id="org-a")

        mock_template = _make_template("org-b")

        with patch(
            "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
            return_value=mock_template,
        ), patch(
            "accounts.services.template_access.OrganizationMembership.objects"
        ) as mock_mgr:
            mock_mgr.filter.return_value.values_list.return_value = ["org-a"]
            result = await consumer.validate_template_access("tpl-id")

        assert result is False
        consumer.send_json.assert_awaited_once()
        sent = consumer.send_json.call_args[0][0]
        assert sent["type"] == "error"
        assert "permission" in sent["message"].lower()
        consumer.close.assert_awaited_once_with(code=WS_CLOSE_CODE_PERMISSION_DENIED)

    async def test_not_found_sends_error_and_closes_4004(self):
        from model_hub.models.run_prompt import PromptTemplate

        consumer = _make_consumer()
        consumer.user = _make_user(org_id="org-a")

        with patch(
            "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
            side_effect=PromptTemplate.DoesNotExist,
        ):
            result = await consumer.validate_template_access("bad-id")

        assert result is False
        consumer.close.assert_awaited_once_with(code=WS_CLOSE_CODE_NOT_FOUND)

    async def test_multi_org_user_passes_for_any_membership(self):
        consumer = _make_consumer()
        consumer.user = _make_user(org_id="org-a")

        mock_template = _make_template("org-c")

        with patch(
            "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
            return_value=mock_template,
        ), patch(
            "accounts.services.template_access.OrganizationMembership.objects"
        ) as mock_mgr:
            mock_mgr.filter.return_value.values_list.return_value = [
                "org-a", "org-b", "org-c"
            ]
            result = await consumer.validate_template_access("tpl-id")

        assert result is True


# ── close-code contract ───────────────────────────────────────────────────────

class TestWsCloseCodes:
    """The BE close codes must stay in sync with the FE WS_CLOSE_CODES constant.

    Having these assertions means a drift (e.g. someone changes 4003 → 4010
    on one side) breaks CI immediately rather than silently shipping a dead
    close-handler.
    """

    def test_permission_denied_code_matches_fe_constant(self):
        assert WS_CLOSE_CODE_PERMISSION_DENIED == 4003

    def test_not_found_code_matches_fe_constant(self):
        assert WS_CLOSE_CODE_NOT_FOUND == 4004

    def test_validate_template_access_uses_constants_not_magic_numbers(self):
        """Ensure the close calls reference the module constants, not literals.

        This test drives a real 4003 close through validate_template_access and
        asserts the consumer closes with the constant value so that if the
        constant changes the assertion fails loudly.
        """
        import asyncio

        consumer = _make_consumer()
        consumer.user = _make_user(org_id="org-a")
        mock_template = _make_template("org-b")

        async def _run():
            with patch(
                "sockets.prompt_stream_consumer.PromptTemplate.objects.get",
                return_value=mock_template,
            ), patch(
                "accounts.services.template_access.OrganizationMembership.objects"
            ) as mock_mgr:
                mock_mgr.filter.return_value.values_list.return_value = ["org-a"]
                return await consumer.validate_template_access("tpl-id")

        result = asyncio.get_event_loop().run_until_complete(_run())
        assert result is False
        consumer.close.assert_awaited_once_with(code=WS_CLOSE_CODE_PERMISSION_DENIED)
        # Verify the constant hasn't drifted from the FE value
        assert consumer.close.call_args.kwargs["code"] == 4003
