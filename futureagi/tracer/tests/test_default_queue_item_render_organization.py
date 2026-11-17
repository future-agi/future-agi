"""A queue item shows only a source of its own organization.

Before annotations added from the trace view stayed in the request's
organization, a person who belongs to two organizations could annotate a
span of the second organization's project (B) while their account named the
first (A). That made a default queue of A over B's project, holding an item
whose project is B's, with the score and the note in A. These tests make
the queue, the item and a note directly and pin what the queue's readers
show:

- the item shows the unavailable-source answer and nothing of B's span, its
  trace or its project, in its content, its preview (live or captured), the
  items list, the annotate view and the whole-item notes target, to the
  person who wrote on it and to another member of A, whichever workspace the
  queue was saved with; the same span on B's own queue still renders;
- an item whose project is its own organization's renders as before, in the
  queue's workspace, and where the queue was saved with no workspace or the
  project has none;
- on a clean database, a person in both organizations who annotates a span
  of each from that span's organization gets one default queue per project,
  each in its own organization, on every annotation, and A's queue list
  names no queue after B's project.

ClickHouse is faked with one copy of each span, in its own project. Postgres,
the helpers, the serializers and the endpoints run for real.
"""

from types import SimpleNamespace

import pytest
from rest_framework import status

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import AnnotationQueueStatusChoices
from model_hub.serializers.annotation_queues import QueueItemSerializer
from model_hub.tests.test_annotation_queues_api import create_workspace_member_user
from model_hub.tests.test_queue_item_source_copy_scope import _Copies, _copy
from model_hub.utils.annotation_queue_helpers import (
    CollectorSourceCache,
    QueueSourceAuthorization,
    _queue_item_source_project_ids,
    preview_payload_for_source,
    resolve_source_content,
    resolve_source_preview,
)
from model_hub.views.annotation_queues import _span_notes_target_for_queue_item
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.project import Project
from tracer.tests.test_add_annotations_request_organization import (
    _annotate,
    _project,
    _second_organization,
    _span,
    _star_label,
)

QUEUE_URL = "/model-hub/annotation-queues/"

UNAVAILABLE = {
    "type": "observation_span",
    "deleted": True,
    "error": "queue_source_unavailable",
}


@pytest.fixture
def span_copies(monkeypatch):
    """The faked ClickHouse: ``(version, CHSpan)`` rows the test adds."""
    rows = []
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.get_reader", lambda: _Copies(rows)
    )
    return rows


def _hold(span_copies, project, span):
    """Put one copy of ``span`` in ``project``; its name and input carry the
    project's name."""
    copy = _copy(project, trace_id=str(span.trace_id), span_id=str(span.id), second=0)
    span_copies.append((len(span_copies) + 1, copy))
    return copy


def _saved(row):
    # Every row passes its model's clean() before it is written.
    row.clean()
    row.save()
    row.refresh_from_db()
    return row


def _first_organization(organization, workspace):
    """The organization stored on the account (A), with a project, a span
    and a star label in its default workspace."""
    project = _project(organization, workspace, "First")
    return SimpleNamespace(
        org=organization,
        workspace=workspace,
        project=project,
        span=_span(project, "First"),
        label=_star_label(organization, workspace, project),
    )


def _stamp_workspace(row, workspace):
    """Keep ``workspace`` (none included) on a saved row, whatever saving
    stamped, as rows made before workspaces were checked carry it."""
    type(row).no_workspace_objects.filter(pk=row.pk).update(workspace=workspace)
    row.refresh_from_db()
    assert row.workspace_id == getattr(workspace, "id", None)
    return row


def _queue_item(organization, workspace, project, span, user, *, preview=None):
    """A default queue of ``organization`` over ``project`` holding ``span``,
    as the default-queue path saves it: the item carries the queue's
    organization and workspace, and the project as its own."""
    queue = _saved(
        AnnotationQueue(
            name=f"Default - {project.name}",
            description=f"Default annotation queue for {project.name}",
            is_default=True,
            status=AnnotationQueueStatusChoices.ACTIVE.value,
            organization=organization,
            workspace=workspace,
            project=project,
            created_by=user,
        )
    )
    item = _saved(
        QueueItem(
            queue=queue,
            source_type="observation_span",
            observation_span_id=str(span.id),
            organization=organization,
            workspace=workspace,
            project=project,
            order=1,
            source_preview=preview,
        )
    )
    return _stamp_workspace(queue, workspace), _stamp_workspace(item, workspace)


def _request(user, organization, workspace):
    return SimpleNamespace(user=user, organization=organization, workspace=workspace)


def _shows_copy(payload, project):
    assert payload.get("deleted") is not True, payload
    assert payload.get("error") is None, payload
    assert payload["name"] == f"{project.name} copy", payload


def _shows_nothing_of(payload, side):
    assert payload == UNAVAILABLE, payload
    text = str(payload)
    for marker in (
        f"{side.project.name} copy",
        f"{side.project.name} input",
        side.project.name,
        str(side.project.id),
        str(side.span.id),
        str(side.span.trace_id),
    ):
        assert marker not in text, (marker, payload)


