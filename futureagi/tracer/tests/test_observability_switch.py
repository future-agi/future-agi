"""The observability switch: the one place an agent's observability is turned on or off."""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from django.db.models import QuerySet
from rest_framework.exceptions import ErrorDetail
from structlog.testing import capture_logs

import tracer.services.observability_switch as switch
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from simulate.models import AgentDefinition
from simulate.services import agent_setup
from simulate.tests.test_agent_setup_service import source_agent
from tfc.middleware.workspace_context import set_workspace_context
from tfc.utils.api_errors import build_error_envelope
from tracer.models.observability_provider import (
    ObservabilityProvider,
    ProviderChoices,
)
from tracer.models.project import Project, ProjectSourceChoices

PROVIDERS_PATH = "/tracer/observability-provider/"


def _row(organization, workspace, metadata):
    project = Project.no_workspace_objects.create(
        name=f"switch-{uuid.uuid4().hex[:10]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    row = ObservabilityProvider.no_workspace_objects.create(
        project=project,
        provider=ProviderChoices.VAPI,
        enabled=True,
        organization=organization,
        workspace=workspace,
        metadata=metadata,
    )
    return row


def _stored(row):
    """The row's (metadata, enabled) as the database holds them."""
    return ObservabilityProvider._base_manager.values_list("metadata", "enabled").get(
        pk=row.pk
    )


def _patch(client, row, payload):
    return client.patch(f"{PROVIDERS_PATH}{row.id}/", payload, format="json")


def _statements():
    """Record the SQL text of every statement the connection runs, in order."""
    seen = []

    def spy(execute, sql, params, many, context):
        seen.append(sql)
        return execute(sql, params, many, context)

    return seen, connection.execute_wrapper(spy)


def _depth_at(statements, index):
    """How many savepoints are open when the statement at ``index`` runs."""
    depth = 0
    for sql in statements[:index]:
        if sql.startswith("SAVEPOINT"):
            depth += 1
        elif sql.startswith(("RELEASE SAVEPOINT", "ROLLBACK TO SAVEPOINT")):
            depth -= 1
    return depth


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------

KEYWORD_ONLY = inspect.Parameter.KEYWORD_ONLY
NO_DEFAULT = inspect.Parameter.empty

SUMMARY_FIELDS = [
    "agent_definition_id",
    "agent_version_id",
    "agent_stored",
    "setup_stale",
    "provider",
    "provider_agent_id",
    "credential_id",
    "key_present",
    "type",
    "kind",
    "unit_ids",
    "instrumentation",
    "setup_hash",
    "provider_info_complete",
    "missing",
]
KEY_MESSAGE = "API key is required when observability is enabled"
AGENT_ID_MESSAGE = "Assistant ID is required when observability is enabled"
INSTRUMENTATION_MESSAGE = (
    "The agent's code carries no OpenTelemetry or traceAI instrumentation the "
    "sandbox can configure (an HTTP exporter, not gRPC alone); add one and "
    "author the agent again"
)
MESSAGE_OF_FIELD = {"api_key": KEY_MESSAGE, "assistant_id": AGENT_ID_MESSAGE}
CODE_OF_FIELD = {
    "api_key": "credential_missing",
    "assistant_id": "provider_agent_id_missing",
}


def make_summary(**overrides):
    """A complete summary of a listed, fully configured hosted agent."""
    values = {
        "agent_definition_id": uuid.uuid4(),
        "agent_version_id": None,
        "agent_stored": False,
        "setup_stale": False,
        "provider": "vapi",
        "provider_agent_id": "assistant-1",
        "credential_id": uuid.uuid4(),
        "key_present": True,
        "type": "voice",
        "kind": "hosted",
        "unit_ids": ("unit-1",),
        "instrumentation": "none",
        "setup_hash": None,
        "provider_info_complete": True,
        "missing": (),
    }
    values.update(overrides)
    if "provider_info_complete" not in overrides:
        values["provider_info_complete"] = not values["missing"]
    return agent_setup.AgentSummary(**values)


@contextmanager
def seams(summary, *, providers=("vapi", "retell", "bland"), published=True):
    """Stand in for the two reads the switch makes; yields their mocks."""
    if published:
        listing = {"return_value": SimpleNamespace(providers=dict.fromkeys(providers))}
    else:
        listing = {"side_effect": switch.ProviderListNotPublished}
    answer = (
        {"side_effect": summary} if callable(summary) else {"return_value": summary}
    )
    with (
        patch.object(switch, "_agent_summary", **answer) as read_summary,
        patch.object(switch, "_read_provider_list", **listing) as read_list,
    ):
        yield read_summary, read_list


def _agent(organization, workspace, **fields):
    values = {
        "agent_name": f"switch-agent-{uuid.uuid4().hex[:8]}",
        "agent_type": AgentDefinition.AgentTypeChoices.VOICE,
        "contact_number": "+12345678901",
        "inbound": True,
        "description": "Switch test agent",
        "provider": "vapi",
        "assistant_id": "assistant-1",
        "api_key": "test-key",
        "organization": organization,
        "workspace": workspace,
        "languages": ["en"],
    }
    values.update(fields)
    return AgentDefinition.objects.create(**values)


def _agent_with_row(organization, workspace, *, enabled, metadata=None, **fields):
    agent = _agent(organization, workspace, **fields)
    row = _row(organization, workspace, metadata or {})
    ObservabilityProvider.no_workspace_objects.filter(pk=row.pk).update(enabled=enabled)
    agent.observability_provider = ObservabilityProvider.no_workspace_objects.get(
        pk=row.pk
    )
    agent.save()
    return agent


def _stored_agent(agent):
    return AgentDefinition.no_workspace_objects.get(pk=agent.pk)


def _turn(agent, enabled, user, organization, workspace):
    return switch.set_agent_observability(
        agent_definition=agent,
        enabled=enabled,
        user_id=str(user.id),
        organization=organization,
        workspace=workspace,
    )


def _created():
    """How many projects and provider rows exist."""
    return (
        Project.no_workspace_objects.count(),
        ObservabilityProvider.no_workspace_objects.count(),
    )


def _events(logs, prefix="observability_switch"):
    return [entry for entry in logs if entry["event"].startswith(prefix)]


def test_names_and_signatures_are_the_contract_ones():
    assert [
        f.name for f in dataclasses.fields(agent_setup.AgentSummary)
    ] == SUMMARY_FIELDS
    assert agent_setup.AgentSummary.__dataclass_params__.frozen
    assert [f.name for f in dataclasses.fields(switch.MissingPrerequisite)] == [
        "code",
        "field",
        "message",
    ]
    assert switch.MissingPrerequisite.__dataclass_params__.frozen
    assert [
        (p.name, p.kind, p.default)
        for p in inspect.signature(switch.set_agent_observability).parameters.values()
    ] == [
        (name, KEYWORD_ONLY, NO_DEFAULT)
        for name in (
            "agent_definition",
            "enabled",
            "user_id",
            "organization",
            "workspace",
        )
    ]
    assert list(inspect.signature(switch.refusal_body).parameters) == ["error"]
    assert list(
        inspect.signature(switch.check_observability_prerequisites).parameters
    ) == ["agent_definition"]
    assert issubclass(switch.ObservabilityRefused, Exception)
    assert issubclass(switch.ObservabilityProjectError, Exception)
    assert issubclass(switch.AgentNotFound, Exception)
    assert issubclass(switch.ProviderListNotPublished, Exception)


