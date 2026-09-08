"""Current definition/index contracts. DB cases run only on the isolated test DB."""

import inspect
import random
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.core import signing

from tracer.services.clickhouse.v2.attribute_catalog_codec import encode_catalog_scalar
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PROPERTY_CATALOG_CURSOR_SALT,
    PropertyCatalogCursorError,
    _digest,
    decode_property_catalog_cursor,
    encode_property_catalog_cursor,
    normalize_property_catalog_query,
    normalize_property_catalog_scope,
)
from tracer.services.clickhouse.v2.property_catalog.reader import PropertyCatalogReader
from tracer.services.clickhouse.v2.property_catalog.value_cursor import (
    PROPERTY_CATALOG_VALUE_CURSOR_SALT,
    PropertyCatalogValueCursorError,
    decode_property_catalog_value_cursor,
    encode_property_catalog_value_cursor,
    normalize_property_catalog_value_query,
)
from tracer.services.clickhouse.v2.property_catalog.value_reader import (
    PropertyCatalogValueReader,
    PropertyCatalogValueUnavailable,
)

ORG, WS, PROJECT = (str(uuid4()) for _ in range(3))
SCOPE = {
    "organization_id": ORG,
    "workspace_id": WS,
    "project_ids": [PROJECT],
    "principal_id": "user-1",
    "auth_id": "token-1",
    "auth_type": "api_key",
}
ORDER = (3, 0, "traces", "key", "key", "custom_attribute:key")
QUERY = {"category": "custom_attribute", "source": "traces"}
VALUE_QUERY = {"property_id": "custom_attribute:key", "source": "traces"}
EXACT_ATTRIBUTE_KEYS = ("\tline\n\x00tail \t", "x" * 4096, "ΐ" * 2048)
_random = random.Random(0)
BOUNDARY_STRING_VALUES = (
    "\x00" * 16384,
    "ΐ" * 8192,
    "".join(_random.choices([chr(i) for i in range(32, 127)], k=16384)),
)


class Executor:
    def __init__(self, *results):
        self.results = iter(results)
        self.calls = []

    def execute(self, sql, params, **kwargs):
        self.calls.append((sql, params.copy(), kwargs))
        return SimpleNamespace(data=next(self.results))


def value_row(value, attribute_type=None):
    encoded = encode_catalog_scalar(value)
    return {
        "attribute_type": attribute_type or encoded.kind,
        "value_fingerprint": encoded.fingerprint,
        "value_json": encoded.value_json,
        "value_search_text_folded": encoded.search_text.casefold(),
        "first_seen": datetime(2026, 1, 1, tzinfo=UTC),
        "last_seen": datetime(2026, 9, 1, tzinfo=UTC),
    }


@pytest.mark.parametrize("value_cursor", [False, True])
@pytest.mark.parametrize(
    "field,changed",
    [
        ("organization_id", str(uuid4())),
        ("workspace_id", str(uuid4())),
        ("project_ids", [str(uuid4())]),
        ("principal_id", "other-user"),
        ("auth_id", "other-token"),
        ("auth_type", "session"),
        ("agent_definition_id", str(uuid4())),
        ("dataset_id", str(uuid4())),
        ("workspace_scope", True),
    ],
)
def test_current_cursor_binds_every_permission_scope(value_cursor, field, changed):
    encode = (
        encode_property_catalog_value_cursor
        if value_cursor
        else encode_property_catalog_cursor
    )
    decode = (
        decode_property_catalog_value_cursor
        if value_cursor
        else decode_property_catalog_cursor
    )
    query = VALUE_QUERY if value_cursor else QUERY
    order = (1, "a" * 64, '"a"') if value_cursor else ORDER
    token = encode(scope=SCOPE, query=query, page_size=1, order=order)
    with pytest.raises(PropertyCatalogCursorError) as error:
        decode(token, scope={**SCOPE, field: changed}, query=query, page_size=1)
    assert error.value.code == "cursor_mismatch"


@pytest.mark.parametrize("value_cursor", [False, True])
def test_verified_v1_restarts_but_tampering_and_scope_changes_fail_closed(value_cursor):
    salt = (
        PROPERTY_CATALOG_VALUE_CURSOR_SALT
        if value_cursor
        else PROPERTY_CATALOG_CURSOR_SALT
    )
    normalize = (
        normalize_property_catalog_value_query
        if value_cursor
        else normalize_property_catalog_query
    )
    decode = (
        decode_property_catalog_value_cursor
        if value_cursor
        else decode_property_catalog_cursor
    )
    query = VALUE_QUERY if value_cursor else QUERY
    payload = {
        "v": 1,
        "scope": _digest(normalize_property_catalog_scope(SCOPE)),
        "query": _digest(normalize(query)),
        "page_size": 1,
        "catalog_epoch": 2,
        "catalog_revision": 5,
        "activation_fingerprint": "f" * 64,
    }
    token = signing.dumps(payload, salt=salt.replace(".v2", ".v1"))
    for candidate, scope, code in [
        (token, SCOPE, "cursor_expired"),
        (token + "bad", SCOPE, "invalid_cursor"),
        (token, {**SCOPE, "project_ids": []}, "cursor_mismatch"),
    ]:
        with pytest.raises(PropertyCatalogCursorError) as error:
            decode(candidate, scope=scope, query=query, page_size=1)
        assert error.value.code == code


