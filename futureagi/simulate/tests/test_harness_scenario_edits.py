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
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_drafts import _amend
from simulate.tests.test_harness_versioning_drafts import (  # noqa: F401 - fixture
    versioning_on as versioning_on,
)
from simulate.tests.test_harness_versioning_record import _save_drafts
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

    _save_drafts(environment, user=user)

    views = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
    )
    assert all(views[key]["state"] == "active" for key in keys if key != edited)
    # Certifying is off in these tests, so an unproved case reads validation_unavailable.
    assert views[edited]["state"] == "draft"
    assert views[edited]["reason"] in ("not_validated", "validation_unavailable")