def test_prerequisites_are_the_contract_words():
    assert switch.PREREQUISITES == {
        "credential": switch.MissingPrerequisite(
            "credential_missing", "api_key", KEY_MESSAGE
        ),
        "provider_agent_id": switch.MissingPrerequisite(
            "provider_agent_id_missing", "assistant_id", AGENT_ID_MESSAGE
        ),
        "instrumentation": switch.MissingPrerequisite(
            "instrumentation_missing", "instrumentation", INSTRUMENTATION_MESSAGE
        ),
    }
    with pytest.raises(ValueError):
        switch.ObservabilityRefused(())
    refused = switch.ObservabilityRefused((switch.PREREQUISITES["credential"],))
    assert refused.missing == (switch.PREREQUISITES["credential"],)


def _stored_code_agent(organization, workspace, **agent_overrides):
    agent = _agent(
        organization,
        workspace,
        provider=None,
        assistant_id=None,
        api_key=None,
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
    )
    version = agent.create_version(
        description="", commit_message="first", status="active"
    )
    stored = agent_setup.store_agent(
        agent_version_id=version.id,
        agent=source_agent(**agent_overrides),
        sdk_version="0.2.3",
    )
    assert stored == "stored"
    return agent


def test_summary_comes_from_the_stored_agent_reader(organization, workspace):
    # Nothing stored: the reader answers today's agent, no kind and no instrumenter.
    plain = _agent(
        organization, workspace, provider="retell", assistant_id="assistant-9"
    )
    summary = switch._agent_summary(plain.id)
    assert (
        summary.agent_definition_id,
        summary.kind,
        summary.instrumentation,
        summary.provider,
        summary.provider_agent_id,
    ) == (plain.id, None, "none", "retell", "assistant-9")

    stored = switch._agent_summary(_stored_code_agent(organization, workspace).id)
    assert (stored.kind, stored.instrumentation) == ("source", "traceai")

    assert switch.AgentNotFound is agent_setup.AgentNotFound
    with pytest.raises(switch.AgentNotFound, match="^agent definition not found$"):
        switch._agent_summary(uuid.uuid4())
    with pytest.raises(switch.ProviderListNotPublished):
        switch._read_provider_list()


@pytest.mark.parametrize("instrumentation", ["traceai", "opentelemetry"])
def test_stored_code_agent_with_an_instrumenter_is_admitted(
    organization, workspace, user, instrumentation
):
    agent = _stored_code_agent(organization, workspace, instrumentation=instrumentation)
    row = _turn(agent, True, user, organization, workspace)
    assert isinstance(row, ObservabilityProvider)
    assert row.provider == "others"
    assert switch.agent_is_source(agent)


def test_stored_code_agent_without_an_instrumenter_is_refused(
    organization, workspace, user
):
    agent = _stored_code_agent(organization, workspace, instrumentation="none")
    before = _created()
    with pytest.raises(switch.ObservabilityRefused) as raised:
        _turn(agent, True, user, organization, workspace)
    assert raised.value.missing == (switch.PREREQUISITES["instrumentation"],)
    assert _created() == before


def test_agent_is_source_asks_the_summary(organization, workspace):
    agent = _agent(organization, workspace)
    with seams(make_summary(kind="source")):
        assert switch.agent_is_source(agent) is True
    for kind in (None, "hosted", "endpoint"):
        with seams(make_summary(kind=kind)):
            assert switch.agent_is_source(agent) is False


def test_on_list_complete_creates_project_and_row(organization, workspace, user):
    agent = _agent(organization, workspace)
    with seams(make_summary(agent_definition_id=agent.id)), capture_logs() as logs:
        row = _turn(agent, True, user, organization, workspace)

    assert isinstance(row, ObservabilityProvider)
    assert (row.enabled, row.provider) == (True, "vapi")
    project = Project.no_workspace_objects.get(pk=row.project_id)
    assert (project.name, project.trace_type, project.source) == (
        agent.agent_name,
        "observe",
        ProjectSourceChoices.SIMULATOR.value,
    )
    assert _stored_agent(agent).observability_provider_id == row.pk
    assert _events(logs) == []


@pytest.mark.parametrize(
    "missing, fields",
    [
        (("credential",), ["api_key"]),
        (("provider_agent_id",), ["assistant_id"]),
        (("credential", "provider_agent_id"), ["api_key", "assistant_id"]),
        (("provider", "credential"), ["api_key"]),
    ],
)
def test_missing_key_or_agent_id_refused_with_exact_body(
    organization, workspace, user, missing, fields
):
    agent = _agent(organization, workspace)
    before = _created()
    summary = make_summary(agent_definition_id=agent.id, missing=missing)
    with seams(summary), capture_logs() as logs:
        with pytest.raises(switch.ObservabilityRefused) as raised:
            _turn(agent, True, user, organization, workspace)

    error = raised.value
    assert [entry.field for entry in error.missing] == fields
    detail = "Observability cannot be turned on: " + "; ".join(
        MESSAGE_OF_FIELD[field] for field in fields
    )
    expected = build_error_envelope(
        detail,
        status_code=400,
        code="observability_prerequisite_missing",
        details={field: [MESSAGE_OF_FIELD[field]] for field in fields},
    )
    body = switch.refusal_body(error)
    assert body == expected
    assert json.dumps(body) == json.dumps(expected)
    assert body["code"] == "observability_prerequisite_missing"
    assert body["attr"] == fields[0]
    assert body["details"] == {field: [MESSAGE_OF_FIELD[field]] for field in fields}
    # Nothing was created and the agent still points at no row.
    assert _created() == before
    assert _stored_agent(agent).observability_provider_id is None
    assert _events(logs) == [
        {
            "event": "observability_switch_refused",
            "log_level": "info",
            "agent_definition_id": str(agent.id),
            "provider": "vapi",
            "codes": [CODE_OF_FIELD[field] for field in fields],
        }
    ]


def test_refusal_body_for_both_entries_is_the_contract_body():
    error = switch.ObservabilityRefused(
        (switch.PREREQUISITES["credential"], switch.PREREQUISITES["provider_agent_id"])
    )
    detail = f"Observability cannot be turned on: {KEY_MESSAGE}; {AGENT_ID_MESSAGE}"
    assert switch.refusal_body(error) == {
        "status": False,
        "type": "validation_error",
        "code": "observability_prerequisite_missing",
        "detail": detail,
        "message": detail,
        "error": detail,
        "result": detail,
        "attr": "api_key",
        "details": {
            "api_key": [KEY_MESSAGE],
            "assistant_id": [AGENT_ID_MESSAGE],
        },
    }


def test_plain_column_key_passes_and_logs(organization, workspace, user):
    agent = _agent(organization, workspace)
    plain = make_summary(agent_definition_id=agent.id, credential_id=None)
    with seams(plain), capture_logs() as logs:
        row = _turn(agent, True, user, organization, workspace)

    assert isinstance(row, ObservabilityProvider)
    assert _events(logs) == [
        {
            "event": "observability_switch_plain_column_key",
            "log_level": "warning",
            "agent_definition_id": str(agent.id),
            "provider": "vapi",
        }
    ]


def test_outbound_agent_is_checked(organization, workspace, user):
    agent = _agent(organization, workspace, inbound=False)
    before = _created()
    summary = make_summary(agent_definition_id=agent.id, missing=("credential",))
    with seams(summary):
        with pytest.raises(switch.ObservabilityRefused):
            _turn(agent, True, user, organization, workspace)
    assert _created() == before


