"""A shared link to a trace too large for the full trace read still opens.

The full read (``retrieve_trace_detail_ch``) refuses a trace with more than
1,000 spans, 5,000 eval results or 5,000 annotations. Through a shared link
such a trace resolves with its spans only: one ``list_by_trace`` read built
into the parent and child tree, flagged ``detail_limited`` with the limit's
code, and carrying no evaluations and no annotations.

The link's access checks run first and unchanged, so a revoked, expired,
deleted or restricted link without access reads no span at all. Every other
failure of the full read still resolves as not found, and a trace within the
limits keeps the full payload.
"""

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rest_framework import status

# Imported before the reader is patched: it binds get_reader by name on first
# import, and would otherwise keep the stand-in for the rest of the session.
import tracer.queries.feed  # noqa: F401
from accounts.models.user import User
from tfc.constants.roles import OrganizationRoles
from tracer.models.shared_link import SharedLink
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.span_reader import CHSpan
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailReadUnavailable,
)

FULL_READ = (
    "tracer.services.clickhouse.v2.query_builders.trace_detail.retrieve_trace_detail_ch"
)
PROJECT_LOOKUP = "tracer.views.shared_link._get_shared_trace_project_id"
READER = "tracer.services.clickhouse.v2.get_reader"

TRACE_ID = "7c0e6d1a-3b2f-4f8e-9a45-1d2c3b4a5e6f"
ROOT_SPAN_ID = "span_root_0001"
STARTED = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
CHILD_COUNT = 1000


def _ch_span(project_id, span_id, parent_span_id, offset_ms):
    """A span row as ``CHSpanReader.list_by_trace`` returns it."""
    started = STARTED + timedelta(milliseconds=offset_ms)
    is_root = not parent_span_id
    return CHSpan(
        id=span_id,
        project_id=str(project_id),
        trace_id=TRACE_ID,
        parent_span_id=parent_span_id,
        name="agent run" if is_root else f"step {span_id}",
        observation_type="agent" if is_root else "tool",
        operation_name="",
        start_time=started,
        end_time=started + timedelta(milliseconds=5),
        latency_ms=5,
        model="",
        provider="",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost=None,
        status="OK",
        status_message="",
        org_id=None,
        project_version_id=None,
        end_user_id=None,
        trace_session_id=None,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input='{"prompt": "plan the refund"}' if is_root else "",
        output="",
        tags="[]",
        span_events="[]",
        metadata="{}",
        resource_attrs="{}",
        attributes_extra="{}",
    )


def _large_trace(project_id):
    """One root span and 1,000 children: 1,001 spans, over the span limit."""
    root = _ch_span(project_id, ROOT_SPAN_ID, "", 0)
    children = [
        _ch_span(project_id, f"span_child_{index:04d}", ROOT_SPAN_ID, index + 1)
        for index in range(CHILD_COUNT)
    ]
    return [root, *children]


class _Reader:
    """Stands in for ``get_reader()``: a context manager over span rows."""

    def __init__(self, spans):
        self.spans = list(spans)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_by_trace(self, trace_id, *, project_id, include_heavy=True):
        self.calls.append((trace_id, str(project_id)))
        return [
            span
            for span in self.spans
            if span.trace_id == trace_id and span.project_id == str(project_id)
        ]


@pytest.fixture
def reads(project):
    """The link's project lookup, the full read and the span reader, faked.

    By default the full read refuses the trace for its span count.
    """
    reader = _Reader(_large_trace(project.id))
    with (
        patch(PROJECT_LOOKUP, return_value=str(project.id)) as lookup,
        patch(
            FULL_READ,
            side_effect=TraceDetailReadUnavailable("span_limit_exceeded"),
        ) as full_read,
        patch(READER, return_value=reader) as get_reader,
    ):
        yield SimpleNamespace(
            lookup=lookup,
            full_read=full_read,
            get_reader=get_reader,
            reader=reader,
        )


def _trace_link(user, project, *, access_type="public", **fields):
    return SharedLink.objects.create(
        resource_type="trace",
        resource_id=TRACE_ID,
        access_type=access_type,
        created_by=user,
        organization=project.organization,
        workspace=project.workspace,
        **fields,
    )


def _resolve(client, link):
    return client.get(f"/tracer/shared/{link.token}/")


def _entries(roots):
    """Every span entry of a tree, children included."""
    pending = list(roots)
    while pending:
        entry = pending.pop()
        yield entry
        pending.extend(entry.get("children") or [])


def _assert_no_span_read(reads):
    reads.full_read.assert_not_called()
    reads.get_reader.assert_not_called()
    assert reads.reader.calls == []


