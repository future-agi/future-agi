"""Native PG producer eligibility; not authorization or complete enrollment."""

from uuid import uuid4

import pytest
from django.db import models, transaction
from django.test import override_settings

from accounts.models.user import User
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from simulate.models.agent_definition import AgentDefinition
from tracer.models.metric_pg_revision import MetricPgResponseRevision
from tracer.models.project import Project
from tracer.models.trace_session import TraceSessionOverlay
from tracer.services.derived_metrics.base_list_current_pg_state import (
    PgProjectScope,
    PgRevisionUnavailable,
    pg_response_mutation,
)
from tracer.services.derived_metrics.deletion_protocol import serialized_project
from tracer.services.simulation_project import ensure_simulation_project
from tracer.tests.test_base_list_current_pg_state import (
    label,
    other_project,
    revision,
    vector,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def agent(project):
    return AgentDefinition.objects.create(
        agent_name="PG producer guard",
        agent_type="text",
        inbound=True,
        organization=project.organization,
        workspace=project.workspace,
    )


@pytest.fixture
def owners(project, agent, settings):
    with override_settings(FI_SESSIONS_PG_REVISIONS_ENABLED=False):
        hidden = ensure_simulation_project(agent)
    settings.FI_SESSIONS_PG_REVISIONS_ENABLED = True
    project.save()
    return project, hidden


def assert_not_enrolled(project):
    assert not MetricPgResponseRevision.objects.filter(project_id=project.pk).exists()


def child(project, family):
    if family == "overlay":
        return TraceSessionOverlay(project_id=str(project.pk), trace_session_id=uuid4())
    common = {
        "organization": project.organization,
        "workspace": project.workspace,
    }
    if family == "label":
        return AnnotationsLabels(
            project=project, name=f"guard-{uuid4()}", type="text", **common
        )
    return Score(
        label=label(project),
        source_type="trace_session",
        trace_session_id=uuid4(),
        # Scope must follow the label, not this deliberately unrelated soft ID.
        tracer_project_id=uuid4(),
        value="initial",
        **common,
    )


def manager(row):
    return type(row).objects


@pytest.mark.parametrize("operation", ["save", "bulk_create", "get_or_create"])
def test_project_creation_excludes_only_simulation(project, agent, settings, operation):
    settings.FI_SESSIONS_PG_REVISIONS_ENABLED = True
    rows = [
        Project(
            organization=project.organization,
            workspace=project.workspace,
            model_type=project.model_type,
            trace_type=project.trace_type,
            name=f"guard-{uuid4()}",
            simulation_agent=owner,
        )
        for owner in (None, agent)
    ]
    if operation == "bulk_create":
        assert Project.with_simulation.bulk_create(rows) == rows
    else:
        for row in rows:
            if operation == "save":
                assert row.save() is None
            else:
                actual, created = Project.with_simulation.get_or_create(
                    pk=row.pk,
                    defaults={
                        field: getattr(row, field)
                        for field in (
                            "organization",
                            "workspace",
                            "model_type",
                            "trace_type",
                            "name",
                            "simulation_agent",
                        )
                    },
                )
                assert created and actual.pk == row.pk
    visible, hidden = rows
    assert revision(visible) > 0
    assert_not_enrolled(hidden)
    assert Project.with_simulation.filter(pk__in=[row.pk for row in rows]).count() == 2
    assert not Project.objects.filter(pk=hidden.pk).exists()


@pytest.mark.parametrize(
    "operation", ["save", "update", "bulk_update", "update_or_create"]
)
def test_project_mutations_keep_native_writes_and_visible_revision(owners, operation):
    visible, hidden = owners
    before = revision(visible)
    for row in owners:
        row.session_config = ["changed"]
    if operation == "bulk_update":
        assert (
            Project.with_simulation.bulk_update(
                owners, ["session_config"], batch_size=1
            )
            == 2
        )
    elif operation == "update":
        assert (
            Project.with_simulation.filter(pk__in=[p.pk for p in owners]).update(
                session_config=["changed"]
            )
            == 2
        )
    else:
        for row in owners:
            if operation == "save":
                row.save(update_fields=["session_config"])
            else:
                actual, created = Project.with_simulation.update_or_create(
                    pk=row.pk, defaults={"session_config": ["changed"]}
                )
                assert not created and actual.session_config == ["changed"]
    assert revision(visible) > before
    assert_not_enrolled(hidden)
    for row in owners:
        assert Project.with_simulation.get(pk=row.pk).session_config == ["changed"]


@pytest.mark.parametrize("family", ["overlay", "label", "score"])
@pytest.mark.parametrize(
    "operation", ["create", "save", "update", "bulk_create", "bulk_update"]
)
def test_owned_children_exclude_hidden_scopes_without_suppressing_writes(
    owners, family, operation
):
    visible, hidden = owners
    rows = [child(owner, family) for owner in owners]
    field = {"overlay": "display_name", "label": "name", "score": "value"}[family]
    before = revision(visible)
    if operation == "bulk_create":
        assert manager(rows[0]).bulk_create(rows) == rows
    else:
        for row in rows:
            row.save()
        if operation != "create":
            before = revision(visible)
            for row in rows:
                setattr(row, field, "changed")
            if operation == "bulk_update":
                assert manager(rows[0]).bulk_update(rows, [field], batch_size=1) == 2
            elif operation == "update":
                assert (
                    manager(rows[0])
                    .filter(pk__in=[r.pk for r in rows])
                    .update(**{field: "changed"})
                    == 2
                )
            else:
                for row in rows:
                    row.save(update_fields=[field])
    assert revision(visible) > before
    assert_not_enrolled(hidden)
    for row in rows:
        persisted = manager(row).get(pk=row.pk)
        assert getattr(persisted, field) == getattr(row, field)


@pytest.mark.parametrize("family", ["overlay", "label", "score"])
@pytest.mark.parametrize("operation", ["save", "update", "bulk_update"])
def test_owner_moves_preserve_visible_old_and_new_even_when_deleted(
    owners, family, operation
):
    visible, hidden = owners
    second = other_project(visible)
    row = child(visible, family)
    row.save()
    # Default managers must not drop a visible tombstone's invalidation.
    Project.all_objects.filter(pk=visible.pk).update(deleted=True)
    for destination in (hidden, second, visible):
        if family == "score":
            field, value = "label", label(destination)
            # Archived labels are still score owners, not missing scopes.
            value.delete()
        else:
            field, value = "project_id", destination.pk
        before = {p.pk: revision(p) for p in (visible, second)}
        setattr(row, field, value)
        if operation == "save":
            row.save(update_fields=[field])
        elif operation == "update":
            assert manager(row).filter(pk=row.pk).update(**{field: value}) == 1
        else:
            assert manager(row).bulk_update([row], [field]) == 1
        after = {p.pk: revision(p) for p in (visible, second)}
        expected = {
            hidden.pk: {visible.pk},
            second.pk: {second.pk},
            visible.pk: {visible.pk, second.pk},
        }[destination.pk]
        for p in (visible, second):
            assert (after[p.pk] > before[p.pk]) == (p.pk in expected)
        assert_not_enrolled(hidden)
        persisted = manager(row).get(pk=row.pk)
        assert getattr(persisted, field + "_id" if field == "label" else field) == (
            value.pk if field == "label" else value
        )


def test_hidden_overlay_delete_and_mixed_rollback(owners):
    visible, hidden = owners
    rows = [child(p, "overlay") for p in owners]
    TraceSessionOverlay.objects.bulk_create(rows)
    before = vector(visible)
    with pytest.raises(ValueError, match="rollback"):
        with transaction.atomic():
            assert (
                TraceSessionOverlay.objects.filter(pk__in=[r.pk for r in rows]).update(
                    display_name="rollback"
                )
                == 2
            )
            assert vector(visible) != before
            assert_not_enrolled(hidden)
            raise ValueError("rollback")
    assert vector(visible) == before
    assert not TraceSessionOverlay.objects.filter(display_name="rollback").exists()
    assert TraceSessionOverlay.objects.filter(pk=rows[1].pk).delete()[0] == 1
    assert vector(visible) == before
    assert_not_enrolled(hidden)
    assert TraceSessionOverlay.objects.filter(pk=rows[0].pk).delete()[0] == 1
    assert vector(visible) != before


@pytest.mark.parametrize("operation", ["save", "update", "bulk_update", "delete"])
def test_hard_deleted_old_owner_keeps_its_durable_fence(owners, operation):
    visible, hidden = owners
    overlay = child(visible, "overlay")
    overlay.save()
    # Use the existing explicit maintenance fence, not claimed Collector hook
    # coverage. Overlay's soft ownership survives the native Project lifetime.
    with serialized_project(visible.pk):
        with pg_response_mutation([PgProjectScope(visible.pk)], False):
            Project.with_simulation.filter(pk=visible.pk).delete()
    assert not Project.with_simulation.filter(pk=visible.pk).exists()
    assert TraceSessionOverlay.objects.filter(pk=overlay.pk).exists()
    before = vector(visible)
    targets = TraceSessionOverlay.objects.filter(pk=overlay.pk)
    if operation == "delete":
        assert targets.delete()[0] == 1
    else:
        overlay.project_id = str(hidden.pk)
        overlay.display_name = "moved"
        fields = ["project_id", "display_name"]
        if operation == "save":
            overlay.save(update_fields=fields)
        elif operation == "update":
            assert targets.update(project_id=hidden.pk, display_name="moved") == 1
        else:
            assert TraceSessionOverlay.objects.bulk_update([overlay], fields) == 1
        persisted = targets.get()
        assert persisted.project_id == hidden.pk and persisted.display_name == "moved"
    after = vector(visible)
    assert after.projects[0].token.incarnation == before.projects[0].token.incarnation
    assert after.projects[0].token.revision > before.projects[0].token.revision
    assert_not_enrolled(hidden)


def test_identity_stays_global_with_hidden_project_present(owners, user):
    visible, hidden = owners
    before = vector(visible)
    assert User.objects.filter(pk=user.pk).update(name="global identity") == 1
    after = vector(visible)
    assert after.projects == before.projects
    assert after.identity.revision == before.identity.revision + 1
    assert_not_enrolled(hidden)


def test_hidden_source_does_not_bypass_routing_or_unenrolled_operations(owners):
    _, hidden = owners
    old = hidden.name
    with pytest.raises(PgRevisionUnavailable, match="default primary alias"):
        hidden.save(using="replica")
    with pytest.raises(PgRevisionUnavailable, match="primary-key update"):
        Project.with_simulation.filter(pk=hidden.pk).update(pk=uuid4())
    with pytest.raises(PgRevisionUnavailable, match="bulk conflict-update"):
        Project.with_simulation.bulk_create(
            [hidden],
            update_conflicts=True,
            update_fields=["name"],
            unique_fields=["pk"],
        )
    assert Project.with_simulation.get(pk=hidden.pk).name == old
    assert_not_enrolled(hidden)


def test_visibility_conversion_preserves_old_fence_without_destructive_cleanup(
    owners, agent
):
    visible, hidden = owners
    # Production simulation writers assign the agent only at creation. Direct
    # conversion is not that contract; never delete a prior visible incarnation
    # or lose OLD invalidation merely to force a synthetic no-row invariant.
    hidden.simulation_agent = None
    hidden.save(update_fields=["simulation_agent"])
    assert revision(hidden) > 0
    before = vector(visible)
    Project.with_simulation.filter(pk=visible.pk).update(simulation_agent=agent)
    after = vector(visible)
    assert after.projects[0].token.incarnation == before.projects[0].token.incarnation
    assert after.projects[0].token.revision > before.projects[0].token.revision
    current = after
    assert (
        Project.with_simulation.filter(pk=visible.pk).update(name="still hidden") == 1
    )
    assert vector(visible) == current
    # The raw/base-manager APIs remain explicitly outside cooperative coverage.
    models.QuerySet(model=Project).filter(pk=visible.pk).update(name="unenrolled")
    assert vector(visible) == current