def test_already_on_is_never_checked(organization, workspace, user):
    agent = _agent_with_row(organization, workspace, enabled=True)
    refusing = make_summary(missing=("credential", "provider_agent_id"))
    with seams(refusing) as (read_summary, read_list):
        row = _turn(agent, True, user, organization, workspace)

    assert row.pk == agent.observability_provider_id
    assert _stored(row)[1] is True
    read_summary.assert_not_called()
    read_list.assert_not_called()


def test_off_is_never_refused(organization, workspace, user):
    refusing = make_summary(missing=("credential", "provider_agent_id"))
    agent = _agent_with_row(organization, workspace, enabled=True)
    with seams(refusing) as (read_summary, read_list):
        row = _turn(agent, False, user, organization, workspace)
    assert _stored(row)[1] is False
    read_summary.assert_not_called()
    read_list.assert_not_called()

    # No row and off: nothing to switch, nothing created.
    bare = _agent(organization, workspace)
    before = _created()
    with seams(refusing) as (read_summary, read_list):
        assert _turn(bare, False, user, organization, workspace) is None
    assert _created() == before
    read_summary.assert_not_called()


def test_turn_on_of_an_existing_off_row_is_checked(organization, workspace, user):
    agent = _agent_with_row(organization, workspace, enabled=False)
    refusing = make_summary(agent_definition_id=agent.id, missing=("credential",))
    with seams(refusing):
        with pytest.raises(switch.ObservabilityRefused):
            _turn(agent, True, user, organization, workspace)
    assert _stored(agent.observability_provider)[1] is False

    with seams(make_summary(agent_definition_id=agent.id)):
        row = _turn(agent, True, user, organization, workspace)
    assert _stored(row)[1] is True


def _delete_project(agent):
    Project._base_manager.filter(pk=agent.observability_provider.project_id).update(
        deleted=True
    )


def _delete_row(agent):
    agent.observability_provider.delete()


@pytest.mark.parametrize("delete", [_delete_project, _delete_row])
def test_dead_row_is_cleared_on_turn_off(organization, workspace, user, delete):
    agent = _agent_with_row(organization, workspace, enabled=True)
    delete(agent)
    agent = _stored_agent(agent)
    with seams(make_summary()) as (read_summary, _):
        assert _turn(agent, False, user, organization, workspace) is None
    assert _stored_agent(agent).observability_provider_id is None
    read_summary.assert_not_called()


def test_turn_on_after_a_deleted_project_makes_a_fresh_project(
    organization, workspace, user
):
    # The user deleted the project, so the link is off; turning it on again
    # makes a new project and row, the way a first turn-on does.
    agent = _agent_with_row(organization, workspace, enabled=True)
    dead = agent.observability_provider
    _delete_project(agent)
    agent = _stored_agent(agent)
    projects, rows = _created()

    with seams(make_summary(agent_definition_id=agent.id)) as (read_summary, _):
        row = _turn(agent, True, user, organization, workspace)

    assert row is not None and row.pk != dead.pk
    assert row.project_id != dead.project_id
    assert row.enabled
    assert _stored_agent(agent).observability_provider_id == row.pk
    assert _created() == (projects + 1, rows + 1)
    # The first-turn-on path: prerequisites, then the agent's own name.
    read_summary.assert_called_once()
    assert _project_name(row) == agent.agent_name


def test_turn_on_after_a_deleted_project_takes_the_agents_plain_name(
    organization, workspace, user
):
    # The project the user deleted carried the agent's own name. The unique
    # constraint leaves deleted projects out, so the new project takes that
    # plain name too and the deleted one is left as it was.
    agent = _agent_with_row(organization, workspace, enabled=True)
    dead = agent.observability_provider
    Project._base_manager.filter(pk=dead.project_id).update(name=agent.agent_name)
    _delete_project(agent)
    agent = _stored_agent(agent)

    with seams(make_summary(agent_definition_id=agent.id)):
        row = _turn(agent, True, user, organization, workspace)

    assert row.pk != dead.pk and row.project_id != dead.project_id
    assert _project_name(row) == agent.agent_name
    old = Project._base_manager.get(pk=dead.project_id)
    assert old.deleted and old.name == agent.agent_name
    named = Project._base_manager.filter(name=agent.agent_name, trace_type="observe")
    assert named.count() == 2
    assert named.filter(deleted=False).get().pk == row.project_id


def test_turn_off_after_a_deleted_project_switches_a_link_made_meanwhile(
    organization, workspace, user
):
    # This copy still holds the dead link. Another request already cleared it
    # and linked a new, enabled row, so this clear matches no row. Only the
    # re-read on the primary shows the new link: a turn-off has no later
    # re-read under a lock to find it.
    agent = _agent_with_row(organization, workspace, enabled=True)
    _delete_project(agent)
    stale = _stored_agent(agent)
    assert stale.observability_provider.project.deleted
    newer = _row(organization, workspace, {})
    AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
        observability_provider=newer
    )
    created = _created()

    with (
        seams(make_summary(agent_definition_id=agent.id)) as (read_summary, _),
        patch.object(
            AgentDefinition,
            "refresh_from_db",
            autospec=True,
            side_effect=AgentDefinition.refresh_from_db,
        ) as reread,
    ):
        row = _turn(stale, False, user, organization, workspace)

    assert row.pk == newer.pk
    assert _stored(newer)[1] is False
    assert _stored_agent(agent).observability_provider_id == newer.pk
    assert _created() == created
    read_summary.assert_not_called()
    reread.assert_called_once()
    assert reread.call_args.kwargs["using"] == "default"


def test_turn_off_after_a_deleted_project_makes_nothing(organization, workspace, user):
    agent = _agent_with_row(organization, workspace, enabled=True)
    _delete_project(agent)
    agent = _stored_agent(agent)
    created = _created()

    with seams(make_summary()) as (read_summary, _):
        assert _turn(agent, False, user, organization, workspace) is None

    assert _stored_agent(agent).observability_provider_id is None
    assert _created() == created
    read_summary.assert_not_called()


def test_two_turn_ons_after_a_deleted_project_make_one_project(
    organization, workspace, user
):
    # Both requests loaded the agent while the link was still the dead one. The
    # primary re-read and the lock both catch the second; two other tests pin
    # each one alone.
    agent = _agent_with_row(organization, workspace, enabled=True)
    _delete_project(agent)
    first, second = _stored_agent(agent), _stored_agent(agent)
    assert first.observability_provider.project.deleted
    assert second.observability_provider.project.deleted
    projects, rows = _created()

    with seams(make_summary(agent_definition_id=agent.id)):
        one = _turn(first, True, user, organization, workspace)
        two = _turn(second, True, user, organization, workspace)

    assert one.pk == two.pk
    assert _created() == (projects + 1, rows + 1)
    assert_one_project_and_row(agent)


def test_link_made_during_the_prerequisite_check_is_switched_not_replaced(
    organization, workspace, user
):
    # The clear and the primary re-read are past: the clear matched the dead
    # link. Another request links a new row while this one reads the agent for
    # the prerequisites. Only the re-read under the lock finds it.
    agent = _agent_with_row(organization, workspace, enabled=True)
    _delete_project(agent)
    stale = _stored_agent(agent)
    newer = _row(organization, workspace, {})
    ObservabilityProvider.no_workspace_objects.filter(pk=newer.pk).update(
        enabled=False
    )
    created = _created()

    def read(agent_id):
        AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
            observability_provider=newer
        )
        return make_summary(agent_definition_id=agent.id)

    with seams(read):
        row = _turn(stale, True, user, organization, workspace)

    assert row.pk == newer.pk
    assert _stored(newer)[1] is True
    assert _stored_agent(agent).observability_provider_id == newer.pk
    assert _created() == created