def _single_item_reads(item, request):
    """Every single-item read of the item's source, freshly loaded."""
    fresh = QueueItem.no_workspace_objects.get(pk=item.pk)
    page = CollectorSourceCache.for_items([fresh], request=request)
    return {
        "content": resolve_source_content(fresh, request=request),
        "preview": resolve_source_preview(fresh, request=request),
        "page content": resolve_source_content(fresh, ch_cache=page, request=request),
        "page preview": resolve_source_preview(fresh, ch_cache=page, request=request),
        "serializer preview": QueueItemSerializer(
            fresh, context={"request": request}
        ).data["source_preview"],
    }


@pytest.mark.django_db
@pytest.mark.integration
class TestQueueItemShowsOnlyItsOwnOrganization:
    @pytest.mark.parametrize(
        "queue_workspace, captured",
        [
            pytest.param("first", False, id="first-workspace-live"),
            pytest.param("first", True, id="first-workspace-captured"),
            pytest.param("none", False, id="no-workspace-live"),
            pytest.param("none", True, id="no-workspace-captured"),
            pytest.param("second", False, id="second-workspace-live"),
            pytest.param("second", True, id="second-workspace-captured"),
        ],
    )
    def test_item_over_another_organizations_project_shows_nothing_of_it(
        self,
        auth_client,
        user,
        organization,
        workspace,
        span_copies,
        queue_workspace,
        captured,
    ):
        second = _second_organization(user)
        copy = _hold(span_copies, second.project, second.span)
        # Rows saved with no workspace keep none.
        clear_workspace_context()

        # Control: the same span on the second organization's own queue
        # renders, so the copy is there to be shown.
        own_queue = _saved(
            AnnotationQueue(
                name=f"Second organization queue {second.project.name}",
                status=AnnotationQueueStatusChoices.ACTIVE.value,
                organization=second.org,
                workspace=second.workspace,
                created_by=user,
            )
        )
        own_item = _saved(
            QueueItem(
                queue=own_queue,
                source_type="observation_span",
                observation_span_id=str(second.span.id),
                organization=second.org,
                workspace=second.workspace,
                project=second.project,
                order=1,
            )
        )
        in_second = _request(user, second.org, second.workspace)
        for name, payload in _single_item_reads(own_item, in_second).items():
            assert payload.get("deleted") is not True, (name, payload)
            _shows_copy(payload, second.project)

        # The first organization's default queue over the second
        # organization's project, with its item and the note written there,
        # as the trace view used to save them. The workspace the queue was
        # saved with: the first organization's default one, none, or the
        # second organization's (the project's own).
        stamp = {
            "first": workspace,
            "none": None,
            "second": second.workspace,
        }[queue_workspace]
        queue, item = _queue_item(
            organization,
            stamp,
            second.project,
            second.span,
            user,
            preview=(
                preview_payload_for_source("observation_span", copy)
                if captured
                else None
            ),
        )
        _stamp_workspace(
            _saved(
                QueueItemNote(
                    queue_item=item,
                    annotator=user,
                    notes="note written in the second organization",
                    organization=organization,
                    workspace=stamp,
                )
            ),
            stamp,
        )
        assert queue.organization_id == item.organization_id == organization.id
        assert second.project.organization_id == second.org.id != organization.id
        assert item.project_id == queue.project_id == second.project.id
        assert queue.workspace_id == item.workspace_id == getattr(stamp, "id", None)
        assert (item.source_preview is not None) is captured

        # The person who wrote on it, and a member of the first
        # organization only, each working in the first organization.
        member = create_workspace_member_user(organization, workspace)
        clear_workspace_context()
        for viewer in (user, member):
            request = _request(viewer, organization, workspace)
            fresh = QueueItem.no_workspace_objects.get(pk=item.pk)
            assert _queue_item_source_project_ids(fresh) == []
            assert (
                QueueSourceAuthorization.for_items(
                    [fresh], request=request
                ).project_ids(fresh)
                == set()
            )
            for name, payload in _single_item_reads(item, request).items():
                assert payload == UNAVAILABLE, (name, payload)
                _shows_nothing_of(payload, second)
            assert _span_notes_target_for_queue_item(fresh, request=request) is None

        # The endpoints, as the person who wrote on it, in the first
        # organization's workspace. Across a whole response only the span's
        # own text is looked for: the queue keeps the name it was saved with
        # ("Default - " and the second project's name), and the item keeps
        # its own locator. The source fields are checked in full below.
        item_url = f"{QUEUE_URL}{queue.pk}/items/"
        detail = auth_client.get(f"{item_url}{item.pk}/annotate-detail/")
        listed = auth_client.get(item_url, {"limit": 50})
        for response in (detail, listed):
            for marker in (
                f"{second.project.name} copy",
                f"{second.project.name} input",
            ):
                assert marker not in str(response.data), (marker, response.data)
        if queue_workspace != "second":
            # A queue saved in the first organization's reach opens there.
            assert detail.status_code == status.HTTP_200_OK, detail.data
            body = detail.data.get("result", detail.data)["item"]
            _shows_nothing_of(body["source_content"], second)
            _shows_nothing_of(body["source_preview"], second)
            assert listed.status_code == status.HTTP_200_OK, listed.data
            previews = [row["source_preview"] for row in listed.data["results"]]
            assert previews == [UNAVAILABLE]

    def test_item_in_its_own_organization_and_workspace_renders_as_before(
        self, user, organization, workspace, span_copies
    ):
        first = _first_organization(organization, workspace)
        _hold(span_copies, first.project, first.span)
        clear_workspace_context()
        _queue, item = _queue_item(
            organization, workspace, first.project, first.span, user
        )
        member = create_workspace_member_user(organization, workspace)
        clear_workspace_context()

        for viewer in (user, member):
            request = _request(viewer, organization, workspace)
            fresh = QueueItem.no_workspace_objects.get(pk=item.pk)
            assert _queue_item_source_project_ids(fresh) == [str(first.project.id)]
            for name, payload in _single_item_reads(item, request).items():
                assert payload.get("deleted") is not True, (name, payload)
                _shows_copy(payload, first.project)
            target = _span_notes_target_for_queue_item(fresh, request=request)
            assert target is not None
            assert target.project_id == str(first.project.id)

    @pytest.mark.parametrize(
        "shape",
        [
            pytest.param("queue-without-workspace", id="queue-without-workspace"),
            pytest.param("project-without-workspace", id="project-without-workspace"),
        ],
    )
    def test_item_whose_queue_and_project_workspaces_differ_renders_as_before(
        self, user, organization, workspace, span_copies, shape
    ):
        # Same organization. A queue saved with no workspace is read in its
        # organization's default workspace, which also reaches a project
        # saved with none.
        project = _project(organization, workspace, "First")
        if shape == "project-without-workspace":
            Project.no_workspace_objects.filter(pk=project.pk).update(workspace=None)
            project.refresh_from_db()
        span = _span(project, "First")
        _hold(span_copies, project, span)
        clear_workspace_context()
        queue, item = _queue_item(
            organization,
            None if shape == "queue-without-workspace" else workspace,
            project,
            span,
            user,
        )
        assert queue.workspace_id != project.workspace_id, (
            "setup: the queue and the project are saved with different workspaces"
        )

        request = _request(user, organization, workspace)
        for name, payload in _single_item_reads(item, request).items():
            assert payload.get("deleted") is not True, (name, payload)
            _shows_copy(payload, project)