@pytest.mark.parametrize(
    "key", EXACT_ATTRIBUTE_KEYS, ids=["controls", "4096-bytes", "casefold-expansion"]
)
def test_exact_custom_key_identity_reader_and_cursor_boundaries(key):
    from tracer.serializers.dashboard import (
        DashboardFilterValuesQuerySerializer,
        DashboardMetricSerializer,
    )

    def row(name):
        return {
            "attribute_key": name,
            "key_folded": name.casefold(),
            "attribute_types": ["string"],
        }

    executor = Executor([row(key), row("\uffff")], [row("\uffff")])
    native = Mock()
    native.read_page.return_value = ()
    reader = PropertyCatalogReader(
        executor, catalog_database="test_index", definition_source=native
    )
    first = reader.read_page(scope=SCOPE, query=QUERY, page_size=1)
    assert first.has_more and first.metrics[0]["name"] == key
    property_id = "custom_attribute:" + key
    assert first.metrics[0]["property_id"] == property_id
    order = decode_property_catalog_cursor(
        first.next_cursor, scope=SCOPE, query=QUERY, page_size=1
    ).order
    assert order[3:] == (key.casefold(), key, property_id)
    reader.read_page(
        scope=SCOPE, query=QUERY, page_size=1, cursor_token=first.next_cursor
    )
    assert executor.calls[1][1]["after_key"] == key
    assert executor.calls[1][1]["after_folded"] == key.casefold()
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": property_id,
            "metric_name": key,
            "metric_type": "custom_attribute",
            "page_size": 1,
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["property_id"] == property_id
    assert serializer.validated_data["metric_name"] == key
    metric = DashboardMetricSerializer(
        data={
            "name": key,
            "attribute_key": key,
            "property_id": property_id,
            "type": "custom_attribute",
            "aggregation": "count",
        }
    )
    assert metric.is_valid(), metric.errors
    assert (
        metric.validated_data["name"] == metric.validated_data["attribute_key"] == key
    )
    values_executor = Executor([{"attribute_type": "string"}], [value_row("ok")])
    PropertyCatalogValueReader(
        values_executor, catalog_database="test_index"
    ).read_page(
        scope=SCOPE,
        query={"property_id": property_id, "source": "traces"},
        page_size=1,
    )
    assert values_executor.calls[0][1]["attribute_key"] == key


def test_custom_key_cursor_bound_is_derived_but_native_ids_stay_strict():
    assert len(EXACT_ATTRIBUTE_KEYS[2].encode()) == 4096
    assert len(EXACT_ATTRIBUTE_KEYS[2].casefold().encode()) > 4096
    with pytest.raises(PropertyCatalogCursorError):
        encode_property_catalog_cursor(
            scope=SCOPE,
            query=QUERY,
            page_size=1,
            order=(
                3,
                0,
                "traces",
                "x" * 4097,
                "x" * 4097,
                "custom_attribute:" + "x" * 4097,
            ),
        )
    with pytest.raises(PropertyCatalogCursorError):
        encode_property_catalog_cursor(
            scope=SCOPE,
            query=QUERY,
            page_size=1,
            order=(
                0,
                0,
                "traces",
                "bad\nkey",
                "bad\nkey",
                "system_attribute:traces:bad\nkey",
            ),
        )


def test_observed_keys_use_string_scope_grouped_history_not_current_types():
    executor = Executor(
        [
            {
                "attribute_key": "key",
                "key_folded": "key",
                "attribute_types": ["number", "string"],
            }
        ]
    )
    native = Mock()
    native.read_page.return_value = ()
    page = PropertyCatalogReader(
        executor, catalog_database="test_index", definition_source=native
    ).read_page(scope=SCOPE, query=QUERY, page_size=1)
    metric = page.metrics[0]
    assert metric["property_id"] == "custom_attribute:key"
    assert metric["type"] == "json"
    assert metric["role"] == "dimension"
    assert metric["attribute_types_exact"] is False
    assert set(metric["allowed_aggregations"]) == {"count", "count_distinct"}
    sql, params, _ = executor.calls[0]
    assert "observed_attribute_keys" in sql and "FINAL" not in sql
    assert "GROUP BY k.attribute_key" in sql and "min(k.key_folded)" in sql
    assert params["organization_id"] == ORG and params["workspace_id"] == WS
    assert params["project_ids"] == (PROJECT,)
    assert "toUUID" not in sql and "catalog_epoch" not in sql


@pytest.mark.parametrize(
    "value,kind",
    [
        (True, "boolean"),
        (True, "array"),
        ("TRUE", "array"),
        (3.25, "array"),
        ("x", "string"),
    ],
)
def test_observed_values_derive_scalar_type_and_group_exact_json(value, kind):
    executor = Executor([{"attribute_type": kind}], [value_row(value, kind)])
    page = PropertyCatalogValueReader(
        executor, catalog_database="test_index"
    ).read_page(scope=SCOPE, query=VALUE_QUERY, page_size=1)
    assert page.values[0].value == value
    assert page.values[0].attribute_type == kind
    assert page.values[0].scalar_kind == encode_catalog_scalar(value).kind
    sql, params, _ = executor.calls[1]
    assert "GROUP BY k.attribute_type, k.value_fingerprint, k.value_json" in sql
    assert "ORDER BY attribute_type_rank, value_fingerprint, value_json" in sql
    assert "scalar_type" not in sql and "FINAL" not in sql
    assert "min(k.first_seen)" in sql and "max(k.last_seen)" in sql
    assert params["source_kind"] == "custom_attribute"


