"""Unrelated members keep resolving while one synthetic refresh is waiting."""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import timedelta
from threading import Event
from unittest.mock import patch

import pytest
from django.db import close_old_connections
from django.utils import timezone

from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_upstream_tokens as tokens
from agentcc.tests.mcp_upstream_oauth_helpers import SERVER_ID, member
from agentcc.tests.test_mcp_internal_tokens import _connected
from agentcc.tests.test_mcp_internal_tokens import _settings as _settings
from agentcc.tests.test_mcp_internal_tokens import config as config
from agentcc.tests.test_mcp_internal_tokens import vendor as vendor


@pytest.mark.django_db(transaction=True)
def test_unrelated_member_grant_resolves_within_token_channel_timeout(
    organization, user, workspace, config, vendor
):
    other, _ = member(organization, workspace)
    _connected(organization, user, vendor, expires_in=timedelta(seconds=-1))
    _connected(organization, other, vendor)
    started, release = Event(), Event()

    def synthetic_refresh(row, url):
        started.set()
        if not release.wait(15):
            raise AssertionError("synthetic refresh release timed out")
        return connect._Tokens(
            access=connect.Sealed("synthetic-refreshed"),
            refresh=None,
            expires_at=timezone.now() + timedelta(hours=1),
            scopes=("mcp",),
        )

    def answer(member_id):
        close_old_connections()
        try:
            return tokens.upstream_token(
                str(organization.pk), str(member_id), SERVER_ID
            ).outcome
        finally:
            close_old_connections()

    with patch.object(tokens, "_refresh", side_effect=synthetic_refresh):
        with ThreadPoolExecutor(max_workers=2) as workers:
            refreshing = workers.submit(answer, user.pk)
            try:
                assert started.wait(5), "first member did not reach synthetic refresh"
                unaffected = workers.submit(answer, other.pk)
                done, _ = wait([unaffected], timeout=5.5)
                completed_before_release = unaffected in done
            finally:
                release.set()
            assert refreshing.result(timeout=10) == "refreshed"
            assert unaffected.result(timeout=10) == "ok"
    assert completed_before_release, (
        "unrelated member's valid grant exceeded the gateway token channel's "
        "5-second timeout while another member's refresh held the organization lock"
    )
