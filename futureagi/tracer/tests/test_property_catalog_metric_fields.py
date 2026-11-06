"""Active metric fields in the scoped current catalog (Postgres only)."""

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from rest_framework import serializers

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
from tracer.models.project import Project
from tracer.serializers.derived_metric import DerivedMetricWriteSerializer
from tracer.serializers.filters import FilterListField
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.property_catalog.codec import (
    MAX_IDENTITY_COMPONENT_BYTES,
    CatalogCodecError,
    canonical_json,
    stable_property_id,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PropertyCatalogCursorError,
)
from tracer.services.clickhouse.v2.property_catalog.models import PropertyCategory
from tracer.services.clickhouse.v2.property_catalog.reader import PropertyCatalogReader
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    CurrentDefinitionSource,
    definition_metric,
)
from tracer.services.derived_metrics import list_filter_capabilities
from tracer.services.derived_metrics.predicates import decode_metric_field_id
from tracer.services.derived_metrics.spec import normalize_spec
from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS


@pytest.fixture
def tenant(db):
    organization = Organization.objects.create(name="metric-catalog-test")
    user = User(
        id=uuid4(), email=f"{uuid4()}@example.invalid", organization=organization
    )
    User.objects.bulk_create([user])
    workspace = Workspace(name="catalog", organization=organization, created_by=user)
    foreign_workspace = Workspace(
        name="foreign", organization=organization, created_by=user
    )
    Workspace.no_workspace_objects.bulk_create([workspace, foreign_workspace])
    projects = [
        Project(
            name=name,
            organization=organization,
            workspace=ws,
            trace_type="observe",
            model_type="generative_llm",
        )
        for name, ws in (
            ("first", workspace),
            ("sibling", workspace),
            ("foreign", foreign_workspace),
        )
    ]
    Project.no_workspace_objects.bulk_create(projects)
    return SimpleNamespace(
        organization=organization,
        workspace=workspace,
        foreign_workspace=foreign_workspace,
        project=projects[0],
        sibling=projects[1],
        foreign_project=projects[2],
        scope={
            "organization_id": str(organization.id),
            "workspace_id": str(workspace.id),
            "project_ids": [str(projects[0].id)],
            "principal_id": str(user.id),
        },
    )


def create_metric(tenant, *, project=None, name=None, metric_id=None, **semantics):
    project = project or tenant.project
    metric = DerivedMetric.no_workspace_objects.create(
        id=metric_id or uuid4(),
        name=name or f"Metric {uuid4()}",
        project=project,
        workspace=project.workspace,
        organization=project.organization,
    )
    version = create_version(metric, **semantics)
    DerivedMetric.activate_version(metric.id, version.id, expected_active_id=None)
    return metric, version


def create_version(metric, *, version=1, **semantics):
    definition = {
        "kind": "share",
        "level": "conversation",
        "value_type": "boolean",
        "value_labels": {"match": "Resolved", "other": "Not resolved"},
        "unit": "%",
        "value_format": "percent",
        "group_dims": ["locale"],
        "formula": "x",
        "inputs": {},
        "selection_unit": "session",
        "weighting": "unit",
        "definition_hash": str(version) * 64,
        "backfill_status": "complete",
    }
    definition.update(semantics)
    return DerivedMetricVersion.objects.create(
        metric=metric, version=version, **definition
    )


def page(tenant, *, scope=None, reader=None, include_counts=False, **query):
    return (
        reader or PropertyCatalogReader(catalog_database="unused_metric_catalog")
    ).read_page(
        scope=scope or tenant.scope,
        query={"property_kind": "metric_field", "category": "metric", **query},
        page_size=50,
        include_counts=include_counts,
    )


