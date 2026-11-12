"""D8-PGRID server switch: OFF is the base by construction.

DSX_DATASET_ROW_SOURCE_ENABLED unset (or anything but True) keeps every server
path of the F54/F82 unit at its base behaviour:

- OFF-1: 0126_row_source only creates the new, empty model_hub_row_source
  table and is reversible; no existing table changes and no data is written.
- OFF-2: GET /model-hub/datasets/{dataset_id}/rows/{row_id}/ answers exactly
  as an unknown route does (status, body and content type) before any
  authentication or lookup, and the generated schema omits it. Without the
  trailing slash it gets no APPEND_SLASH redirect to the route: it answers as
  an unknown slashless path does.
- OFF-3: get-row-data and get-dataset-table return the bytes the base code
  path returns and run no query on model_hub_row_source.
- OFF-5: the span export behind an Observe import into an existing dataset
  selects exactly the base columns, in the base order; trace_session_id is
  never read.

Each OFF case has its ON counterpart here. The unit's own modules
(test_dataset_row_read.py, test_row_source_reads.py and
tracer/tests/test_dataset_row_sources.py) are its ON arm and turn the switch
on explicitly.
"""

import copy
import importlib
import json
import uuid
from unittest.mock import patch

import pytest
from django.db import connection, migrations
from django.db.migrations.loader import MigrationLoader
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.codecs import OpenAPICodecJson
from drf_yasg.generators import OpenAPISchemaGenerator
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework.views import APIView

from model_hub.models.row_sources import RowSource
from model_hub.tests.test_dsx_l1_dataset_list_tenancy import _client, _tenant
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.tests.test_settings_env import _load_settings
from tracer.services.clickhouse.v2 import span_reader
from tracer.services.clickhouse.v2.span_reader import CHSpanReader
from tracer.tasks.dataset import _export_span_field_value, _mapped_span_fields

SETTING = "DSX_DATASET_ROW_SOURCE_ENABLED"
ROW_READ = "/model-hub/datasets/{dataset_id}/rows/{row_id}/"
ATTACH = "model_hub.services.row_sources.attach_row_sources"
TABLE = "model_hub_row_source"
LABEL = "pgrid-gate"


def _url(dataset_id, row_id):
    return ROW_READ.format(dataset_id=dataset_id, row_id=row_id)


def _unknown(row_id):
    """A path no route matches, shaped like the row read."""

    return f"/model-hub/dsx-pgrid-unknown-route-{uuid.uuid4().hex}/{row_id}/"


@pytest.fixture
def unset(settings):
    """OFF as deployed: the env var is unset, so the setting keeps its default."""

    assert getattr(settings, SETTING, False) is not True


@pytest.fixture
def on(settings):
    settings.DSX_DATASET_ROW_SOURCE_ENABLED = True


@pytest.fixture
def tenant(db):
    a = _tenant(LABEL)
    client = _client(a)
    yield a, client
    client.stop_workspace_injection()
    clear_workspace_context()


def _source_record(a, row):
    """A recorded import source: OFF must not show it, ON shows it."""

    return RowSource.objects.create(
        row=row,
        dataset=a["dataset"],
        organization=a["org"],
        workspace=a["workspace"],
        kind="span",
        project_id=uuid.uuid4(),
        trace_id=f"{LABEL}-trace",
        span_id=f"{LABEL}-span",
    )


def _row_source_queries(queries):
    return [q["sql"] for q in queries.captured_queries if TABLE in q["sql"]]


# ---------------------------------------------------------------- the switch


def test_setting_defaults_off(settings):
    # The qualification job must leave DSX_DATASET_ROW_SOURCE_ENABLED unset.
    assert getattr(settings, SETTING, None) is False


@pytest.mark.unit
@pytest.mark.parametrize(
    "value, expected",
    [
        (None, "False"),
        ("", "False"),
        ("false", "False"),
        ("0", "False"),
        ("no", "False"),
        ("off", "False"),
        (" ture ", "False"),
        ("true", "True"),
        (" TRUE ", "True"),
        ("1", "True"),
        ("yes", "True"),
        ("on", "True"),
    ],
)
def test_the_env_switch_is_fail_closed(monkeypatch, value, expected):
    env = {"DJANGO_SETTINGS_MODULE": "tfc.settings.test"}
    if value is None:
        monkeypatch.delenv(SETTING, raising=False)
    else:
        env[SETTING] = value
    loaded = _load_settings([SETTING], **env)
    assert loaded.returncode == 0, loaded.stderr
    assert loaded.stdout.splitlines()[-1] == expected


# ------------------------------------------------------- OFF-1: the migration


def _migration(name):
    return importlib.import_module(f"model_hub.migrations.{name}").Migration


