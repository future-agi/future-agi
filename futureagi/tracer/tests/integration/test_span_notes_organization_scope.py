"""A span note is read only in the organization it was written in.

A span note carries no organization: it points at a span id, and a span id can
be held by projects of several organizations. Another organization can create
a span with an id it learned (a Langfuse batch naming it), and then every note
reader that keys on the span alone hands it this organization's notes, while
this organization's own read can resolve the id to the other copy and lose
them.

Each case lets one organization (the owner) write a note on a span, lets a
second organization (the sender) post the owner's span id through the Langfuse
upsert, then reads the notes as both: through the trace annotation values
(``_get_notes_from_pg``), the trace-detail Span Notes panel
(``/model-hub/scores/for-source/``) and an annotation queue item's notes
(``_item_note_payloads``; a queue item reads notes only through a Postgres span
of its own organization). The sender reads none of the owner's notes. The span
is held in Postgres in one case, and the owner still reads its own. In the
other only ClickHouse holds it: the upsert refuses the id there too, the owner
reads after the batch what it read before, and its Span Notes panel lists its
note both times. ClickHouse is a stand-in reader with one copy of the span per
project, and a stand-in answers the upsert's id check; Postgres runs for real.

Then who counts as the organization's author: a current member, a member who
has left (deactivated or removed), an annotator of one of its queues, but not
another organization's member. An author who belongs to both organizations is
read in both.

Last, notes the owner must keep, and the queue lookup for a source
(``/model-hub/annotation-queues/for-source/``): an account older than
memberships that later joins another organization, and an annotator whose
queue is deleted for good, wrote their notes in the owner's organization, so
the owner still reads them; the queue lookup lists an organization only its
own notes on a span id both hold (through a Postgres span of its own, as a
queue item reads them).
"""

import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from integrations.transformers.langfuse_transformer import LangfuseTransformer
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueStatusChoices,
    QueueItem,
)
from model_hub.views.annotation_queues import _item_note_payloads
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.services.clickhouse.v2.span_reader import SpanScope
from tracer.utils.langfuse_upsert import upsert_langfuse_trace
from tracer.views.annotation import TraceAnnotationView

pytestmark = pytest.mark.django_db

READER_PATH = "tracer.services.clickhouse.v2.get_reader"
FOR_SOURCE_URL = "/model-hub/scores/for-source/"
QUEUE_FOR_SOURCE_URL = "/model-hub/annotation-queues/for-source/"
START = "2026-11-13T09:00:00Z"
END = "2026-11-13T09:00:01Z"

OWNER_TRACE = "lfnotes-owner-trace"
SENDER_TRACE = "lfnotes-sender-trace"
SPAN = "lfnotes-owner-span"
OWNER_NOTE = "owner: refund approved after the second call"
SENDER_NOTE = "sender: escalated to billing"