@pytest.mark.parametrize(
    "key,value",
    [
        ("value_json", "[1]"),
        ("value_json", "1.0"),
        ("value_fingerprint", "f" * 64),
        ("value_search_text_folded", "wrong"),
        ("attribute_type", "bool"),
    ],
)
def test_invalid_observed_payload_fails_closed(key, value):
    row = {**value_row(1), key: value}
    executor = Executor([{"attribute_type": "number"}], [row])
    with pytest.raises(PropertyCatalogValueUnavailable):
        PropertyCatalogValueReader(executor, catalog_database="test_index").read_page(
            scope=SCOPE, query=VALUE_QUERY, page_size=1
        )


def test_current_keyset_does_not_pin_a_build_or_time_window():
    encode = encode_property_catalog_value_cursor
    decode = decode_property_catalog_value_cursor
    token = encode(
        scope=SCOPE, query=VALUE_QUERY, page_size=1, order=(1, "f" * 64, '"A"')
    )
    assert decode(token, scope=SCOPE, query=VALUE_QUERY, page_size=1).order[-1] == '"A"'
    payload = signing.loads(token, salt=PROPERTY_CATALOG_VALUE_CURSOR_SALT)
    assert set(payload) == {"v", "scope", "query", "page_size", "order"}
    with pytest.raises(PropertyCatalogValueCursorError):
        decode(
            token,
            scope=SCOPE,
            query={**VALUE_QUERY, "search": "different"},
            page_size=1,
        )


@pytest.mark.parametrize(
    "value", BOUNDARY_STRING_VALUES, ids=["escaped", "folded", "entropy"]
)
def test_eligible_string_expansion_and_value_cursor_roundtrip(value):
    row = value_row(value)
    decoded = PropertyCatalogValueReader._decode_value(row)
    assert decoded.value == value and len(value.encode()) == 16384
    order = (1, row["value_fingerprint"], row["value_json"])
    token = encode_property_catalog_value_cursor(
        scope=SCOPE, query=VALUE_QUERY, page_size=1, order=order
    )
    assert (
        decode_property_catalog_value_cursor(
            token, scope=SCOPE, query=VALUE_QUERY, page_size=1
        ).order
        == order
    )


@pytest.mark.parametrize("kind,limit", [("string", 16384), ("array", 4096)])
def test_string_eligibility_uses_raw_utf8_bytes(kind, limit):
    assert (
        PropertyCatalogValueReader._decode_value(value_row("\x00" * limit, kind)).value
        == "\x00" * limit
    )
    with pytest.raises(PropertyCatalogValueUnavailable):
        PropertyCatalogValueReader._decode_value(value_row("x" * (limit + 1), kind))


def test_empty_workspace_does_not_open_clickhouse():
    executor = Mock(side_effect=AssertionError("CH must stay lazy"))
    page = PropertyCatalogValueReader(
        executor, catalog_database="test_index"
    ).read_page(scope={**SCOPE, "project_ids": []}, query=VALUE_QUERY, page_size=1)
    assert page.values == () and page.attribute_types == ()
    executor.execute.assert_not_called()


def test_system_definitions_have_unique_ids_and_current_native_adapters():
    from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
        canonical_system_definitions,
    )

    definitions = canonical_system_definitions()
    identities = {definition.property_id for definition in definitions}
    assert len(identities) == len(definitions)
    assert {
        "system_attribute:traces:model",
        "system_attribute:datasets:row_count",
        "system_attribute:simulation:call_count",
        "system_attribute:prompts:avg_latency",
    } <= identities
    with patch(
        "tracer.services.clickhouse.v2.property_catalog.connection.PropertyCatalogReadExecutor",
        side_effect=AssertionError("static native definitions cannot open CH"),
    ):
        page = PropertyCatalogReader(catalog_database="unused").read_page(
            scope=SCOPE,
            query={"category": "system_metric", "source": "datasets"},
            page_size=50,
        )
    assert page.metrics and all(
        m["source"] in {"datasets", "all", "both"} for m in page.metrics
    )


@pytest.fixture
def current_tenant(transactional_db):
    from accounts.models import Organization, User
    from accounts.models.workspace import Workspace
    from tracer.models.project import Project

    organization = Organization.objects.create(name="current-catalog-test")
    user = User(
        id=uuid4(),
        email=f"{uuid4()}@example.invalid",
        name="catalog",
        organization=organization,
    )
    User.objects.bulk_create([user])
    workspace = Workspace(name="first", organization=organization, created_by=user)
    other_workspace = Workspace(
        name="second", organization=organization, created_by=user
    )
    Workspace.no_workspace_objects.bulk_create([workspace, other_workspace])
    project = Project(
        name="live",
        trace_type="observe",
        model_type="generative_llm",
        organization=organization,
        workspace=workspace,
    )
    Project.no_workspace_objects.bulk_create([project])
    scope = {
        **SCOPE,
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
        "project_ids": [str(project.id)],
        "workspace_scope": True,
    }
    return SimpleNamespace(
        organization=organization,
        user=user,
        workspace=workspace,
        other_workspace=other_workspace,
        project=project,
        scope=scope,
    )