def test_turn_on_refused_after_a_deleted_project_writes_nothing(
    organization, workspace, user
):
    agent = _agent_with_row(organization, workspace, enabled=True)
    dead_id = agent.observability_provider_id
    _delete_project(agent)
    agent = _stored_agent(agent)
    created = _created()

    refusing = make_summary(agent_definition_id=agent.id, missing=("credential",))
    with seams(refusing), pytest.raises(switch.ObservabilityRefused):
        with transaction.atomic():
            _turn(agent, True, user, organization, workspace)

    assert _created() == created
    # The clear of the dead link is one of the writes the caller's rollback undoes.
    assert _stored_agent(agent).observability_provider_id == dead_id


def test_turn_on_after_a_deleted_row_makes_a_fresh_one(organization, workspace, user):
    agent = _agent_with_row(organization, workspace, enabled=False)
    dead = agent.observability_provider
    _delete_row(agent)

    row = _turn_on(_stored_agent(agent), user, organization, workspace)

    assert row is not None and row.pk != dead.pk
    assert row.project_id != dead.project_id
    linked = _stored_agent(agent).observability_provider_id
    assert linked == row.pk
    assert ObservabilityProvider.objects.filter(pk=linked, enabled=True).exists()


@pytest.mark.parametrize("kind", [None, "hosted", "endpoint"])
def test_unlisted_provider_as_today(organization, workspace, user, kind):
    agent = _agent(organization, workspace)
    # It would be refused if it were checked.
    summary = make_summary(
        agent_definition_id=agent.id,
        provider="acme",
        kind=kind,
        missing=("credential", "provider_agent_id"),
    )
    with seams(summary), capture_logs() as logs:
        row = _turn(agent, True, user, organization, workspace)

    assert isinstance(row, ObservabilityProvider)
    # The row takes the agent's own column, not the summary's word.
    assert row.provider == "vapi"
    assert _events(logs) == []


@pytest.mark.parametrize("kind", [None, "hosted", "endpoint"])
def test_list_not_published_as_today_with_event(organization, workspace, user, kind):
    agent = _agent(organization, workspace)
    summary = make_summary(
        agent_definition_id=agent.id, kind=kind, missing=("credential",)
    )
    with seams(summary, published=False), capture_logs() as logs:
        row = _turn(agent, True, user, organization, workspace)

    assert isinstance(row, ObservabilityProvider)
    assert row.provider == "vapi"
    assert _events(logs) == [
        {
            "event": "observability_switch_unchecked",
            "log_level": "warning",
            "agent_definition_id": str(agent.id),
            "provider": "vapi",
            "reason": "provider_list_not_published",
        }
    ]


def test_list_not_published_still_decides_a_source_agent(organization, workspace, user):
    agent = _agent(organization, workspace, provider=None)
    none = make_summary(
        agent_definition_id=agent.id,
        kind="source",
        provider="",
        instrumentation="none",
    )
    before = _created()
    with seams(none, published=False), capture_logs() as logs:
        with pytest.raises(switch.ObservabilityRefused):
            _turn(agent, True, user, organization, workspace)
    assert _created() == before
    assert [entry["event"] for entry in _events(logs)] == [
        "observability_switch_unchecked",
        "observability_switch_refused",
    ]

    traced = dataclasses.replace(none, instrumentation="traceai")
    with seams(traced, published=False):
        row = _turn(agent, True, user, organization, workspace)
    assert row.provider == "others"


def test_kind_none_changes_nothing(organization, workspace, user):
    agent = _agent(organization, workspace)
    unlisted = make_summary(
        agent_definition_id=agent.id,
        provider="acme",
        kind=None,
        missing=("credential",),
        instrumentation="none",
    )
    with seams(unlisted), capture_logs() as logs:
        row = _turn(agent, True, user, organization, workspace)
    assert isinstance(row, ObservabilityProvider)
    assert row.provider == "vapi"
    assert _events(logs) == []


@pytest.mark.parametrize("kind", [None, "hosted", "source", "endpoint"])
def test_listed_provider_is_checked_whatever_the_kind(
    organization, workspace, user, kind
):
    agent = _agent(organization, workspace)
    before = _created()
    summary = make_summary(
        agent_definition_id=agent.id,
        kind=kind,
        instrumentation="traceai",
        missing=("credential",),
    )
    with seams(summary):
        with pytest.raises(switch.ObservabilityRefused) as raised:
            _turn(agent, True, user, organization, workspace)
    assert [entry.code for entry in raised.value.missing] == ["credential_missing"]
    assert _created() == before


@pytest.mark.parametrize("column", [None, "livekit", "phone"])
@pytest.mark.parametrize("instrumenter", ["traceai", "opentelemetry"])
def test_source_agent_admitted_with_an_instrumenter_and_refused_without(
    organization, workspace, user, column, instrumenter
):
    # An unlisted code agent: admitted when its code names an instrumenter, and
    # its row takes the generic provider word whatever its own column says.
    agent = _agent(organization, workspace, provider=column)
    summary = make_summary(
        agent_definition_id=agent.id,
        kind="source",
        provider=column or "",
        instrumentation=instrumenter,
        missing=("credential", "provider_agent_id"),
    )
    with seams(summary), capture_logs() as logs:
        row = _turn(agent, True, user, organization, workspace)
    assert isinstance(row, ObservabilityProvider)
    assert row.provider == "others"
    assert _events(logs) == []

    # Without one: refused before anything is created, with the one entry.
    refused_agent = _agent(organization, workspace, provider=column)
    before = _created()
    without = dataclasses.replace(
        summary, agent_definition_id=refused_agent.id, instrumentation="none"
    )
    with seams(without), capture_logs() as logs:
        with pytest.raises(switch.ObservabilityRefused) as raised:
            _turn(refused_agent, True, user, organization, workspace)
    assert raised.value.missing == (switch.PREREQUISITES["instrumentation"],)
    (entry,) = raised.value.missing
    assert (entry.code, entry.field, entry.message) == (
        "instrumentation_missing",
        "instrumentation",
        INSTRUMENTATION_MESSAGE,
    )
    assert switch.refusal_body(raised.value)["details"] == {
        "instrumentation": [INSTRUMENTATION_MESSAGE]
    }
    assert _created() == before
    assert _events(logs) == [
        {
            "event": "observability_switch_refused",
            "log_level": "info",
            "agent_definition_id": str(refused_agent.id),
            # Only our own provider words reach the log; free text reads "unknown".
            "provider": "livekit" if column == "livekit" else "unknown",
            "codes": ["instrumentation_missing"],
        }
    ]


def test_source_agent_with_a_listed_provider_goes_through_todays_checks(
    organization, workspace, user
):
    agent = _agent(organization, workspace)
    complete = make_summary(
        agent_definition_id=agent.id, kind="source", instrumentation="none"
    )
    with seams(complete):
        row = _turn(agent, True, user, organization, workspace)
    assert row.provider == "vapi"

    keyless = _agent(organization, workspace)
    without_key = dataclasses.replace(
        complete, agent_definition_id=keyless.id, missing=("credential",)
    )
    with seams(without_key):
        with pytest.raises(switch.ObservabilityRefused) as raised:
            _turn(keyless, True, user, organization, workspace)
    assert [entry.code for entry in raised.value.missing] == ["credential_missing"]