def _touches_row_source(operation):
    if isinstance(operation, migrations.CreateModel):
        return operation.name_lower == "rowsource"
    if getattr(operation, "model_name_lower", None) == "rowsource":
        return True
    if getattr(operation, "name_lower", None) == "rowsource":
        return not hasattr(operation, "model_name_lower")
    if isinstance(operation, migrations.RunSQL):
        return TABLE in str(operation.sql)
    return False


@pytest.mark.unit
def test_off1_row_source_migration_only_creates_a_new_table_and_reverses():
    # Structural: this passes before the gate as well; the migration is the
    # same with the switch on or off.
    migration = _migration("0126_row_source")
    operations = migration.operations
    assert [type(op) for op in operations] == [migrations.CreateModel]
    (create,) = operations
    assert create.name == "RowSource"
    assert create.options == {"db_table": TABLE}
    assert not any(
        isinstance(
            op,
            (
                migrations.RunPython,
                migrations.RunSQL,
                migrations.AddField,
                migrations.AlterField,
                migrations.RemoveField,
                migrations.RenameField,
                migrations.DeleteModel,
                migrations.RenameModel,
                migrations.AlterModelTable,
                migrations.SeparateDatabaseAndState,
            ),
        )
        for op in operations
    )
    assert all(op.reversible for op in operations)

    join = _migration("0138_merge_dsx3e_pgrid_row_source")
    assert join.operations == []

    # The table is new: no other migration of any app creates, alters, renames
    # or writes it, so the deploy-time step adds one empty table and nothing
    # else, and reversing it drops only that table.
    loader = MigrationLoader(None, ignore_no_migrations=True)
    touching = sorted(
        key
        for key, disk in loader.disk_migrations.items()
        if any(_touches_row_source(op) for op in disk.operations)
    )
    assert touching == [("model_hub", "0126_row_source")]


# ------------------------------------------------------ OFF-2: the new route


def _assert_unknown_route_404(known, unknown):
    assert unknown.status_code == 404, unknown.content  # control
    assert known.status_code == unknown.status_code, known.content
    assert known.content == unknown.content
    assert known["Content-Type"] == unknown["Content-Type"]


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete", "options"])
@pytest.mark.parametrize("who", ["owner", "anonymous"])
def test_off2_row_read_route_is_the_unknown_route_404(tenant, unset, who, method):
    a, owner = tenant
    client = owner if who == "owner" else APIClient()
    row = a["rows"][0]
    known = getattr(client, method)(_url(a["dataset"].id, row.id))
    unknown = getattr(client, method)(_unknown(row.id))
    _assert_unknown_route_404(known, unknown)
    assert f"{LABEL}-value".encode() not in known.content


def _slashless(url):
    return url.rstrip("/")


@pytest.mark.django_db
@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete", "options"])
@pytest.mark.parametrize("who", ["owner", "anonymous"])
def test_off2_slashless_row_read_is_the_unknown_route_404(tenant, unset, who, method):
    # APPEND_SLASH (CommonMiddleware in every settings module) must not 301 a
    # slashless request to the disabled route: at base, and for a path no
    # route matches, it is the resolver's 404 with no redirect.
    a, owner = tenant
    client = owner if who == "owner" else APIClient()
    row = a["rows"][0]
    known = getattr(client, method)(_slashless(_url(a["dataset"].id, row.id)))
    unknown = getattr(client, method)(_slashless(_unknown(row.id)))
    _assert_unknown_route_404(known, unknown)
    assert not known.has_header("Location")


@pytest.mark.unit
def test_off2_gate_precedes_drf_dispatch(unset, monkeypatch):
    from model_hub.views.dataset_row_read import DatasetRowReadView

    def refuse(*args, **kwargs):
        raise AssertionError("OFF must not enter authentication or negotiation")

    monkeypatch.setattr(APIView, "initialize_request", refuse)
    monkeypatch.setattr(APIView, "initial", refuse)
    dataset_id, row_id = uuid.uuid4(), uuid.uuid4()
    request = APIRequestFactory().get(
        _url(dataset_id, row_id), HTTP_AUTHORIZATION="Bearer not-a-valid-token"
    )
    with pytest.raises(Http404):
        DatasetRowReadView.as_view()(request, dataset_id=dataset_id, row_id=row_id)


@pytest.mark.django_db
def test_on2_row_read_route_reads_the_row(tenant, on):
    a, owner = tenant
    row = a["rows"][0]
    response = owner.get(_url(a["dataset"].id, row.id))
    assert response.status_code == 200, response.content
    assert response["Content-Type"].startswith("application/json")
    result = response.json()["result"]
    assert result["row_id"] == str(row.id)
    assert result["cells"][str(a["column"].id)]["cell_value"] == f"{LABEL}-value-0"
    anonymous = APIClient().get(_url(a["dataset"].id, row.id))
    assert anonymous.status_code in (401, 403), anonymous.content


