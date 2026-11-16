"""A scenario's starting records: the seeded rows its fixture names, read from the world's seed files, and the choice
of another row written to the suite draft."""

from __future__ import annotations

import json
import sqlite3

import pytest

from accounts.models import Organization
from simulate.models import HostedHarnessJob
from simulate.services.harness_versioning import drafts
from simulate.services.harness_versioning.versions import (
    record_authoring,
    suite_documents,
)
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
    {"rider_id": "rdr_kavita", "phone": "+14155550103", "first_name": "Kavita"},
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


def _world_files(**world):
    documents = [_scenario(SCENARIO, fixture=FIXTURE)]
    return {
        "scenarios.json": json.dumps(documents),
        **{
            f"scenarios/{one['name']}/scenario.json": json.dumps(one)
            for one in documents
        },
        **world,
    }


def _rides(user, workspace, storage, key, **world):
    environment = _versioned(user, workspace, key, storage)
    record_authoring(
        environment,
        _authoring(
            names=NAMES[:1],
            contract=CONTRACT,
            **_world_files(**world or {"store.json": json.dumps(STORE)}),
        ),
        origin="manual",
        user=user,
    )
    environment.refresh_from_db()
    return environment


def _url(environment, key=KEY):
    return f"{BASE}/{environment.id}/scenarios/{key}/starting-records/"


def _choose(auth_client, environment, **body):
    return auth_client.put(
        _url(environment),
        {"path": ["identity", "rider_id"], "table": "users", **body},
        format="json",
    )


def _draft_fixture(environment):
    suite = suite_documents(drafts.edit_sides(environment)["suite"])
    return next(one for one in suite if one["name"] == SCENARIO)["fixture"]


def _sqlite(tables):
    connection = sqlite3.connect(":memory:")
    for name, (columns, rows) in tables.items():
        connection.execute(f"create table {name} ({columns})")
        for row in rows:
            connection.execute(
                f"insert into {name} values ({', '.join('?' for _ in row)})", row
            )
    connection.commit()
    data = connection.serialize()
    connection.close()
    return data


def test_the_rows_the_fixture_names_are_served_with_the_rows_it_could_start_from_instead(
    user, workspace, storage, auth_client
):
    environment = _rides(user, workspace, storage, "starting-served")

    response = auth_client.get(_url(environment))

    assert response.status_code == 200, response.content
    body = response.json()
    assert (
        body["scenario_key"],
        body["origin"],
        body["editable"],
        body["rows_unavailable"],
    ) == (
        KEY,
        "seed",
        True,
        None,
    )
    by_label = {one["label"]: one for one in body["records"]}
    # The rider and the phone are records; the pickup and the first name are facts no key holds.
    assert sorted(by_label) == ["identity.rider_id", "phone"]
    rider = by_label["identity.rider_id"]
    # The owning table wins over the wallet that points at it.
    assert (rider["table"], rider["key_field"], rider["key"]) == (
        "users",
        "rider_id",
        "rdr_arjun",
    )
    assert rider["path"] == ["identity", "rider_id"]
    assert rider["fields"] == USERS[0]
    assert [one["key"] for one in rider["alternatives"]] == ["rdr_priya", "rdr_kavita"]
    assert rider["alternatives"][0]["fields"] == USERS[1]
    assert rider["alternatives_total"] == 2
    # The phone names the rider's row, not the one-time code that shares its key.
    assert (by_label["phone"]["table"], by_label["phone"]["key_field"]) == (
        "users",
        "phone",
    )


def test_a_world_sqlite_is_read_when_there_is_no_store(
    user, workspace, storage, auth_client
):
    database = _sqlite(
        {
            "users": (
                "rider_id text primary key, phone text, first_name text",
                [tuple(one.values()) for one in USERS],
            )
        }
    )
    environment = _rides(
        user, workspace, storage, "starting-sqlite", **{"world.sqlite": database}
    )

    body = auth_client.get(_url(environment)).json()

    # The phone is UNIQUE by the contract, so it names the same row.
    assert [one["label"] for one in body["records"]] == ["identity.rider_id", "phone"]
    rider = body["records"][0]
    assert (rider["table"], rider["key"], rider["fields"]) == (
        "users",
        "rdr_arjun",
        USERS[0],
    )
    assert rider["alternatives_total"] == 2


def test_a_chosen_row_is_written_to_the_suite_draft_and_the_saved_version_waits(
    user, workspace, storage, auth_client
):
    environment = _rides(user, workspace, storage, "starting-chosen")
    saved = environment.suite_version_id

    response = _choose(auth_client, environment, key="rdr_priya")

    assert response.status_code == 200, response.content
    rider = next(
        one for one in response.json()["records"] if one["label"] == "identity.rider_id"
    )
    assert (rider["key"], rider["fields"]) == ("rdr_priya", USERS[1])
    environment.refresh_from_db()
    assert environment.suite_version_id == saved
    assert drafts.fresh_draft(environment, "suite") is not None
    # Only the chosen fact moves; the rest of the fixture is as it was.
    assert _draft_fixture(environment) == {
        **FIXTURE,
        "identity": {"rider_id": "rdr_priya", "first_name": "Arjun"},
    }


@pytest.mark.parametrize(
    "body, field",
    [
        ({"key": "rdr_nobody"}, "key"),
        ({"key": "rdr_priya", "table": "wallets"}, "table"),
        ({"key": "rdr_priya", "path": ["identity", "first_name"]}, "path"),
        ({"path": ["identity", "rider_id"], "table": "users"}, "key"),
    ],
)
def test_a_choice_the_world_cannot_hold_is_refused_with_a_problem_on_its_field(
    user, workspace, storage, auth_client, body, field
):
    environment = _rides(user, workspace, storage, f"starting-refused-{field}")
    sent = (
        auth_client.put(_url(environment), body, format="json")
        if "key" not in body
        else _choose(auth_client, environment, **body)
    )

    assert sent.status_code == 400, sent.content
    answer = sent.json()
    assert answer["error"] == "starting_record_invalid"
    assert [one["field"] for one in answer["problems"]] == [field]
    assert drafts.fresh_draft(environment, "suite") is None


def test_another_organization_s_environment_is_not_found(
    user, workspace, storage, auth_client
):
    environment = _rides(user, workspace, storage, "starting-foreign")
    HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
        organization=Organization.objects.create(name="Other org for starting records")
    )

    read = auth_client.get(_url(environment))
    chosen = _choose(auth_client, environment, key="rdr_priya")

    assert (read.status_code, chosen.status_code) == (404, 404)
    assert drafts.fresh_draft(environment, "suite") is None


def test_an_unknown_scenario_is_not_found(user, workspace, storage, auth_client):
    environment = _rides(user, workspace, storage, "starting-unknown")

    response = auth_client.get(_url(environment, key="no-such-scenario"))

    assert response.status_code == 404
    assert response.json()["error"] == "scenario_not_found"


def test_an_older_environment_without_seeded_rows_says_so_and_takes_no_choice(
    user, workspace, storage, auth_client
):
    environment = _legacy(user, workspace, "starting-legacy", storage)

    read = auth_client.get(_url(environment))
    chosen = _choose(auth_client, environment, key="rdr_priya")

    assert read.status_code == 200, read.content
    body = read.json()
    assert body["records"] == [] and body["editable"] is False
    assert body["rows_unavailable"]["code"] == "no_seed_rows"
    assert chosen.status_code == 409
    assert chosen.json()["error"] == "starting_records_read_only"
