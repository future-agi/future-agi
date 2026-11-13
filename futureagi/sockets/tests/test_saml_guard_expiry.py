"""A membership change makes the next push revalidate a SAML socket."""

import asyncio
import types
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from sockets.saml_guard import SamlSocketGuard


@pytest.mark.parametrize("monotonic_now", [5.0, 10_000.0], ids=["early", "later"])
def test_expire_makes_the_next_push_revalidate(monotonic_now):
    consumer = types.SimpleNamespace(
        scope={
            "user": types.SimpleNamespace(id=uuid4()),
            "auth_scope": types.SimpleNamespace(org_id=uuid4()),
            "auth_token_id": uuid4(),
        },
        close=AsyncMock(),
    )
    guard = SamlSocketGuard(consumer)

    async def run():
        assert await guard.ensure_live(inbound=True)
        # A push within the interval reuses the result.
        assert await guard.ensure_live(inbound=False)
        guard.expire()
        assert await guard.ensure_live(inbound=False)

    with (
        patch("sockets.saml_guard.time.monotonic", return_value=monotonic_now),
        patch.object(SamlSocketGuard, "_validate", new_callable=AsyncMock) as check,
    ):
        asyncio.run(run())
    assert check.await_count == 2
