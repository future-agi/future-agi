"""A restricted shared trace link carries the span annotations its owner sees.

On the View only page the owner saw a span's annotation and an invited
teammate from another organization saw "No annotations yet": the span pane
read ``/model-hub/scores/for-source/`` with the viewer's own session. The
shared trace read (``GET /tracer/shared/<token>/``) of a restricted ("Only
specific people") link now puts each span's scores and notes on its entry as
``shared_annotations``, read through the link only: the same access check,
the link's workspace, the shared trace's spans and project.

A public ("Anyone with the link") link carries no annotations at all: no
annotator names and no notes. A free-text value counts as a note, so the
``annotations`` the trace-detail builder puts on every span (label name and
value) is dropped from a public link's trace too, and from a public Error
Feed issue link's trace. Restricted links and the in-app trace detail keep
it.
"""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.constants.roles import OrganizationRoles
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.shared_link import SharedLink
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace

SPAN_SCORE_NOTE = "checked against the refund policy"


def _label(organization, workspace, project, name):
    return AnnotationsLabels.objects.create(
        name=f"{name} {uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=project,
    )


def _span_score(span_id, trace_id, label, annotator, rating, **scope):
    # Span scores carry the trace id as a denormalized locator, as the score
    # write stamps it.
    return Score.objects.create(
        source_type="observation_span",
        observation_span_id=span_id,
        trace_id=trace_id,
        label=label,
        annotator=annotator,
        value={"rating": rating},
        score_source="human",
        **scope,
    )


def _seeded_span(project, trace, name):
    from tracer.tests._ch_seed import seed_ch_span

    span = ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=name,
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi"}}]},
        latency_ms=500,
        status="OK",
    )
    seed_ch_span(span)
    return span


def _share(client, trace_id, access_type="restricted", emails=None):
    response = client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "trace",
            "resource_id": str(trace_id),
            "access_type": access_type,
            "emails": emails or [],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return response.json()["result"]


