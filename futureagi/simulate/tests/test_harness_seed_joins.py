"""Broken joins between attached seed-record files: only the cases citing the broken file are held, with the fix."""

import json

import pytest

from simulate.services import harness_scenario_lifecycle, harness_seed_joins
from simulate.services.harness_actor_rules import KNOWLEDGE_ROLES
from simulate.services.harness_scenario_lifecycle import runnable
from simulate.tests.test_harness_affected_scenarios import (
    LOOKUP_KEYS,
    PROVED,
    REFUND_KEYS,
    _suite,
    _ten,
)
from simulate.tests.test_harness_readiness import _environment
from simulate.tests.test_harness_readiness import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_scenario_lifecycle import _proofs, _views

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("readiness_on")]
ORDERS = "knowledge/orders.csv"
REFUNDS = "knowledge/refunds.json"


def _files(refunds):
    return {
        KNOWLEDGE_ROLES: json.dumps(
            {ORDERS: "seed_records", REFUNDS: "seed_records"}
        ).encode(),
        ORDERS: b"id,total\nO-1,40\nO-2,15\n",
        REFUNDS: json.dumps(refunds).encode(),
    }


def test_a_key_with_no_row_on_the_other_side_is_a_broken_join():
    found = harness_seed_joins.broken_joins(
        _files([{"id": "R-1", "order_id": "O-1"}, {"id": "R-2", "order_id": "O-999"}])
    )

    assert list(found) == [REFUNDS]
    [join] = found[REFUNDS]
    assert (join.field, join.value, join.target) == ("order_id", "O-999", ORDERS)
    assert (
        harness_seed_joins.broken_join_text(found[REFUNDS])
        == "refunds.json: order_id O-999 has no row in orders.csv"
    )


@pytest.mark.parametrize(
    "refunds",
    [
        [{"id": "R-1", "order_id": "O-2"}],
        # A field pointing at a file nobody attached says nothing about where its rows live.
        [{"id": "R-1", "customer_id": "C-404"}],
        [{"id": "R-1", "order_id": ""}],
    ],
    ids=["joined", "unattached-target", "empty"],
)
def test_rows_that_join_or_name_no_attached_file_are_not_broken(refunds):
    assert harness_seed_joins.broken_joins(_files(refunds)) == {}


def test_only_the_cases_citing_the_broken_file_are_held(
    user, workspace, storage, monkeypatch
):
    from simulate.models import HarnessSuiteMember

    environment = _environment(user, workspace, storage, "seed-joins")
    _suite(environment, _ten())
    citing = REFUND_KEYS[0]
    member = HarnessSuiteMember.no_workspace_objects.get(
        suite_version=environment.suite_version, scenario_key=citing
    )
    HarnessSuiteMember.no_workspace_objects.filter(id=member.id).update(
        content={
            **member.content,
            "sources": [{"kind": "file", "ref": f"file:{REFUNDS}", "quote": "R-2"}],
        }
    )
    _proofs(environment, dict.fromkeys(REFUND_KEYS + LOOKUP_KEYS, PROVED))
    files = _files([{"id": "R-2", "order_id": "O-999"}])
    monkeypatch.setattr(
        harness_seed_joins,
        "file_bodies",
        lambda version, paths: {path: files[path] for path in paths if path in files},
    )

    views = _views(environment)

    assert (views[citing]["state"], views[citing]["reason"]) == ("draft", "broken_join")
    assert "order_id O-999 has no row in orders.csv" in views[citing]["reason_text"]
    assert "Map the field or add the rows" in views[citing]["reason_text"]
    assert not runnable(views[citing])
    assert all(views[key]["reason"] != "broken_join" for key in views if key != citing)
    assert harness_scenario_lifecycle.REASON_TEXTS["broken_join"]