def definitions(tenant, **query):
    return (
        PropertyCatalogReader(catalog_database="unused_for_native")
        .read_page(scope=tenant.scope, query=query, page_size=50)
        .metrics
    )


def test_live_native_options_updates_and_soft_deletes_without_clickhouse(
    current_tenant,
):
    from model_hub.models.develop_annotations import AnnotationsLabels
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig

    t = current_tenant
    template = EvalTemplate(
        name="native",
        organization=t.organization,
        workspace=t.workspace,
        config={"output": "choices"},
        choices=["Old"],
    )
    EvalTemplate.no_workspace_objects.bulk_create([template])
    config = CustomEvalConfig(
        name="configured", project=t.project, eval_template=template
    )
    CustomEvalConfig.no_workspace_objects.bulk_create([config])
    label = AnnotationsLabels(
        name="label",
        type="categorical",
        organization=t.organization,
        workspace=t.workspace,
        project=t.project,
        settings={"options": [{"value": "a", "label": "Alpha"}]},
    )
    AnnotationsLabels.no_workspace_objects.bulk_create([label])
    with patch(
        "tracer.services.clickhouse.v2.property_catalog.connection.PropertyCatalogReadExecutor",
        side_effect=AssertionError("native definitions must not open CH"),
    ):
        assert definitions(t, category="eval_metric")[0]["choices"] == ("Old",)
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
            choices=["New"], name="renamed"
        )
        assert definitions(t, category="eval_metric")[0]["choices"] == ("New",)
        assert definitions(t, category="eval_metric", per_eval_config=True)[0][
            "name"
        ] == str(config.id)
        assert (
            definitions(t, category="annotation_metric")[0]["choice_options"][0][
                "value"
            ]
            == "a"
        )
        AnnotationsLabels.no_workspace_objects.filter(pk=label.pk).update(
            settings={"options": ["Updated"]}
        )
        assert definitions(t, category="annotation_metric")[0]["choices"] == (
            "Updated",
        )
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(deleted=True)
        assert definitions(t, category="eval_metric") == ()
        assert definitions(t, category="eval_metric", per_eval_config=True) == ()


def test_all_dataset_types_current_roles_parent_deletion_and_workspace_isolation(
    current_tenant,
):
    from model_hub.models.choices import DataTypeChoices
    from model_hub.models.develop_dataset import Column, Dataset

    t = current_tenant
    dataset = Dataset(
        name="all-types", organization=t.organization, workspace=t.workspace
    )
    foreign = Dataset(
        name="foreign", organization=t.organization, workspace=t.other_workspace
    )
    Dataset.no_workspace_objects.bulk_create([dataset, foreign])
    columns = [
        Column(name=kind.value, data_type=kind.value, source="user", dataset=dataset)
        for kind in DataTypeChoices
    ]
    Column.no_workspace_objects.bulk_create(
        columns
        + [Column(name="hidden", data_type="text", source="user", dataset=foreign)]
    )
    metrics = definitions(t, category="custom_column")
    assert {m["name"] for m in metrics} == {str(c.id) for c in columns}
    assert {
        m["data_type"] for m in definitions(t, category="custom_column", role="metric")
    } == {"integer", "float", "boolean"}
    Column.no_workspace_objects.filter(pk=columns[0].pk).update(name="edited")
    assert "edited" in {
        m["display_name"] for m in definitions(t, category="custom_column")
    }
    Dataset.no_workspace_objects.filter(pk=dataset.pk).update(deleted=True)
    assert definitions(t, category="custom_column") == ()


