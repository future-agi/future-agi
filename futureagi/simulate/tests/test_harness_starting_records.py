"""A scenario's starting records, read-only: the seeded rows its fixture names, each looked up by its key in the
world's seed files, held or missing."""

from __future__ import annotations

import json
import sqlite3

import pytest
from urllib3.exceptions import HTTPError

from accounts.models import Organization, Workspace
from simulate.models import HostedHarnessJob
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_versioning_api import _versioned
from simulate.tests.test_harness_versioning_backfill import _legacy
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_harness_versioning_record import _authoring, _scenario

pytestmark = pytest.mark.django_db

BASE = "/simulate/api/harness-environments"
SCENARIO = NAMES[0]
KEY = _key(SCENARIO)

# A rides world as the builder writes one: who rides, their wallets (a row per rider, keyed by the rider) and the
# one-time codes keyed by phone.
CONTRACT = {
    "agent": "rides",
    "tools": [],
    "data_schema": {
        "users": {
            "rider_id": "TEXT PRIMARY KEY",
            "phone": "TEXT UNIQUE NOT NULL",
            "first_name": "TEXT NOT NULL",
        },
        "wallets": {
            "rider_id": "TEXT PRIMARY KEY REFERENCES users(rider_id)",
            "balance": "NUMERIC(10,2)",
        },
        "otp_codes": {"phone": "TEXT PRIMARY KEY", "code": "TEXT"},
    },
}
USERS = [
    {"rider_id": "rdr_arjun", "phone": "+14155550101", "first_name": "Arjun"},
    {"rider_id": "rdr_priya", "phone": "+14155550102", "first_name": "Priya"},
]
STORE = {
    "schema": [],
    "rows": {
        "users": USERS,
        "wallets": [{"rider_id": one["rider_id"], "balance": "10.00"} for one in USERS],
        "otp_codes": [{"phone": one["phone"], "code": "638204"} for one in USERS],
    },
}
FIXTURE = {
    "origin": "seed",
    "identity": {"rider_id": "rdr_arjun", "first_name": "Arjun"},
    "phone": "+14155550101",
    # Not a key of any table: a fact, never a record.
    "pickup": "MG Road, Bengaluru",
}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


def _author(environment, user, *, fixture=FIXTURE, scenario=None, extra=None, **world):
    """Land the environment's next authoring, as the builder's save does: the scenario, the world and any suite file."""
    document = _scenario(SCENARIO, fixture=fixture, **(scenario or {}))
    record_authoring(
        environment,
        _authoring(
            names=NAMES[:1],
            contract=CONTRACT,
            **{
                "scenarios.json": json.dumps([document]),
                f"scenarios/{SCENARIO}/scenario.json": json.dumps(document),
                **(world or {"store.json": json.dumps(STORE)}),
                **(extra or {}),
            },
        ),
        origin="manual",
        user=user,
    )
    environment.refresh_from_db()
    return environment


def _rides(user, workspace, storage, key, *, fixture=FIXTURE, scenario=None, **world):
    environment = _versioned(user, workspace, key, storage)
    return _author(environment, user, fixture=fixture, scenario=scenario, **world)


def _url(environment, key=KEY):
    return f"{BASE}/{environment.id}/scenarios/{key}/starting-records/"


def _by_label(response):
    assert response.status_code == 200, response.content
    return {one["label"]: one for one in response.json()["records"]}


def _users_database(rows):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "create table users (rider_id text primary key, phone text unique, first_name text)"
    )
    connection.executemany("insert into users values (?, ?, ?)", rows)
    connection.commit()
    data = connection.serialize()
    connection.close()
    return data


def test_the_rows_the_fixture_names_are_served_with_their_fields(
    user, workspace, storage, auth_client
):
    environment = _rides(user, workspace, storage, "starting-served")

    by_label = _by_label(auth_client.get(_url(environment)))

    # The rider and the phone are records; the pickup and the first name are facts no key holds.
    assert sorted(by_label) == ["identity.rider_id", "phone"]
    rider = by_label["identity.rider_id"]
    # The owning table wins over the wallet that points at it.
    assert (rider["table"], rider["key"], rider["status"], rider["fields"]) == (
        "users",
        "rdr_arjun",
        "seeded",
        USERS[0],
    )
    # The phone names the rider's row, not the one-time code that shares its key.
    assert by_label["phone"]["table"] == "users"