def validate_descriptor(tenant, item, context, *, op="equals", value=None):
    if value is None:
        if op in {"between", "not_between"}:
            value = [0, 10]
        elif op in {"in", "not_in"}:
            value = ["example"]
        else:
            value = {
                "number": 25,
                "boolean": item["value_labels"].get("match", True),
                "text": "example",
            }[item["type"]]
    chip = {
        "column_id": item["column_id"],
        "property_id": item["property_id"],
        "display_name": item["display_name"],
        "source": item["source"],
        "filter_config": {
            "col_type": "METRIC_FIELD",
            "filter_type": item["type"],
            "filter_op": op,
            "filter_value": value,
        },
    }

    class Request(serializers.Serializer):
        filters = FilterListField()

    request = Request(
        data={"filters": deepcopy([chip])},
        context={
            "project_id": item["project_id"],
            "organization_id": str(tenant.organization.id),
            "workspace_id": str(tenant.workspace.id),
            "metric_filter_context": context,
        },
    )
    request.is_valid(raise_exception=True)
    assert request.validated_data["filters"] == [chip]
    return chip


def test_metric_id_codec_is_canonical_and_shared_with_filter_validation():
    metric_id = UUID("82e4bdfc-fb55-482d-a7a0-28e8755bf66a")
    identity = stable_property_id("metric_field", str(metric_id).upper())
    assert identity == f"metric_field:{metric_id}"
    assert decode_metric_field_id(identity) == str(metric_id)


@pytest.mark.parametrize(
    "key", ["not-a-uuid", "00000000-0000-0000-0000-000000000000", "x:bad"]
)
def test_metric_id_codec_rejects_invalid_or_zero_ids(key):
    with pytest.raises(CatalogCodecError):
        stable_property_id("metric_field", key)
    with pytest.raises(CatalogCodecError):
        stable_property_id("metric_field", uuid4(), primary_source="users")


def test_catalog_scopes_metrics_and_their_parents_before_pages_counts_and_resolve(
    tenant,
):
    visible, _ = create_metric(tenant)
    sibling, _ = create_metric(tenant, project=tenant.sibling)
    foreign, _ = create_metric(tenant, project=tenant.foreign_project)
    deleted, _ = create_metric(tenant)
    DerivedMetric.no_workspace_objects.filter(id=deleted.id).update(deleted=True)
    orphan = DerivedMetric.no_workspace_objects.create(
        name="No active version",
        project=tenant.project,
        workspace=tenant.workspace,
        organization=tenant.organization,
    )
    other_org = Organization.objects.create(name="other-catalog-test")
    malformed, _ = create_metric(tenant)
    DerivedMetric.no_workspace_objects.filter(id=malformed.id).update(
        organization=other_org
    )
    bad_parent, _ = create_metric(tenant)
    DerivedMetric.no_workspace_objects.filter(id=bad_parent.id).update(
        project=tenant.foreign_project
    )
    query = {"property_kind": "metric_field", "context": "sessions"}
    reader = PropertyCatalogReader(catalog_database="unused")
    found = reader.read_page(
        scope=tenant.scope, query=query, page_size=1, include_counts=True
    )
    assert [m["metric_id"] for m in found.metrics] == [str(visible.id)]
    assert (
        found.category_counts[PropertyCategory.METRIC]
        == found.category_counts["all"]
        == 1
    )
    source = CurrentDefinitionSource(ReadDeadline.start(10000))
    for metric in (visible, sibling, foreign, deleted, orphan, malformed, bad_parent):
        resolved = source.resolve(
            scope=tenant.scope,
            property_id=f"metric_field:{metric.id}",
            context="sessions",
        )
        assert (resolved is not None) == (metric is visible)
    workspace_scope = {
        **tenant.scope,
        "workspace_scope": True,
        "project_ids": [
            str(p.id) for p in (tenant.project, tenant.sibling, tenant.foreign_project)
        ],
    }
    assert {
        m["metric_id"]
        for m in page(tenant, scope=workspace_scope, context="sessions").metrics
    } == {str(visible.id), str(sibling.id)}
    assert (
        page(
            tenant, scope={**tenant.scope, "project_ids": []}, context="sessions"
        ).metrics
        == ()
    )
    assert (
        page(
            tenant,
            scope={**tenant.scope, "workspace_id": str(tenant.foreign_workspace.id)},
            context="sessions",
        ).metrics
        == ()
    )
    assert (
        page(
            tenant,
            scope={**tenant.scope, "organization_id": str(other_org.id)},
            context="sessions",
        ).metrics
        == ()
    )
    Project.no_workspace_objects.filter(id=tenant.project.id).update(deleted=True)
    assert page(tenant, context="sessions").metrics == ()
    assert (
        source.resolve(
            scope=tenant.scope,
            property_id=f"metric_field:{visible.id}",
            context="sessions",
        )
        is None
    )