def test_current_api_values_and_deleted_project_scope(current_tenant):
    from model_hub.models.evals_metric import EvalTemplate
    from tracer.models.custom_eval_config import CustomEvalConfig
    from tracer.models.project import Project
    from tracer.views.dashboard import DashboardViewSet

    t = current_tenant
    template = EvalTemplate(
        name="native_api",
        organization=t.organization,
        workspace=t.workspace,
        config={"output": "choices"},
        choices=["first"],
    )
    EvalTemplate.no_workspace_objects.bulk_create([template])
    config = CustomEvalConfig(
        name="live_api", project=t.project, eval_template=template
    )
    CustomEvalConfig.no_workspace_objects.bulk_create([config])
    request = SimpleNamespace(
        workspace=t.workspace,
        organization=t.organization,
        user=t.user,
        auth=None,
        query_params={},
        validated_query_data={
            "property_id": f"eval_config:{config.id}",
            "_property_kind": "eval_config",
            "metric_name": str(config.id),
            "metric_type": "eval_metric",
            "source": "traces",
            "project_ids": [str(t.project.id)],
            "page_size": 10,
        },
    )
    endpoint = inspect.unwrap(DashboardViewSet.filter_values)
    with patch(
        "tracer.services.clickhouse.v2.property_catalog.connection.PropertyCatalogReadExecutor",
        side_effect=AssertionError("configured values must not open CH"),
    ):
        response = endpoint(DashboardViewSet(), request)
        assert response.status_code == 200, response.data
        assert response.data["result"]["values"][0]["value"] == "first"
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
            choices=["second"]
        )
        assert (
            endpoint(DashboardViewSet(), request).data["result"]["values"][0]["value"]
            == "second"
        )
        # A malformed "global" template cannot escape an explicit foreign
        # workspace binding through a locally authorized config.
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
            organization=None, workspace=t.other_workspace
        )
        assert endpoint(DashboardViewSet(), request).data["result"]["values"] == []
        assert definitions(t, category="eval_metric", per_eval_config=True) == ()
        EvalTemplate.no_workspace_objects.filter(pk=template.pk).update(
            organization=t.organization, workspace=t.workspace
        )
        request.workspace = t.other_workspace
        assert endpoint(DashboardViewSet(), request).status_code == 400
        request.workspace = t.workspace
        Project.no_workspace_objects.filter(pk=t.project.pk).update(deleted=True)
        assert endpoint(DashboardViewSet(), request).status_code == 400


@pytest.mark.parametrize(
    "label_type,settings,expected",
    [
        (
            "star",
            {"no_of_stars": 2},
            [
                {"value": "1", "label": "1 star"},
                {"value": "2", "label": "2 stars"},
            ],
        ),
        (
            "thumbs_up_down",
            {},
            [
                {"value": "thumbs_up", "label": "Thumbs Up"},
                {"value": "thumbs_down", "label": "Thumbs Down"},
            ],
        ),
        ("numeric", {"options": ["ignored"]}, []),
        ("text", {}, []),
    ],
)
def test_current_annotation_api_preserves_configured_option_values(
    current_tenant, label_type, settings, expected
):
    from model_hub.models.develop_annotations import AnnotationsLabels
    from tracer.views.dashboard import DashboardViewSet

    t = current_tenant
    label = AnnotationsLabels(
        name="native-options",
        type=label_type,
        settings=settings,
        organization=t.organization,
        workspace=t.workspace,
        project=t.project,
    )
    AnnotationsLabels.no_workspace_objects.bulk_create([label])
    request = SimpleNamespace(
        workspace=t.workspace,
        organization=t.organization,
        user=t.user,
        auth=None,
        query_params={},
        validated_query_data={
            "property_id": f"annotation:{label.id}",
            "_property_kind": "annotation",
            "metric_name": str(label.id),
            "metric_type": "annotation_metric",
            "source": "traces",
            "project_ids": [str(t.project.id)],
            "page_size": 10,
        },
    )
    with patch(
        "tracer.services.clickhouse.v2.property_catalog.connection.PropertyCatalogReadExecutor",
        side_effect=AssertionError("configured annotation values cannot open CH"),
    ):
        response = inspect.unwrap(DashboardViewSet.filter_values)(
            DashboardViewSet(), request
        )
    assert response.status_code == 200, response.data
    assert response.data["result"]["values"] == expected


@pytest.mark.parametrize(
    "output,expected", [("SCORE", ()), ("PASS_FAIL", ("Passed", "Failed"))]
)
def test_current_eval_choices_follow_output_contract(output, expected):
    from tracer.services.clickhouse.v2.property_catalog.models import PropertyKind
    from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
        _eval_definition,
    )

    definition = _eval_definition(
        {"id": uuid4(), "config": {"output": output}, "choices": ["stale-choice"]},
        kind=PropertyKind.EVAL_TEMPLATE,
    )
    assert definition.details.get("choices", ()) == expected


def test_native_fact_dispatch_does_not_resolve_catalog_project_scope():
    from tracer.services.clickhouse.v2.property_catalog.value_reader import (
        PropertyCatalogValueNotReady,
    )
    from tracer.views.dashboard import _read_property_catalog_value_page

    with (
        patch(
            "tracer.views.dashboard.resolve_property_catalog_project_scope",
            side_effect=AssertionError(
                "native facts retain their bounded authorization"
            ),
        ),
        pytest.raises(PropertyCatalogValueNotReady),
    ):
        _read_property_catalog_value_page(
            SimpleNamespace(),
            {"metric_name": "status", "source": "traces", "page_size": 10},
            deadline=None,
        )


