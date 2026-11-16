"""How far an actor got in a call is one rule: the call detail (Python) and the run's actor filter (SQL) read the same
summary the same way, malformed values included, and neither fails on them."""

from __future__ import annotations

import pytest

from simulate.models import CallExecution
from simulate.services.run_results_v3_queries import (
    _call_actor_facet,
    _call_actor_facet_python,
    call_actor_delivery,
)
from simulate.tests.test_harness_call_actors import _ingest

# Each entry, and how far the database reads it as getting.
MALFORMED = [
    ({"triggered": []}, "dropped"),
    ({"triggered": {}}, "dropped"),
    ({"triggered": "yes"}, "dropped"),
    ({"triggered": ""}, "not_triggered"),
    ({"triggered": 0}, "not_triggered"),
    ({"triggered": 0.0}, "not_triggered"),
    ({"triggered": None}, "not_triggered"),
    ({"delivered": "2"}, "not_triggered"),
    ({"delivered": "2", "triggered": True}, "dropped"),
    ({"delivered": True}, "not_triggered"),
    ({"delivered": 0.5}, "delivered"),
    ({"delivered": -1, "triggered": 1}, "dropped"),
    ({"delivered": None, "drawn": False}, "not_drawn"),
    ({"drawn": "false"}, "not_triggered"),
]


@pytest.mark.parametrize(("entry", "delivery"), MALFORMED)
def test_a_malformed_summary_entry_reads_as_the_database_reads_it(entry, delivery):
    assert call_actor_delivery(entry) == delivery


@pytest.mark.django_db
def test_the_actor_filter_counts_malformed_entries_as_the_call_detail_reads_them(
    organization, workspace
):
    ids = []
    for index, (entry, _) in enumerate(MALFORMED):
        call = _ingest(
            organization,
            workspace,
            f"delivery-rule-{index}",
            CallExecution.SimulationCallType.TEXT,
            {"messages": []},
            {},
        )
        call.call_metadata = {
            **(call.call_metadata or {}),
            "harness_call_actors": {
                "summary": [{"actor": f"actor-{index}", "name": "A", **entry}]
            },
        }
        call.save(update_fields=["call_metadata"])
        ids.append(call.id)
    calls = CallExecution.objects.filter(id__in=ids)

    in_database = _call_actor_facet(calls)
    assert in_database == _call_actor_facet_python(calls)
    by_actor = {one["value"]: one for one in in_database}
    for index, (_, delivery) in enumerate(MALFORMED):
        if delivery == "not_drawn":
            assert f"actor-{index}" not in by_actor
        else:
            assert by_actor[f"actor-{index}"][delivery] == 1, MALFORMED[index]
