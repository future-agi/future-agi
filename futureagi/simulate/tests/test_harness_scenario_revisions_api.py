"""A case's revisions are its distinct contents across saved suite versions."""

import pytest

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
BASE = "/simulate/api/harness-environments"


def test_revisions_by_digest(user, workspace, storage, auth_client):
    environment = _built(user, workspace, "rv-digest")
    _amend(auth_client, environment, NAMES[0], "tests", "a second wording")
    _save(environment, user, key="first")
    # A save that leaves this case alone makes a third suite version with the same content.
    _amend(auth_client, environment, NAMES[1], "tests", "another case changed")
    _save(environment, user, key="second")

    response = auth_client.get(
        f"{BASE}/{environment.id}/scenarios/{_key(NAMES[0])}/revisions/"
    )

    assert response.status_code == 200, response.content
    revisions = response.json()["revisions"]
    assert [one["revision"] for one in revisions] == [2, 1]
    assert len(revisions[0]["suite_versions"]) == 2
    assert revisions[0]["changed_fields"] == ["tests"]
    assert response.json()["truncated"] is False