def test_cross_metric_active_pointer_is_hidden(tenant):
    metric, _ = create_metric(tenant)
    other, other_version = create_metric(tenant, project=tenant.sibling)
    DerivedMetric.no_workspace_objects.filter(id=metric.id).update(
        active_version=other_version
    )
    assert page(tenant, context="sessions").metrics == ()
    assert other.project_id != metric.project_id


def test_metric_pages_need_no_clickhouse_and_follow_active_version_only(tenant):
    metric, active = create_metric(tenant)
    with patch(
        "tracer.services.clickhouse.v2.property_catalog.connection.PropertyCatalogReadExecutor",
        side_effect=AssertionError("metric metadata cannot open CH"),
    ):
        before = page(tenant, context="sessions").metrics[0]
        draft = create_version(
            metric,
            version=2,
            level="user",
            user_rule="any",
            value_labels={"match": "New match", "other": "New other"},
            group_dims=["model"],
            definition_hash="f" * 64,
            backfill_status="running",
        )
        DerivedMetric.no_workspace_objects.filter(id=metric.id).update(
            current_version=2
        )
        assert page(tenant, context="sessions").metrics == (before,)
        assert page(tenant, context="users").metrics[0]["projection"] == "per_user"
        assert before["version_id"] == str(active.id)
        assert before["group_dims"] == ["locale"]
        assert before["definition_hash"] == "1" * 64
        assert before["value_labels"] == {"match": "Resolved", "other": "Not resolved"}
        validate_descriptor(tenant, before, "sessions")
        DerivedMetricVersion.objects.filter(id=draft.id).update(
            backfill_status="complete"
        )
        DerivedMetric.activate_version(
            metric.id, draft.id, expected_active_id=active.id
        )
        assert page(tenant, context="sessions").metrics == ()
        after = page(tenant, context="users").metrics[0]
        assert after["property_id"] == before["property_id"]
        assert after["projection"] == "user_metric" and after["version"] == 2
        assert after["group_dims"] == ["model"] and after["definition_hash"] == "f" * 64
        assert after["value_labels"] == {"match": "New match", "other": "New other"}


@pytest.mark.parametrize(
    "kind,unit,fmt",
    [
        ("share", "%", "percent"),
        ("count", "", "number"),
        ("average", "ms", "decimal"),
        ("total", "ms", "decimal"),
    ],
)
def test_users_conversation_projection_has_numeric_metadata_without_boolean_labels(
    tenant, kind, unit, fmt
):
    metric, _ = create_metric(
        tenant,
        kind=kind,
        unit="ms",
        value_format="decimal",
        value_type="boolean" if kind in {"share", "count"} else "number",
    )
    session = page(tenant, context="sessions").metrics[0]
    projected = page(tenant, context="users").metrics[0]
    assert (
        projected["property_id"]
        == session["property_id"]
        == f"metric_field:{metric.id}"
    )
    assert projected["source_level"] == "conversation" and projected["level"] == "user"
    assert projected["projection"] == "per_user" and projected["source"] == "users"
    assert projected["type"] == projected["output_type"] == "number"
    assert (projected["unit"], projected["value_format"]) == (unit, fmt)
    assert projected["value_labels"] == {} and projected["filterable"] is True
    assert set(projected["operators"]) == FILTER_TYPE_ALLOWED_OPS["number"]
    assert session["type"] == ("boolean" if kind in {"share", "count"} else "number")
    source = CurrentDefinitionSource(ReadDeadline.start(10000))
    assert (
        definition_metric(
            source.resolve(
                scope=tenant.scope,
                property_id=projected["property_id"],
                context="users",
            )
        )
        == projected
    )
    validate_descriptor(tenant, session, "sessions")
    validate_descriptor(tenant, projected, "users")


