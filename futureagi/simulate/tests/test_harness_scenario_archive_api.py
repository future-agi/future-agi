"""Archive takes a case out of future runs and keeps its identity; restore brings it back as a draft."""

from unittest.mock import patch

import pytest

from simulate.models import HostedHarnessScenario
from simulate.services.harness_versioning import drafts
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_drafts import _draft
from simulate.tests.test_harness_versioning_drafts import (  # noqa: F401 - fixture
    versioning_on as versioning_on,
)
from simulate.tests.test_harness_versioning_record import _save_drafts
from simulate.tests.test_harness_versioning_rollback import _amend_drop, _built

pytestmark = pytest.mark.django_db
BASE = "/simulate/api/harness-environments"


def _archive(client, environment, key, reason="retired route"):
    return client.post(
        f"{BASE}/{environment.id}/scenarios/{key}/archive/",
        {"reason": reason},
        format="json",
    )


def _restore(client, environment, key):
    return client.post(
        f"{BASE}/{environment.id}/scenarios/{key}/restore/", {}, format="json"
    )


def _draft_keys(environment):
    rows = drafts.draft_rows(environment)
    return [one.scenario_key for one in rows or []]


def test_archive_drops_the_key_from_the_draft_and_twice_is_refused(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "ar-once")
    key = _key(NAMES[1])

    first = _archive(auth_client, environment, key)
    second = _archive(auth_client, environment, key)

    assert first.status_code == 200, first.content
    assert first.json()["lifecycle"]["state"] == "archived"
    assert key not in _draft_keys(environment)
    row = HostedHarnessScenario.no_workspace_objects.get(
        job=environment, scenario_key=key
    )
    assert row.archive_reason == "retired route" and row.archived_content
    assert (second.status_code, second.json()["error"]) == (409, "scenario_archived")


def test_restore_puts_the_same_key_back_in_draft(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-restore")
    key = _key(NAMES[1])
    _archive(auth_client, environment, key)

    restored = _restore(auth_client, environment, key)

    assert restored.status_code == 200, restored.content
    assert restored.json()["lifecycle"]["reason"] == "in_draft"
    assert key in _draft_keys(environment)
    assert (
        HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=key
        ).archived_at
        is None
    )


def test_a_new_scenario_with_an_archived_key_is_refused(
    user, workspace, storage, auth_client
):
    from simulate.services.harness_versioning.versions import _refuse_retired_keys

    environment = _built(user, workspace, "ar-key")
    key = _key(NAMES[1])
    _archive(auth_client, environment, key)
    _save_drafts(environment, user=user)

    with pytest.raises(HostedHarnessError) as refused:
        _refuse_retired_keys(environment, [{"scenario_key": key, "name": "again"}])

    assert (refused.value.code, refused.value.status_code) == (
        "scenario_key_archived",
        422,
    )


def test_a_concurrent_suite_edit_is_draft_changed(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "ar-race")
    refused = HostedHarnessError("draft_changed", "edited elsewhere", status_code=409)

    with patch.object(drafts, "write_suite_edit", side_effect=refused):
        response = _archive(auth_client, environment, _key(NAMES[1]))

    assert (response.status_code, response.json()["error"]) == (409, "draft_changed")
    assert (
        HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        ).archived_at
        is None
    )


def test_restore_not_archived_409(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-not")
    response = _restore(auth_client, environment, _key(NAMES[0]))
    assert (response.status_code, response.json()["error"]) == (
        409,
        "scenario_not_archived",
    )


def test_archive_unsaved_added_row_409(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-unsaved")
    suite = drafts.suite_documents(drafts.edit_sides(environment)["suite"])
    drafts.write_suite_edit(
        environment,
        [*suite, {**suite[0], "name": "brand_new", "scenario_key": "brand-new"}],
        user=user,
    )
    assert _draft(environment) is not None

    response = _archive(auth_client, environment, "brand-new")

    assert (response.status_code, response.json()["error"]) == (409, "scenario_unsaved")


def test_prune_keeps_archived_rows(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-prune")
    archived, dropped = _key(NAMES[1]), _key(NAMES[2])
    _archive(auth_client, environment, archived)
    _amend_drop(auth_client, environment, NAMES[2])

    _save_drafts(environment, user=user)

    rows = {
        row.scenario_key: row
        for row in HostedHarnessScenario.all_objects.filter(job=environment)
    }
    assert rows[archived].deleted is False and rows[archived].archived_at is not None
    assert rows[dropped].deleted is True