@pytest.mark.django_db
def test_on2_slashless_row_read_redirects_to_the_route(tenant, on):
    # ON keeps Django's APPEND_SLASH redirect to the registered route.
    a, owner = tenant
    url = _url(a["dataset"].id, a["rows"][0].id)
    response = owner.get(_slashless(url))
    assert response.status_code == 301, response.content
    assert response["Location"] == url


def _schema():
    from model_hub import urls

    names = {"dataset-row-read", "get-row-data"}
    patterns = [p for p in urls.urlpatterns if getattr(p, "name", None) in names]
    assert {p.name for p in patterns} == names
    generator = OpenAPISchemaGenerator(
        openapi.Info(title="Row read boundary", default_version="v1"),
        patterns=[path("model-hub/", include(patterns))],
    )
    return json.loads(
        OpenAPICodecJson(validators=[]).encode(
            generator.get_schema(request=None, public=True)
        )
    )


def _route(schema, suffix):
    found = [item for key, item in schema["paths"].items() if key.endswith(suffix)]
    assert len(found) <= 1, schema["paths"]
    return found[0] if found else None


@pytest.mark.parametrize("enabled", [False, True])
def test_schema_tracks_the_switch(settings, enabled):
    settings.DSX_DATASET_ROW_SOURCE_ENABLED = enabled
    schema = _schema()
    # Control: the existing row read stays documented either way.
    assert "post" in _route(schema, "/develops/{dataset_id}/get-row-data/")
    row_read = _route(schema, "/datasets/{dataset_id}/rows/{row_id}/")
    if enabled:
        assert set(row_read) - {"parameters"} == {"get"}
        assert row_read["get"]["responses"]["200"]["schema"]["$ref"] == (
            "#/definitions/DatasetRowReadResponse"
        )
    else:
        assert row_read is None
        assert "DatasetRowReadResponse" not in schema.get("definitions", {})


# ------------------------------------------------------ OFF-3: the read hooks


def _row_data(client, a, row):
    return client.post(
        f"/model-hub/develops/{a['dataset'].id}/get-row-data/",
        {"row_id": str(row.id)},
        format="json",
    )