def _organization(label):
    """An organization with its owner, default workspace and Observe project."""
    clear_workspace_context()
    org = Organization.objects.create(name=f"Synthetic {label}")
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=f"lfnotes-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="synthetic-password",
        name=f"Synthetic {label}",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=f"Synthetic {label} workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    set_workspace_context(workspace=workspace, organization=org, user=user)
    project = Project.objects.create(
        name=f"Synthetic {label} observe",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    clear_workspace_context()
    return SimpleNamespace(org=org, user=user, workspace=workspace, project=project)


@pytest.fixture
def owner(db):
    """The organization that wrote the note."""
    yield _organization("owner")
    clear_workspace_context()


@pytest.fixture
def sender(db):
    """The organization that posts the owner's span id."""
    yield _organization("sender")
    clear_workspace_context()


class _ClickHouseSpans:
    """``get_reader()`` stand-in: one copy of a span per project.

    It honours ``project_ids`` as the real read does. Unscoped, the real read
    keeps one arbitrary copy per id; here the newest write stands in for it.
    """

    def __init__(self):
        self.copies = []

    def add(self, span_id, project, version):
        self.copies.append(
            SimpleNamespace(
                id=span_id,
                project_id=str(project.id),
                trace_id=str(uuid.uuid4()),
                version=version,
            )
        )

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def scope_by_ids(self, span_ids, *, project_ids=None):
        wanted = {str(span_id) for span_id in span_ids}
        rows = sorted(self.copies, key=lambda row: row.version, reverse=True)
        scopes = {}
        for row in rows:
            if row.id not in wanted:
                continue
            if project_ids is not None and row.project_id not in project_ids:
                continue
            scopes.setdefault(
                row.id, SpanScope(project_id=row.project_id, trace_id=row.trace_id)
            )
        return scopes


class _ClickHouseSpanPages(_ClickHouseSpans):
    """The stand-in with the batch read a queue page makes: ``list_by_ids``,
    scoped to the project a queue item names, as the real read is."""

    def list_by_ids(self, span_ids, *, project_id=None, **_):
        wanted = {str(span_id) for span_id in span_ids}
        return [
            row
            for row in self.copies
            if row.id in wanted
            and (project_id is None or row.project_id == str(project_id))
        ]


@pytest.fixture
def clickhouse(monkeypatch):
    spans = _ClickHouseSpans()
    monkeypatch.setattr(READER_PATH, spans)
    return spans


def _upsert(organization, trace_id, span_name):
    """A Langfuse trace with one span ``SPAN`` into ``organization``'s project."""
    upsert_langfuse_trace(
        assembled_trace=deepcopy(
            {
                "id": trace_id,
                "name": f"{span_name} question",
                "observations": [
                    {
                        "id": SPAN,
                        "type": "SPAN",
                        "name": span_name,
                        "startTime": START,
                        "endTime": END,
                    }
                ],
                "scores": [],
            }
        ),
        transformer=LangfuseTransformer(),
        project_id=str(organization.project.id),
        org=organization.org,
        workspace=organization.workspace,
        org_id=organization.org.id,
    )


def _note(author, text, span_id=SPAN):
    return SpanNotes.objects.create(
        span_id=span_id,
        notes=text,
        created_by_user=author,
        created_by_annotator=author.email,
    )


def _annotation_values_notes(organization, span_id=SPAN):
    """The notes the trace annotation values return to ``organization``."""
    request = SimpleNamespace(
        organization=organization.org,
        workspace=organization.workspace,
        user=organization.user,
    )
    notes = TraceAnnotationView._get_notes_from_pg(request, span_id)
    return [note["notes"] for note in notes]


def _span_notes_panel(organization, span_id=SPAN):
    """The notes the trace-detail Span Notes panel lists for ``organization``."""
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=organization.user)
    client.set_workspace(organization.workspace)
    response = client.get(
        FOR_SOURCE_URL, {"source_type": "observation_span", "source_id": span_id}
    )
    assert response.status_code == 200, response.content
    return sorted(note["notes"] for note in response.data["span_notes"])


def _queue_item_notes(organization, span_id=SPAN):
    """The notes a queue item of ``organization`` on its own copy of the span
    lists."""
    queue = AnnotationQueue.no_workspace_objects.create(
        name=f"Synthetic review {uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization.org,
        workspace=organization.workspace,
        created_by=organization.user,
    )
    item = QueueItem.no_workspace_objects.create(
        queue=queue,
        source_type="observation_span",
        observation_span_id=span_id,
        organization=organization.org,
        workspace=organization.workspace,
    )
    # The notes target the queue resolves from ClickHouse: the copy in the
    # queue organization's own project.
    target = SimpleNamespace(id=span_id, project_id=str(organization.project.id))
    payloads = _item_note_payloads(item, target)
    return sorted(payload["notes"] for payload in payloads)