def _canonical_detail(project_id, *, annotations=None):
    """A full-read payload: span entries with rollups, a summary and a graph."""
    child = {
        "observation_span": {
            "id": "span_child_0000",
            "project": str(project_id),
            "trace": TRACE_ID,
            "parent_span_id": ROOT_SPAN_ID,
            "name": "step span_child_0000",
            "start_time": (STARTED + timedelta(milliseconds=1)).isoformat(),
        },
        "children": [],
        "eval_rollup": {"scope": "span", "evals": []},
    }
    root = {
        "observation_span": {
            "id": ROOT_SPAN_ID,
            "project": str(project_id),
            "trace": TRACE_ID,
            "parent_span_id": None,
            "name": "agent run",
            "start_time": STARTED.isoformat(),
        },
        "children": [child],
        "eval_rollup": {
            "scope": "trace",
            "evals": [{"eval_config_id": "config-1", "eval_name": "Tone"}],
        },
    }
    if annotations is not None:
        root["annotations"] = copy.deepcopy(annotations)
        child["annotations"] = copy.deepcopy(annotations)
    return {
        "trace": {"id": TRACE_ID, "project": str(project_id), "name": "agent run"},
        "observation_spans": [root],
        "summary": {
            "total_spans": 2,
            "total_duration_ms": 5,
            "total_tokens": 0,
            "total_cost": 0,
            "error_count": 0,
        },
        "graph": {"nodes": [{"id": ROOT_SPAN_ID}], "edges": []},
    }


@pytest.mark.django_db
def test_public_link_to_a_trace_over_the_span_limit_opens_with_every_span(
    api_client, user, project, reads
):
    link = _trace_link(user, project)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_200_OK, response.content
    payload = response.json()
    assert payload["resource_type"] == "trace"
    assert payload["resource_id"] == TRACE_ID
    assert payload["access_type"] == "public"
    data = payload["data"]
    assert data["detail_limited"] is True
    assert data["detail_limited_reason"] == "span_limit_exceeded"

    # The parent and child tree of the span rows: one root holding the other
    # 1,000 spans, every span once.
    [root] = data["observation_spans"]
    assert root["observation_span"]["id"] == ROOT_SPAN_ID
    assert len(root["children"]) == CHILD_COUNT
    entries = list(_entries(data["observation_spans"]))
    assert len(entries) == CHILD_COUNT + 1
    assert {entry["observation_span"]["id"] for entry in entries} == {
        span.id for span in reads.reader.spans
    }
    assert data["summary"] == {"total_spans": CHILD_COUNT + 1}

    # No Postgres trace row: the header comes from the root span.
    assert data["trace"]["id"] == TRACE_ID
    assert data["trace"]["project_id"] == str(project.id)
    assert data["trace"]["name"] == "agent run"
    assert data["trace"]["input"] == {"prompt": "plan the refund"}
    assert data["trace"]["created_at"] == STARTED.isoformat()

    # Spans only: no evaluations, rollups, annotations or graph.
    for entry in entries:
        assert set(entry) == {"observation_span", "children"}
    assert "graph" not in data

    # The full read ran first, pinned to the link's project; the span read
    # came after it, once, in the same project.
    reads.full_read.assert_called_once()
    assert reads.full_read.call_args.kwargs["project_id"] == str(project.id)
    assert reads.full_read.call_args.args[1].organization == project.organization
    assert reads.reader.calls == [(TRACE_ID, str(project.id))]


@pytest.mark.django_db
@pytest.mark.parametrize("code", ["eval_limit_exceeded", "annotation_limit_exceeded"])
def test_link_to_a_trace_over_the_eval_or_annotation_limit_opens_with_its_spans(
    api_client, user, project, reads, code
):
    reads.full_read.side_effect = TraceDetailReadUnavailable(code)
    link = _trace_link(user, project)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_200_OK, response.content
    data = response.json()["data"]
    assert data["detail_limited"] is True
    assert data["detail_limited_reason"] == code
    entries = list(_entries(data["observation_spans"]))
    assert len(entries) == CHILD_COUNT + 1
    for entry in entries:
        assert set(entry) == {"observation_span", "children"}


@pytest.mark.django_db
def test_restricted_link_to_a_large_trace_opens_for_its_creator_without_annotations(
    auth_client, user, project, reads
):
    link = _trace_link(user, project, access_type="restricted")

    with patch("tracer.views.shared_link._attach_shared_annotations") as attach:
        response = _resolve(auth_client, link)

    assert response.status_code == status.HTTP_200_OK, response.content
    payload = response.json()
    assert payload["access_type"] == "restricted"
    data = payload["data"]
    assert data["detail_limited"] is True
    entries = list(_entries(data["observation_spans"]))
    assert len(entries) == CHILD_COUNT + 1
    for entry in entries:
        assert set(entry) == {"observation_span", "children"}
    attach.assert_not_called()


