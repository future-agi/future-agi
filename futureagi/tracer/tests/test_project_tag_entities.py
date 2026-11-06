"""Regression: project-scoped tags with ClickHouse assignments (TH-7275).

After the CH25 cutover trace detail, the trace grid and span detail read
ClickHouse, while ``TraceView.update_tags`` and
``ObservationSpanView.update_tags`` still loaded Postgres rows and answered
400 "not found" for a ClickHouse-only trace or span. The UI also posts
``{name, color}`` objects, which the old serializer rejected. Colour belongs on
the project tag; every assignment stays a list of names.

These tests use the real ClickHouse test sidecar, not a mock.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project, ProjectTagDefinition
from tracer.models.trace import Trace
from tracer.tests._ch_seed import _get_ch_client, seed_ch_trace

_START = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


class _Span:
    """A span row that exists only in ClickHouse."""

    def __init__(
        self, project, *, trace_id, span_id=None, parent="", tags=None, start=None
    ):
        self.id = span_id or uuid.uuid4().hex[:16]
        self.trace_id = trace_id
        self.project_id = project.id
        self.project = project
        self.project_version_id = None
        self.parent_span_id = parent
        self.name = "root" if not parent else "child"
        self.observation_type = "agent" if not parent else "llm"
        self.status = "OK"
        self.start_time = start or _START
        self.end_time = self.start_time + timedelta(milliseconds=20)
        self.tags = list(tags or [])
        self.deleted = False


def _trace_row(project, trace_id, tags=(), created_at=None):
    now = datetime.now(UTC)
    return {
        "id": trace_id,
        "project_id": str(project.id),
        "project_version_id": None,
        "name": "root",
        "session_id": None,
        "external_id": None,
        "tags": json.dumps(list(tags)),
        "metadata": "{}",
        "input": "",
        "output": "",
        "error": "",
        "error_analysis_status": "PENDING",
        "created_at": created_at or _START,
        "updated_at": now,
        "is_deleted": 0,
    }


def _ch(sql, **params):
    client = _get_ch_client()
    try:
        return client.query(sql, parameters=params).result_rows
    finally:
        client.close()


def _span_tags(project, span_id, trace_id=None):
    """Latest tags of each live physical row for a span id in one project."""
    rows = _ch(
        "SELECT trace_id, argMax(tags, _version) FROM spans "
        "WHERE project_id = {p:UUID} AND id = {s:String} "
        "GROUP BY project_id, trace_id, id, start_time ORDER BY trace_id",
        p=str(project.id),
        s=span_id,
    )
    if trace_id is not None:
        rows = [row for row in rows if row[0] == trace_id]
    return [json.loads(row[1]) for row in rows]


def _trace_tags(project, trace_id):
    """What the trace grid reads: the latest ``traces`` version's tags."""
    rows = _ch(
        "SELECT argMax(tags, _version) FROM traces "
        "WHERE project_id = {p:UUID} AND id = {t:UUID} GROUP BY project_id, id",
        p=str(project.id),
        t=trace_id,
    )
    return json.loads(rows[0][0]) if rows else None


def _project(organization, workspace, name):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )


@pytest.fixture(autouse=True)
def _clean_traces():
    yield
    client = _get_ch_client()
    try:
        client.command("TRUNCATE TABLE IF EXISTS traces")
    finally:
        client.close()


@pytest.fixture
def other_org_project(db, user):
    """A project in another organization, never visible to ``auth_client``."""
    other = Organization.objects.create(name="Other Tag Org")
    other_ws = Workspace.objects.create(
        name="Other Tag Workspace",
        organization=other,
        is_active=True,
        created_by=user,
    )
    return _project(other, other_ws, "Other Org Project")