def test_project_error_when_no_row_comes_back(organization, workspace, user):
    agent = _agent(organization, workspace)
    with seams(make_summary(agent_definition_id=agent.id)):
        with patch.object(
            switch, "create_observability_provider", return_value={"error": "Invalid"}
        ):
            with pytest.raises(
                switch.ObservabilityProjectError,
                match="^observability project could not be created$",
            ):
                _turn(agent, True, user, organization, workspace)
    assert _stored_agent(agent).observability_provider_id is None


def _write_meanwhile(row_id):
    # Another request's write to a column the switch does not own.
    ObservabilityProvider.no_workspace_objects.filter(pk=row_id).update(
        metadata={"assistant_id": "a1", "written": "meanwhile"}
    )


def test_switch_saves_only_enabled(organization, workspace, user):
    agent = _agent_with_row(
        organization, workspace, enabled=False, metadata={"assistant_id": "a1"}
    )
    summary = make_summary(agent_definition_id=agent.id)

    def write_lands_during_the_check(agent_definition_id):
        # After the switch loaded the row and before it saves it.
        _write_meanwhile(agent.observability_provider_id)
        return summary

    with seams(write_lands_during_the_check):
        row = _turn(agent, True, user, organization, workspace)

    assert _stored(row) == ({"assistant_id": "a1", "written": "meanwhile"}, True)


def test_switch_saves_only_enabled_when_turning_off(organization, workspace, user):
    agent = _agent_with_row(
        organization, workspace, enabled=True, metadata={"assistant_id": "a1"}
    )
    row = agent.observability_provider
    real_save = ObservabilityProvider.save

    def write_then_save(self, *args, **kwargs):
        _write_meanwhile(self.pk)
        return real_save(self, *args, **kwargs)

    with patch.object(ObservabilityProvider, "save", write_then_save):
        _turn(agent, False, user, organization, workspace)

    assert _stored(row) == ({"assistant_id": "a1", "written": "meanwhile"}, False)


def test_agent_with_nothing_stored_keeps_todays_rule(
    auth_client, organization, workspace
):
    # Neither read is replaced: a vapi agent with no key is turned on through the
    # version view exactly as before, and the switch says it did not check.
    agent = _agent(organization, workspace, api_key=None, assistant_id=None)
    with capture_logs() as logs:
        response = auth_client.post(
            f"/simulate/agent-definitions/{agent.id}/versions/create/",
            {"commit_message": "turn on", "observability_enabled": True},
            format="json",
        )

    assert response.status_code == 201
    row = _stored_agent(agent).observability_provider
    assert row is not None
    assert (row.enabled, row.provider) == (True, "vapi")
    assert _events(logs) == [
        {
            "event": "observability_switch_unchecked",
            "log_level": "warning",
            "agent_definition_id": str(agent.id),
            "provider": "vapi",
            "reason": "provider_list_not_published",
        }
    ]


# ---------------------------------------------------------------------------
# The provider endpoint
# ---------------------------------------------------------------------------


def _put(client, row, payload):
    return client.put(f"{PROVIDERS_PATH}{row.id}/", payload, format="json")


@pytest.mark.parametrize("send", [_patch, _put])
def test_patch_on_provider_row_is_checked_before_perform_update(
    auth_client, organization, workspace, send
):
    agent = _agent_with_row(
        organization, workspace, enabled=False, metadata={"assistant_id": "a1"}
    )
    row = agent.observability_provider
    refusing = make_summary(
        agent_definition_id=agent.id, missing=("credential", "provider_agent_id")
    )
    with seams(refusing) as (read_summary, _):
        response = send(auth_client, row, {"enabled": True, "metadata": {"k": "v"}})

    refusal = switch.ObservabilityRefused(
        (switch.PREREQUISITES["credential"], switch.PREREQUISITES["provider_agent_id"])
    )
    assert response.status_code == 400
    assert response.json() == switch.refusal_body(refusal)
    # Refused before anything of the request was saved.
    assert _stored(row) == ({"assistant_id": "a1"}, False)
    read_summary.assert_called_once_with(agent.id)

    with seams(make_summary(agent_definition_id=agent.id)):
        response = send(auth_client, row, {"enabled": True})
    assert response.status_code == 200
    assert _stored(row) == ({"assistant_id": "a1"}, True)


def test_patch_checks_only_a_turn_on_of_a_row_with_a_live_agent(
    auth_client, organization, workspace
):
    refusing = make_summary(missing=("credential",))

    # Turning on a row whose agent is gone: not checked.
    gone = _agent_with_row(organization, workspace, enabled=False)
    AgentDefinition.no_workspace_objects.filter(pk=gone.pk).update(deleted=True)
    with seams(refusing) as (read_summary, _):
        response = _patch(auth_client, gone.observability_provider, {"enabled": True})
    assert response.status_code == 200
    read_summary.assert_not_called()

    # A row no agent points at: not checked.
    loose = _row(organization, workspace, {})
    ObservabilityProvider.no_workspace_objects.filter(pk=loose.pk).update(enabled=False)
    with seams(refusing) as (read_summary, _):
        response = _patch(auth_client, loose, {"enabled": True})
    assert response.status_code == 200
    read_summary.assert_not_called()

    # A request that does not turn it on, or that turns it off: not checked.
    live = _agent_with_row(organization, workspace, enabled=True)
    row = live.observability_provider
    with seams(refusing) as (read_summary, _):
        assert _patch(auth_client, row, {"enabled": True}).status_code == 200
        assert _patch(auth_client, row, {"metadata": {"k": 1}}).status_code == 200
        assert _patch(auth_client, row, {"enabled": False}).status_code == 200
        assert _patch(auth_client, row, {"metadata": {"k": 2}}).status_code == 200
    read_summary.assert_not_called()
    assert _stored(row)[1] is False


@pytest.mark.parametrize("word", ["", "grpc", "TRACEAI"])
def test_source_agent_with_an_unknown_instrumentation_word_is_refused(
    organization, workspace, user, word
):
    # Only the two named instrumenters admit a code agent; any other word fails closed.
    agent = _agent(organization, workspace, provider=None)
    summary = make_summary(
        agent_definition_id=agent.id,
        kind="source",
        provider="",
        instrumentation=word,
        missing=("credential", "provider_agent_id"),
    )
    before = _created()
    with seams(summary):
        with pytest.raises(switch.ObservabilityRefused) as raised:
            _turn(agent, True, user, organization, workspace)
    assert raised.value.missing == (switch.PREREQUISITES["instrumentation"],)
    assert _created() == before


def _short_tag(agent):
    return str(agent.id).replace("-", "")[:8]


def _turn_on(agent, user, organization, workspace):
    with seams(make_summary(agent_definition_id=agent.id)):
        return _turn(agent, True, user, organization, workspace)


def _project(organization, workspace, name, *, trace_type="observe", deleted=False):
    project = Project.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type=trace_type,
    )
    if deleted:
        Project._base_manager.filter(pk=project.pk).update(deleted=True)
    return project