def test_a_record_the_seed_does_not_hold_reads_as_missing(
    user, workspace, storage, auth_client
):
    fixture = {**FIXTURE, "identity": {"rider_id": "rdr_nobody"}}
    environment = _rides(user, workspace, storage, "starting-missing", fixture=fixture)

    rider = _by_label(auth_client.get(_url(environment)))["identity.rider_id"]

    assert (rider["table"], rider["status"], rider["fields"]) == (
        "users",
        "missing",
        None,
    )


def test_a_world_sqlite_is_looked_up_by_key_however_far_down_the_row_is(
    user, workspace, storage, auth_client
):
    # The named rider is the last of more rows than any read of the first ones would reach.
    filler = [(f"rdr_{n}", f"+1000{n}", f"Rider {n}") for n in range(1500)]
    database = _users_database([*filler, tuple(USERS[0].values())])
    environment = _rides(
        user, workspace, storage, "starting-sqlite", **{"world.sqlite": database}
    )

    rider = _by_label(auth_client.get(_url(environment)))["identity.rider_id"]

    assert (rider["table"], rider["status"], rider["fields"]) == (
        "users",
        "seeded",
        USERS[0],
    )


def test_a_scenario_whose_setup_changes_the_world_says_the_rows_are_as_seeded(
    user, workspace, storage, auth_client
):
    plain = _rides(user, workspace, storage, "starting-no-setup")
    with_setup = _rides(
        user,
        workspace,
        storage,
        "starting-setup",
        scenario={"setup_code": "def setup(world):\n    world.insert('users', {})\n"},
    )

    assert auth_client.get(_url(plain)).json()["setup_note"] is None
    assert auth_client.get(_url(with_setup)).json()["setup_note"]


def test_the_records_are_read_only(user, workspace, storage, auth_client):
    environment = _rides(user, workspace, storage, "starting-read-only")

    sent = auth_client.put(
        _url(environment),
        {"path": ["identity", "rider_id"], "table": "users", "key": "rdr_priya"},
        format="json",
    )

    assert sent.status_code == 405


def test_an_environment_outside_the_caller_s_workspace_is_not_found(
    user, workspace, storage, auth_client
):
    foreign = _rides(user, workspace, storage, "starting-foreign")
    HostedHarnessJob.no_workspace_objects.filter(id=foreign.id).update(
        organization=Organization.objects.create(name="Other org for starting records")
    )
    neighbour = _rides(user, workspace, storage, "starting-other-workspace")
    HostedHarnessJob.no_workspace_objects.filter(id=neighbour.id).update(
        workspace=Workspace.no_workspace_objects.create(
            name="Other workspace for starting records",
            organization=user.organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
    )

    assert auth_client.get(_url(foreign)).status_code == 404
    assert auth_client.get(_url(neighbour)).status_code == 404


def test_world_files_storage_cannot_serve_read_as_unreadable_not_an_error(
    user, workspace, storage, auth_client, monkeypatch
):
    environment = _rides(user, workspace, storage, "starting-storage-down")

    def storage_down(job):
        raise HTTPError("connection reset")

    monkeypatch.setattr(drafts, "edit_sides", storage_down)

    response = auth_client.get(_url(environment))

    assert response.status_code == 200, response.content
    body = response.json()
    assert (body["records"], body["rows_unavailable"]["code"]) == (
        [],
        "files_unreadable",
    )


def test_an_unknown_scenario_is_not_found(user, workspace, storage, auth_client):
    environment = _rides(user, workspace, storage, "starting-unknown")

    response = auth_client.get(_url(environment, key="no-such-scenario"))

    assert response.status_code == 404
    assert response.json()["error"] == "scenario_not_found"


def test_an_older_environment_without_seeded_rows_says_so(
    user, workspace, storage, auth_client
):
    environment = _legacy(user, workspace, "starting-legacy", storage)

    response = auth_client.get(_url(environment))

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["records"] == []
    assert body["rows_unavailable"]["code"] == "no_seed_rows"