@pytest.fixture
def other_workspace_project(db, organization, user):
    """Same organization, a non-default workspace the client is not in."""
    other_ws = Workspace.objects.create(
        name="Other Tag Workspace",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    return _project(organization, other_ws, "Other Workspace Project")


def _patch_trace(client, trace_id, tags, project=None):
    query = f"project_id={project.id}" if project is not None else ""
    return client.patch(
        f"/tracer/trace/{trace_id}/tags/",
        {"tags": tags},
        format="json",
        QUERY_STRING=query,
    )


def _post_span(client, span_id, tags, project=None):
    body = {"span_id": span_id, "tags": tags}
    if project is not None:
        body["project_id"] = str(project.id)
    return client.post("/tracer/observation-span/update-tags/", body, format="json")


@pytest.mark.integration
@pytest.mark.django_db
class TestTraceAndSpanTagWrites:
    def test_clickhouse_only_trace_is_tagged_on_every_read_surface(
        self, auth_client, observe_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        root = _Span(observe_project, trace_id=trace_id, tags=["sdk-root"])
        child = _Span(
            observe_project,
            trace_id=trace_id,
            parent=root.id,
            tags=["sdk-child"],
            start=_START + timedelta(seconds=1),
        )
        ch_seed([root, child])
        seed_ch_trace(_trace_row(observe_project, trace_id))
        assert not Trace.objects.filter(id=trace_id).exists()
        assert not ObservationSpan.objects.filter(id=root.id).exists()

        response = _patch_trace(
            auth_client,
            trace_id,
            [{"name": "plain-one", "color": "#22C55E"}, "bare-name"],
            project=observe_project,
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"] == {
            "id": trace_id,
            "tags": ["plain-one", "bare-name"],
        }
        # Colour is on the project tag, never in an assignment.
        assert (
            ProjectTagDefinition.objects.get(
                project=observe_project, name="plain-one"
            ).color
            == "#22C55E"
        )
        assert _span_tags(observe_project, root.id) == [["plain-one", "bare-name"]]
        assert _span_tags(observe_project, child.id) == [["sdk-child"]]
        assert _trace_tags(observe_project, trace_id) == ["plain-one", "bare-name"]

        detail = auth_client.get(
            f"/tracer/trace/{trace_id}/",
            QUERY_STRING=f"project_id={observe_project.id}",
        )
        assert detail.status_code == status.HTTP_200_OK, detail.data
        assert detail.data["result"]["trace"]["tags"] == ["plain-one", "bare-name"]

    def test_trace_without_project_id_is_found_in_the_callers_projects(
        self, auth_client, observe_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        ch_seed(_Span(observe_project, trace_id=trace_id))

        response = _patch_trace(auth_client, trace_id, ["legacy-caller"])

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"]["tags"] == ["legacy-caller"]

    def test_trace_id_in_two_of_the_callers_projects_asks_for_project_id(
        self, auth_client, organization, workspace, observe_project, ch_seed
    ):
        second = _project(organization, workspace, "Second Observe Project")
        trace_id = str(uuid.uuid4())
        span_id = uuid.uuid4().hex[:16]
        ch_seed(
            [
                _Span(observe_project, trace_id=trace_id, span_id=span_id),
                _Span(second, trace_id=trace_id, span_id=span_id, tags=["second"]),
            ]
        )

        ambiguous = _patch_trace(auth_client, trace_id, ["x"])
        assert ambiguous.status_code == status.HTTP_400_BAD_REQUEST
        assert "project_id" in str(ambiguous.data)

        pinned = _patch_trace(auth_client, trace_id, ["x"], project=observe_project)
        assert pinned.status_code == status.HTTP_200_OK, pinned.data
        assert _span_tags(observe_project, span_id) == [["x"]]
        assert _span_tags(second, span_id) == [["second"]]

    def test_postgres_backed_trace_updates_postgres_and_clickhouse(
        self, auth_client, observe_project, ch_seed
    ):
        trace = Trace.objects.create(project=observe_project, name="pg", tags=["old"])
        trace_id = str(trace.id)
        root = _Span(observe_project, trace_id=trace_id, tags=["old"])
        ch_seed(root)
        seed_ch_trace(_trace_row(observe_project, trace_id, tags=["old"]))

        response = _patch_trace(auth_client, trace_id, ["new"])

        assert response.status_code == status.HTTP_200_OK, response.data
        trace.refresh_from_db()
        # Detail prefers this row, and the ingestion mirror copies it to CH.
        assert trace.tags == ["new"]
        assert _span_tags(observe_project, root.id) == [["new"]]
        assert _trace_tags(observe_project, trace_id) == ["new"]

    def test_missing_trace_is_not_found(self, auth_client, observe_project):
        response = _patch_trace(
            auth_client, str(uuid.uuid4()), ["x"], project=observe_project
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Trace not found" in str(response.data)

    def test_clickhouse_read_failure_is_unavailable_not_not_found(
        self, auth_client, observe_project, monkeypatch
    ):
        from tracer.services.clickhouse.v2.query_service import (
            V2AnalyticsQueryService,
        )

        def _down(*args, **kwargs):
            raise ConnectionError("clickhouse is down")

        monkeypatch.setattr(V2AnalyticsQueryService, "execute_ch_query", _down)

        trace = _patch_trace(auth_client, str(uuid.uuid4()), ["x"])
        span = _post_span(auth_client, uuid.uuid4().hex[:16], ["x"])

        for response in (trace, span):
            assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
            assert "not found" not in str(response.data).lower()

    def test_clickhouse_write_failure_is_unavailable_and_keeps_postgres(
        self, auth_client, observe_project, ch_seed, monkeypatch
    ):
        trace_id = str(uuid.uuid4())
        root = _Span(observe_project, trace_id=trace_id, tags=["kept"])
        ch_seed(root)

        def _down():
            from tracer.services.project_tags import TagStoreUnavailable

            raise TagStoreUnavailable("clickhouse client unavailable")

        monkeypatch.setattr("tracer.services.project_tags._write_client", _down)

        response = _patch_trace(
            auth_client, trace_id, [{"name": "fresh", "color": "#111111"}]
        )

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert not ProjectTagDefinition.objects.filter(name="fresh").exists()
        assert _span_tags(observe_project, root.id) == [["kept"]]

    def test_clickhouse_only_span_is_tagged(
        self, auth_client, observe_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        root = _Span(observe_project, trace_id=trace_id)
        child = _Span(
            observe_project,
            trace_id=trace_id,
            parent=root.id,
            start=_START + timedelta(seconds=1),
        )
        ch_seed([root, child])

        response = _post_span(
            auth_client, child.id, [{"name": "slow", "color": "#F59E0B"}, "llm"]
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"] == {"id": child.id, "tags": ["slow", "llm"]}
        assert _span_tags(observe_project, child.id) == [["slow", "llm"]]
        assert _span_tags(observe_project, root.id) == [[]]
        assert (
            ProjectTagDefinition.objects.get(project=observe_project, name="slow").color
            == "#F59E0B"
        )

    def test_same_span_id_in_two_traces_rewrites_only_the_chosen_row(
        self, auth_client, observe_project, ch_seed
    ):
        span_id = uuid.uuid4().hex[:16]
        first_trace, second_trace = str(uuid.uuid4()), str(uuid.uuid4())
        ch_seed(
            _Span(
                observe_project, trace_id=first_trace, span_id=span_id, tags=["first"]
            )
        )
        ch_seed(
            _Span(
                observe_project, trace_id=second_trace, span_id=span_id, tags=["second"]
            )
        )

        response = _post_span(auth_client, span_id, ["changed"])

        assert response.status_code == status.HTTP_200_OK, response.data
        after = {
            first_trace: _span_tags(observe_project, span_id, first_trace),
            second_trace: _span_tags(observe_project, span_id, second_trace),
        }
        # Exactly one physical row (the copy span detail shows) changes; the
        # other trace's row with the same OTel span id keeps its tags.
        changed = [trace for trace, tags in after.items() if tags == [["changed"]]]
        assert len(changed) == 1, after
        untouched = second_trace if changed[0] == first_trace else first_trace
        original = ["first"] if untouched == first_trace else ["second"]
        assert after[untouched] == [original]


@pytest.mark.integration
@pytest.mark.django_db
class TestTagTenantIsolation:
    def test_same_ids_in_another_org_are_never_read_or_written(
        self, auth_client, observe_project, other_org_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        span_id = uuid.uuid4().hex[:16]
        ch_seed(
            [
                _Span(observe_project, trace_id=trace_id, span_id=span_id),
                _Span(
                    other_org_project,
                    trace_id=trace_id,
                    span_id=span_id,
                    tags=["theirs"],
                ),
            ]
        )
        seed_ch_trace(_trace_row(other_org_project, trace_id, tags=["theirs"]))

        assert _patch_trace(auth_client, trace_id, ["mine"]).status_code == 200
        assert _post_span(auth_client, span_id, ["mine"]).status_code == 200
        assert _span_tags(observe_project, span_id) == [["mine"]]

        trace_denied = _patch_trace(
            auth_client, trace_id, ["leak"], project=other_org_project
        )
        span_denied = _post_span(
            auth_client, span_id, ["leak"], project=other_org_project
        )
        assert trace_denied.status_code == status.HTTP_400_BAD_REQUEST
        assert "Trace not found" in str(trace_denied.data)
        assert span_denied.status_code == status.HTTP_400_BAD_REQUEST
        assert "Observation span not found" in str(span_denied.data)

        base = f"/tracer/project/{other_org_project.id}/tag-definitions/"
        denied = (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND)
        assert auth_client.get(base).status_code in denied
        assert (
            auth_client.patch(
                base, {"name": "theirs", "new_name": "leak"}, format="json"
            ).status_code
            in denied
        )
        assert (
            auth_client.delete(base, QUERY_STRING="name=theirs").status_code in denied
        )
        assert _span_tags(other_org_project, span_id) == [["theirs"]]
        assert _trace_tags(other_org_project, trace_id) == ["theirs"]
        assert not ProjectTagDefinition.objects.filter(
            project=other_org_project
        ).exists()

    def test_another_workspace_in_the_same_org_is_not_written(
        self, auth_client, other_workspace_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        span_id = uuid.uuid4().hex[:16]
        ch_seed(
            _Span(
                other_workspace_project,
                trace_id=trace_id,
                span_id=span_id,
                tags=["hidden"],
            )
        )

        trace = _patch_trace(auth_client, trace_id, ["leak"])
        span = _post_span(auth_client, span_id, ["leak"])

        assert trace.status_code == status.HTTP_400_BAD_REQUEST
        assert span.status_code == status.HTTP_400_BAD_REQUEST
        assert _span_tags(other_workspace_project, span_id) == [["hidden"]]


@pytest.mark.integration
@pytest.mark.django_db
class TestProjectTagDefinitions:
    def _url(self, project):
        return f"/tracer/project/{project.id}/tag-definitions/"

    def test_list_is_the_union_of_definitions_and_assigned_names(
        self, auth_client, observe_project, ch_seed
    ):
        trace_id = str(uuid.uuid4())
        ch_seed(
            [
                # An SDK repeated a name in one list: it still counts once.
                _Span(
                    observe_project,
                    trace_id=trace_id,
                    tags=["sdk-only", "sdk-only", "shared"],
                ),
                _Span(
                    observe_project,
                    trace_id=trace_id,
                    parent="p",
                    tags=["shared"],
                    start=_START + timedelta(seconds=1),
                ),
            ]
        )
        seed_ch_trace(_trace_row(observe_project, trace_id, tags=["trace-only"]))
        ProjectTagDefinition.objects.create(
            project=observe_project, name="stored-only", color="#3B82F6"
        )

        response = auth_client.get(self._url(observe_project))

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"]["tags"] == [
            {"name": "sdk-only", "color": "", "usage_count": 1},
            {"name": "shared", "color": "", "usage_count": 2},
            {"name": "stored-only", "color": "#3B82F6", "usage_count": 0},
            {"name": "trace-only", "color": "", "usage_count": 1},
        ]

    def test_rename_merges_into_an_existing_tag_everywhere(
        self, auth_client, observe_project, ch_seed
    ):
        ProjectTagDefinition.objects.create(
            project=observe_project, name="old", color="#111111"
        )
        ProjectTagDefinition.objects.create(
            project=observe_project, name="new", color="#222222"
        )
        trace = Trace.objects.create(project=observe_project, tags=["old", "x"])
        trace_id = str(trace.id)
        root = _Span(observe_project, trace_id=trace_id, tags=["old"])
        child = _Span(
            observe_project,
            trace_id=trace_id,
            parent=root.id,
            tags=["old", "new"],
            start=_START + timedelta(seconds=1),
        )
        ch_seed([root, child])
        seed_ch_trace(_trace_row(observe_project, trace_id, tags=["old"]))

        response = auth_client.patch(
            self._url(observe_project),
            {"name": "old", "new_name": "new"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"] == {"name": "new", "color": "#222222"}
        assert list(
            ProjectTagDefinition.objects.filter(project=observe_project).values_list(
                "name", flat=True
            )
        ) == ["new"]
        assert _span_tags(observe_project, root.id) == [["new"]]
        assert _span_tags(observe_project, child.id) == [["new"]]
        assert _trace_tags(observe_project, trace_id) == ["new"]
        trace.refresh_from_db()
        assert trace.tags == ["new", "x"]

    def test_rename_clickhouse_only_tag_onto_a_stored_tag_with_colour(
        self, auth_client, observe_project, ch_seed
    ):
        ProjectTagDefinition.objects.create(
            project=observe_project, name="kept", color="#222222"
        )
        span = _Span(observe_project, trace_id=str(uuid.uuid4()), tags=["sdk"])
        ch_seed(span)

        response = auth_client.patch(
            self._url(observe_project),
            {"name": "sdk", "new_name": "kept", "color": "#333333"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"] == {"name": "kept", "color": "#333333"}
        assert (
            ProjectTagDefinition.objects.filter(
                project=observe_project, name="kept"
            ).count()
            == 1
        )
        assert _span_tags(observe_project, span.id) == [["kept"]]

    def test_recolour_changes_only_the_definition(
        self, auth_client, observe_project, ch_seed, monkeypatch
    ):
        span = _Span(observe_project, trace_id=str(uuid.uuid4()), tags=["blue"])
        ch_seed(span)

        def _no_writes(*args, **kwargs):
            raise AssertionError("recolouring must not write ClickHouse")

        monkeypatch.setattr("tracer.services.project_tags._command", _no_writes)

        response = auth_client.patch(
            self._url(observe_project),
            {"name": "blue", "color": "#0000FF"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"] == {"name": "blue", "color": "#0000FF"}
        assert _span_tags(observe_project, span.id) == [["blue"]]

    def test_delete_removes_the_name_from_every_assignment(
        self, auth_client, observe_project, ch_seed
    ):
        ProjectTagDefinition.objects.create(
            project=observe_project, name="gone", color="#111111"
        )
        trace = Trace.objects.create(project=observe_project, tags=["gone"])
        trace_id = str(trace.id)
        span = _Span(observe_project, trace_id=trace_id, tags=["gone", "stay"])
        ch_seed(span)
        seed_ch_trace(_trace_row(observe_project, trace_id, tags=["gone"]))

        response = auth_client.delete(
            self._url(observe_project), QUERY_STRING="name=gone"
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"] == {"deleted": True}
        assert _span_tags(observe_project, span.id) == [["stay"]]
        assert _trace_tags(observe_project, trace_id) == []
        trace.refresh_from_db()
        assert trace.tags == []
        listed = auth_client.get(self._url(observe_project)).data["result"]["tags"]
        assert [item["name"] for item in listed] == ["stay"]

    @pytest.mark.parametrize(
        "body",
        [
            {"name": "x"},
            {"name": "x", "color": "#12345G"},
            {"name": "x", "color": "#FFFFFF\n"},
            {"name": "x", "new_name": ""},
            {"name": "x", "color": "#FFFFFF", "unexpected": True},
        ],
    )
    def test_invalid_update_is_a_400(self, auth_client, observe_project, body):
        response = auth_client.patch(self._url(observe_project), body, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data

    def test_unknown_tag_is_a_400_that_says_how_to_list_tags(
        self, auth_client, observe_project
    ):
        response = auth_client.patch(
            self._url(observe_project),
            {"name": "nope", "color": "#FFFFFF"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "tag-definitions" in str(response.data)

    @pytest.mark.parametrize(
        "tags", ["not-a-list", [{"color": "#FFFFFF"}], [{"name": "x", "color": "red"}]]
    )
    def test_invalid_tag_payload_is_a_400(self, auth_client, observe_project, tags):
        response = _patch_trace(
            auth_client, str(uuid.uuid4()), tags, project=observe_project
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