def _lookup_queue_item(organization, span_id=SPAN):
    """A queue item of ``organization`` on its own project's copy of the span,
    in a queue its owner made (so the owner is one of its annotators)."""
    queue = AnnotationQueue.no_workspace_objects.create(
        name=f"Synthetic lookup {uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization.org,
        workspace=organization.workspace,
        created_by=organization.user,
    )
    return QueueItem.no_workspace_objects.create(
        queue=queue,
        source_type="observation_span",
        observation_span_id=span_id,
        project=organization.project,
        organization=organization.org,
        workspace=organization.workspace,
    )


def _queue_lookup_notes(organization, span_id=SPAN):
    """The span notes the queue lookup for a source lists to ``organization``."""
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=organization.user)
    client.set_workspace(organization.workspace)
    response = client.get(
        QUEUE_FOR_SOURCE_URL,
        {"source_type": "observation_span", "source_id": span_id},
    )
    assert response.status_code == 200, response.content
    return sorted(
        note["notes"]
        for entry in response.data["result"]
        for note in entry["span_notes"]
    )


def _user(label, organization=None):
    return User.objects.create_user(
        email=f"lfnotes-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="synthetic-password",
        name=f"Synthetic {label}",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


def _membership(user, organization, **fields):
    return OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization.org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        **fields,
    )


# ---------------------------------------------------------------------------
# The sender posts the owner's span id; then both read the notes.
# ---------------------------------------------------------------------------


def test_notes_on_a_span_held_in_postgres_stay_with_the_owner(
    owner, sender, clickhouse
):
    _upsert(owner, OWNER_TRACE, "owner step")
    clickhouse.add(SPAN, owner.project, version=10)
    _note(owner.user, OWNER_NOTE)

    # The sender's batch names the owner's span id. The upsert refuses it, so
    # the span stays in the owner's project.
    _upsert(sender, SENDER_TRACE, "sender content")
    span = ObservationSpan.no_workspace_objects.get(id=SPAN)
    assert span.project_id == owner.project.id

    assert _annotation_values_notes(sender) == []
    assert _span_notes_panel(sender) == []
    assert _queue_item_notes(sender) == []
    assert _annotation_values_notes(owner) == [OWNER_NOTE]
    assert _span_notes_panel(owner) == [OWNER_NOTE]
    assert _queue_item_notes(owner) == [OWNER_NOTE]


# The name the Langfuse upsert calls for its ClickHouse read client.
UPSERT_ID_CHECK_PATH = "tracer.utils.langfuse_upsert.get_v2_query_client"


class _UpsertIdCheck:
    """Stands in for the ClickHouse read client the Langfuse upsert asks which
    incoming span ids another project holds there: it holds ``SPAN`` in one
    project. ``reads`` keeps the parameters of every read."""

    def __init__(self, project):
        self.holder = str(project.id)
        self.reads = []

    def execute_read(self, query, params=None, timeout_ms=None, settings=None):
        self.reads.append(dict(params or {}))
        if str(params["project_id"]) == self.holder:
            return [], [("id", "String")], 0.0
        rows = [(span_id,) for span_id in params["ids"] if span_id == SPAN]
        return rows, [("id", "String")], 0.0