def _member(organization, email, name):
    return User.objects.create_user(
        email=email,
        password="testpassword123",
        name=name,
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


def _entries(payload):
    pending = list(payload["data"]["observation_spans"])
    found = {}
    while pending:
        entry = pending.pop()
        found[entry["observation_span"]["id"]] = entry
        pending.extend(entry.get("children") or [])
    return found


def _score_ids(entry):
    return {row["id"] for row in entry["shared_annotations"]["scores"]}


@pytest.fixture
def annotated(organization, workspace, user, project, trace, observation_span):
    """The shared trace's span with a span score and a trace score, and the
    rows the link must never carry: another trace's, another project's copy
    of the same span id, and another organization's."""
    label = _label(organization, workspace, project, "Quality")
    trace_label = _label(organization, workspace, project, "Overall")
    span_score = _span_score(
        observation_span.id,
        str(trace.id),
        label,
        user,
        4,
        notes=SPAN_SCORE_NOTE,
        organization=organization,
        workspace=workspace,
        tracer_project_id=project.id,
    )
    trace_score = Score.objects.create(
        source_type="trace",
        trace=trace,
        label=trace_label,
        annotator=user,
        value={"rating": 5},
        score_source="human",
        organization=organization,
        workspace=workspace,
        tracer_project_id=project.id,
    )

    other_trace = Trace.objects.create(project=project, name="Other Trace")
    other_span = _seeded_span(project, other_trace, "other span")
    other_trace_score = _span_score(
        other_span.id,
        str(other_trace.id),
        label,
        user,
        1,
        organization=organization,
        workspace=workspace,
        tracer_project_id=project.id,
    )

    # The same span id in another project of this organization.
    other_project = Project.objects.create(
        name="Replayed Copy",
        organization=organization,
        workspace=workspace,
        model_type=project.model_type,
        trace_type="observe",
    )
    replay_label = _label(organization, workspace, other_project, "Replay")
    other_project_score = _span_score(
        observation_span.id,
        str(trace.id),
        replay_label,
        user,
        2,
        organization=organization,
        workspace=workspace,
        tracer_project_id=other_project.id,
    )

    # The same span id in another organization.
    foreign_org = Organization.objects.create(name="Foreign Org")
    foreign_user = User.objects.create_user(
        email=f"foreign-{uuid.uuid4().hex[:8]}@example.com",
        password="testpassword123",
        name="Foreign Annotator",
        organization=foreign_org,
        organization_role=OrganizationRoles.OWNER,
    )
    foreign_workspace = Workspace.no_workspace_objects.create(
        name=f"Foreign Workspace {uuid.uuid4().hex[:8]}",
        organization=foreign_org,
        is_default=True,
        is_active=True,
        created_by=foreign_user,
    )
    foreign_label = AnnotationsLabels.no_workspace_objects.create(
        name=f"Foreign {uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=foreign_org,
        workspace=foreign_workspace,
    )
    foreign_score = _span_score(
        observation_span.id,
        str(trace.id),
        foreign_label,
        foreign_user,
        3,
        organization=foreign_org,
        workspace=foreign_workspace,
    )
    return {
        "span_score": span_score,
        "trace_score": trace_score,
        "other_trace": other_trace,
        "other_span": other_span,
        "other_trace_score": other_trace_score,
        "other_project_score": other_project_score,
        "foreign_user": foreign_user,
        "foreign_score": foreign_score,
    }


@pytest.mark.django_db
def test_restricted_link_carries_its_span_scores_only(
    api_client, auth_client, organization, trace, observation_span, annotated
):
    link = _share(auth_client, trace.id, emails=["listed-member@example.com"])
    viewer = _member(organization, "listed-member@example.com", "Listed Member")
    api_client.force_authenticate(user=viewer)

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")

    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    entry = _entries(resolved.json())[observation_span.id]
    # Not another trace's, another project's copy of the span, or another
    # organization's.
    assert _score_ids(entry) == {
        str(annotated["span_score"].id),
        str(annotated["trace_score"].id),
    }
    row = next(
        row
        for row in entry["shared_annotations"]["scores"]
        if row["id"] == str(annotated["span_score"].id)
    )
    assert row["source_type"] == "observation_span"
    assert row["source_id"] == observation_span.id
    assert row["label_name"] == annotated["span_score"].label.name
    assert row["label_type"] == AnnotationTypeChoices.STAR.value
    assert row["value"] == {"rating": 4}
    assert row["notes"] == SPAN_SCORE_NOTE
    assert row["annotator_name"] == "Test User"
    # Nothing beyond what the read-only tab shows leaves through a link.
    assert "annotator_email" not in row
    assert "queue_id" not in row
    assert "queue_item" not in row


@pytest.mark.django_db
def test_invited_viewer_from_another_org_sees_the_span_annotations(
    auth_client, trace, observation_span, annotated
):
    """The walk: a restricted link, opened by an invited teammate whose own
    session is another organization's."""
    from conftest import WorkspaceAwareAPIClient

    viewer_org = Organization.objects.create(name="Viewer Org")
    viewer = User.objects.create_user(
        email="shared-annotations-viewer@example.com",
        password="testpassword123",
        name="Invited Viewer",
        organization=viewer_org,
        organization_role=OrganizationRoles.OWNER,
    )
    viewer_workspace = Workspace.no_workspace_objects.create(
        name=f"Viewer Workspace {uuid.uuid4().hex[:8]}",
        organization=viewer_org,
        is_default=True,
        is_active=True,
        created_by=viewer,
    )
    link = _share(
        auth_client,
        trace.id,
        emails=["shared-annotations-viewer@example.com"],
    )

    viewer_client = WorkspaceAwareAPIClient()
    viewer_client.force_authenticate(user=viewer)
    viewer_client.set_workspace(viewer_workspace)
    try:
        resolved = viewer_client.get(f"/tracer/shared/{link['token']}/")
    finally:
        viewer_client.stop_workspace_injection()

    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    entry = _entries(resolved.json())[observation_span.id]
    assert str(annotated["span_score"].id) in _score_ids(entry)
    assert str(annotated["foreign_score"].id) not in _score_ids(entry)


@pytest.mark.django_db
def test_a_link_never_carries_another_traces_scores(
    auth_client, trace, observation_span, annotated
):
    other = _share(auth_client, annotated["other_trace"].id)

    # The creator may open their own restricted link.
    resolved = auth_client.get(f"/tracer/shared/{other['token']}/")

    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    entries = _entries(resolved.json())
    assert set(entries) == {annotated["other_span"].id}
    assert _score_ids(entries[annotated["other_span"].id]) == {
        str(annotated["other_trace_score"].id)
    }


@pytest.mark.django_db
def test_a_public_link_carries_no_annotations(
    api_client, auth_client, user, trace, observation_span, annotated
):
    """Ruling: annotator names and notes show only on restricted links; a
    public link shows neither, to a signed-out viewer or a signed-in one."""
    SpanNotes.objects.create(
        span_id=observation_span.id,
        notes="a span note that stays in the app",
        created_by_user=user,
        created_by_annotator=user.email,
    )
    link = _share(auth_client, trace.id, access_type="public")
    url = f"/tracer/shared/{link['token']}/"

    for resolved in (api_client.get(url), auth_client.get(url)):
        assert resolved.status_code == status.HTTP_200_OK, resolved.content
        assert resolved.json()["access_type"] == "public"
        entries = _entries(resolved.json())
        assert observation_span.id in entries
        for entry in entries.values():
            assert "shared_annotations" not in entry
        # No note text anywhere in the payload.
        body = resolved.content.decode()
        assert SPAN_SCORE_NOTE not in body
        assert "a span note that stays in the app" not in body


@pytest.mark.django_db
def test_revoked_and_expired_links_carry_no_annotations(
    api_client, auth_client, user, trace, observation_span, annotated
):
    revoked = _share(auth_client, trace.id)
    auth_client.delete(f"/tracer/shared-links/{revoked['id']}/")

    gone = auth_client.get(f"/tracer/shared/{revoked['token']}/")

    assert gone.status_code == status.HTTP_410_GONE
    assert "data" not in gone.json()

    expired = SharedLink.objects.create(
        resource_type="trace",
        resource_id=str(trace.id),
        access_type="restricted",
        created_by=user,
        organization=trace.project.organization,
        workspace=trace.project.workspace,
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    stale = auth_client.get(f"/tracer/shared/{expired.token}/")

    assert stale.status_code == status.HTTP_410_GONE
    assert "data" not in stale.json()

    unknown = api_client.get(f"/tracer/shared/{uuid.uuid4().hex}/")
    assert unknown.status_code == status.HTTP_404_NOT_FOUND
    assert "data" not in unknown.json()


@pytest.mark.django_db
def test_restricted_link_refuses_outsiders_before_any_annotation(
    api_client, auth_client, organization, trace, observation_span, annotated
):
    from unittest.mock import patch

    link = _share(auth_client, trace.id, emails=["listed-viewer@example.com"])
    url = f"/tracer/shared/{link['token']}/"
    outsider = _member(organization, "not-listed@example.com", "Not Listed")

    with patch("tracer.views.shared_link._attach_shared_annotations") as attach:
        signed_out = api_client.get(url)
        api_client.force_authenticate(user=outsider)
        refused = api_client.get(url)

    assert signed_out.status_code == status.HTTP_401_UNAUTHORIZED
    assert "data" not in signed_out.json()
    assert refused.status_code == status.HTTP_403_FORBIDDEN
    assert "data" not in refused.json()
    attach.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("verb", ["post", "put", "patch", "delete"])
def test_the_shared_read_has_no_write_verbs(
    auth_client, trace, observation_span, annotated, verb
):
    link = _share(auth_client, trace.id)
    before = Score.no_workspace_objects.filter(deleted=False).count()

    response = getattr(auth_client, verb)(
        f"/tracer/shared/{link['token']}/",
        data={"scores": [], "span_notes": "overwrite"},
        format="json",
    )

    assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED
    assert Score.no_workspace_objects.filter(deleted=False).count() == before


@pytest.mark.django_db
def test_shared_span_notes_show_by_name_and_only_from_the_links_org(
    auth_client, user, trace, observation_span, annotated
):
    SpanNotes.objects.create(
        span_id=observation_span.id,
        notes="checked the refund policy",
        created_by_user=user,
        created_by_annotator=user.email,
    )
    # A legacy note on the same span id by someone outside the link's
    # organization (span ids repeat across organizations).
    SpanNotes.objects.create(
        span_id=observation_span.id,
        notes="foreign note",
        created_by_user=annotated["foreign_user"],
        created_by_annotator=annotated["foreign_user"].email,
    )
    link = _share(auth_client, trace.id)

    resolved = auth_client.get(f"/tracer/shared/{link['token']}/")

    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    notes = _entries(resolved.json())[observation_span.id]["shared_annotations"][
        "span_notes"
    ]
    assert [(note["notes"], note["annotator"]) for note in notes] == [
        ("checked the refund policy", "Test User")
    ]


@pytest.mark.django_db
def test_a_failed_annotation_read_still_opens_the_trace(
    auth_client, trace, observation_span, annotated
):
    from unittest.mock import patch

    link = _share(auth_client, trace.id)
    with patch(
        "tracer.views.shared_link._attach_shared_annotations",
        side_effect=RuntimeError("annotation store unavailable"),
    ) as attach:
        resolved = auth_client.get(f"/tracer/shared/{link['token']}/")

    attach.assert_called_once()
    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    entry = _entries(resolved.json())[observation_span.id]
    assert "shared_annotations" not in entry


# --------------------------------------------------------------------------
# The builder's per-span ``annotations`` (label name and value)
# --------------------------------------------------------------------------


@pytest.fixture
def planted(organization, workspace, user, project, trace, observation_span):
    """A free-text annotation on the shared span, also in the ClickHouse
    mirror the trace-detail builder reads, so the builder puts its label name
    and value on the span entry as ``annotations``."""
    from tracer.tests._ch_seed import seed_ch_score

    label = AnnotationsLabels.objects.create(
        name=f"Refund wording {uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.TEXT.value,
        settings={"placeholder": "note", "max_length": 500, "min_length": 0},
        organization=organization,
        workspace=workspace,
        project=project,
    )
    text = f"promised a refund past the window {uuid.uuid4().hex[:8]}"
    score = Score.objects.create(
        source_type="observation_span",
        observation_span_id=observation_span.id,
        trace_id=str(trace.id),
        label=label,
        annotator=user,
        value={"text": text},
        score_source="human",
        organization=organization,
        workspace=workspace,
        tracer_project_id=project.id,
    )
    seed_ch_score(score)
    return {"label": label, "text": text, "score": score}


def _span_entries(spans):
    pending = list(spans or [])
    found = {}
    while pending:
        entry = pending.pop()
        found[entry["observation_span"]["id"]] = entry
        pending.extend(entry.get("children") or [])
    return found


def _assert_builder_annotation(entry, planted):
    """The span entry carries the planted annotation as the builder puts it."""
    assert {
        "label_id": str(planted["label"].id),
        "label_name": planted["label"].name,
        "label_type": AnnotationTypeChoices.TEXT.value,
        "value": {"text": planted["text"]},
    } in entry["annotations"]


def _assert_no_annotations(response, planted, span_id, spans):
    entries = _span_entries(spans)
    # The trace still opens with its span.
    assert span_id in entries
    for entry in entries.values():
        assert "annotations" not in entry
        assert "shared_annotations" not in entry
    # Neither the label name nor the value anywhere in the payload.
    body = response.content.decode()
    assert planted["label"].name not in body
    assert planted["text"] not in body


@pytest.mark.django_db
def test_a_public_link_carries_no_annotation_names_or_values(
    api_client, auth_client, trace, observation_span, planted
):
    """Ruling: a free-text value counts as a note. The page tells a
    signed-out viewer "This link does not include annotations", so a public
    link's payload carries no annotation label names or values at all, for a
    signed-out viewer or a signed-in one."""
    # Control: the owner's restricted link carries the planted annotation
    # from the builder, so its absence below is the public link's doing.
    restricted = _share(auth_client, trace.id)
    control = auth_client.get(f"/tracer/shared/{restricted['token']}/")
    assert control.status_code == status.HTTP_200_OK, control.content
    _assert_builder_annotation(_entries(control.json())[observation_span.id], planted)

    link = _share(auth_client, trace.id, access_type="public")
    url = f"/tracer/shared/{link['token']}/"

    for resolved in (api_client.get(url), auth_client.get(url)):
        assert resolved.status_code == status.HTTP_200_OK, resolved.content
        assert resolved.json()["access_type"] == "public"
        _assert_no_annotations(
            resolved,
            planted,
            observation_span.id,
            resolved.json()["data"]["observation_spans"],
        )


@pytest.mark.django_db
def test_a_restricted_link_still_carries_annotation_names_and_values(
    api_client, auth_client, organization, trace, observation_span, planted
):
    link = _share(auth_client, trace.id, emails=["planted-viewer@example.com"])
    viewer = _member(organization, "planted-viewer@example.com", "Planted Viewer")
    api_client.force_authenticate(user=viewer)

    resolved = api_client.get(f"/tracer/shared/{link['token']}/")

    assert resolved.status_code == status.HTTP_200_OK, resolved.content
    assert resolved.json()["access_type"] == "restricted"
    entry = _entries(resolved.json())[observation_span.id]
    _assert_builder_annotation(entry, planted)
    row = next(
        row
        for row in entry["shared_annotations"]["scores"]
        if row["id"] == str(planted["score"].id)
    )
    assert row["label_name"] == planted["label"].name
    assert row["value"] == {"text": planted["text"]}


@pytest.mark.django_db
def test_the_in_app_trace_detail_still_carries_annotations(
    api_client, auth_client, trace, observation_span, planted
):
    # Opening a public link first drops nothing from the app's own read.
    link = _share(auth_client, trace.id, access_type="public")
    shared = api_client.get(f"/tracer/shared/{link['token']}/")
    assert shared.status_code == status.HTTP_200_OK, shared.content

    response = auth_client.get(
        f"/tracer/trace/{trace.id}/", {"project_id": str(trace.project_id)}
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    entry = _span_entries(response.json()["result"]["observation_spans"])[
        observation_span.id
    ]
    _assert_builder_annotation(entry, planted)


@pytest.fixture
def error_feed_licensed():
    from unittest.mock import patch

    with patch("tfc.ee_gating.check_ee_feature", return_value=None) as gate:
        yield gate


def _error_issue_with_trace(project, trace, cluster_id="E-ANNOTATED"):
    from tracer.models.trace_error_analysis import (
        ErrorClusterTraces,
        TraceErrorGroup,
    )

    now = timezone.now()
    group = TraceErrorGroup.objects.create(
        project=project,
        cluster_id=cluster_id,
        target_type="error_feed",
        source="eval",
        error_type="Refund Policy Check",
        issue_group="Refund Policy Check",
        title="Agent promises refunds outside the policy window",
        first_seen=now - timedelta(days=2),
        last_seen=now,
        total_events=2,
        unique_traces=2,
    )
    for _ in range(2):
        ErrorClusterTraces.objects.create(cluster=group, trace_id=str(uuid.uuid4()))
    ErrorClusterTraces.objects.create(cluster=group, trace_id=str(trace.id))
    return group


def _issue_trace_url(client, group, trace, access_type):
    response = client.post(
        "/tracer/shared-links/",
        data={
            "resource_type": "error_feed_issue",
            "resource_id": group.cluster_id,
            "access_type": access_type,
            "emails": [],
        },
        format="json",
    )
    assert response.status_code == status.HTTP_201_CREATED, response.content
    return f"/tracer/shared/{response.json()['result']['token']}/traces/{trace.id}/"


@pytest.mark.django_db
def test_a_public_error_feed_issue_trace_carries_no_annotations(
    api_client,
    auth_client,
    project,
    trace,
    observation_span,
    planted,
    error_feed_licensed,
):
    """The Error Feed issue link's trace read follows the same rule: public
    carries no annotation names or values; restricted keeps them."""
    group = _error_issue_with_trace(project, trace)

    # The owner's restricted issue link keeps the builder's annotations.
    restricted = auth_client.get(
        _issue_trace_url(auth_client, group, trace, "restricted")
    )
    assert restricted.status_code == status.HTTP_200_OK, restricted.content
    _assert_builder_annotation(
        _span_entries(restricted.json()["result"]["observation_spans"])[
            observation_span.id
        ],
        planted,
    )

    url = _issue_trace_url(auth_client, group, trace, "public")
    for resolved in (api_client.get(url), auth_client.get(url)):
        assert resolved.status_code == status.HTTP_200_OK, resolved.content
        _assert_no_annotations(
            resolved,
            planted,
            observation_span.id,
            resolved.json()["result"]["observation_spans"],
        )
