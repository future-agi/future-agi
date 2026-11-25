"""Offline public Users eligibility checks, not SQL or production proof."""

import json
import socket
from copy import deepcopy

import pytest

from tracer.serializers.project import (
    ProjectUserGraphDataRequestSerializer,
    ProjectUserMetricsRequestSerializer,
    ProjectUsersAggregateGraphDataRequestSerializer,
)
from tracer.serializers.trace import UsersQuerySerializer

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"


@pytest.mark.parametrize(
    "family",
    [None, "NORMAL", "SYSTEM_METRIC", "SPAN_ATTRIBUTE", "EVAL_METRIC", "ANNOTATION"],
)
@pytest.mark.parametrize("registry", [False, True])
def test_native_user_resolution_does_not_steal_custom_attribute_identity(
    family, registry
):
    from tracer.services.user_filter_capabilities import is_native_user_id_filter

    item = {
        "column_id": "user_id",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "0012",
        },
    }
    if family is not None:
        item["filter_config"]["col_type"] = family
    if registry:
        item["property_id"] = "custom_attribute:user_id"
    assert is_native_user_id_filter(item) is (
        not registry and family in (None, "NORMAL", "SYSTEM_METRIC")
    )


def test_registry_native_user_resolution_keeps_legacy_label_semantics():
    from tracer.services.user_filter_capabilities import is_native_user_id_filter

    assert is_native_user_id_filter(
        {
            "column_id": "user_id",
            "property_id": "system_attribute:traces:user_id",
            "filter_config": {},
        }
    )
    assert not is_native_user_id_filter(
        {"column_id": "company_id", "filter_config": {}}
    )


UNSUPPORTED = (
    "dataset",
    "eval_source",
    "active_users",
    "avg_cost_per_user",
    "avg_traces_per_user",
)
POPULATION_METRICS = UNSUPPORTED[2:]
SURFACES = (
    "workspace_list",
    "project_list",
    "aggregate_graph",
    "detail_graph",
    "detail_metrics",
)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    """No integration DDL in this source-only suite."""
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    """No integration DDL in this source-only suite."""
    yield


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Users capability test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _leaf(name, *, attribute=False, registry=True):
    text = attribute or name in {"dataset", "eval_source"}
    leaf = {
        "column_id": name,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE" if attribute else "SYSTEM_METRIC",
            "filter_type": "text" if text else "number",
            "filter_op": "in" if attribute else "equals",
            "filter_value": ["10000001", "fixture-only"]
            if attribute
            else ("fixture-only" if text else 0.01),
        },
    }
    if attribute:
        leaf["filter_config"]["attribute_value_types"] = ["string", "string"]
    if registry:
        namespace = "all" if name in {"dataset", "eval_source"} else "users"
        leaf["property_id"] = (
            f"custom_attribute:{name}"
            if attribute
            else f"system_attribute:{namespace}:{name}"
        )
        leaf["source"] = "traces" if attribute else "sessions"
    return leaf


def _serializer(surface, filters, **extra):
    data = {"filters": deepcopy(filters), **extra}
    if surface in {"workspace_list", "project_list"}:
        data["filters"] = json.dumps(data["filters"])
        if surface == "project_list":
            data["project_id"] = PROJECT
        return UsersQuerySerializer(data=data)
    if surface == "aggregate_graph":
        return ProjectUsersAggregateGraphDataRequestSerializer(
            data={"project_id": PROJECT, **data}
        )
    if surface == "detail_metrics":
        return ProjectUserMetricsRequestSerializer(
            data={"project_id": PROJECT, "end_user_id": USER, **data}
        )
    return ProjectUserGraphDataRequestSerializer(data=data)


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("name", UNSUPPORTED)
@pytest.mark.parametrize(
    "wire", ("registry", "legacy", "registry_without_col_type", "legacy_lowercase")
)
def test_users_boundaries_reject_unsupported_system_predicates(surface, name, wire):
    leaf = _leaf(name, registry=wire.startswith("registry"))
    if wire == "registry_without_col_type":
        leaf["filter_config"].pop("col_type")
    elif wire == "legacy_lowercase":
        leaf["filter_config"]["col_type"] = "system_metric"
    serializer = _serializer(surface, [leaf])
    assert not serializer.is_valid()
    assert serializer.errors["filters"][0].code == "unsupported_users_filter"
    assert name in str(serializer.errors["filters"])


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("name", UNSUPPORTED)
@pytest.mark.parametrize("registry", (False, True))
def test_same_named_span_attributes_keep_identity_and_string_values(
    surface, name, registry
):
    leaf = _leaf(name, attribute=True, registry=registry)
    serializer = _serializer(surface, [leaf])
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"] == [leaf]


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("name", ("total_cost", "num_traces", "total_tokens"))
def test_supported_per_user_system_filters_remain_available(surface, name):
    leaf = _leaf(name)
    serializer = _serializer(surface, [leaf])
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"] == [leaf]


@pytest.mark.parametrize("name", POPULATION_METRICS)
@pytest.mark.parametrize("registry", (False, True))
def test_population_metric_selection_is_not_a_filter(name, registry):
    metric = {"id": name, "type": "SYSTEM_METRIC"}
    if registry:
        metric.update(property_id=f"system_attribute:users:{name}", source="sessions")
    # Same-named raw attribute is independent from the selected graph output.
    leaf = _leaf(name, attribute=True)
    serializer = _serializer("aggregate_graph", [leaf], req_data_config=metric)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["req_data_config"] == metric
    assert serializer.validated_data["filters"] == [leaf]


def test_rejection_does_not_rewrite_or_drop_mixed_filter_leaves():
    from rest_framework.exceptions import ValidationError

    from tracer.services.user_filter_capabilities import (
        validate_users_filter_capabilities,
    )

    leaves = [_leaf("company_id", attribute=True), *map(_leaf, UNSUPPORTED)]
    original = deepcopy(leaves)
    with pytest.raises(ValidationError) as rejected:
        validate_users_filter_capabilities(leaves)
    assert all(name in str(rejected.value.detail) for name in UNSUPPORTED)
    assert leaves == original


@pytest.mark.parametrize("surface", SURFACES)
def test_empty_filters_remain_valid(surface):
    serializer = _serializer(surface, [])
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"] == []
