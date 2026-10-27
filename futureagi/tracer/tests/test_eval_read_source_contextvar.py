"""The eval read-source override: the new engine forces CH for one entry's run
without changing the global default or the legacy cron path."""

import asyncio

from asgiref.sync import sync_to_async

from tracer.services.clickhouse.v2.eval_loader import _read_source, eval_read_source


def test_override_visible_inside_block_and_reverts():
    before = _read_source()
    with eval_read_source("clickhouse"):
        assert _read_source() == "clickhouse"
    assert _read_source() == before


def test_nested_override_restores_outer():
    with eval_read_source("clickhouse"):
        assert _read_source() == "clickhouse"
        with eval_read_source("postgres"):
            assert _read_source() == "postgres"
        assert _read_source() == "clickhouse"


def test_default_without_override_is_not_forced():
    # No context set → falls through to settings/env/default, never "forced".
    assert _read_source() in {"postgres", "clickhouse"}


def test_override_visible_through_thread_sensitive_false_sync_to_async():
    # Mirrors the temporal activity: the sync body (which enters the context)
    # runs in a thread_sensitive=False worker thread.
    def _inner():
        with eval_read_source("clickhouse"):
            return _read_source()

    async def _run():
        return await sync_to_async(_inner, thread_sensitive=False)()

    assert asyncio.run(_run()) == "clickhouse"
