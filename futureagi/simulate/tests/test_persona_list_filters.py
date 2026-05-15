"""
Tests for the persona list endpoint's filter pipeline.

Covers the `filters` query param introduced for multi-value selection and
the is/is_not operator on `GET /simulate/api/personas/`. The endpoint
parses a JSON array of {column_id, filter_config: {filter_op, filter_value}}
clauses and applies each to the queryset via `__in` / `.exclude(__in=...)`.
"""

import json
from contextlib import contextmanager

import pytest
from rest_framework import status

from simulate.models import Persona
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_organization,
    get_current_workspace,
    set_workspace_context,
)

PERSONA_LIST_URL = "/simulate/api/personas/"


@contextmanager
def _no_workspace_context():
    """Suspend the thread-local workspace/org so save signals don't auto-attach
    them — required when creating SYSTEM personas (DB check constraint forbids
    org/workspace on system rows)."""
    ws = get_current_workspace()
    org = get_current_organization()
    clear_workspace_context()
    try:
        yield
    finally:
        set_workspace_context(workspace=ws, organization=org)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def system_voice_persona(db):
    """A prebuilt (system) voice persona — visible to every workspace."""
    with _no_workspace_context():
        return Persona.no_workspace_objects.create(
            name="System Voice",
            persona_type=Persona.PersonaType.SYSTEM,
            simulation_type=Persona.SimulationTypeChoices.VOICE,
        )


@pytest.fixture
def system_text_persona(db):
    """A prebuilt (system) chat persona."""
    with _no_workspace_context():
        return Persona.no_workspace_objects.create(
            name="System Chat",
            persona_type=Persona.PersonaType.SYSTEM,
            simulation_type=Persona.SimulationTypeChoices.TEXT,
        )


@pytest.fixture
def workspace_voice_persona(db, organization, workspace):
    """A custom (workspace) voice persona in the test workspace."""
    return Persona.objects.create(
        name="Workspace Voice",
        persona_type=Persona.PersonaType.WORKSPACE,
        simulation_type=Persona.SimulationTypeChoices.VOICE,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def workspace_text_persona(db, organization, workspace):
    """A custom (workspace) chat persona in the test workspace."""
    return Persona.objects.create(
        name="Workspace Chat",
        persona_type=Persona.PersonaType.WORKSPACE,
        simulation_type=Persona.SimulationTypeChoices.TEXT,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def all_personas(
    system_voice_persona,
    system_text_persona,
    workspace_voice_persona,
    workspace_text_persona,
):
    """Convenience fixture that materializes all four persona variants."""
    return {
        "system_voice": system_voice_persona,
        "system_text": system_text_persona,
        "workspace_voice": workspace_voice_persona,
        "workspace_text": workspace_text_persona,
    }


def _body(response):
    """Persona list view wraps DRF's paginated payload in {status, result}."""
    data = response.json()
    return data.get("result", data)


def _names(response):
    return {p["name"] for p in _body(response)["results"]}


def _count(response):
    return _body(response)["count"]


def _filters_param(*clauses):
    return {"filters": json.dumps(list(clauses))}


def _clause(column_id, op, value):
    if not isinstance(value, list):
        value = [value]
    return {
        "column_id": column_id,
        "filter_config": {"filter_op": op, "filter_value": value},
    }


# ============================================================================
# Happy-path filter application
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestPersonaListFiltersHappyPath:
    """Each filter clause shape from the FE maps to the right ORM call."""

    def test_no_filters_returns_all_visible_personas(self, auth_client, all_personas):
        response = auth_client.get(PERSONA_LIST_URL)

        assert response.status_code == status.HTTP_200_OK
        names = _names(response)
        assert names == {
            "System Voice",
            "System Chat",
            "Workspace Voice",
            "Workspace Chat",
        }

    def test_is_prebuilt_returns_only_system_personas(
        self, auth_client, all_personas
    ):
        response = auth_client.get(
            PERSONA_LIST_URL, _filters_param(_clause("type", "is", ["prebuilt"]))
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"System Voice", "System Chat"}

    def test_is_custom_returns_only_workspace_personas(
        self, auth_client, all_personas
    ):
        response = auth_client.get(
            PERSONA_LIST_URL, _filters_param(_clause("type", "is", ["custom"]))
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"Workspace Voice", "Workspace Chat"}

    def test_is_multi_value_returns_union(self, auth_client, all_personas):
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("type", "is", ["prebuilt", "custom"])),
        )

        assert response.status_code == status.HTTP_200_OK
        assert len(_names(response)) == 4

    def test_is_not_prebuilt_excludes_system_personas(
        self, auth_client, all_personas
    ):
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("type", "is_not", ["prebuilt"])),
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"Workspace Voice", "Workspace Chat"}

    def test_is_plus_is_not_on_same_field_applied_together(
        self, auth_client, all_personas
    ):
        # type IN (prebuilt, custom) AND type NOT IN (custom) → only prebuilt
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(
                _clause("type", "is", ["prebuilt", "custom"]),
                _clause("type", "is_not", ["custom"]),
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"System Voice", "System Chat"}

    def test_cross_field_is_and_is_not(self, auth_client, all_personas):
        # type IS prebuilt AND simulation_type IS NOT voice → System Chat only
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(
                _clause("type", "is", ["prebuilt"]),
                _clause("simulation_type", "is_not", ["voice"]),
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"System Chat"}

    def test_contradiction_returns_zero_rows_not_500(
        self, auth_client, all_personas
    ):
        # type IS prebuilt AND type IS NOT prebuilt → impossible, empty result
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(
                _clause("type", "is", ["prebuilt"]),
                _clause("type", "is_not", ["prebuilt"]),
            ),
        )

        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 0

    def test_simulation_type_is_voice(self, auth_client, all_personas):
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("simulation_type", "is", ["voice"])),
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"System Voice", "Workspace Voice"}

    def test_filter_combined_with_search(self, auth_client, all_personas):
        # Search narrows by name; filter narrows by type. Both should apply.
        response = auth_client.get(
            PERSONA_LIST_URL,
            {
                "search": "Workspace",
                **_filters_param(_clause("simulation_type", "is", ["text"])),
            },
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"Workspace Chat"}


