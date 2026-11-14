"""Archive takes a case out of future runs at once, with no draft change, and keeps its identity; the next
Save leaves it out. Restore brings a still-saved case back at once, or a saved-out one back as a draft."""

import json
from unittest.mock import patch

import pytest
from django.test import override_settings

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


def _save(environment, user, key="save"):
    """Save, as `_amend_drop` does: a Scenarios-tab edit is left for its first run to certify."""
    versions = "simulate.services.harness_versioning.versions"
    with (
        override_settings(HARNESS_BUNDLE_CERTIFY_ENABLED=True),
        patch(f"{versions}._default_runtime", return_value=object()),
        patch(f"{versions}._cached_sealed", return_value=(None, "")),
    ):
        return _save_drafts(environment, user=user, key=key)


def _draft_keys(environment):
    rows = drafts.draft_rows(environment)
    return [one.scenario_key for one in rows or []]


def _draft_with_a_new_case(environment, user):
    """A suite draft that adds one case, so a Save has something to save."""
    files = drafts.edit_sides(environment)["suite"]
    suite = drafts.suite_documents(files)
    drafts.write_suite_edit(
        environment,
        [*suite, {**suite[0], "name": "brand_new", "scenario_key": "brand-new"}],
        user=user,
        restored_files=_copied_folder(files, suite[0], "brand_new"),
    )


def _saved_keys(environment):
    from simulate.models import HarnessSuiteMember

    environment.refresh_from_db()
    return set(
        HarnessSuiteMember.no_workspace_objects.filter(
            suite_version_id=environment.suite_version_id
        ).values_list("scenario_key", flat=True)
    )


def test_archive_once_and_twice_is_refused(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-once")
    key = _key(NAMES[1])

    first = _archive(auth_client, environment, key)
    second = _archive(auth_client, environment, key)

    assert first.status_code == 200, first.content
    assert first.json()["lifecycle"]["state"] == "archived"
    row = HostedHarnessScenario.no_workspace_objects.get(
        job=environment, scenario_key=key
    )
    assert row.archive_reason == "retired route" and row.archived_content
    assert (second.status_code, second.json()["error"]) == (409, "scenario_archived")


def test_archive_leaves_no_draft_change(user, workspace, storage, auth_client):
    from simulate.services.harness_scenario_lifecycle import (
        runnable,
        scenario_lifecycle,
    )

    environment = _built(user, workspace, "ar-no-draft")
    key = _key(NAMES[1])

    response = _archive(auth_client, environment, key)

    assert response.status_code == 200, response.content
    assert _draft(environment) is None
    assert not drafts.draft_rows(environment)
    environment.refresh_from_db()
    view = scenario_lifecycle(
        environment,
        suite_version=environment.suite_version,
        environment_version=environment.environment_version,
        keys=[key],
    )[key]
    # Selection holds an archived case back at once: it is still in the saved suite, archived.
    assert view["state"] == "archived" and runnable(view) is False


def test_save_drops_an_archived_member(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-save-drops")
    key = _key(NAMES[1])
    _archive(auth_client, environment, key)
    assert key in _saved_keys(environment)
    _draft_with_a_new_case(environment, user)

    _save(environment, user)

    saved = _saved_keys(environment)
    assert key not in saved and "brand-new" in saved


def test_restore_of_a_live_member_reads_its_lifecycle(
    user, workspace, storage, auth_client
):
    environment = _built(user, workspace, "ar-restore-live")
    key = _key(NAMES[1])
    _archive(auth_client, environment, key)

    restored = _restore(auth_client, environment, key)

    assert restored.status_code == 200, restored.content
    assert restored.json()["live"] is True
    assert restored.json()["lifecycle"]["reason"] != "in_draft"
    assert restored.json()["lifecycle"]["state"] != "archived"
    assert _draft(environment) is None


def test_restore_puts_the_same_key_back_in_draft(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-restore")
    key = _key(NAMES[1])
    _archive(auth_client, environment, key)
    _draft_with_a_new_case(environment, user)
    _save(environment, user)

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
    _draft_with_a_new_case(environment, user)
    _save(environment, user)

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
    key = _key(NAMES[1])
    _archive(auth_client, environment, key)
    _draft_with_a_new_case(environment, user)
    _save(environment, user)
    refused = HostedHarnessError("draft_changed", "edited elsewhere", status_code=409)

    with patch.object(drafts, "write_suite_edit", side_effect=refused):
        response = _restore(auth_client, environment, key)

    assert (response.status_code, response.json()["error"]) == (409, "draft_changed")
    assert (
        HostedHarnessScenario.no_workspace_objects.get(
            job=environment, scenario_key=key
        ).archived_at
        is not None
    )


def test_restore_not_archived_409(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-not")
    response = _restore(auth_client, environment, _key(NAMES[0]))
    assert (response.status_code, response.json()["error"]) == (
        409,
        "scenario_not_archived",
    )


def _copied_folder(files, document, name):
    """The first scenario's folder copied under ``name``, its scenario.json renamed."""
    source = next(
        path.split("/")[1]
        for path, body in files.items()
        if path.startswith("scenarios/")
        and path.endswith("/scenario.json")
        and json.loads(body).get("name") == document["name"]
    )
    copied = {}
    for path, body in files.items():
        if path.startswith(f"scenarios/{source}/"):
            target = f"scenarios/{name}/" + path.split("/", 2)[2]
            if target.endswith("/scenario.json"):
                body = json.dumps(
                    {**json.loads(body), "name": name, "scenario_key": "brand-new"}
                ).encode()
            copied[target] = body
    return copied


def test_archive_unsaved_added_row_409(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "ar-unsaved")
    files = drafts.edit_sides(environment)["suite"]
    suite = drafts.suite_documents(files)
    drafts.write_suite_edit(
        environment,
        [*suite, {**suite[0], "name": "brand_new", "scenario_key": "brand-new"}],
        user=user,
        restored_files=_copied_folder(files, suite[0], "brand_new"),
    )
    assert _draft(environment) is not None

    response = _archive(auth_client, environment, "brand-new")

    assert (response.status_code, response.json()["error"]) == (409, "scenario_unsaved")


def test_prune_keeps_archived_rows(user, workspace, storage, auth_client):
    from simulate.services.harness_versioning.versions import record_authoring
    from simulate.tests import test_harness_versioning_record as record_tests
    from simulate.tests.test_harness_versioning_rollback import _rows

    # Three scenarios, so one archived and one dropped still leave a suite.
    environment = record_tests._environment(user, workspace, "ar-prune")
    record_authoring(environment, record_tests._authoring(NAMES), origin="authoring")
    environment.refresh_from_db()
    _rows(environment)
    archived, dropped = _key(NAMES[1]), _key(NAMES[2])
    _archive(auth_client, environment, archived)
    # The drop's own Save makes both changes live.
    _amend_drop(auth_client, environment, NAMES[2])

    rows = {
        row.scenario_key: row
        for row in HostedHarnessScenario.all_objects.filter(job=environment)
    }
    assert rows[archived].deleted is False and rows[archived].archived_at is not None
    assert rows[dropped].deleted is True
