"""`HARNESS_ACTORS_ENABLED`: off, no actor surface is offered, no actor edit or test condition is taken, and every
job document tells the harness actors are off; on, all of it behaves as before the flag."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from simulate.models.test_execution import CallExecution
from simulate.services.harness_environment import _actors_tab
from simulate.services.harness_provider import HostedHarnessProvider
from simulate.services.harness_versioning.assembly import certify_job_payload
from simulate.tests import test_harness_versioning_picks as picks
from simulate.tests import test_model_tools_journey as journey
from simulate.tests.test_harness_actors import BASE, MANAGER, _add, _built
from simulate.tests.test_harness_amend_receipt_codes import _receipts, _set
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_hosted_harness_gateway import (
    _authoring_document,
    _chat_document,
    _ExportWorld,
)
from tfc.settings.settings import _actors_share

pytestmark = pytest.mark.django_db

FAULT = [{"action": "orders.refund", "mode": "unavailable"}]
LISTED = {"call_actors": [{"id": "friend", "name": "Rowan"}]}


@pytest.fixture
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.mark.parametrize(
    ("enabled", "versioning", "listed", "tab"),
    [
        (False, True, None, None),
        (False, False, LISTED, None),
        (True, True, None, "edit"),
        (True, False, LISTED, "list"),
    ],
)
def test_the_actors_tab_shows_only_while_actors_are_on(
    settings, enabled, versioning, listed, tab
):
    settings.HARNESS_ACTORS_ENABLED = enabled
    settings.HARNESS_VERSIONING_ENABLED = versioning

    assert _actors_tab(listed) == tab


@pytest.mark.usefixtures("versioning_on", "storage")
def test_actor_endpoints_answer_not_enabled_while_actors_are_off(
    user, workspace, auth_client, settings
):
    environment = _built(user, workspace, "actors-flag-off")
    settings.HARNESS_ACTORS_ENABLED = False

    answers = [
        auth_client.get(f"{BASE}/{environment.id}/actors/"),
        _add(auth_client, environment, MANAGER),
        auth_client.get(f"{BASE}/{environment.id}/actors/manager/"),
        auth_client.delete(f"{BASE}/{environment.id}/actors/manager/"),
        auth_client.post(
            f"{BASE}/{environment.id}/actor-validation/",
            {"op": "create", "changes": {"name": "Priya"}},
            format="json",
        ),
        auth_client.put(
            f"{BASE}/{environment.id}/actor-limits/", {"limits": {}}, format="json"
        ),
        auth_client.post(
            f"{BASE}/{environment.id}/actor-unresolved/accept/",
            {"policy": "refunds"},
            format="json",
        ),
    ]

    assert [(one.status_code, one.json()["error"]) for one in answers] == [
        (404, "actors_disabled")
    ] * len(answers)
    # Nothing was written: on again, the draft has no actor.
    settings.HARNESS_ACTORS_ENABLED = True
    assert auth_client.get(f"{BASE}/{environment.id}/actors/").json()["actors"] == []


@pytest.mark.usefixtures("versioning_on", "storage")
def test_an_actor_is_added_while_actors_are_on(user, workspace, auth_client, settings):
    environment = _built(user, workspace, "actors-flag-on")
    settings.HARNESS_ACTORS_ENABLED = True

    added = _add(auth_client, environment, MANAGER)

    assert added.status_code == 200, added.content
    assert [one["id"] for one in added.json()["actors"]] == ["manager"]


@pytest.mark.parametrize("enabled", [False, True])
def test_test_conditions_are_editable_only_while_actors_are_on(settings, enabled):
    settings.HARNESS_ACTORS_ENABLED = enabled

    editing = HostedHarnessProvider()._editing_contract(spoken=True)

    assert ("faults" in editing["editable_fields"]) is enabled


def test_a_test_condition_is_refused_as_not_editable_while_actors_are_off(
    user, workspace, settings
):
    settings.HARNESS_ACTORS_ENABLED = False

    assert _receipts(user, workspace, [_set("faults", FAULT)]) == [
        ("refused", "not_editable", "faults")
    ]


def test_a_test_condition_is_applied_while_actors_are_on(user, workspace, settings):
    settings.HARNESS_ACTORS_ENABLED = True

    assert _receipts(user, workspace, [_set("faults", FAULT)]) == [
        ("applied", "applied", "faults")
    ]


@pytest.mark.parametrize("kind", ["build", "chat", "run", "certify"])
@pytest.mark.parametrize(
    ("enabled", "sent"),
    [
        (False, {"enabled": False, "share": [0.0, 0.0]}),
        (True, {"enabled": True, "share": [0.1, 0.3]}),
    ],
)
def test_every_job_document_says_whether_actors_are_on_and_their_share(
    organization, workspace, user, settings, monkeypatch, kind, enabled, sent
):
    settings.HARNESS_ACTORS_ENABLED = enabled
    settings.HARNESS_ACTORS_SHARE = (0.1, 0.3)
    if kind == "build":
        document, _ = _authoring_document(monkeypatch)
    elif kind == "chat":
        document, _ = _chat_document(monkeypatch)
    else:
        world = _ExportWorld(
            organization, workspace, user, settings, monkeypatch, bind="run_test"
        )
        settings.HARNESS_ACTORS_ENABLED = enabled
        document = world.launch().job_json
        if kind == "certify":
            # A launch's certify job is made from its run document.
            document = certify_job_payload(document, 3)

    assert document["metadata"]["actors"] == sent


@pytest.mark.parametrize(
    "text", ["0.3-0.2", "-0.1-0.2", "0.1-1.5", "1.5", "abc", "nan-nan", ""]
)
def test_a_share_outside_zero_to_one_or_not_min_max_stops_startup(text):
    with pytest.raises(ImproperlyConfigured):
        _actors_share(text)


@pytest.mark.parametrize(
    ("text", "share"),
    [("0.15-0.25", (0.15, 0.25)), (" 0-1 ", (0.0, 1.0)), ("0.2-0.2", (0.2, 0.2))],
)
def test_a_share_reads_as_min_and_max(text, share):
    assert _actors_share(text) == share


@pytest.mark.usefixtures("versioning_on")
@pytest.mark.parametrize("enabled", [False, True])
def test_a_call_says_whether_actors_are_on(
    auth_client, user, workspace, settings, request, enabled
):
    environment = journey._authored(
        user, workspace, f"flag-run-{enabled}", request.getfixturevalue("storage")
    )
    settings.HARNESS_ACTORS_ENABLED = enabled

    run = journey._post_run(
        auth_client,
        environment,
        {"scenario_ids": [journey._key(journey.NAMES[0])], "trials": 1},
        key=f"flag-run-{enabled}",
    )
    assert run.status_code == 202, run.content
    call = CallExecution.objects.filter(
        test_execution_id=run.json()["test_execution_id"]
    ).first()
    detail = auth_client.get(f"/simulate/v3/call-executions/{call.id}/")

    assert detail.status_code == 200, detail.content
    assert detail.json()["actors_enabled"] is enabled


@pytest.mark.usefixtures("versioning_on")
@pytest.mark.parametrize(("enabled", "status"), [(False, 202), (True, 409)])
def test_a_run_needing_a_production_only_tool_is_refused_only_while_actors_are_on(
    auth_client, user, workspace, settings, request, enabled, status
):
    environment, suite = picks.TestProductionOnly()._blocked(
        user, workspace, request.getfixturevalue("storage"), f"flag-prod-{enabled}"
    )
    settings.HARNESS_ACTORS_ENABLED = enabled

    response = picks._post_run(
        auth_client,
        environment,
        {
            "scenario_ids": [picks._key(picks.NAMES[0])],
            "trials": 1,
            "suite_version_id": str(suite.id),
        },
    )

    assert response.status_code == status, response.content


@pytest.mark.parametrize(("enabled", "fails"), [(False, False), (True, True)])
def test_a_bad_share_stops_startup_only_while_actors_are_on(
    monkeypatch, enabled, fails
):
    import importlib

    import tfc.settings.settings as module

    monkeypatch.setenv("HARNESS_ACTORS_ENABLED", "true" if enabled else "")
    monkeypatch.setenv("HARNESS_ACTORS_SHARE", "0.3-0.2")
    try:
        if fails:
            with pytest.raises(ImproperlyConfigured):
                importlib.reload(module)
        else:
            assert importlib.reload(module).HARNESS_ACTORS_SHARE == (0.15, 0.25)
    finally:
        monkeypatch.delenv("HARNESS_ACTORS_SHARE")
        monkeypatch.delenv("HARNESS_ACTORS_ENABLED")
        importlib.reload(module)