def _table(client, a):
    return client.get(f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/")


def _base_then_head(call):
    """The base code path's response and queries (the hook removed), then the
    head's, each after one warm-up request."""

    with patch(ATTACH, lambda rows, **kwargs: rows):
        assert call().status_code == 200
        with CaptureQueriesContext(connection) as base_queries:
            base = call()
    with CaptureQueriesContext(connection) as head_queries:
        head = call()
    return base, base_queries, head, head_queries


def _sources(read, response):
    result = response.json()["result"]
    rows = [result["current"]] if read == "get-row-data" else result["table"]
    return {
        row["row_id"]: (row.get("row_meta") or {}).get("source")
        for row in rows
        if isinstance(row.get("row_meta"), dict) and "source" in row["row_meta"]
    }


@pytest.mark.django_db
@pytest.mark.parametrize("read", ["get-row-data", "get-dataset-table"])
def test_off3_row_reads_return_the_base_bytes_and_no_extra_query(tenant, unset, read):
    a, owner = tenant
    sourced = a["rows"][0]
    _source_record(a, sourced)

    def call():
        if read == "get-row-data":
            return _row_data(owner, a, sourced)
        return _table(owner, a)

    base, base_queries, head, head_queries = _base_then_head(call)
    assert base.status_code == 200, base.content
    assert head.status_code == base.status_code, head.content
    assert head.content == base.content
    assert head["Content-Type"] == base["Content-Type"]
    assert _sources(read, head) == {}
    assert _row_source_queries(head_queries) == []
    assert len(head_queries.captured_queries) == len(base_queries.captured_queries)


@pytest.mark.django_db
def test_off3_attach_row_sources_is_inert(tenant, unset):
    from model_hub.services.row_sources import attach_row_sources

    a, _ = tenant
    sourced, plain = a["rows"]
    _source_record(a, sourced)
    rows = [{"row_id": str(sourced.id)}, {"row_id": str(plain.id), "row_meta": "x"}]
    before = copy.deepcopy(rows)
    for live in (False, True):
        with CaptureQueriesContext(connection) as queries:
            returned = attach_row_sources(rows, dataset=a["dataset"], live=live)
        assert returned is rows
        assert rows == before
        assert queries.captured_queries == []


@pytest.mark.django_db
@pytest.mark.parametrize("read", ["get-row-data", "get-dataset-table"])
def test_on3_row_reads_carry_the_recorded_source(tenant, on, read):
    a, owner = tenant
    sourced, plain = a["rows"]
    _source_record(a, sourced)
    with CaptureQueriesContext(connection) as queries:
        if read == "get-row-data":
            response = _row_data(owner, a, sourced)
        else:
            response = _table(owner, a)
    assert response.status_code == 200, response.content
    sources = _sources(read, response)
    assert set(sources) == {str(sourced.id)}
    assert sources[str(sourced.id)]["span_id"] == f"{LABEL}-span"
    # The record's project is not in Observe, so the source reads as expired.
    assert sources[str(sourced.id)]["expired"] is True
    assert _row_source_queries(queries)


# -------------------------------------------------- OFF-5: the export columns


class _CapturingClient:
    def __init__(self):
        self.queries = []

    def query(self, sql, parameters=None, settings=None):
        self.queries.append(sql)
        return type("Result", (), {"result_rows": []})()


def _export_sql(field_names, *, base_map=False):
    """The span export read; ``base_map`` uses the column map without the one
    entry the unit added (trace_session_id), i.e. the base map."""

    reader = CHSpanReader.__new__(CHSpanReader)
    reader._client = _CapturingClient()
    columns = dict(span_reader._EXPORT_COLUMN_SQL)
    if base_map:
        columns.pop("trace_session_id", None)
    with patch.dict(span_reader._EXPORT_COLUMN_SQL, columns, clear=True):
        reader.export_fields_by_ids(
            ["span-1", "span-2"], set(field_names), project_id="p-1", org_id="o-1"
        )
    return reader._client.queries[0]


_WHERE = (
    "FROM spans PREWHERE id IN %(ids)s AND project_id = %(pid)s AND org_id = %(oid)s "
    "GROUP BY id HAVING argMax(is_deleted, _version) = 0"
)
_SESSION = "argMax(toString(trace_session_id), _version) AS trace_session_id"


@pytest.mark.unit
def test_off5_existing_dataset_export_reads_exactly_the_base_columns_in_order(unset):
    # An existing dataset with a column literally named trace_session_id (no
    # span field) next to mapped columns: the import's export read is the base
    # read, column for column and in the base order.
    unmapped = {
        "column_id": str(uuid.uuid4()),
        "span_field": None,
        "column_name": "trace_session_id",
    }
    mapping = [unmapped] + [
        {"column_id": str(uuid.uuid4()), "span_field": field, "column_name": field}
        for field in ("output", "input", "trace_id", "metadata")
    ]
    fields = _mapped_span_fields(mapping)
    assert "trace_session_id" in fields
    assert _export_sql(fields) == (
        "SELECT id, argMax(input, _version) AS input, "
        "argMax(toJSONString(metadata), _version) AS metadata, "
        "argMax(output, _version) AS output, "
        "argMax(trace_id, _version) AS trace_id " + _WHERE
    )
    assert _export_sql({"trace_session_id"}) == "SELECT id " + _WHERE
    # The cell of that column stays as the base leaves it.
    assert _export_span_field_value({"id": "span-1"}, "trace_session_id") is None


@pytest.mark.unit
def test_off5_no_field_set_adds_a_column(unset):
    names = set(span_reader._EXPORT_FIELD_COLUMNS)
    field_sets = [{name, "trace_session_id"} for name in names]
    field_sets += [names | {"trace_session_id"}, {"trace_session_id"}, set(names)]
    for fields in field_sets:
        sql = _export_sql(fields)
        assert sql == _export_sql(fields, base_map=True), fields
        assert "trace_session_id" not in sql, fields


@pytest.mark.unit
def test_on5_the_session_id_is_read_only_when_asked(on):
    assert _export_sql({"input", "trace_session_id"}) == (
        "SELECT id, argMax(input, _version) AS input, " + _SESSION + " " + _WHERE
    )
    assert _SESSION not in _export_sql({"input"})


# ------------------------------------------- the predicate: only True opens


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["missing", None, False, 0, 1, "true", "True"])
def test_only_an_explicit_true_opens_the_unit(tenant, settings, value):
    from model_hub.services.row_sources import attach_row_sources

    if value == "missing":
        del settings.DSX_DATASET_ROW_SOURCE_ENABLED
    else:
        setattr(settings, SETTING, value)
    a, owner = tenant
    sourced = a["rows"][0]
    _source_record(a, sourced)
    _assert_unknown_route_404(
        owner.get(_url(a["dataset"].id, sourced.id)), owner.get(_unknown(sourced.id))
    )
    _assert_unknown_route_404(
        owner.get(_slashless(_url(a["dataset"].id, sourced.id))),
        owner.get(_slashless(_unknown(sourced.id))),
    )
    rows = [{"row_id": str(sourced.id)}]
    with CaptureQueriesContext(connection) as queries:
        attach_row_sources(rows, dataset=a["dataset"], live=True)
    assert rows == [{"row_id": str(sourced.id)}]
    assert queries.captured_queries == []
    assert "trace_session_id" not in _export_sql({"input", "trace_session_id"})