def test_dataset_config_choices_project_linked_labels_and_column_dispatch(
    current_tenant,
):
    from model_hub.models.develop_annotations import AnnotationsLabels
    from model_hub.models.develop_dataset import Column, Dataset
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
    from tracer.models.project import Project
    from tracer.views.dashboard import DashboardViewSet

    t = current_tenant
    dataset = Dataset(
        name="configured-dataset", organization=t.organization, workspace=t.workspace
    )
    Dataset.no_workspace_objects.bulk_create([dataset])
    column = Column(name="text", data_type="text", source="user", dataset=dataset)
    Column.no_workspace_objects.bulk_create([column])
    template = EvalTemplate(
        name="dataset-global", config={"output": "choices"}, choices=["Dataset choice"]
    )
    EvalTemplate.no_workspace_objects.bulk_create([template])
    config = UserEvalMetric(
        name="dataset-specific",
        dataset=dataset,
        template=template,
        organization=t.organization,
        workspace=t.workspace,
    )
    UserEvalMetric.no_workspace_objects.bulk_create([config])
    experiment = Project(
        name="experiment",
        trace_type="experiment",
        model_type="generative_llm",
        organization=t.organization,
        workspace=t.workspace,
    )
    Project.no_workspace_objects.bulk_create([experiment])
    label = AnnotationsLabels(
        name="dataset-label",
        type="categorical",
        organization=t.organization,
        workspace=t.workspace,
        project=experiment,
        settings={"options": ["Dataset label"]},
    )
    AnnotationsLabels.no_workspace_objects.bulk_create([label])
    assert {
        m["name"] for m in definitions(t, category="eval_metric", source="datasets")
    } == {str(template.id)}
    assert {
        m["name"]
        for m in definitions(
            t, category="eval_metric", source="datasets", per_eval_config=True
        )
    } == {str(config.id)}
    assert {
        m["name"]
        for m in definitions(t, category="annotation_metric", source="datasets")
    } == {str(label.id)}
    assert definitions(t, category="annotation_metric", source="traces") == ()
    request = SimpleNamespace(
        workspace=t.workspace,
        organization=t.organization,
        user=t.user,
        auth=None,
        query_params={},
        validated_query_data={},
    )
    endpoint = inspect.unwrap(DashboardViewSet.filter_values)
    for kind, name, metric_type, expected in [
        ("eval_template", template.id, "eval_metric", "Dataset choice"),
        ("eval_config", config.id, "eval_metric", "Dataset choice"),
        ("annotation", label.id, "annotation_metric", "Dataset label"),
    ]:
        request.validated_query_data = {
            "property_id": f"{kind}:{name}",
            "_property_kind": kind,
            "metric_name": str(name),
            "metric_type": metric_type,
            "source": "datasets",
            "dataset_id": str(dataset.id),
            "project_ids": [],
            "page_size": 10,
        }
        with patch(
            "tracer.services.clickhouse.v2.property_catalog.connection.PropertyCatalogReadExecutor",
            side_effect=AssertionError("native options must not open observed CH"),
        ):
            result = endpoint(DashboardViewSet(), request)
        assert result.status_code == 200, result.data
        assert result.data["result"]["values"][0]["value"] == expected
    request.validated_query_data.update(
        property_id=f"dataset_column:{column.id}",
        _property_kind="dataset_column",
        metric_name=str(column.id),
        metric_type="custom_column",
    )
    with patch.object(
        DashboardViewSet, "_filter_values_dataset_column", return_value="native-column"
    ) as adapter:
        assert endpoint(DashboardViewSet(), request) == "native-column"
    assert adapter.call_args.kwargs["column_id"] == str(column.id)
    # Parent deletion invalidates every configured dataset option immediately.
    Dataset.no_workspace_objects.filter(pk=dataset.id).update(deleted=True)
    request.validated_query_data.update(
        property_id=f"eval_config:{config.id}",
        _property_kind="eval_config",
        metric_name=str(config.id),
        metric_type="eval_metric",
    )
    assert endpoint(DashboardViewSet(), request).status_code == 400


def test_native_search_role_and_uuid_keyset_are_applied_before_limit(current_tenant):
    from model_hub.models.develop_dataset import Column, Dataset

    t = current_tenant
    dataset = Dataset(name="paging", organization=t.organization, workspace=t.workspace)
    Dataset.no_workspace_objects.bulk_create([dataset])
    columns = [
        Column(name=f"match-{i}", data_type="float", source="user", dataset=dataset)
        for i in range(4)
    ]
    Column.no_workspace_objects.bulk_create(
        columns
        + [Column(name="excluded", data_type="text", source="user", dataset=dataset)]
    )
    query = {
        "category": "custom_column",
        "source": "datasets",
        "search": "match",
        "role": "metric",
    }
    reader = PropertyCatalogReader(catalog_database="unused")
    first = reader.read_page(scope=t.scope, query=query, page_size=2)
    assert len(first.metrics) == 2 and first.has_more
    second = reader.read_page(
        scope=t.scope, query=query, page_size=2, cursor_token=first.next_cursor
    )
    assert len(second.metrics) == 2 and not second.has_more
    assert {m["name"] for m in (*first.metrics, *second.metrics)} == {
        str(c.id) for c in columns
    }