def _other_workspace(organization, user):
    return Workspace.objects.create(
        name=f"Other {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )


def _project_name(row):
    return Project.no_workspace_objects.get(pk=row.project_id).name


def test_two_agents_with_one_name_get_two_projects(organization, workspace, user):
    name = f"Shared {uuid.uuid4().hex[:8]}"
    first = _agent(organization, workspace, agent_name=name)
    second = _agent(organization, workspace, agent_name=name)

    first_row = _turn_on(first, user, organization, workspace)
    second_row = _turn_on(second, user, organization, workspace)

    assert first_row.pk != second_row.pk
    assert first_row.project_id != second_row.project_id
    assert _project_name(first_row) == name
    assert _project_name(second_row) == f"{name} {_short_tag(second)}"
    assert _stored_agent(second).observability_provider_id == second_row.pk


def test_toggle_reuses_the_agents_own_project(organization, workspace, user):
    # The agent's own project holds its name, so a toggle that went through the
    # name check again would tag it.
    name = f"Toggled {uuid.uuid4().hex[:8]}"
    first = _agent(organization, workspace, agent_name=name)
    second = _agent(organization, workspace, agent_name=name)
    first_row = _turn_on(first, user, organization, workspace)
    second_row = _turn_on(second, user, organization, workspace)
    created = _created()

    for agent, row, expected in (
        (first, first_row, name),
        (second, second_row, f"{name} {_short_tag(second)}"),
    ):
        agent = _stored_agent(agent)
        with seams(make_summary(agent_definition_id=agent.id)):
            _turn(agent, False, user, organization, workspace)
            again = _turn(agent, True, user, organization, workspace)
        assert (again.pk, again.project_id) == (row.pk, row.project_id)
        assert _project_name(again) == expected
    assert _created() == created


def test_second_turn_on_of_the_same_agent_reuses_the_row(organization, workspace, user):
    # A request that loaded the agent before another one committed its row.
    name = f"Twice {uuid.uuid4().hex[:8]}"
    agent = _agent(organization, workspace, agent_name=name)
    first = _turn_on(_stored_agent(agent), user, organization, workspace)
    created = _created()

    second = _turn_on(agent, user, organization, workspace)

    assert (second.pk, second.project_id) == (first.pk, first.project_id)
    assert second.enabled
    assert ObservabilityProvider.no_workspace_objects.get(pk=first.pk).enabled
    assert _project_name(second) == name
    assert _created() == created
    assert _stored_agent(agent).observability_provider_id == first.pk


def test_project_no_row_uses_still_forces_the_tag(organization, workspace, user):
    # Made by an Observe-only user; it may belong to another agent.
    name = f"Customer {uuid.uuid4().hex[:8]}"
    own = _project(organization, workspace, name)
    agent = _agent(organization, workspace, agent_name=name)

    row = _turn_on(agent, user, organization, workspace)

    assert row.project_id != own.pk
    assert _project_name(row) == f"{name} {_short_tag(agent)}"


def test_project_of_another_trace_type_does_not_force_the_tag(
    organization, workspace, user
):
    name = f"Experiment {uuid.uuid4().hex[:8]}"
    _project(organization, workspace, name, trace_type="experiment")
    agent = _agent(organization, workspace, agent_name=name)

    row = _turn_on(agent, user, organization, workspace)

    assert _project_name(row) == name


def test_name_of_a_deleted_project_is_free(organization, workspace, user):
    name = f"Removed {uuid.uuid4().hex[:8]}"
    old = _project(organization, workspace, name, deleted=True)
    agent = _agent(organization, workspace, agent_name=name)

    row = _turn_on(agent, user, organization, workspace)

    assert row.project_id != old.pk
    assert _project_name(row) == name


def test_name_in_another_workspace_does_not_force_the_tag(
    organization, workspace, user
):
    name = f"Elsewhere {uuid.uuid4().hex[:8]}"
    _project(organization, _other_workspace(organization, user), name)
    agent = _agent(organization, workspace, agent_name=name)

    assert _project_name(_turn_on(agent, user, organization, workspace)) == name


def test_tag_is_stable(organization, workspace):
    name = f"Stable {uuid.uuid4().hex[:8]}"
    _project(organization, workspace, name)
    agent = _agent(organization, workspace, agent_name=name)

    first = switch._project_name_for_new_row(agent, organization, workspace)
    second = switch._project_name_for_new_row(agent, organization, workspace)

    assert first == second == f"{name} {_short_tag(agent)}"


@pytest.mark.parametrize("length", [255, 255 - 9, 255 - 8])
def test_truncation_keeps_the_longest_prefix_that_fits(organization, workspace, length):
    # The tag is a space and 8 characters: a name of 246 characters fits whole.
    name = "n" * length
    _project(organization, workspace, name)
    agent = _agent(organization, workspace, agent_name=name)

    chosen = switch._project_name_for_new_row(agent, organization, workspace)

    suffix = f" {_short_tag(agent)}"
    assert len(chosen) == 255
    assert chosen == name[: 255 - len(suffix)] + suffix


def test_tagged_name_taken_too_uses_the_longer_tag(organization, workspace, user):
    name = f"Crowded {uuid.uuid4().hex[:8]}"
    agent = _agent(organization, workspace, agent_name=name)
    _project(organization, workspace, name)
    _project(organization, workspace, f"{name} {_short_tag(agent)}")

    row = _turn_on(agent, user, organization, workspace)

    assert _project_name(row) == f"{name} {str(agent.id).replace('-', '')}"


def test_every_name_taken_is_a_project_error_and_writes_nothing(
    organization, workspace, user
):
    name = f"Full {uuid.uuid4().hex[:8]}"
    agent = _agent(organization, workspace, agent_name=name)
    _project(organization, workspace, name)
    _project(organization, workspace, f"{name} {_short_tag(agent)}")
    _project(organization, workspace, f"{name} {str(agent.id).replace('-', '')}")
    before = _created()

    with seams(make_summary(agent_definition_id=agent.id)):
        with pytest.raises(
            switch.ObservabilityProjectError,
            match="^observability project could not be created$",
        ):
            _turn(agent, True, user, organization, workspace)

    assert _created() == before
    assert _stored_agent(agent).observability_provider_id is None


def test_agent_named_like_a_hidden_project_gets_the_tagged_project(
    organization, workspace, user
):
    # The name check cannot see hidden projects, and the create refuses the name.
    name = f"fi-simulation-{uuid.uuid4()}"
    agent = _agent(organization, workspace, agent_name=name)

    row = _turn_on(agent, user, organization, workspace)

    assert _project_name(row) == f"{name} {_short_tag(agent)}"
    assert _stored_agent(agent).observability_provider_id == row.pk


def test_turn_on_after_a_deleted_row_tags_the_name_its_project_holds(
    organization, workspace, user
):
    name = f"Revived {uuid.uuid4().hex[:8]}"
    agent = _agent(organization, workspace, agent_name=name)
    dead = _turn_on(agent, user, organization, workspace)
    assert _project_name(dead) == name
    _delete_row(agent)

    row = _turn_on(_stored_agent(agent), user, organization, workspace)

    # The dead row's project is live and still holds the name.
    assert row.pk != dead.pk
    assert row.project_id != dead.project_id
    assert _project_name(row) == f"{name} {_short_tag(agent)}"
    assert _project_name(dead) == name
    assert _stored_agent(agent).observability_provider_id == row.pk


def _project_error_reason(logs, agent):
    # One error event: our own reason word and the agent id, nothing else.
    [logged] = _events(logs, "observability_switch_project_error")
    assert logged["log_level"] == "error"
    assert set(logged) - {"event", "log_level"} == {"agent_definition_id", "reason"}
    assert logged["agent_definition_id"] == str(agent.id)
    assert agent.agent_name not in str(logged)
    return logged["reason"]


def test_names_running_out_is_logged_with_its_reason(organization, workspace, user):
    name = f"Full {uuid.uuid4().hex[:8]}"
    agent = _agent(organization, workspace, agent_name=name)
    _project(organization, workspace, name)
    _project(organization, workspace, f"{name} {_short_tag(agent)}")
    _project(organization, workspace, f"{name} {str(agent.id).replace('-', '')}")

    with (
        seams(make_summary(agent_definition_id=agent.id)),
        capture_logs() as logs,
        pytest.raises(switch.ObservabilityProjectError),
    ):
        _turn(agent, True, user, organization, workspace)

    assert _project_error_reason(logs, agent) == "names_exhausted"


def test_no_default_workspace_is_logged_with_its_reason(organization, workspace, user):
    agent = _agent(organization, workspace)
    Workspace.no_workspace_objects.filter(
        organization=organization, is_default=True
    ).update(is_active=False)

    with (
        seams(make_summary(agent_definition_id=agent.id)),
        capture_logs() as logs,
        pytest.raises(
            switch.ObservabilityProjectError,
            match="^observability project could not be created$",
        ),
    ):
        _turn(agent, True, user, organization, None)

    assert _project_error_reason(logs, agent) == "no_default_workspace"
    assert _stored_agent(agent).observability_provider_id is None


def test_malformed_workspace_id_is_logged_with_its_reason(
    organization, workspace, user
):
    agent = _agent(organization, workspace)
    before = _created()

    with (
        seams(make_summary(agent_definition_id=agent.id)),
        capture_logs() as logs,
        pytest.raises(switch.ObservabilityProjectError),
    ):
        _turn(agent, True, user, organization, "not-a-workspace-id")

    assert _project_error_reason(logs, agent) == "bad_workspace_id"
    assert "not-a-workspace-id" not in str(logs)
    assert _created() == before


def test_no_workspace_is_checked_in_the_default_workspace(
    organization, workspace, user
):
    other = _other_workspace(organization, user)
    taken = f"Default taken {uuid.uuid4().hex[:8]}"
    elsewhere = f"Elsewhere {uuid.uuid4().hex[:8]}"
    _project(organization, workspace, taken)
    _project(organization, other, elsewhere)
    first = _agent(organization, workspace, agent_name=taken)
    second = _agent(organization, workspace, agent_name=elsewhere)

    row = _turn_on(first, user, organization, None)
    free = _turn_on(second, user, organization, None)

    assert _project_name(row) == f"{taken} {_short_tag(first)}"
    assert _project_name(free) == elsewhere
    assert Project.no_workspace_objects.get(pk=row.project_id).workspace_id == (
        workspace.pk
    )


def test_malformed_workspace_id_is_a_project_error(organization, workspace):
    agent = _agent(organization, workspace)

    with pytest.raises(
        switch.ObservabilityProjectError,
        match="^observability project could not be created$",
    ):
        switch._project_name_for_new_row(agent, organization, "not-a-workspace-id")


def test_name_check_ignores_the_requests_workspace(organization, workspace, user):
    # The scope is the agent's workspace, not the one the request runs in.
    name = f"Scoped {uuid.uuid4().hex[:8]}"
    _project(organization, workspace, name)
    agent = _agent(organization, workspace, agent_name=name)
    set_workspace_context(
        workspace=_other_workspace(organization, user),
        organization=organization,
        user=user,
    )
    try:
        chosen = switch._project_name_for_new_row(agent, organization, workspace)
    finally:
        set_workspace_context(workspace=workspace, organization=organization, user=user)

    assert chosen == f"{name} {_short_tag(agent)}"


def test_name_check_reads_the_primary(organization, workspace):
    with patch.object(
        QuerySet, "using", autospec=True, side_effect=QuerySet.using
    ) as using:
        switch._project_name_taken(organization, str(workspace.id), "any name")

    assert "default" in [call.args[1] for call in using.call_args_list]


def test_workspace_is_locked_once_inside_the_switchs_own_savepoint(
    organization, workspace, user
):
    # Taken before the first project read, and still held when the row is
    # created: the lock, the checks of every candidate and the inserts share one
    # savepoint that the switch opens, so a caller outside a transaction is
    # covered too.
    name = f"Locked {uuid.uuid4().hex[:8]}"
    _project(organization, workspace, name)
    agent = _agent(organization, workspace, agent_name=name)
    seen = []

    def spy(execute, sql, params, many, context):
        seen.append((sql, params))
        return execute(sql, params, many, context)

    with connection.execute_wrapper(spy):
        _turn_on(agent, user, organization, workspace)

    sqls = [sql for sql, _ in seen]

    def first(match):
        return next(i for i, sql in enumerate(sqls) if match(sql))

    agent_locked = first(
        lambda sql: "simulate_agent_definition" in sql and " FOR NO KEY UPDATE" in sql
    )
    locks = [i for i, sql in enumerate(sqls) if "pg_advisory_xact_lock" in sql]
    project_read = first(
        lambda sql: sql.startswith("SELECT") and '"tracer_project"' in sql
    )
    project_insert = first(lambda sql: sql.startswith('INSERT INTO "tracer_project"'))
    row_insert = first(
        lambda sql: sql.startswith('INSERT INTO "tracer_observability_provider"')
    )
    assert len(locks) == 1
    lock = locks[0]
    assert agent_locked < lock < project_read < project_insert < row_insert
    assert seen[lock][1] == [
        switch._workspace_lock_key(organization, str(workspace.id))
    ]
    assert _depth_at(sqls, agent_locked) == _depth_at(sqls, lock) == 1
    assert _depth_at(sqls, row_insert) >= 1


def test_lock_key_follows_organization_and_workspace(organization, workspace):
    key = switch._workspace_lock_key(organization, str(workspace.id))
    assert key == switch._workspace_lock_key(organization, str(workspace.id))
    assert key != switch._workspace_lock_key(organization, str(uuid.uuid4()))
    other_org = SimpleNamespace(id=uuid.uuid4())
    assert key != switch._workspace_lock_key(other_org, str(workspace.id))
    assert -(2**63) <= key < 2**63


def agent_update_columns(statements):
    """The columns each recorded UPDATE of an agent row wrote."""
    written = []
    for sql in statements:
        match = re.match(r'UPDATE "simulate_agent_definition" SET (.*?) WHERE', sql)
        if match:
            written.append(set(re.findall(r'"(\w+)" = ', match.group(1))))
    return written


@contextmanager
def stale_copies(agent, count=2):
    """Serve ``count`` copies of the agent, all read now, to the next lookups.

    A request's own ``AgentDefinition.objects.get`` returns them in turn, so each
    request starts from a copy that predates the others' writes.
    """
    manager = AgentDefinition.objects
    real_get = manager.get
    copies = [real_get(pk=agent.pk) for _ in range(count)]

    def get(*args, **kwargs):
        wanted = str(kwargs.get("id", kwargs.get("pk")))
        if wanted == str(agent.pk) and copies:
            return copies.pop(0)
        return real_get(*args, **kwargs)

    with patch.object(manager, "get", side_effect=get):
        yield


def assert_one_project_and_row(agent):
    """One observe project and one provider row for the agent, linked to it."""
    short, full = switch._agent_tags(agent)
    names = [
        agent.agent_name,
        switch._tagged_name(agent.agent_name, short),
        switch._tagged_name(agent.agent_name, full),
    ]
    rows = list(
        ObservabilityProvider.no_workspace_objects.filter(project__name__in=names)
    )
    projects = list(Project._base_manager.filter(name__in=names, trace_type="observe"))
    assert len(rows) == 1
    assert len(projects) == 1
    assert rows[0].project_id == projects[0].pk
    assert _stored_agent(agent).observability_provider_id == rows[0].pk


def test_switch_writes_only_the_link_when_it_creates_the_row(
    organization, workspace, user
):
    agent = _agent(organization, workspace)
    seen, recording = _statements()
    with seams(make_summary(agent_definition_id=agent.id)), recording:
        _turn(agent, True, user, organization, workspace)

    assert agent_update_columns(seen) == [{"observability_provider_id", "updated_at"}]


def test_switch_writes_only_the_link_when_it_clears_a_deleted_projects_row(
    organization, workspace, user
):
    agent = _agent_with_row(organization, workspace, enabled=True)
    _delete_project(agent)
    agent = _stored_agent(agent)
    seen, recording = _statements()
    with seams(make_summary()), recording:
        _turn(agent, False, user, organization, workspace)

    assert agent_update_columns(seen) == [{"observability_provider_id", "updated_at"}]


def test_switch_keeps_a_field_another_copy_wrote_meanwhile(
    organization, workspace, user
):
    agent = _agent(organization, workspace, description="Before")
    AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
        description="Written meanwhile"
    )

    _turn_on(agent, user, organization, workspace)

    stored = _stored_agent(agent)
    assert stored.description == "Written meanwhile"
    assert stored.observability_provider_id is not None