def test_notes_on_a_span_held_only_in_clickhouse_stay_with_the_owner(
    owner, sender, clickhouse, monkeypatch
):
    # The owner's span came in through the collector: ClickHouse holds it,
    # Postgres has no row for it. The owner's note points at its id.
    clickhouse.add(SPAN, owner.project, version=10)
    id_check = _UpsertIdCheck(owner.project)
    monkeypatch.setattr(UPSERT_ID_CHECK_PATH, lambda: id_check)
    _note(owner.user, OWNER_NOTE)
    owner_before = (
        _annotation_values_notes(owner),
        _span_notes_panel(owner),
        _queue_item_notes(owner),
    )
    # Before the batch, the owner's Span Notes panel lists its note. (The trace
    # annotation values and the queue item read notes only through a Postgres
    # span of the reader's own, and this span has none.)
    assert owner_before[1] == [OWNER_NOTE]

    # The sender's batch names that id. The upsert asks ClickHouse, which
    # holds it in the owner's project, so the id is refused: no Postgres span
    # takes it, in the sender's project or anywhere.
    _upsert(sender, SENDER_TRACE, "sender content")
    assert any(SPAN in read["ids"] for read in id_check.reads)
    assert not ObservationSpan._base_manager.filter(id=SPAN).exists()

    assert _annotation_values_notes(sender) == []
    assert _span_notes_panel(sender) == []
    assert _queue_item_notes(sender) == []
    # The owner reads after the batch what it read before it: its Span Notes
    # panel still lists its note.
    owner_after = (
        _annotation_values_notes(owner),
        _span_notes_panel(owner),
        _queue_item_notes(owner),
    )
    assert owner_after == owner_before
    assert owner_after[1] == [OWNER_NOTE]


# ---------------------------------------------------------------------------
# Whose notes an organization reads. The owner reads through its Span Notes
# panel; the sender, whose project holds the span in Postgres, through a queue
# item.
# ---------------------------------------------------------------------------


@pytest.fixture
def shared_span(owner, sender, clickhouse):
    """``SPAN`` held by both: the owner's copy in ClickHouse (the newest write)
    and the sender's in Postgres and ClickHouse."""
    _upsert(sender, SENDER_TRACE, "sender step")
    clickhouse.add(SPAN, sender.project, version=10)
    clickhouse.add(SPAN, owner.project, version=20)


@pytest.mark.usefixtures("shared_span")
def test_current_and_departed_members_notes_stay_with_the_organization(owner):
    current = _user("current", owner.org)
    _membership(current, owner)
    deactivated = _user("deactivated", owner.org)
    _membership(deactivated, owner, is_active=False)
    removed = _user("removed", owner.org)
    row = _membership(removed, owner)
    OrganizationMembership._base_manager.filter(id=row.id).update(
        deleted=True, is_active=False
    )
    _note(current, "current member")
    _note(deactivated, "deactivated member")
    _note(removed, "removed member")

    assert _span_notes_panel(owner) == [
        "current member",
        "deactivated member",
        "removed member",
    ]


@pytest.mark.usefixtures("shared_span")
def test_another_organizations_member_is_not_read(owner, sender):
    _note(owner.user, OWNER_NOTE)
    _note(sender.user, "sender's own note")

    assert _span_notes_panel(owner) == [OWNER_NOTE]
    assert _queue_item_notes(sender) == ["sender's own note"]


@pytest.mark.usefixtures("shared_span")
def test_an_annotator_of_the_organizations_queue_is_read_there_only(owner, sender):
    # An account with no membership anywhere, annotating one of the owner's
    # queues.
    annotator = _user("annotator")
    queue = AnnotationQueue.no_workspace_objects.create(
        name="Synthetic external review",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=owner.org,
        workspace=owner.workspace,
        created_by=owner.user,
    )
    AnnotationQueueAnnotator.no_workspace_objects.create(queue=queue, user=annotator)
    _note(annotator, "annotator")

    assert _span_notes_panel(owner) == ["annotator"]
    assert _queue_item_notes(sender) == []


@pytest.mark.usefixtures("shared_span")
def test_an_author_in_both_organizations_is_read_in_both(owner, sender):
    # A person with an active membership in each organization. Their note is
    # read in both: the rule knows the author, not which organization the
    # note was written for.
    both = _user("both", owner.org)
    _membership(both, owner)
    _membership(both, sender)
    _note(both, "written by a member of both")

    assert _span_notes_panel(owner) == ["written by a member of both"]
    assert _queue_item_notes(sender) == ["written by a member of both"]


