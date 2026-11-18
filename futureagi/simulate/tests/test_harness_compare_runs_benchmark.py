"""Latency of the compare endpoint at its target sizes: 5 and 8 runs x 500 x 3."""

from __future__ import annotations

import gc
import json
import os
import time
from collections.abc import Iterator
from typing import Any

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from simulate.models import TestExecution
from simulate.tests.test_harness_compare_runs import WARM_QUERIES, build_environment
from tfc.asgi_startup import freeze_startup_heap

BASE = "/simulate/api/harness-environments"
SCENARIOS = 500
TRIALS = 3
TIMED = 10
COLD_LIMIT_SECONDS = 1.0
WARM_LIMIT_SECONDS = 0.4
CASES: dict[str, dict[str, Any]] = {
    "none": {},
    "status failed": {"filters": {"status": ["failed"]}},
    "goal": {"filters": {"goal": ["Refund a damaged order"]}},
    "sub_goal": {"filters": {"sub_goal": ["Logged the case"]}},
    "search": {"search": "refund"},
    "group goal": {"group_by": "goal"},
    "group sub_goal": {"group_by": "sub_goal"},
    "group status": {"group_by": "status"},
    "page 5": {"page": 5},
    "page_size 100": {"page_size": 100},
    "page_size 100 + group sub_goal": {"page_size": 100, "group_by": "sub_goal"},
}
# The served cache, so warm reads pay Redis transfer and unpickling.
REDIS_CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ["REDIS_URL"],
        "KEY_PREFIX": "compare-benchmark",
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}


@pytest.fixture(scope="module", autouse=True)
def _served_process() -> Iterator[None]:
    """The start-up heap frozen as a served worker's is, and its Redis cache."""
    freeze_startup_heap()
    with override_settings(CACHES=REDIS_CACHES):
        yield
        cache.delete_pattern("*")
    gc.unfreeze()


def _timed(client, url: str, params: dict[str, Any]) -> tuple[float, int]:
    # Seeding fills the capped query log, which then captures nothing.
    connection.queries_log.clear()
    with CaptureQueriesContext(connection) as queries:
        started = time.perf_counter()
        response = client.get(url, params)
        elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.content[:500]
    return elapsed, len(queries)


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    rank = (len(ordered) - 1) * 0.95
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


# Benchmark only: `bin/test integration` overrides the default marker filter.
@pytest.mark.benchmark
@pytest.mark.django_db
@pytest.mark.parametrize("runs", [5, 8])
def test_compare_is_fast_at_its_target_size(auth_client, workspace, runs):
    environment = build_environment(
        workspace, runs=runs, scenarios=SCENARIOS, trials=TRIALS, edited=()
    )
    played = TestExecution.objects.filter(run_test_id=environment.run_test_id)
    ids = ",".join(str(run.id) for run in played.order_by("created_at"))
    url = f"{BASE}/{environment.id}/compare-runs/"
    # The process's first request pays its lazy imports; only the cache is cold here.
    _timed(auth_client, url, {"runs": ids})
    measured, slow = {}, {}
    for name, case in CASES.items():
        params = {
            "runs": ids,
            **{
                key: json.dumps(value) if key == "filters" else value
                for key, value in case.items()
            },
        }
        cold, warm = [], []
        for _ in range(TIMED):
            cache.delete_pattern("simulate:v3:*")
            cold.append(_timed(auth_client, url, params)[0])
            seconds, queries = _timed(auth_client, url, params)
            warm.append(seconds)
            assert queries == WARM_QUERIES[0] + WARM_QUERIES[1] * runs, name
        p95 = measured[name] = tuple(round(_p95(one), 3) for one in (cold, warm))
        if p95[0] >= COLD_LIMIT_SECONDS or p95[1] >= WARM_LIMIT_SECONDS:
            slow[name] = p95
    # Every case's cold and warm p95, so a miss shows against the others.
    assert not slow, measured