def test_first_turn_on_reads_the_agent_once(organization, workspace, user):
    agent = _agent(organization, workspace)
    with seams(make_summary(agent_definition_id=agent.id)) as (read_summary, _):
        row = _turn(agent, True, user, organization, workspace)
    assert row is not None
    assert read_summary.call_count == 1


def test_project_error_is_logged_before_the_refusal(organization, workspace, user):
    agent = _agent(organization, workspace)
    # What the serializer's errors look like when the helper returns them.
    errors = {
        "provider": [
            ErrorDetail('"phone x" is not a valid choice.', code="invalid_choice")
        ]
    }
    with (
        seams(make_summary(agent_definition_id=agent.id)),
        patch.object(switch, "create_observability_provider", return_value=errors),
        capture_logs() as logs,
        pytest.raises(switch.ObservabilityProjectError),
    ):
        _turn(agent, True, user, organization, workspace)

    [logged] = _events(logs, "observability_switch_project_error")
    assert logged["log_level"] == "error"
    assert logged["agent_definition_id"] == str(agent.id)
    assert (logged["fields"], logged["codes"]) == (["provider"], ["invalid_choice"])
    # The message text can carry free text, so it is never logged.
    assert "phone x" not in str(logged)
    assert _stored_agent(agent).observability_provider_id is None


