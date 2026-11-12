"""The short-lock preflight cannot overwrite an account installed meanwhile."""

from unittest.mock import patch

import pytest
from django.db.models.query import QuerySet

from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.tests.test_mcp_vendor_lifecycle import vendor_settings as vendor_settings
from agentcc.tests.test_mcp_vendor_round2 import fake_api, setup_connection

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("new_row", [False, True])
def test_preflight_race_refuses_without_overwriting_account(new_row):
    _, user, _, bound, row, identity = setup_connection("composio")
    subject = row.end_user_key
    if new_row:
        row.delete()
    first = QuerySet.first
    raced = False

    def first_and_race(queryset):
        nonlocal raced
        snapshot = first(queryset)
        if queryset.model is type(row) and not raced:
            raced = True
            if new_row:
                type(row).objects.create(
                    binding=bound,
                    user=user,
                    source="member",
                    end_user_key=subject,
                    status="active",
                    vendor_account_ref="ca_racing_fixture",
                )
            else:
                type(row).objects.filter(pk=row.pk).update(
                    status="active", vendor_account_ref="ca_racing_fixture"
                )
        return snapshot

    api = fake_api()
    api.create.return_value = (
        "trs_new_fixture",
        "https://backend.composio.dev/mcp/new",
    )
    api.connected_account_ref = "ca_new_fixture"
    with (
        patch.object(QuerySet, "first", first_and_race),
        patch.object(lifecycle, "vendor_client", return_value=api),
    ):
        with pytest.raises(lifecycle.ConnectionRefused, match="cleanup_pending"):
            lifecycle.start(bound.pk, identity)
    api.create.assert_not_called()
    api.connect.assert_not_called()
    assert (
        type(row).objects.get(binding=bound).vendor_account_ref == "ca_racing_fixture"
    )