# ---------------------------------------------------------------------------
# Notes the owner must keep, read through its Span Notes panel. Each note is
# read first, so a later miss is the change's doing.
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("shared_span")
def test_an_account_older_than_memberships_keeps_its_notes_after_joining_elsewhere(
    owner, sender
):
    # An account with no membership row, whose stored organization is the
    # owner's, writes a note there.
    legacy = _user("legacy", owner.org)
    _note(legacy, "written before memberships")
    assert _span_notes_panel(owner) == ["written before memberships"]

    # Later the account joins another organization.
    _membership(legacy, sender)

    # The note was written in the owner's organization; the owner keeps it.
    assert _span_notes_panel(owner) == ["written before memberships"]


@pytest.mark.usefixtures("shared_span")
def test_an_account_stored_in_another_organization_is_not_read_here(owner, sender):
    # An account with no membership row anywhere, whose stored organization
    # is the sender's, writes a note on the owner's span id.
    elsewhere = _user("stored-elsewhere", sender.org)
    _note(elsewhere, "written from elsewhere")

    # The owner does not read it.
    assert _span_notes_panel(owner) == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Stated limit: a span note carries no organization, so once a queue is "
        "permanently deleted its annotator rows go with it and the notes of an "
        "author whose only tie was that queue leave the owner's view. A tenancy "
        "column on span notes is the fix."
    ),
)
@pytest.mark.usefixtures("shared_span")
def test_an_annotator_only_authors_note_stays_after_the_queue_is_deleted(owner):
    # An account with no membership anywhere, annotating one of the owner's
    # queues, writes a note.
    annotator = _user("annotator")
    queue = AnnotationQueue.no_workspace_objects.create(
        name="Synthetic external review",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=owner.org,
        workspace=owner.workspace,
        created_by=owner.user,
    )
    AnnotationQueueAnnotator.no_workspace_objects.create(queue=queue, user=annotator)
    _note(annotator, "annotator")
    assert _span_notes_panel(owner) == ["annotator"]

    # The queue is deleted for good, by the same call the queue's permanent
    # delete makes.
    AnnotationQueue.all_objects.filter(pk=queue.pk).delete()

    # The note was written in the owner's organization; the owner keeps it.
    assert _span_notes_panel(owner) == ["annotator"]


# ---------------------------------------------------------------------------
# The queue lookup for a source. A queue lists a legacy note only through a
# Postgres span in its own organization's project, and one organization's
# project holds the span id's Postgres row: each holder in turn.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("holder", ["owner", "sender"], ids=["owner-row", "sender-row"])
def test_the_queue_lookup_for_a_span_lists_only_the_organizations_notes(
    owner, sender, monkeypatch, holder
):
    spans = _ClickHouseSpanPages()
    monkeypatch.setattr(READER_PATH, spans)
    holding = owner if holder == "owner" else sender
    _upsert(holding, f"lfnotes-{holder}-lookup-trace", f"{holder} step")
    span = ObservationSpan.no_workspace_objects.get(id=SPAN)
    assert span.project_id == holding.project.id
    # Both organizations' projects hold the span id in ClickHouse, and each
    # organization has a queue item on its own copy.
    spans.add(SPAN, owner.project, version=10)
    spans.add(SPAN, sender.project, version=20)
    _lookup_queue_item(owner)
    _lookup_queue_item(sender)
    # The lookup leaves out the requester's own note, so a colleague of each
    # requester writes one.
    owner_colleague = _user("owner-colleague", owner.org)
    _membership(owner_colleague, owner)
    sender_colleague = _user("sender-colleague", sender.org)
    _membership(sender_colleague, sender)
    _note(owner_colleague, OWNER_NOTE)
    _note(sender_colleague, SENDER_NOTE)

    if holder == "owner":
        assert _queue_lookup_notes(owner) == [OWNER_NOTE]
        assert _queue_lookup_notes(sender) == []
    else:
        assert _queue_lookup_notes(sender) == [SENDER_NOTE]
