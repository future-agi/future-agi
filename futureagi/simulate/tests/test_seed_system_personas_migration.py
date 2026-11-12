"""Every install gets the 18 built-in personas from a data migration.

Before this migration, nothing called insert_system_personas(), so a new
self-hosted install had no built-in personas and the persona picker listed no
built-in personas. Production may already hold them from a manual run: the
migration matches each one on its stable key (persona_id of a system persona),
creates only the missing ones, and never changes, duplicates or deletes an
existing row.

Like mcp_server/tests/test_tool_group_migration.py, these tests run the
migration's step against the historical models of its parent state. Expected
values come from SYSTEM_PERSONAS, not from the migration.
"""

from functools import cache
from importlib import import_module

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from rest_framework import status

from simulate.models import Persona
from simulate.services.system_personas import SYSTEM_PERSONAS

MIGRATION = "simulate.migrations.0110_seed_system_personas"
KEY = ("simulate", "0110_seed_system_personas")
BUILT_IN = {persona["persona_id"]: persona for persona in SYSTEM_PERSONAS}
SYSTEM = Persona.PersonaType.SYSTEM


def _seed_migration():
    try:
        return import_module(MIGRATION)
    except ModuleNotFoundError as exc:
        if exc.name != MIGRATION:
            raise
        pytest.fail(
            "No migration creates the built-in personas, so a new install has none"
        )


@cache
def _historical_apps(parents):
    return MigrationLoader(None).project_state(list(parents)).apps


def _run_seed():
    """Run the migration's forward step as `migrate` would."""
    migration = _seed_migration()
    apps = _historical_apps(tuple(migration.Migration.dependencies))
    with connection.schema_editor(atomic=False) as editor:
        migration.forward(apps, editor)


def _system_rows():
    return list(
        Persona._base_manager.filter(persona_type=SYSTEM)
        .order_by("persona_id", "id")
        .values()
    )


def _hand_run(persona_ids):
    """System rows as an operator's earlier manual run left them."""
    Persona._base_manager.bulk_create(
        [
            Persona(
                persona_type=SYSTEM,
                persona_id=persona_id,
                name=BUILT_IN[persona_id]["name"],
                description=BUILT_IN[persona_id]["description"],
                additional_instruction=BUILT_IN[persona_id]["additional_instruction"],
                organization=None,
                workspace=None,
                is_default=True,
            )
            for persona_id in persona_ids
        ]
    )


def _expected(built_in):
    return {
        "persona_type": SYSTEM,
        "persona_id": built_in["persona_id"],
        "name": built_in["name"],
        "description": built_in["description"],
        "gender": built_in["gender"],
        "age_group": built_in["age_group"],
        "location": built_in["location"],
        "occupation": built_in["occupation"],
        "personality": built_in["personality"],
        "communication_style": built_in["communication_style"],
        "accent": built_in["accent"],
        "multilingual": False,
        "languages": built_in["languages"],
        "conversation_speed": built_in["conversation_speed"],
        "background_sound": built_in["background_sound"],
        "finished_speaking_sensitivity": built_in["finished_speaking_sensitivity"],
        "interrupt_sensitivity": built_in["interrupt_sensitivity"],
        "keywords": [],
        "metadata": built_in["custom_properties"],
        "additional_instruction": built_in["additional_instruction"],
        "organization_id": None,
        "workspace_id": None,
        "is_default": True,
        "deleted": False,
    }


@pytest.fixture
def no_system_personas(db):
    """Start from a database without built-in personas, like a new install."""
    Persona._base_manager.filter(persona_type=SYSTEM).delete()
    assert not _system_rows()


@pytest.mark.django_db
def test_new_install_gets_exactly_the_18_built_in_personas(
    no_system_personas, auth_client
):
    _run_seed()

    rows = _system_rows()
    # 18 is the released set. If SYSTEM_PERSONAS grows, installs that already
    # ran this migration will not get the new ones: add a new migration too.
    assert len(rows) == len(SYSTEM_PERSONAS) == 18
    assert [row["persona_id"] for row in rows] == sorted(BUILT_IN)
    for row in rows:
        expected = _expected(BUILT_IN[row["persona_id"]])
        assert {field: row[field] for field in expected} == expected

    # What a user sees: the API lists all of them, and the persona picker
    # shows them for voice agents (all 18 are voice personas).
    response = auth_client.get("/simulate/api/personas/system/")
    assert response.status_code == status.HTTP_200_OK
    listed = sorted(row["name"] for row in response.json()["result"])
    assert listed == sorted(persona["name"] for persona in SYSTEM_PERSONAS)

    # A plain `migrate` reaches this step on its way to Simulate's leaf.
    graph = MigrationLoader(None).graph
    leaves = graph.leaf_nodes("simulate")
    assert leaves
    assert all(KEY in graph.forwards_plan(leaf) for leaf in leaves)


@pytest.mark.django_db
def test_install_that_already_holds_the_18_is_left_unchanged(no_system_personas):
    _hand_run(sorted(BUILT_IN))
    Persona._base_manager.filter(persona_type=SYSTEM, persona_id=3).update(
        description="Edited by our support team"
    )
    before = _system_rows()

    _run_seed()
    _run_seed()  # Running the step again changes nothing either.

    after = _system_rows()
    assert after == before
    assert len(after) == 18
    assert len({row["persona_id"] for row in after}) == 18
    edited = next(row for row in after if row["persona_id"] == 3)
    assert edited["description"] == "Edited by our support team"


@pytest.mark.django_db
def test_install_with_5_of_them_gets_exactly_the_13_missing(no_system_personas):
    present = [2, 5, 9, 14, 18]
    _hand_run(present)
    # An operator hid one of them. It is still present: kept hidden, not recreated.
    Persona._base_manager.filter(persona_type=SYSTEM, persona_id=9).update(deleted=True)
    before = {row["id"]: row for row in _system_rows()}

    _run_seed()

    rows = _system_rows()
    assert len(rows) == 18
    assert sorted(row["persona_id"] for row in rows) == sorted(BUILT_IN)
    assert {row["id"]: row for row in rows if row["id"] in before} == before
    added = [row for row in rows if row["id"] not in before]
    assert len(added) == 13
    assert {row["persona_id"] for row in added} == set(BUILT_IN) - set(present)
    for row in added:
        expected = _expected(BUILT_IN[row["persona_id"]])
        assert {field: row[field] for field in expected} == expected


@pytest.mark.django_db
def test_user_personas_are_untouched(no_system_personas, organization, workspace):
    # Created while the install had no built-ins: same name and number as
    # built-in 1, but it belongs to a workspace, so it is not that persona.
    Persona.no_workspace_objects.create(
        persona_type=Persona.PersonaType.WORKSPACE,
        organization=organization,
        workspace=workspace,
        persona_id=1,
        name=BUILT_IN[1]["name"],
        description="Our own version",
    )
    Persona.no_workspace_objects.create(
        persona_type=Persona.PersonaType.WORKSPACE,
        organization=organization,
        workspace=workspace,
        name="Billing escalation caller",
    )
    user_rows = Persona._base_manager.exclude(persona_type=SYSTEM).order_by("id")
    before = list(user_rows.values())
    assert len(before) == 2

    _run_seed()

    assert list(user_rows.values()) == before
    rows = _system_rows()
    assert [row["persona_id"] for row in rows] == sorted(BUILT_IN)
    assert all(
        row["organization_id"] is None and row["workspace_id"] is None for row in rows
    )