def test_simulation_and_prompt_definitions_live_options_and_parent_deletion(
    current_tenant,
):
    from model_hub.models.evals_metric import EvalTemplate
    from model_hub.models.run_prompt import PromptEvalConfig, PromptTemplate
    from simulate.models import AgentDefinition, RunTest, SimulateEvalConfig
    from tracer.serializers.dashboard import DashboardFilterValuesQuerySerializer
    from tracer.views.dashboard import DashboardViewSet

    t = current_tenant
    template = EvalTemplate(
        name="native_prompt_sim",
        organization=t.organization,
        workspace=t.workspace,
        config={"output": "choices"},
        choices=["Current"],
    )
    EvalTemplate.no_workspace_objects.bulk_create([template])
    prompt = PromptTemplate(
        name="current-prompt", organization=t.organization, workspace=t.workspace
    )
    PromptTemplate.no_workspace_objects.bulk_create([prompt])
    prompt_config = PromptEvalConfig(
        name="prompt-eval", prompt_template=prompt, eval_template=template
    )
    PromptEvalConfig.no_workspace_objects.bulk_create([prompt_config])
    agent = AgentDefinition(
        agent_name="test-agent",
        inbound=False,
        description="test",
        organization=t.organization,
        workspace=t.workspace,
    )
    AgentDefinition.no_workspace_objects.bulk_create([agent])
    run = RunTest(
        name="test-run",
        agent_definition=agent,
        organization=t.organization,
        workspace=t.workspace,
    )
    RunTest.no_workspace_objects.bulk_create([run])
    sim = SimulateEvalConfig(
        name="test-simulation", run_test=run, eval_template=template
    )
    SimulateEvalConfig.no_workspace_objects.bulk_create([sim])
    t.scope["agent_definition_id"] = str(agent.id)
    assert {
        m["name"]
        for m in definitions(
            t, category="eval_metric", source="simulation", per_eval_config=True
        )
    } == {str(sim.id)}
    assert {
        m["name"]
        for m in definitions(
            t, category="eval_metric", source="prompts", per_eval_config=True
        )
    } == {str(prompt_config.id)}
    serializer = DashboardFilterValuesQuerySerializer(
        data={
            "property_id": f"eval_config:{prompt_config.id}",
            "source": "prompts",
            "page_size": 10,
        }
    )
    assert serializer.is_valid(), serializer.errors
    request = SimpleNamespace(
        workspace=t.workspace,
        organization=t.organization,
        user=t.user,
        auth=None,
        query_params={},
        validated_query_data=serializer.validated_data,
    )
    endpoint = inspect.unwrap(DashboardViewSet.filter_values)
    result = endpoint(DashboardViewSet(), request)
    assert result.status_code == 200, result.data
    assert result.data["result"]["values"][0]["value"] == "Current"
    EvalTemplate.no_workspace_objects.filter(pk=template.id).update(choices=["Edited"])
    assert (
        endpoint(DashboardViewSet(), request).data["result"]["values"][0]["value"]
        == "Edited"
    )
    PromptTemplate.no_workspace_objects.filter(pk=prompt.id).update(deleted=True)
    RunTest.no_workspace_objects.filter(pk=run.id).update(deleted=True)
    assert (
        definitions(
            t, category="eval_metric", source="simulation", per_eval_config=True
        )
        == ()
    )
    assert (
        definitions(t, category="eval_metric", source="prompts", per_eval_config=True)
        == ()
    )


