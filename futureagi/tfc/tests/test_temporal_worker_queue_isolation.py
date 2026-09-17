from pathlib import Path

import pytest

from tfc.management.commands.start_temporal_worker import (
    _generic_all_queues,
    _workflow_cache_kwargs,
)


@pytest.mark.unit
def test_generic_all_queue_worker_excludes_dedicated_queues():
    registered = [
        "default",
        "tasks_xl",
        "exact_aggregation",
        "property_catalog_dev_sidecar",
        "trace_ingestion",
    ]

    assert _generic_all_queues(registered) == [
        "default",
        "tasks_xl",
        "trace_ingestion",
    ]


@pytest.mark.unit
def test_generic_all_queue_worker_preserves_other_queue_order():
    registered = ["agent_compass", "tasks_s", "tasks_l"]

    assert _generic_all_queues(registered) == registered


@pytest.mark.unit
def test_exact_worker_disables_sticky_workflow_cache_for_single_slot_queue():
    assert _workflow_cache_kwargs("exact_aggregation") == {"max_cached_workflows": 0}


@pytest.mark.unit
def test_other_workers_keep_temporal_default_workflow_cache():
    assert _workflow_cache_kwargs("default") == {}


@pytest.mark.unit
def test_always_on_exact_worker_disables_startup_database_mutations():
    compose_path = Path(__file__).resolve().parents[3] / "docker-compose.yml"
    compose = compose_path.read_text(encoding="utf-8")
    start = compose.index("\n  worker-exact-aggregation:")
    end = compose.index("\n  worker-trace-ingestion:", start)
    exact_worker = compose[start:end]

    assert 'NO_STARTUP_DB_MUTATIONS: "true"' in exact_worker
    assert 'CH25_DROP_LEGACY_CDC_CHAIN: "false"' in exact_worker
    assert 'CH25_TRACE_DUAL_WRITE: "false"' in exact_worker
    assert 'CH_DUAL_WRITE: "false"' in exact_worker