def test_project_error_from_an_exception_logs_its_type(organization, workspace, user):
    agent = _agent(organization, workspace)
    errors = {"error": "Invalid data", "details": ValueError("agent name here")}
    with (
        seams(make_summary(agent_definition_id=agent.id)),
        patch.object(switch, "create_observability_provider", return_value=errors),
        capture_logs() as logs,
        pytest.raises(switch.ObservabilityProjectError),
    ):
        _turn(agent, True, user, organization, workspace)

    [logged] = _events(logs, "observability_switch_project_error")
    assert logged["error_type"] == "ValueError"
    assert "agent name here" not in str(logged)


@pytest.mark.parametrize("enabled", [True, False])
def test_clearing_a_dead_link_switches_a_link_made_meanwhile(
    organization, workspace, user, enabled
):
    # This copy still holds the dead link; another request already linked a new row.
    agent = _agent_with_row(organization, workspace, enabled=True)
    _delete_row(agent)
    stale = _stored_agent(agent)
    assert stale.observability_provider.deleted  # loaded while dead
    newer = _row(organization, workspace, {})
    ObservabilityProvider.no_workspace_objects.filter(pk=newer.pk).update(
        enabled=not enabled
    )
    AgentDefinition.no_workspace_objects.filter(pk=agent.pk).update(
        observability_provider=newer
    )
    created = _created()

    with seams(make_summary(agent_definition_id=agent.id)):
        _turn(stale, enabled, user, organization, workspace)

    assert _stored_agent(agent).observability_provider_id == newer.pk
    assert _stored(newer)[1] is enabled
    assert _created() == created


@pytest.mark.parametrize(
    "delete, enabled", [(_delete_project, False), (_delete_row, False)]
)
def test_clear_that_matches_no_row_still_drops_the_dead_link(
    organization, workspace, user, delete, enabled
):
    # The agent was soft-deleted meanwhile, so the clear finds no row and the
    # re-read still shows the dead link: the copy in hand drops it in memory.
    agent = _agent_with_row(organization, workspace, enabled=True)
    delete(agent)
    stale = _stored_agent(agent)
    dead_id = stale.observability_provider_id
    AgentDefinition._base_manager.filter(pk=agent.pk).update(deleted=True)
    created = _created()

    with (
        seams(make_summary(agent_definition_id=agent.id)),
        patch.object(
            AgentDefinition,
            "refresh_from_db",
            autospec=True,
            side_effect=AgentDefinition.refresh_from_db,
        ) as reread,
    ):
        assert _turn(stale, enabled, user, organization, workspace) is None

    assert stale.observability_provider_id is None
    stored = AgentDefinition._base_manager.get(pk=agent.pk)
    assert stored.observability_provider_id == dead_id
    assert _created() == created
    # The re-read goes to the primary, so a lagging replica cannot answer it.
    reread.assert_called_once()
    assert reread.call_args.kwargs["using"] == "default"


def test_turn_on_of_a_deleted_agent_after_a_deleted_project_raises(
    organization, workspace, user
):
    # The agent was soft-deleted meanwhile. The turn-on takes the first-turn-on
    # path, whose locked re-read finds no agent, so it raises (the version view
    # answers that with a 404) and nothing is made.
    agent = _agent_with_row(organization, workspace, enabled=True)
    _delete_project(agent)
    stale = _stored_agent(agent)
    AgentDefinition._base_manager.filter(pk=agent.pk).update(deleted=True)
    created = _created()

    with seams(make_summary(agent_definition_id=agent.id)):
        with pytest.raises(AgentDefinition.DoesNotExist):
            _turn(stale, True, user, organization, workspace)

    assert stale.observability_provider_id is None
    assert _created() == created


def _project_workspace(row):
    return str(Project.no_workspace_objects.get(pk=row.project_id).workspace_id)


def test_version_view_puts_the_project_in_the_agents_workspace(
    auth_client, organization, user
):
    other = _other_workspace(organization, user)
    auth_client.set_workspace(other)
    agent = _agent(organization, other)

    response = auth_client.post(
        f"/simulate/agent-definitions/{agent.id}/versions/create/",
        {"commit_message": "on", "observability_enabled": True},
        format="json",
    )

    assert response.status_code == 201
    assert _project_workspace(_stored_agent(agent).observability_provider) == str(
        other.id
    )


def test_create_view_puts_the_project_in_the_request_workspace(
    auth_client, organization, user
):
    other = _other_workspace(organization, user)
    auth_client.set_workspace(other)

    response = auth_client.post(
        "/simulate/agent-definitions/create/",
        {
            "agent_name": f"Elsewhere {uuid.uuid4().hex[:8]}",
            "agent_type": "voice",
            "provider": "vapi",
            "contact_number": "+12345678901",
            "inbound": True,
            "commit_message": "Initial version",
            "languages": ["en"],
            "api_key": "test-key",
            "assistant_id": "assistant-1",
            "authentication_method": "api_key",
            "observability_enabled": True,
        },
        format="json",
    )

    assert response.status_code == 201
    agent = AgentDefinition.no_workspace_objects.get(pk=response.json()["agent"]["id"])
    assert str(agent.workspace_id) == str(other.id)
    assert _project_workspace(agent.observability_provider) == str(other.id)