@pytest.mark.django_db
def test_public_link_to_a_trace_within_the_limits_keeps_the_full_payload(
    api_client, user, project, reads
):
    detail = _canonical_detail(project.id)
    reads.full_read.side_effect = lambda *args, **kwargs: copy.deepcopy(detail)
    link = _trace_link(user, project)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_200_OK, response.content
    data = response.json()["data"]
    assert data["observation_spans"] == detail["observation_spans"]
    assert data["summary"] == detail["summary"]
    assert data["graph"] == detail["graph"]
    assert data["trace"]["id"] == TRACE_ID
    assert data["trace"]["project_id"] == str(project.id)
    assert "detail_limited" not in data
    assert "detail_limited_reason" not in data
    reads.full_read.assert_called_once()
    reads.get_reader.assert_not_called()


@pytest.mark.django_db
def test_public_link_to_a_trace_within_the_limits_carries_no_annotations(
    api_client, user, project, reads
):
    annotations = [{"label_id": "label-1", "name": "Tone", "value": "polite"}]
    detail = _canonical_detail(project.id, annotations=annotations)
    reads.full_read.side_effect = lambda *args, **kwargs: copy.deepcopy(detail)
    link = _trace_link(user, project)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_200_OK, response.content
    entries = list(_entries(response.json()["data"]["observation_spans"]))
    assert len(entries) == 2
    for entry in entries:
        assert "annotations" not in entry
        assert "shared_annotations" not in entry
    reads.get_reader.assert_not_called()


@pytest.mark.django_db
def test_link_to_a_trace_no_project_holds_is_not_found_without_any_span_read(
    api_client, user, project, reads
):
    reads.lookup.return_value = None
    link = _trace_link(user, project)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "no longer exists" in response.json()["detail"]
    _assert_no_span_read(reads)


@pytest.mark.django_db
def test_link_to_a_trace_the_full_read_cannot_find_is_not_found(
    api_client, user, project, reads
):
    reads.full_read.side_effect = Trace.DoesNotExist
    link = _trace_link(user, project)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "no longer exists" in response.json()["detail"]
    reads.full_read.assert_called_once()
    reads.get_reader.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "code",
    [
        "deadline_exceeded",
        "read_budget_exceeded",
        "clickhouse_query_failed",
        "ambiguous_span_identity",
    ],
)
def test_link_whose_full_read_fails_for_another_reason_is_not_found_as_before(
    api_client, user, project, reads, code
):
    reads.full_read.side_effect = TraceDetailReadUnavailable(code)
    link = _trace_link(user, project)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "no longer exists" in response.json()["detail"]
    reads.full_read.assert_called_once()
    reads.get_reader.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("fields", "expected_status"),
    [
        ({"is_active": False}, status.HTTP_410_GONE),
        ({"expires_at": datetime(2025, 1, 1, tzinfo=UTC)}, status.HTTP_410_GONE),
        ({"deleted": True}, status.HTTP_404_NOT_FOUND),
    ],
    ids=["revoked", "expired", "deleted"],
)
def test_revoked_expired_or_deleted_link_to_a_large_trace_reads_nothing(
    api_client, user, project, reads, fields, expected_status
):
    link = _trace_link(user, project, **fields)

    response = _resolve(api_client, link)

    assert response.status_code == expected_status
    assert "data" not in response.json()
    reads.lookup.assert_not_called()
    _assert_no_span_read(reads)


@pytest.mark.django_db
def test_restricted_link_to_a_large_trace_reads_nothing_for_a_signed_out_viewer(
    api_client, user, project, reads
):
    link = _trace_link(user, project, access_type="restricted")

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.data["code"] == "not_authenticated"
    assert "data" not in response.json()
    reads.lookup.assert_not_called()
    _assert_no_span_read(reads)


@pytest.mark.django_db
def test_restricted_link_to_a_large_trace_reads_nothing_for_a_viewer_not_invited(
    api_client, organization, user, project, reads
):
    link = _trace_link(user, project, access_type="restricted")
    stranger = User.objects.create_user(
        email="not-invited@example.com",
        password="testpassword123",
        name="Not Invited",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    api_client.force_authenticate(user=stranger)

    response = _resolve(api_client, link)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert "data" not in response.json()
    reads.lookup.assert_not_called()
    _assert_no_span_read(reads)