@pytest.mark.django_db
@pytest.mark.integration
class TestOneDefaultQueuePerProjectInItsOwnOrganization:
    @pytest.mark.parametrize(
        "order",
        [
            pytest.param(("first", "second"), id="first-organization-first"),
            pytest.param(("second", "first"), id="second-organization-first"),
        ],
    )
    def test_member_of_two_organizations_gets_one_default_queue_per_project(
        self, auth_client, user, organization, workspace, order
    ):
        sides = {
            "first": _first_organization(organization, workspace),
            "second": _second_organization(user),
        }
        assert user.organization_id == organization.id

        # Two rounds: the first makes each project's default queue, the
        # second finds it again.
        for round_number in (1, 2):
            for name in order:
                side = sides[name]
                auth_client.set_workspace(side.workspace)
                response = _annotate(
                    auth_client,
                    side.span,
                    side.label,
                    5 - round_number,
                    f"{name} organization note {round_number}",
                )
                assert response.status_code == status.HTTP_200_OK, (
                    name,
                    round_number,
                    response.data,
                )
                assert response.data["result"]["success_labels"] == [str(side.label.id)]
                assert response.data["result"]["failed_labels"] == []

        for side in sides.values():
            queues = list(
                AnnotationQueue.no_workspace_objects.filter(
                    project=side.project, is_default=True
                )
            )
            assert len(queues) == 1, side.project.name
            assert queues[0].organization_id == side.org.id
            item = QueueItem.no_workspace_objects.get(observation_span_id=side.span.id)
            assert item.queue_id == queues[0].id
            assert item.organization_id == side.org.id
            assert item.project_id == side.project.id

    def test_first_organizations_queue_list_names_no_queue_after_second_project(
        self, auth_client, user, organization, workspace
    ):
        first = _first_organization(organization, workspace)
        second = _second_organization(user)
        for side in (first, second):
            auth_client.set_workspace(side.workspace)
            response = _annotate(auth_client, side.span, side.label, 4, "a note")
            assert response.status_code == status.HTTP_200_OK, response.data

        names = {}
        for name, side in (("first", first), ("second", second)):
            auth_client.set_workspace(side.workspace)
            listed = auth_client.get(QUEUE_URL)
            assert listed.status_code == status.HTTP_200_OK, listed.data
            names[name] = [row["name"] for row in listed.data["results"]]

        assert f"Default - {first.project.name}" in names["first"]
        assert [n for n in names["first"] if second.project.name in n] == []
        assert f"Default - {second.project.name}" in names["second"]
        assert [n for n in names["second"] if first.project.name in n] == []