@pytest.mark.parametrize(
    "context,level",
    [
        ("sessions", "conversation"),
        ("spans", "span"),
        ("task_sessions", "conversation"),
        ("task_spans", "span"),
    ],
)
@pytest.mark.parametrize("value_type", ["boolean", "number", "text"])
def test_every_offered_native_operator_round_trips_through_scoped_b06(
    tenant, context, level, value_type
):
    metric, _ = create_metric(tenant, level=level, value_type=value_type)
    item = page(tenant, context=context).metrics[0]
    assert item["column_id"] == item["property_id"] == f"metric_field:{metric.id}"
    assert set(item["operators"]) == FILTER_TYPE_ALLOWED_OPS[value_type]
    for op in item["operators"]:
        validate_descriptor(tenant, item, context, op=op)


@pytest.mark.parametrize(
    "kind,rule,value_type",
    [
        ("share", "any", "boolean"),
        ("share", "every", "boolean"),
        ("count", "any", "boolean"),
        ("count", "every", "boolean"),
        ("average", "average", "number"),
        ("average", "total", "number"),
    ],
)
@pytest.mark.parametrize("filterable", [False, True])
def test_native_user_fields_are_always_columns_but_filterability_tracks_capability(
    tenant, monkeypatch, kind, rule, value_type, filterable
):
    monkeypatch.setattr(
        list_filter_capabilities, "USER_LEVEL_METRIC_FILTER_ON_USERS", filterable
    )
    # A native Share/Count's catalog type follows its rule, not a stale recipe hint.
    metric, _ = create_metric(
        tenant,
        kind=kind,
        level="user",
        user_rule=rule,
        value_type=value_type,
        unit="ms",
        value_format="decimal",
    )
    item = page(tenant, context="users").metrics[0]
    assert item["property_id"] == f"metric_field:{metric.id}"
    assert item["projection"] == "user_metric" and item["source_level"] == "user"
    assert item["type"] == value_type and item["filterable"] is filterable
    assert set(item["operators"]) == FILTER_TYPE_ALLOWED_OPS[value_type]
    if value_type == "boolean":
        assert item["value_labels"] == {"match": "Resolved", "other": "Not resolved"}
    else:
        assert (
            item["value_labels"] == {}
            and item["value_format"] == "decimal"
            and item["unit"] == "ms"
        )
    if filterable:
        for op in item["operators"]:
            validate_descriptor(tenant, item, "users", op=op)
    else:
        with pytest.raises(serializers.ValidationError, match="not available"):
            validate_descriptor(tenant, item, "users")


@pytest.mark.parametrize(
    "context",
    [
        "",
        "sessions",
        "traces",
        "spans",
        "task",
        "tasks",
        "task_sessions",
        "task_traces",
        "task_spans",
    ],
)
def test_user_metrics_never_escape_into_wrong_grain_or_task_pickers(tenant, context):
    metric, _ = create_metric(tenant, level="user", user_rule="any")
    assert page(tenant, context=context).metrics == ()
    source = CurrentDefinitionSource(ReadDeadline.start(10000))
    assert (
        source.resolve(
            scope=tenant.scope, property_id=f"metric_field:{metric.id}", context=context
        )
        is None
    )


@pytest.mark.parametrize(
    "context", ["traces", "task_traces", "users", "sessions", "spans", ""]
)
def test_trace_fields_stay_off_in_all_catalog_contexts(tenant, context, settings):
    # Model a previously activated definition after its capability is disabled.
    # Normal activation with the flag OFF must remain forbidden.
    settings.DERIVED_METRICS_TRACE_ENABLED = True
    metric, _ = create_metric(tenant, level="trace")
    settings.DERIVED_METRICS_TRACE_ENABLED = False
    assert page(tenant, context=context).metrics == ()
    source = CurrentDefinitionSource(ReadDeadline.start(10000))
    assert (
        source.resolve(
            scope=tenant.scope, property_id=f"metric_field:{metric.id}", context=context
        )
        is None
    )


