"""trajectory_seen_reset against the test Redis (a private database index)."""

from __future__ import annotations

import io
import os
import re
from pathlib import Path

import pytest
from django.core.management import call_command
from django.test import override_settings

from tracer.services.trajectory_seen_contract import CONTRACT_KEY, EPOCH_KEY

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def reset_redis_url():
    import redis

    base = os.environ.get("REDIS_URL", "redis://localhost:16379/0")
    url = base.rsplit("/", 1)[0] + "/14"
    client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=5)
    try:
        client.ping()
    except Exception as exc:
        pytest.skip(f"test Redis unavailable: {exc!r}")
    client.flushdb()
    try:
        yield url, client
    finally:
        client.flushdb()
        client.close()


def test_trajectory_seen_reset_changes_epoch_and_removes_only_traj_seen_keys(
    reset_redis_url,
):
    url, client = reset_redis_url
    seen = [
        "traj:seen:v2:11111111-1111-1111-1111-111111111111:e1:sess:7",
        "traj:seen:22222222-2222-2222-2222-222222222222:__project__",
    ] + [f"traj:seen:bulk:{n}" for n in range(1200)]
    for key in seen:
        client.sadd(key, "h")
    client.set(EPOCH_KEY, "old-epoch")
    client.set(CONTRACT_KEY, "1")
    client.set("usage:events:marker", "keep")
    client.set("traj:other", "keep")

    out = io.StringIO()
    with override_settings(REDIS_URL=url):
        call_command("trajectory_seen_reset", stdout=out)

    assert not any(client.exists(key) for key in seen)
    epoch = client.get(EPOCH_KEY)
    assert epoch and epoch != "old-epoch"
    assert client.get(CONTRACT_KEY) == "1"
    assert client.get("usage:events:marker") == "keep"
    assert client.get("traj:other") == "keep"
    assert f"removed {len(seen)} seen-set keys" in out.getvalue()
    # Counts only: no key name or epoch token is printed.
    assert "traj:" not in out.getvalue() and epoch not in out.getvalue()


def test_standalone_restore_needs_no_seen_reset():
    """The updater's Standalone restore does not call trajectory_seen_reset.

    That is safe only while Standalone Redis keeps nothing on disk (a restart
    empties the seen sets), Redis is not a restored domain, and the restore
    runs with the app container stopped. If any of these changes, the restore
    path must call the reset.
    """
    start = (REPO_ROOT / "deploy/standalone/bin/start").read_text()
    redis_conf = re.search(r"redis\.conf <<EOF\n(.*?)\nEOF", start, re.S)
    assert redis_conf, "standalone start no longer writes redis.conf inline"
    lines = {line.strip() for line in redis_conf.group(1).splitlines()}
    assert 'save ""' in lines and "appendonly no" in lines

    contracts = (REPO_ROOT / "deploy/update/contracts.py").read_text()
    domains = re.search(r"PERSISTENT_DOMAINS = \(([^)]*)\)", contracts)
    assert domains and "redis" not in domains.group(1)

    restore = (REPO_ROOT / "deploy/update/standalone_restore.py").read_text()
    assert "def assert_no_running_consumers" in restore
