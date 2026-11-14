"""Editing a case's grounding keeps its proof; an edit's Save carries the unchanged cases' proofs."""

import pytest
from django.utils import timezone

from simulate.models import (
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HostedHarnessScenario,
)
from simulate.services.harness_scenario_lifecycle import scenario_lifecycle
from simulate.services.harness_versioning.versions import _carried_proofs
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_scenario_archive_api import _save
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_drafts import _amend
from simulate.tests.test_harness_versioning_drafts import (  # noqa: F401 - fixture
    versioning_on as versioning_on,
)
from simulate.tests.test_harness_versioning_rollback import _built

pytestmark = pytest.mark.django_db
PROVED = {"format": 2, "holds": True, "proved": True, "assumed": []}


def _prove_all(environment):
    keys = list(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=environment.suite_version
        ).values_list("scenario_key", flat=True)
    )
    HarnessSuiteValidation.no_workspace_objects.update_or_create(
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
        defaults={
            "status": "passed",
            "proved_at": timezone.now(),
            "proofs": dict.fromkeys(keys, PROVED),
        },
    )
    return keys


def test_amend_sources_validated_by_receipt(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ed-sources")

    refused = _amend(
        auth_client, environment, NAMES[0], "sources", [{"kind": "rumour", "ref": ""}]
    )
    applied = _amend(
        auth_client,
        environment,
        NAMES[0],
        "sources",
        [
            {
                "kind": "requirement",
                "ref": "",
                "quote": "refund once",
                "supports": "the count",
            }
        ],
    )

    assert refused["receipts"][0]["outcome"] == "refused"
    assert refused["receipts"][0]["why"].startswith("sources[0]:")
    assert applied["receipts"][0]["outcome"] == "applied"
    from simulate.services.harness_versioning import drafts

    suite = drafts.suite_documents(drafts.edit_sides(environment)["suite"])
    edited = next(one for one in suite if one.get("scenario_key") == _key(NAMES[0]))
    assert edited["sources"][0]["quote"] == "refund once"


def test_carried_proofs_ignore_sources(user, workspace, storage):
    environment = _built(user, workspace, "ed-carry")
    keys = _prove_all(environment)
    members = {
        member.scenario_key: member
        for member in HarnessSuiteMember.no_workspace_objects.filter(
            suite_version=environment.suite_version
        )
    }
    rows = {
        row.scenario_key: row
        for row in HostedHarnessScenario.no_workspace_objects.filter(job=environment)
    }
    grounded = {
        **members[keys[0]].content,
        "scenario_key": keys[0],
        "sources": [{"kind": "assumption"}],
    }
    reworded = {
        **members[keys[1]].content,
        "scenario_key": keys[1],
        "instruction": "something else",
    }

    carried = _carried_proofs(
        environment.environment_version,
        [(rows[keys[0]], grounded), (rows[keys[1]], reworded)],
    )

    assert keys[0] in carried
    assert keys[1] not in carried


def test_manual_save_carries_unchanged_proofs(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ed-manual")
    keys = _prove_all(environment)
    edited = _key(NAMES[0])
    _amend(
        auth_client,
        environment,
        NAMES[0],
        "tests",
        "a new description of what it tests",
    )

    _save(environment, user)

    views = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )
    assert all(views[key]["state"] == "active" for key in keys if key != edited)
    # Certifying is off in these tests, so an unproved case reads validation_unavailable.
    assert views[edited]["state"] == "draft"
    assert views[edited]["reason"] in ("not_validated", "validation_unavailable")


def test_coverage_lists_unused_planned_levels(user, workspace, storage, auth_client):
    from simulate.models import HarnessSuiteVersion

    environment = _built(user, workspace, "ed-coverage")
    suite = environment.suite_version
    HarnessSuiteVersion._base_manager.filter(id=suite.id).update(
        documents={
            **(suite.documents or {}),
            "coverage": {
                "axes": {"task": {"levels": ["book", "cancel"], "unused": ["cancel"]}}
            },
        }
    )

    response = auth_client.get(
        f"/simulate/api/harness-jobs/{environment.id}/scenarios/coverage/"
    )

    assert response.status_code == 200, response.content
    assert response.json()["uncovered"] == [{"axis": "task", "level": "cancel"}]
    assert response.json()["coverage_recorded"] is True


def _amend_with(auth_client, environment, name, field, value, *, rework=True):
    response = auth_client.post(
        f"/simulate/api/harness-jobs/{environment.id}/scenarios/amend/",
        {
            "rework": rework,
            "changes": [
                {"op": "set_field", "scenario": name, "field": field, "value": value}
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    environment.refresh_from_db()
    return response.json()


def _edited(environment, name):
    from simulate.services.harness_versioning import drafts

    suite = drafts.suite_documents(drafts.edit_sides(environment)["suite"])
    return next(one for one in suite if one.get("scenario_key") == _key(name))


def _a_sub_goal(environment, name):
    goals = _edited(environment, name).get("sub_goals") or []
    first = goals[0]
    return first.get("name") if isinstance(first, dict) else first


def test_control_edit_needs_reproof(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ed-control")
    # A built case names the sub-goals it checks; a control may only fail one of those.
    _amend_with(auth_client, environment, NAMES[0], "sub_goals", ["refunded"])
    control = {
        "name": "refunds_twice",
        "why": "refunds the order twice",
        "steps": [{"tool": "refund", "arguments": {"order_id": "O-10"}}],
        "must_fail": [_a_sub_goal(environment, NAMES[0])],
    }

    declined = _amend_with(
        auth_client, environment, NAMES[0], "negative_controls", [control], rework=False
    )
    applied = _amend_with(
        auth_client, environment, NAMES[0], "negative_controls", [control]
    )

    assert declined["receipts"][0]["outcome"] == "refused"
    assert "re-proof" in declined["receipts"][0]["why"]
    assert applied["receipts"][0]["outcome"] == "applied", applied
    assert (
        _edited(environment, NAMES[0])["negative_controls"][0]["name"]
        == "refunds_twice"
    )


def test_control_with_unknown_must_fail_is_refused(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "ed-control-unknown")
    control = {
        "name": "refunds_twice",
        "why": "refunds the order twice",
        "steps": [{"tool": "refund", "arguments": {}}],
        "must_fail": ["no_such_check"],
    }

    refused = _amend_with(
        auth_client, environment, NAMES[0], "negative_controls", [control]
    )

    assert refused["receipts"][0]["outcome"] == "refused"
    assert refused["receipts"][0]["why"] == (
        "negative_controls[0]: must_fail names no_such_check, which this case does not check"
    )


def test_must_pass_is_descriptive(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ed-must-pass")

    applied = _amend_with(
        auth_client, environment, NAMES[0], "must_pass", True, rework=False
    )
    refused = _amend_with(auth_client, environment, NAMES[0], "must_pass", "yes")

    assert applied["receipts"][0]["outcome"] == "applied", applied
    assert _edited(environment, NAMES[0])["must_pass"] is True
    assert refused["receipts"][0]["why"] == "must_pass must be true or false"


def test_amend_to_empty_sources_is_refused(user, workspace, storage, auth_client):
    from simulate.services.harness_scenario_sources import NO_SOURCES

    environment = _built(user, workspace, "ed-empty-sources")

    refused = _amend_with(auth_client, environment, NAMES[0], "sources", [])

    assert refused["receipts"][0]["outcome"] == "refused"
    assert refused["receipts"][0]["why"] == NO_SOURCES


def test_amend_to_a_blank_assumption_is_refused(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ed-blank-assumption")

    refused = _amend_with(
        auth_client,
        environment,
        NAMES[0],
        "sources",
        [{"kind": "assumption", "quote": "  ", "supports": ""}],
    )

    assert refused["receipts"][0]["outcome"] == "refused"
    assert refused["receipts"][0]["why"] == (
        "sources[0]: an assumption source says what was assumed"
    )