def test_disabled_trace_activation_rejected_without_hiding_conversation_fields(
    tenant, settings
):
    from tracer.services.derived_metrics.errors import MetricInputError

    settings.DERIVED_METRICS_TRACE_ENABLED = False
    metric, active = create_metric(tenant)
    trace = create_version(metric, version=2, level="trace")
    with pytest.raises(MetricInputError, match="Trace metrics are not enabled"):
        DerivedMetric.activate_version(
            metric.id, trace.id, expected_active_id=active.id
        )
    metric.refresh_from_db()
    assert metric.active_version_id == active.id
    assert page(tenant, context="sessions").metrics[0]["metric_id"] == str(metric.id)


def test_default_labels_and_numeric_metadata_do_not_reuse_boolean_labels(tenant):
    metric, active = create_metric(tenant, value_labels={})
    item = page(tenant, context="sessions").metrics[0]
    assert item["value_labels"] == {"match": "Counts", "other": "Doesn't count"}
    validate_descriptor(tenant, item, "sessions")
    draft = create_version(
        metric,
        version=2,
        value_type="number",
        kind="average",
        unit="tokens",
        value_format="integer",
        group_dims=[],
    )
    DerivedMetric.activate_version(metric.id, draft.id, expected_active_id=active.id)
    item = page(tenant, context="sessions").metrics[0]
    assert item["type"] == "number" and item["value_labels"] == {}
    assert item["unit"] == "tokens" and item["value_format"] == "integer"


@pytest.mark.parametrize("kind", ["share", "count"])
@pytest.mark.parametrize(
    "level,context", [("conversation", "sessions"), ("user", "users")]
)
@pytest.mark.parametrize("separator", ["\n", "\t"])
@pytest.mark.parametrize("label_key", ["match", "other"])
def test_accepted_display_labels_survive_activation_catalog_and_b06(
    tenant, monkeypatch, kind, level, context, separator, label_key
):
    monkeypatch.setattr(
        list_filter_capabilities, "USER_LEVEL_METRIC_FILTER_ON_USERS", True
    )
    sibling, _ = create_metric(tenant)
    before = page(tenant, context=context).metrics[0]
    labels = {"match": "Resolved", "other": "Not resolved"}
    labels[label_key] += separator + "fully"
    serializer = DerivedMetricWriteSerializer(
        data={
            "project_id": str(tenant.project.id),
            "name": "Metric with display-label whitespace",
            "kind": kind,
            "level": level,
            "user_rule": "any" if level == "user" else "",
            "inputs": [
                {
                    "alias": "resolved",
                    "source": "span",
                    "field": "attribute_bool",
                    "attribute_key": "resolved",
                    "value_type": "boolean",
                    "reducer": "latest",
                }
            ],
            "value_labels": labels,
        }
    )
    serializer.is_valid(raise_exception=True)
    recipe = normalize_spec(serializer.validated_data)
    assert serializer.validated_data["value_labels"] == recipe["value_labels"] == labels
    metric, version = create_metric(tenant, **recipe)
    metric.refresh_from_db()
    assert metric.active_version_id == version.id
    result = page(tenant, context=context, include_counts=True)
    by_id = {item["metric_id"]: item for item in result.metrics}
    assert set(by_id) == {str(metric.id), str(sibling.id)}
    assert by_id[str(sibling.id)] == before
    assert result.category_counts["metric"] == result.category_counts["all"] == 2
    item = by_id[str(metric.id)]
    assert item["value_labels"] == version.value_labels == labels
    assert json.loads(canonical_json(item))["value_labels"] == labels
    for op in ("equals", "not_equals"):
        for label in labels.values():
            validate_descriptor(tenant, item, context, op=op, value=label)