# ============================================================================
# Defensive parsing — bad clauses must never crash get_queryset
# ============================================================================


@pytest.mark.integration
@pytest.mark.api
class TestPersonaListFiltersDefensiveParsing:
    """Regression guard: a bad clause must not make get_queryset return a
    Response (which used to crash the paginator with `object has no len()`)."""

    def test_malformed_json_returns_200_no_filters_applied(
        self, auth_client, all_personas
    ):
        response = auth_client.get(PERSONA_LIST_URL, {"filters": "not-json"})

        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_filters_is_not_a_list_returns_200(self, auth_client, all_personas):
        response = auth_client.get(
            PERSONA_LIST_URL, {"filters": json.dumps({"not": "a list"})}
        )

        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_unknown_column_id_is_skipped(self, auth_client, all_personas):
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("not_a_real_column", "is", ["whatever"])),
        )

        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_unknown_simulation_type_value_is_skipped(
        self, auth_client, all_personas
    ):
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("simulation_type", "is", ["bogus_value"])),
        )

        # All values invalid → clause produces no `valid` list → skipped.
        # Endpoint behaves as if the clause weren't there.
        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_unknown_operator_is_skipped(self, auth_client, all_personas):
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("type", "starts_with", ["prebuilt"])),
        )

        # Unknown op falls through both `if op == "is"` and `elif op == "is_not"`.
        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_clause_is_a_string_not_a_dict_is_skipped(
        self, auth_client, all_personas
    ):
        response = auth_client.get(
            PERSONA_LIST_URL, {"filters": json.dumps(["just a string"])}
        )

        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_filter_config_missing_is_skipped(self, auth_client, all_personas):
        response = auth_client.get(
            PERSONA_LIST_URL, {"filters": json.dumps([{"column_id": "type"}])}
        )

        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_filter_value_is_a_non_string_is_skipped(
        self, auth_client, all_personas
    ):
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("type", "is", [123, None, {"k": "v"}])),
        )

        # No element survives `isinstance(v, str)` — clause becomes a no-op.
        assert response.status_code == status.HTTP_200_OK
        assert _count(response) == 4

    def test_partially_invalid_values_use_only_the_valid_ones(
        self, auth_client, all_personas
    ):
        # Mix valid + invalid: only `prebuilt` survives mapping.
        response = auth_client.get(
            PERSONA_LIST_URL,
            _filters_param(_clause("type", "is", ["prebuilt", "bogus", 42])),
        )

        assert response.status_code == status.HTTP_200_OK
        assert _names(response) == {"System Voice", "System Chat"}
