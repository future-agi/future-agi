"""What the environment detail answers ready to show: where each tool runs, which tools a fault can name, the
world's own tables, why a scenario cannot work, who each scenario actor is, and whether the Actors tab shows."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from urllib3.exceptions import MaxRetryError

from simulate.models import HostedHarnessScenario
from simulate.services.harness_actor_rules import actor_rules
from simulate.services.harness_environment import environment_detail
from simulate.services.harness_scenarios import index_scenarios, scenario_row
from simulate.services.harness_tool_reach import door_targets
from simulate.services.hosted_harness import create_hosted_job
from simulate.tests.test_harness_tool_status import CONTRACT
from simulate.tests.test_hosted_harness_channels import _payload

pytestmark = pytest.mark.django_db

STORES = [
    {
        "capability": "orders",
        "engine": "postgres",
        "tables": [
            {"name": "orders", "rows": 3},
            {"name": "_alk_actors", "rows": 5},
            {"name": "harness_seed_sentinel", "rows": 1},
            {"name": "refunds", "rows": 2},
        ],
        "total_rows": 11,
    }
]
SCENARIOS = [
    {"scenario_key": "plain", "name": "plain", "instruction": "Ask."},
    {
        "scenario_key": "held",
        "name": "held",
        "instruction": "Ask.",
        "faults": [{"action": "lookup_order", "mode": "unavailable"}],
        "actors": [
            {
                "id": "boss",
                "instruction": "Approve it.",
                "trigger": {"kind": "on_tool", "tool": "issue_refund"},
            }
        ],
    },
]


def _environment(organization, workspace, key, *, call_actors=None):
    job, _ = create_hosted_job(
        organization,
        _payload(scenario_count=2),
        idempotency_key=key,
        workspace=workspace,
    )
    outputs = [
        {
            "kind": "contract",
            "data": {
                **CONTRACT,
                "data_schema": {"orders": {"id": "text", "total": "int"}},
            },
        },
        {"kind": "stores", "data": STORES},
        {"kind": "environment", "data": {"services": []}},
        {"kind": "scenarios", "data": SCENARIOS},
    ]
    if call_actors is not None:
        outputs.append({"kind": "call_actors", "data": {"call_actors": call_actors}})
    job.stage_outputs = outputs
    job.save(update_fields=["stage_outputs"])
    index_scenarios(job, SCENARIOS)
    return job


def test_the_contract_says_where_each_tool_runs_and_what_a_fault_can_name(
    organization, workspace
):
    contract = environment_detail(_environment(organization, workspace, "d-tools"))[
        "contract"
    ]

    statuses = {one["tool"]: one["status"] for one in contract["tool_status"]}
    assert statuses == {
        "issue_refund": "simulated",
        "lookup_order": "in_process",
        "notify": "unsupported",
        "audit": "unknown",
    }
    targets = {one["action"]: one for one in contract["fault_targets"]}
    assert targets["orders.refund"]["writes"] is True
    assert targets["lookup_order"]["reachable"] is False
    # The kept fields stay as they were.
    assert contract["production_only"] == ["notify"]


def test_the_world_lists_only_its_own_tables(organization, workspace):
    world = environment_detail(_environment(organization, workspace, "d-tables"))[
        "world"
    ]

    assert world["tables"] == [
        {
            "name": "orders",
            "rows": 3,
            "note": "orders · postgres",
            "fields": [
                {"name": "id", "type": "text"},
                {"name": "total", "type": "int"},
            ],
        },
        {"name": "refunds", "rows": 2, "note": "orders · postgres", "fields": []},
    ]
    assert world["total_rows"] == 5
    # The stores themselves are kept whole.
    assert world["stores"] == STORES


def test_a_scenario_that_cannot_work_says_why_and_its_actors_are_resolved(
    organization, workspace
):
    scenarios = {
        one["scenario_key"]: one
        for one in environment_detail(_environment(organization, workspace, "d-door"))[
            "scenarios"
        ]
    }

    assert scenarios["plain"]["door_problem"] is None
    assert scenarios["held"]["door_problem"]["code"] == "fault_unreachable"
    [boss] = scenarios["held"]["actors"]
    assert boss["instruction"] == "Approve it."
    # The cast has no such actor, so it reads by its id, and the scenario's own trigger runs.
    assert (boss["name"], boss["side"], boss["effective_trigger"]) == (
        "boss",
        "world",
        {"kind": "on_tool", "tool": "issue_refund"},
    )
    assert boss["when_label"]


def test_a_list_row_carries_the_same_door_problem(organization, workspace):
    job = _environment(organization, workspace, "d-row")
    row = HostedHarnessScenario.no_workspace_objects.get(job=job, scenario_key="held")

    targets = door_targets(CONTRACT, actor_rules({}), [])
    problem = scenario_row(
        row, targets=targets, fault_targets=targets or [], actor_cast=[]
    )["door_problem"]

    assert problem["code"] == "fault_unreachable"


@pytest.mark.parametrize(
    ("versioning", "call_actors", "tab"),
    [
        (True, None, "edit"),
        (
            False,
            [{"id": "friend", "name": "Rowan", "role": "r", "channel": "audio_mix"}],
            "list",
        ),
        (False, None, None),
    ],
)
def test_the_actors_tab(
    organization, workspace, settings, versioning, call_actors, tab
):
    settings.HARNESS_VERSIONING_ENABLED = versioning
    job = _environment(
        organization,
        workspace,
        f"d-tab-{versioning}-{bool(call_actors)}",
        call_actors=call_actors,
    )

    assert environment_detail(job)["actors_tab"] == tab


def _elsewhere(user, organization, where):
    """An environment with a table of its own, in another organisation or another workspace of this one."""
    from accounts.models import Organization, Workspace

    owner = (
        Organization.objects.create(name="Stranger tables org")
        if where == "organization"
        else organization
    )
    place = Workspace.objects.create(
        name=f"Elsewhere {where}",
        organization=owner,
        is_active=True,
        created_by=user,
    )
    job = _environment(owner, place, f"d-elsewhere-{where}")
    job.stage_outputs = [
        {
            **one,
            "data": [
                {"capability": "secret", "tables": [{"name": "secrets", "rows": 9}]}
            ],
        }
        if one["kind"] == "stores"
        else one
        for one in job.stage_outputs
    ]
    job.save(update_fields=["stage_outputs"])
    return job


@pytest.mark.parametrize("where", ["organization", "workspace"])
def test_another_tenants_environment_never_shows_its_tables_or_scenarios(
    auth_client, user, organization, workspace, where
):
    mine = _environment(organization, workspace, f"d-mine-{where}")
    theirs = _elsewhere(user, organization, where)

    detail = auth_client.get(f"/simulate/api/harness-environments/{theirs.id}/")
    rows = auth_client.get(f"/simulate/api/harness-jobs/{theirs.id}/scenarios/")
    own = auth_client.get(f"/simulate/api/harness-environments/{mine.id}/")

    assert detail.status_code == 404
    assert rows.status_code == 404
    assert own.status_code == 200, own.content
    assert "secrets" not in {table["name"] for table in own.json()["world"]["tables"]}


def test_the_scenario_list_answers_the_shape_it_documents(
    auth_client, organization, workspace
):
    from simulate.serializers.harness_environment import (
        HarnessScenarioListResponseSerializer,
        HarnessScenarioRowSerializer,
    )

    job = _environment(organization, workspace, "d-list-shape")

    response = auth_client.get(f"/simulate/api/harness-jobs/{job.id}/scenarios/")

    assert response.status_code == 200, response.content
    body = response.json()
    checked = HarnessScenarioListResponseSerializer(data=body)
    assert checked.is_valid(), checked.errors
    # Grouped by default, and no unsaved changes: every documented row key but `draft_change`, and nothing else.
    expected = set(HarnessScenarioRowSerializer().fields) - {"draft_change"}
    assert [set(row) for row in body["results"]] == [expected, expected]
    held = next(row for row in body["results"] if row["scenario_key"] == "held")
    assert held["door_problem"]["code"] == "fault_unreachable"


def test_an_environment_without_actors_never_reads_its_files(organization, workspace):
    job = _environment(organization, workspace, "d-no-actors")
    for one in job.stage_outputs:
        if one["kind"] == "scenarios":
            one["data"] = [dict(doc, actors=[]) for doc in SCENARIOS]
    job.save(update_fields=["stage_outputs"])

    with patch(
        "simulate.services.harness_versioning.drafts.edit_side",
        side_effect=AssertionError("the archive was read"),
    ):
        detail = environment_detail(job)

    assert [table["name"] for table in detail["world"]["tables"]] == [
        "orders",
        "refunds",
    ]


def test_storage_that_cannot_be_reached_leaves_the_detail_readable(
    organization, workspace
):
    job = _environment(organization, workspace, "d-storage-down")

    with patch(
        "simulate.services.harness_versioning.drafts.edit_side",
        side_effect=MaxRetryError(None, "/bucket/key", "connection refused"),
    ):
        detail = environment_detail(job)

    held = next(one for one in detail["scenarios"] if one["scenario_key"] == "held")
    # No cast was read, so the actor reads by its id; the page still answers.
    assert held["actors"][0]["name"] == "boss"
    assert [table["name"] for table in detail["world"]["tables"]] == [
        "orders",
        "refunds",
    ]