@pytest.mark.parametrize(
    "label,error,match",
    [
        ("", CatalogCodecError, "must not be empty"),
        ("x" * (MAX_IDENTITY_COMPONENT_BYTES + 1), CatalogCodecError, "exceeds"),
        ("\ud800", CatalogCodecError, "invalid Unicode surrogate"),
        (None, TypeError, "must be a string"),
        (123, TypeError, "must be a string"),
    ],
)
def test_display_label_controls_do_not_relax_encoding_type_or_size_guards(
    tenant, label, error, match
):
    metric, _ = create_metric(tenant)
    definition = CurrentDefinitionSource(ReadDeadline.start(10000)).resolve(
        scope=tenant.scope,
        property_id=f"metric_field:{metric.id}",
        context="sessions",
    )
    with pytest.raises(error, match=match):
        replace(
            definition,
            details={
                **definition.details,
                "value_labels": {"match": label, "other": "Not resolved"},
            },
        )


def test_metric_level_role_search_and_counts_agree_before_paging(tenant):
    conversation, _ = create_metric(tenant, name="Match conversation")
    span, _ = create_metric(tenant, name="Match span", level="span")
    create_metric(tenant, name="No search hit")
    for level, expected in (
        ("conversation", {str(conversation.id)}),
        ("span", {str(span.id)}),
        ("trace", set()),
        ("user", set()),
    ):
        result = page(tenant, level=level, search="MATCH", include_counts=True)
        assert {item["metric_id"] for item in result.metrics} == expected
        assert (
            result.category_counts["metric"]
            == result.category_counts["all"]
            == len(expected)
        )
    assert page(tenant, level="conversation", role="dimension").metrics == ()
    assert [
        item["metric_id"] for item in page(tenant, search=str(span.id)).metrics
    ] == [str(span.id)]


def test_metric_keysets_are_uuid_based_and_survive_display_rename(tenant):
    ids = [UUID(int=i) for i in (2, 1, 3)]
    metrics = [
        create_metric(tenant, metric_id=pk, name=f"Match {i}")[0]
        for i, pk in enumerate(ids)
    ]
    reader = PropertyCatalogReader(catalog_database="unused")
    query = {"property_kind": "metric_field", "context": "sessions", "search": "match"}
    first = reader.read_page(scope=tenant.scope, query=query, page_size=1)
    assert first.has_more and first.metrics[0]["metric_id"] == str(UUID(int=1))
    DerivedMetric.no_workspace_objects.filter(id=metrics[0].id).update(
        name="Match AAA renamed"
    )
    second = reader.read_page(
        scope=tenant.scope, query=query, page_size=1, cursor_token=first.next_cursor
    )
    third = reader.read_page(
        scope=tenant.scope, query=query, page_size=1, cursor_token=second.next_cursor
    )
    assert [p.metrics[0]["metric_id"] for p in (first, second, third)] == [
        str(UUID(int=i)) for i in (1, 2, 3)
    ]
    assert (
        second.metrics[0]["display_name"] == "Match AAA renamed" and not third.has_more
    )


@pytest.mark.parametrize("field,value", [("context", "users"), ("level", "span")])
def test_metric_cursor_binds_effective_grain_filters(tenant, field, value):
    create_metric(tenant)
    create_metric(tenant)
    reader = PropertyCatalogReader(catalog_database="unused")
    query = {
        "property_kind": "metric_field",
        "context": "sessions",
        "level": "conversation",
    }
    first = reader.read_page(scope=tenant.scope, query=query, page_size=1)
    with pytest.raises(PropertyCatalogCursorError) as exc:
        reader.read_page(
            scope=tenant.scope,
            query={**query, field: value},
            page_size=1,
            cursor_token=first.next_cursor,
        )
    assert exc.value.code == "cursor_mismatch"


@pytest.mark.parametrize("query", [{"context": "wrong"}, {"level": "wrong"}])
def test_invalid_metric_context_or_level_is_rejected_before_io(query):
    with pytest.raises(ValueError, match="invalid metric field"):
        PropertyCatalogReader._validate_query(query)


def test_metric_metadata_is_canonical_json_and_read_budget_remains_lazy(tenant):
    create_metric(tenant)
    executor = Mock()
    item = page(
        tenant,
        context="sessions",
        reader=PropertyCatalogReader(executor, catalog_database="unused"),
    ).metrics[0]
    canonical_json(item)
    executor.execute.assert_not_called()
