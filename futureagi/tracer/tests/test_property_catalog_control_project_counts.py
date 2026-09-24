"""Project inventory size is independent of catalog byte/resource admission."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tracer.management.commands import (
    ch25_property_catalog_lifecycle_controller as controller,
)
from tracer.services.clickhouse.v2.property_catalog import (
    revision_fence_registry as registry,
)
from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    RevisionBuildPlan,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    MAX_DEFINITION_JSON_BYTES,
    ZERO_UUID,
    CatalogCodecError,
)
from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
    WorkspaceCatalogScope,
)
from tracer.services.clickhouse.v2.property_catalog.models import (
    VisibilityBinding,
    VisibilityScope,
)
from tracer.services.clickhouse.v2.property_catalog.projection import (
    PostgresSnapshotContext,
    VisibilityContext,
    is_visible,
)
from tracer.tests import (
    test_property_catalog_control_plane as control_fixtures,
)
from tracer.tests import (
    test_property_catalog_durable_lifecycle as lifecycle_fixtures,
)
from tracer.tests import (
    test_property_catalog_production_lifecycle as production_fixtures,
)
from tracer.tests import (
    test_property_catalog_projection as projection_fixtures,
)
from tracer.tests import (
    test_property_catalog_revision_fence_registry as fence_fixtures,
)

ORG = production_fixtures.ORG
WORKSPACE = production_fixtures.WORKSPACE
SECOND_ORG = production_fixtures.SECOND_ORG
SECOND_WORKSPACE = production_fixtures.SECOND_WORKSPACE
NOW = fence_fixtures.NOW

Scope = (
    BuildPlanSourceScope
    | PostgresSnapshotContext
    | WorkspaceCatalogScope
    | controller.WorkspaceScope
)


def _projects(count: int) -> tuple[str, ...]:
    return tuple(fence_fixtures._uuid(index) for index in range(1, count + 1))


@pytest.fixture(params=(178, 257, 1024))
def project_ids(request: pytest.FixtureRequest) -> tuple[str, ...]:
    return _projects(request.param)


@pytest.fixture(params=("build", "postgres", "durable", "controller"))
def scope(request: pytest.FixtureRequest) -> Scope:
    if request.param == "build":
        return control_fixtures._plan().source_scope
    if request.param == "durable":
        return lifecycle_fixtures._scope()
    if request.param == "controller":
        return production_fixtures._scope()
    return PostgresSnapshotContext(
        organization_id=ORG,
        workspace_id=WORKSPACE,
        project_ids=_projects(1),
        catalog_epoch=1,
        catalog_revision=1,
        projection_version=1,
        snapshot_cutoff=NOW,
    )


def test_control_scopes_accept_project_inventory_without_count_cap(
    scope: Scope, project_ids: tuple[str, ...]
) -> None:
    accepted = replace(scope, project_ids=tuple(reversed(project_ids)))

    assert accepted.project_ids == project_ids


@pytest.mark.parametrize("invalid", ("empty", "duplicate", "uuid", "zero_uuid"))
def test_control_scopes_keep_project_validation(scope: Scope, invalid: str) -> None:
    projects = _projects(1024)
    invalid_projects = {
        "empty": (),
        "duplicate": (*projects, projects[-1]),
        "uuid": (*projects, "not-a-uuid"),
        "zero_uuid": (*projects, ZERO_UUID),
    }[invalid]
    error = (
        controller.ProductionLifecycleControllerError
        if isinstance(scope, controller.WorkspaceScope)
        and invalid in {"empty", "duplicate"}
        else ValueError
    )

    with pytest.raises(error, match="project"):
        replace(scope, project_ids=invalid_projects)


@pytest.mark.parametrize("count", (178, 257))
def test_build_plan_round_trips_large_inventory_below_byte_limit(count: int) -> None:
    plan = control_fixtures._plan()
    plan = replace(
        plan, source_scope=replace(plan.source_scope, project_ids=_projects(count))
    )

    decoded = RevisionBuildPlan.from_json(plan.canonical_json)

    assert decoded.source_scope == plan.source_scope
    assert set(decoded.streams) == set(plan.streams)
    assert decoded.canonical_json == plan.canonical_json
    assert decoded.sha256 == plan.sha256
    assert len(plan.canonical_json.encode()) <= MAX_DEFINITION_JSON_BYTES


def test_1024_project_build_plan_keeps_independent_json_byte_limit() -> None:
    plan = control_fixtures._plan()
    plan = replace(
        plan, source_scope=replace(plan.source_scope, project_ids=_projects(1024))
    )

    assert len(plan.source_scope.project_ids) == 1024
    with pytest.raises(
        CatalogCodecError, match=f"exceeds {MAX_DEFINITION_JSON_BYTES} UTF-8 bytes"
    ):
        _ = plan.canonical_json


def test_fence_round_trips_large_project_inventory(
    project_ids: tuple[str, ...],
) -> None:
    assignment = fence_fixtures._assignment(project_ids=tuple(reversed(project_ids)))

    raw = registry.encode_revision_fence_registry((assignment,), now=NOW)

    assert registry.decode_revision_fence_registry(raw, now=NOW) == (
        assignment.document,
    )
    assert assignment.project_ids == project_ids
    assert len(raw) < registry.MAX_REVISION_FENCE_BYTES


@pytest.mark.parametrize(
    ("invalid", "message"),
    (
        ([], "at least one"),
        ([*_projects(1024), "not-a-uuid"], "inventory is invalid"),
        ([*_projects(1024), ZERO_UUID], "inventory is invalid"),
        ([*_projects(1024), _projects(1024)[-1]], "sorted and unique"),
        (list(reversed(_projects(1024))), "sorted and unique"),
        ([*_projects(1024), "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"], "not canonical"),
        ([*_projects(1024), None], "inventory is invalid"),
    ),
)
def test_fence_decoder_keeps_inventory_validation(
    invalid: list[object], message: str
) -> None:
    raw = registry.encode_revision_fence_registry(
        (fence_fixtures._assignment(),), now=NOW
    )
    document = json.loads(raw)
    document["fences"][0]["project_ids"] = invalid
    raw = json.dumps(document, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(registry.RevisionFenceRegistryError, match=message):
        registry.decode_revision_fence_registry(raw, now=NOW)


def test_large_fence_keeps_byte_limit_on_encode_and_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assignment = fence_fixtures._assignment(project_ids=_projects(1024))
    raw = registry.encode_revision_fence_registry((assignment,), now=NOW)
    monkeypatch.setattr(registry, "MAX_REVISION_FENCE_BYTES", len(raw))
    assert registry.encode_revision_fence_registry((assignment,), now=NOW) == raw
    assert registry.decode_revision_fence_registry(raw, now=NOW) == (
        assignment.document,
    )
    monkeypatch.setattr(registry, "MAX_REVISION_FENCE_BYTES", len(raw) - 1)

    with pytest.raises(registry.RevisionFenceRegistryError, match="byte limit"):
        registry.encode_revision_fence_registry((assignment,), now=NOW)
    with pytest.raises(registry.RevisionFenceRegistryError, match="byte limit"):
        registry.decode_revision_fence_registry(raw, now=NOW)


@pytest.mark.parametrize("field", ("workspace_id", "organization_id", "project_ids"))
def test_large_fence_digest_binds_exact_tenant_and_inventory(field: str) -> None:
    assignment = fence_fixtures._assignment(project_ids=_projects(1024))
    raw = registry.encode_revision_fence_registry((assignment,), now=NOW)
    document = json.loads(raw)
    document["fences"][0][field] = (
        [*_projects(1023), fence_fixtures._uuid(2048)]
        if field == "project_ids"
        else fence_fixtures._uuid(9999)
    )
    raw = json.dumps(document, separators=(",", ":")).encode() + b"\n"

    with pytest.raises(
        registry.RevisionFenceRegistryError, match="digest does not match"
    ):
        registry.decode_revision_fence_registry(raw, now=NOW)


class _FakeQuery:
    """Same read-only query double used by the production lifecycle fixtures."""

    def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self.rows = rows
        self.filters: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def filter(self, *args: object, **kwargs: object) -> _FakeQuery:
        self.filters.append((args, kwargs))
        return self

    def order_by(self, *_fields: str) -> _FakeQuery:
        return self

    def values_list(self, *_fields: str) -> tuple[tuple[object, ...], ...]:
        return self.rows


def _discovery_queries(
    monkeypatch: pytest.MonkeyPatch,
    workspaces: tuple[tuple[object, ...], ...],
    projects: tuple[tuple[object, ...], ...],
) -> tuple[_FakeQuery, _FakeQuery]:
    workspace_query, project_query = _FakeQuery(workspaces), _FakeQuery(projects)
    monkeypatch.setattr(
        controller, "Workspace", SimpleNamespace(no_workspace_objects=workspace_query)
    )
    monkeypatch.setattr(
        controller, "Project", SimpleNamespace(no_workspace_objects=project_query)
    )
    return workspace_query, project_query


@pytest.mark.parametrize("allowlisted", (False, True))
def test_discovery_accepts_large_inventory_with_exact_workspace_isolation(
    monkeypatch: pytest.MonkeyPatch, project_ids: tuple[str, ...], allowlisted: bool
) -> None:
    third_workspace = fence_fixtures._uuid(9003)
    empty_workspace = fence_fixtures._uuid(9004)
    second_project = fence_fixtures._uuid(9001)
    third_project = fence_fixtures._uuid(9002)
    selected = (WORKSPACE, SECOND_WORKSPACE, third_workspace, empty_workspace)
    workspace_query, project_query = _discovery_queries(
        monkeypatch,
        (
            (WORKSPACE, ORG, True),
            (SECOND_WORKSPACE, ORG, False),
            (third_workspace, SECOND_ORG, False),
            (empty_workspace, SECOND_ORG, False),
        ),
        (
            *((project, ORG, WORKSPACE) for project in project_ids[:-1]),
            (project_ids[-1], ORG, None),
            (second_project, ORG, SECOND_WORKSPACE),
            (third_project, SECOND_ORG, third_workspace),
            (fence_fixtures._uuid(9991), SECOND_ORG, WORKSPACE),
            (fence_fixtures._uuid(9992), ORG, fence_fixtures._uuid(9993)),
            (fence_fixtures._uuid(9994), SECOND_ORG, None),
        ),
    )

    scopes, skipped = controller.discover_workspace_scopes(
        selected if allowlisted else None
    )

    assert tuple(scope.workspace_id for scope in scopes) == selected[:-1]
    assert scopes[0].project_ids == project_ids
    assert scopes[0].legacy_project_ids == (project_ids[-1],)
    assert scopes[1].project_ids == (second_project,)
    assert scopes[2].project_ids == (third_project,)
    assert scopes[1].legacy_project_ids == scopes[2].legacy_project_ids == ()
    assert skipped == (empty_workspace,)
    assert workspace_query.filters == (
        [((), {"is_active": True}), ((), {"id__in": selected})]
        if allowlisted
        else [((), {"is_active": True})]
    )
    assert project_query.filters == [
        (
            (
                controller.Q(workspace_id__in=selected)
                | controller.Q(organization_id__in=(ORG,), workspace_id__isnull=True),
            ),
            {},
        )
    ]


@pytest.mark.parametrize("duplicate", (False, True))
def test_discovery_rejects_invalid_project_after_large_inventory(
    monkeypatch: pytest.MonkeyPatch, duplicate: bool
) -> None:
    projects = _projects(1024)
    _discovery_queries(
        monkeypatch,
        ((WORKSPACE, ORG, False),),
        (
            *((project, ORG, WORKSPACE) for project in projects),
            (projects[-1] if duplicate else "not-a-uuid", ORG, WORKSPACE),
        ),
    )
    error = controller.ProductionLifecycleControllerError if duplicate else ValueError

    with pytest.raises(error, match="project"):
        controller.discover_workspace_scopes((WORKSPACE,))


def test_discovery_still_requires_every_allowlisted_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, project_query = _discovery_queries(monkeypatch, (), ())

    with pytest.raises(
        controller.ProductionLifecycleControllerError, match="missing or inactive"
    ):
        controller.discover_workspace_scopes((WORKSPACE,))
    assert project_query.filters == []


@pytest.mark.parametrize(
    ("is_default", "legacy", "message"),
    (
        (False, _projects(1), "default workspace"),
        (True, (_projects(1)[0],) * 2, "unique subset"),
        (True, (fence_fixtures._uuid(2048),), "unique subset"),
    ),
)
def test_large_controller_scope_keeps_legacy_tenant_rules(
    is_default: bool, legacy: tuple[str, ...], message: str
) -> None:
    with pytest.raises(controller.ProductionLifecycleControllerError, match=message):
        replace(
            production_fixtures._scope(),
            project_ids=_projects(1024),
            is_default=is_default,
            legacy_project_ids=legacy,
        )


def test_large_visibility_scope_does_not_grant_cross_workspace_or_org_access(
    project_ids: tuple[str, ...],
) -> None:
    row = projection_fixtures._row(
        visibility=VisibilityBinding(VisibilityScope.PROJECT, project_ids[-1])
    )
    context = VisibilityContext(
        organization_id=ORG, workspace_id=WORKSPACE, project_ids=frozenset(project_ids)
    )

    assert is_visible(row, context)
    assert not is_visible(row, replace(context, workspace_id=SECOND_WORKSPACE))
    assert not is_visible(row, replace(context, organization_id=SECOND_ORG))
    assert not is_visible(
        row, replace(context, project_ids=frozenset(project_ids[:-1]))
    )