def test_real_clickhouse_grouped_observed_index_and_scoped_keysets():
    import os
    from pathlib import Path

    import clickhouse_connect

    host = os.environ.get("OBSERVED_CATALOG_TEST_CH_HOST")
    if not host:
        pytest.skip("requires an explicitly isolated observed catalog test ClickHouse")
    assert host in {"clickhouse", "127.0.0.1", "localhost"}
    database = "test_observed_api_" + uuid4().hex
    kwargs = {
        "host": host,
        "port": int(os.environ.get("OBSERVED_CATALOG_TEST_CH_PORT", "8123")),
        "username": os.environ.get("OBSERVED_CATALOG_TEST_CH_USER", "test"),
        "password": os.environ.get("OBSERVED_CATALOG_TEST_CH_PASSWORD", "test"),
    }
    admin = clickhouse_connect.get_client(**kwargs)
    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(**kwargs, database=database)
    read_user = database + "_reader"
    try:
        ddl = (
            Path(__file__).parents[1]
            / "services/clickhouse/v2/observed_catalog/schema.sql"
        ).read_text()
        for statement in ddl.split(";"):
            if "CREATE TABLE" in statement:
                client.command(statement)

        class HTTPExecutor:
            def execute(self, sql, params, **options):
                result = client.query(
                    sql, parameters=params, settings={"max_threads": 1}
                )
                return SimpleNamespace(data=list(result.named_results()))

        rows = [value_row("a"), value_row("b")]
        keys = [ORG, WS, PROJECT, "custom_attribute", "key", "string", "key"]
        client.insert(
            "observed_attribute_keys",
            [
                keys + [rows[0]["first_seen"], rows[0]["last_seen"]],
                [
                    ORG,
                    WS,
                    PROJECT,
                    "custom_attribute",
                    "",
                    "string",
                    "",
                    rows[0]["first_seen"],
                    rows[0]["last_seen"],
                ],
            ],
            column_names=[
                "organization_id",
                "workspace_id",
                "project_id",
                "source_kind",
                "attribute_key",
                "attribute_type",
                "key_folded",
                "first_seen",
                "last_seen",
            ],
        )
        value_columns = [
            "organization_id",
            "workspace_id",
            "project_id",
            "source_kind",
            "attribute_key",
            "attribute_type",
            "value_fingerprint",
            "value_json",
            "value_search_text_folded",
            "first_seen",
            "last_seen",
        ]
        values = [
            [ORG, WS, PROJECT, "custom_attribute", "key", "string"]
            + [row[k] for k in value_columns[6:]]
            for row in rows
        ]
        # Retry rows and foreign tenant rows cannot inflate/leak suggestions.
        client.insert(
            "observed_attribute_values",
            values + values + [[str(uuid4()), *values[0][1:]]],
            column_names=value_columns,
        )
        reader = PropertyCatalogValueReader(HTTPExecutor(), catalog_database=database)
        first = reader.read_page(scope=SCOPE, query=VALUE_QUERY, page_size=1)
        assert len(first.values) == 1 and first.has_more
        second = reader.read_page(
            scope=SCOPE, query=VALUE_QUERY, page_size=1, cursor_token=first.next_cursor
        )
        assert not second.has_more and {
            first.values[0].value,
            second.values[0].value,
        } == {"a", "b"}
        native = Mock()
        native.read_page.return_value = ()
        keypage = PropertyCatalogReader(
            HTTPExecutor(), catalog_database=database, definition_source=native
        ).read_page(scope=SCOPE, query=QUERY, page_size=2)
        assert len(keypage.metrics) == 1
        client.insert(
            "observed_attribute_keys",
            [
                [
                    ORG,
                    WS,
                    PROJECT,
                    "custom_attribute",
                    key,
                    "string",
                    key.casefold(),
                    rows[0]["first_seen"],
                    rows[0]["last_seen"],
                ]
                for key in EXACT_ATTRIBUTE_KEYS
            ],
            column_names=[
                "organization_id",
                "workspace_id",
                "project_id",
                "source_kind",
                "attribute_key",
                "attribute_type",
                "key_folded",
                "first_seen",
                "last_seen",
            ],
        )
        keyreader = PropertyCatalogReader(
            HTTPExecutor(), catalog_database=database, definition_source=native
        )
        names, cursor = [], None
        for _ in range(5):
            page = keyreader.read_page(
                scope=SCOPE, query=QUERY, page_size=1, cursor_token=cursor
            )
            names.extend(metric["name"] for metric in page.metrics)
            if not page.has_more:
                break
            cursor = page.next_cursor
        assert names == sorted(
            ["key", *EXACT_ATTRIBUTE_KEYS], key=lambda key: (key.casefold(), key)
        )
        for key in EXACT_ATTRIBUTE_KEYS:
            client.insert(
                "observed_attribute_values",
                [
                    [ORG, WS, PROJECT, "custom_attribute", key, "string"]
                    + [row[field] for field in value_columns[6:]]
                    for row in rows
                ],
                column_names=value_columns,
            )
            query = {"property_id": "custom_attribute:" + key, "source": "traces"}
            first = reader.read_page(scope=SCOPE, query=query, page_size=1)
            second = reader.read_page(
                scope=SCOPE, query=query, page_size=1, cursor_token=first.next_cursor
            )
            assert {first.values[0].value, second.values[0].value} == {"a", "b"}
            assert not second.has_more
        # The actual ClickHouse path must preserve eligible strings after JSON
        # escaping/case folding and continue through them without a size error.
        boundary_rows = [value_row(value) for value in BOUNDARY_STRING_VALUES]
        client.insert(
            "observed_attribute_values",
            [
                [ORG, WS, PROJECT, "custom_attribute", "key", "string"]
                + [row[field] for field in value_columns[6:]]
                for row in boundary_rows
            ],
            column_names=value_columns,
        )
        found, cursor = [], None
        for _ in range(6):
            page = reader.read_page(
                scope=SCOPE, query=VALUE_QUERY, page_size=1, cursor_token=cursor
            )
            found.extend(item.value for item in page.values)
            if not page.has_more:
                break
            cursor = page.next_cursor
        assert set(found) == {"a", "b", *BOUNDARY_STRING_VALUES}
        native_port = os.environ.get("OBSERVED_CATALOG_TEST_NATIVE_PORT")
        if native_port:
            from django.test import override_settings

            from tracer.services.clickhouse.application_read_policy import (
                application_read_context,
            )
            from tracer.services.clickhouse.v2.property_catalog.connection import (
                reset_property_catalog_read_client,
            )

            admin.command(
                f"CREATE USER {read_user} IDENTIFIED WITH sha256_password BY 'fixture' SETTINGS readonly=2"
            )
            admin.command(f"GRANT SELECT ON {database}.* TO {read_user}")
            try:
                with (
                    override_settings(
                        PROPERTY_CATALOG_CH_HOST=host,
                        PROPERTY_CATALOG_CH_PORT=int(native_port),
                        PROPERTY_CATALOG_DATABASE=database,
                        PROPERTY_CATALOG_CH_USER=read_user,
                        PROPERTY_CATALOG_CH_PASSWORD="fixture",
                    ),
                    application_read_context(True),
                ):
                    native_page = PropertyCatalogValueReader(
                        catalog_database=database
                    ).read_page(scope=SCOPE, query=VALUE_QUERY, page_size=10)
                assert not native_page.has_more
                assert {item.value for item in native_page.values} == set(found)
            finally:
                reset_property_catalog_read_client()
    finally:
        client.close()
        admin.command(f"DROP USER IF EXISTS {read_user}")
        admin.command(f"DROP DATABASE {database}")
        admin.close()
