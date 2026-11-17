"""A Langfuse id already stored in another project stays where it is.

Observation ids, the synthetic root id ("root-" + the Langfuse trace id) and
score ids are chosen by the sender, and one table holds the spans of every
project. When a batch names an id that another organization already stored,
that organization's row must keep its project, its trace and its content. The
sender's observation (or root, or score) is refused and counted, and the rest
of the sender's batch is stored as usual. The same id sent again into its own
project still updates in place, and a new id is still created.

Every case runs through ``upsert_langfuse_trace``, which both the live
ingestion endpoint and the Langfuse import call; some cases post to the live
endpoint and one runs an import sync. The tests build their own two
organizations and read none of this directory's committed corpus, so they do
not need ClickHouse.

The upsert also asks ClickHouse which span ids another project holds there:
the collector writes most spans to ClickHouse only. tracer/conftest.py answers
"none" for every test; the cases at the end of this file answer with
``ClickHouseStandIn`` instead.
"""

import json
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from integrations.models import (
    ConnectionStatus,
    IntegrationConnection,
    IntegrationPlatform,
    SyncLog,
    SyncStatus,
)
from integrations.temporal.activities import sync_integration_connection
from integrations.transformers.langfuse_transformer import LangfuseTransformer
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_annotation import TraceAnnotation
from tracer.utils.langfuse_upsert import upsert_langfuse_trace

pytestmark = pytest.mark.django_db

START = "2026-11-13T09:00:00Z"
END = "2026-11-13T09:00:01Z"

# The owner's rows, stored first in the owner's project.
OWNER_TRACE = "lfscope-owner-trace"
OWNER_ROOT = f"root-{OWNER_TRACE}"
OWNER_SPAN = "lfscope-owner-span"
OWNER_SCORE = "lfscope-owner-score"

# The sender's own rows, new ids in the sender's project.
SENDER_TRACE = "lfscope-sender-trace"
SENDER_ROOT = f"root-{SENDER_TRACE}"
SENDER_SPAN = "lfscope-sender-span"
SENDER_SCORE = "lfscope-sender-score"

# A span held by another project of the sender's own organization.
ARCHIVE_TRACE = "lfscope-archive-trace"
ARCHIVE_SPAN = "lfscope-archive-span"

# The sender's scores on spans another project holds.
SCORE_ON_REFUSED = "lfscope-sender-score-on-refused"
SCORE_ON_HELD = "lfscope-sender-score-on-held"

# Ids made of digits, which a batch can send as JSON numbers.
NUMBER_TRACE = "lfscope-number-trace"
NUMBER_SPAN = "4242"
NUMBER_SCORE = "4343"

REFUSED_MESSAGE = "Not stored: the id is already used in another project."
REFUSED_ROOT_MESSAGE = (
    "Stored without its root span: the trace id is already used in another project."
)
IMPORT_REFUSED_NOTE = (
    "1 row was not imported: its id is already used in another project."
)


def _organization(label):
    """An organization with its owner, default workspace and Observe project."""
    clear_workspace_context()
    org = Organization.objects.create(name=f"Synthetic {label}")
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=f"lfscope-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
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
    """The organization whose rows are already stored."""
    yield _organization("owner")
    clear_workspace_context()


@pytest.fixture
def sender(db):
    """The organization whose batch names the owner's ids."""
    yield _organization("sender")
    clear_workspace_context()


def _observation(obs_id, name):
    return {
        "id": obs_id,
        "type": "SPAN",
        "name": name,
        "input": f"{name} input",
        "output": f"{name} output",
        "startTime": START,
        "endTime": END,
    }


def _score(score_id, observation_id, value):
    return {
        "id": score_id,
        "name": "helpfulness",
        "dataType": "NUMERIC",
        "value": value,
        "observationId": observation_id,
    }


def _trace(trace_id, name, observations=(), scores=()):
    return {
        "id": trace_id,
        "name": name,
        "input": f"{name} input",
        "output": f"{name} output",
        "observations": list(observations),
        "scores": list(scores),
    }


def _upsert(organization, body):
    return upsert_langfuse_trace(
        assembled_trace=deepcopy(body),
        transformer=LangfuseTransformer(),
        project_id=str(organization.project.id),
        org=organization.org,
        workspace=organization.workspace,
        org_id=organization.org.id,
    )


def _store_owner_rows(owner):
    """The owner's trace: one span, its synthetic root and one score."""
    _upsert(
        owner,
        _trace(
            OWNER_TRACE,
            "owner question",
            [_observation(OWNER_SPAN, "owner step")],
            [_score(OWNER_SCORE, OWNER_SPAN, 0.9)],
        ),
    )
    return SimpleNamespace(
        span=ObservationSpan.no_workspace_objects.get(id=OWNER_SPAN),
        root=ObservationSpan.no_workspace_objects.get(id=OWNER_ROOT),
        score=EvalLogger.no_workspace_objects.get(eval_id=OWNER_SCORE),
    )


def _assert_owner_span_unchanged(owned):
    span = ObservationSpan.no_workspace_objects.get(id=OWNER_SPAN)
    assert (span.project_id, span.trace_id) == (
        owned.span.project_id,
        owned.span.trace_id,
    )
    assert (span.name, span.input, span.output) == (
        "owner step",
        "owner step input",
        "owner step output",
    )


def _refused(result):
    """The rows the upsert refused: the element after the five counts."""
    return result[5]


def test_a_span_id_stored_in_another_project_is_refused_and_the_rest_stored(
    owner, sender
):
    owned = _store_owner_rows(owner)

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [
                _observation(OWNER_SPAN, "sender content"),
                _observation(SENDER_SPAN, "sender step"),
            ],
        ),
    )

    # The owner's span keeps its project, its trace and its content.
    _assert_owner_span_unchanged(owned)
    # The sender's trace holds its own span and its root, nothing else.
    sender_trace = Trace.no_workspace_objects.get(
        project=sender.project, external_id=SENDER_TRACE
    )
    stored = ObservationSpan.no_workspace_objects.filter(trace=sender_trace)
    assert set(stored.values_list("id", flat=True)) == {SENDER_SPAN, SENDER_ROOT}
    # The refused observation is counted, and the stored count leaves it out.
    _, spans_count, _, spans_created, _, _ = result
    assert (spans_count, spans_created) == (1, 1)
    refused = _refused(result)
    assert refused.observation_ids == (OWNER_SPAN,)
    assert refused.count == 1


def test_a_trace_id_whose_root_is_in_another_project_keeps_that_root(owner, sender):
    owned = _store_owner_rows(owner)

    result = _upsert(
        sender,
        _trace(
            OWNER_TRACE,
            "sender question",
            [_observation(SENDER_SPAN, "sender step")],
        ),
    )

    # The owner's synthetic root keeps its project, its trace and its content.
    root = ObservationSpan.no_workspace_objects.get(id=OWNER_ROOT)
    assert (root.project_id, root.trace_id) == (
        owned.root.project_id,
        owned.root.trace_id,
    )
    assert (root.name, root.input, root.output) == (
        "owner question",
        "owner question input",
        "owner question output",
    )
    # The sender's trace and its own span are stored in the sender's project;
    # the span does not point at the other project's root.
    sender_trace = Trace.no_workspace_objects.get(
        project=sender.project, external_id=OWNER_TRACE
    )
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert (own.project_id, own.trace_id) == (sender.project.id, sender_trace.id)
    assert own.parent_span_id is None
    refused = _refused(result)
    assert refused.root is True
    assert refused.count == 1


def test_a_score_id_stored_in_another_project_is_refused_and_the_rest_stored(
    owner, sender
):
    owned = _store_owner_rows(owner)

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [_observation(SENDER_SPAN, "sender step")],
            [
                _score(OWNER_SCORE, SENDER_SPAN, 0.1),
                _score(SENDER_SCORE, SENDER_SPAN, 0.5),
            ],
        ),
    )

    # The owner's score keeps its row, its trace, its span and its value.
    score = EvalLogger.no_workspace_objects.get(eval_id=OWNER_SCORE)
    assert (score.id, score.trace_id, score.observation_span_id) == (
        owned.score.id,
        owned.score.trace_id,
        owned.score.observation_span_id,
    )
    assert score.output_float == 0.9
    # The sender's own score is stored on the sender's span.
    own = EvalLogger.no_workspace_objects.get(eval_id=SENDER_SCORE)
    assert (own.observation_span_id, own.output_float) == (SENDER_SPAN, 0.5)
    _, _, scores_count, _, scores_created, _ = result
    assert (scores_count, scores_created) == (1, 1)
    refused = _refused(result)
    assert refused.score_ids == (OWNER_SCORE,)
    assert refused.count == 1


def test_a_score_id_held_by_a_deleted_row_of_another_project_is_refused(owner, sender):
    owned = _store_owner_rows(owner)
    # The owner deletes its score; the row stays, marked deleted, with its id.
    EvalLogger._base_manager.filter(id=owned.score.id).update(deleted=True)

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [_observation(SENDER_SPAN, "sender step")],
            [_score(OWNER_SCORE, SENDER_SPAN, 0.1)],
        ),
    )

    # The score id is refused and counted, and no second row takes it.
    refused = _refused(result)
    assert refused.score_ids == (OWNER_SCORE,)
    assert refused.count == 1
    rows = EvalLogger._base_manager.filter(eval_id=OWNER_SCORE)
    assert list(rows.values_list("id", "deleted", "trace_id")) == [
        (owned.score.id, True, owned.score.trace_id)
    ]


def test_a_score_on_a_span_another_project_holds_is_refused_and_counted(owner, sender):
    owned = _store_owner_rows(owner)

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [
                _observation(OWNER_SPAN, "sender content"),
                _observation(SENDER_SPAN, "sender step"),
            ],
            [
                # On a span of the batch that is refused.
                _score(SCORE_ON_REFUSED, OWNER_SPAN, 0.2),
                # On a span the batch does not send, held by the owner.
                _score(SCORE_ON_HELD, OWNER_ROOT, 0.3),
                _score(SENDER_SCORE, SENDER_SPAN, 0.5),
            ],
        ),
    )

    # Both scores on the owner's spans are refused and counted; none is
    # stored, and the owner's span keeps only its own score.
    refused = _refused(result)
    assert refused.observation_ids == (OWNER_SPAN,)
    assert refused.score_ids == (SCORE_ON_REFUSED, SCORE_ON_HELD)
    assert refused.count == 3
    _, _, scores_count, _, scores_created, _ = result
    assert (scores_count, scores_created) == (1, 1)
    assert not EvalLogger._base_manager.filter(
        eval_id__in=[SCORE_ON_REFUSED, SCORE_ON_HELD]
    ).exists()
    on_owner_spans = EvalLogger.no_workspace_objects.filter(
        observation_span_id__in=[OWNER_SPAN, OWNER_ROOT]
    )
    assert list(on_owner_spans.values_list("id", flat=True)) == [owned.score.id]


def test_a_number_id_another_project_holds_is_refused_per_row(owner, sender):
    # The owner's span and score ids are digits; the sender's batch sends the
    # same ids as JSON numbers.
    _upsert(
        owner,
        _trace(
            NUMBER_TRACE,
            "owner numbers",
            [_observation(NUMBER_SPAN, "owner number step")],
            [_score(NUMBER_SCORE, NUMBER_SPAN, 0.7)],
        ),
    )

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [
                _observation(int(NUMBER_SPAN), "sender content"),
                _observation(SENDER_SPAN, "sender step"),
            ],
            [_score(int(NUMBER_SCORE), SENDER_SPAN, 0.1)],
        ),
    )

    # Each held id is refused on its own row; the rest of the trace is stored.
    refused = _refused(result)
    assert refused.observation_ids == (NUMBER_SPAN,)
    assert refused.score_ids == (NUMBER_SCORE,)
    span = ObservationSpan.no_workspace_objects.get(id=NUMBER_SPAN)
    assert (span.project_id, span.name) == (owner.project.id, "owner number step")
    score = EvalLogger._base_manager.get(eval_id=NUMBER_SCORE)
    assert (score.observation_span_id, score.output_float) == (NUMBER_SPAN, 0.7)
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert own.project_id == sender.project.id


def test_a_batch_naming_a_span_and_its_score_leaves_every_owner_row_in_place(
    owner, sender
):
    """The owner holds a span with an annotation and an eval row on it; the
    sender posts the owner's span id and score id, and a new score on that
    span. Nothing of the owner's moves, and nothing of the owner's ends up in
    another project."""
    owned = _store_owner_rows(owner)
    # A numeric label needs all four settings to pass the label's validation
    # (AnnotationsLabels.validate_settings).
    label = AnnotationsLabels.objects.create(
        name="Synthetic quality",
        type=AnnotationTypeChoices.NUMERIC.value,
        settings={"min": 0, "max": 100, "step_size": 1, "display_type": "slider"},
        organization=owner.org,
        workspace=owner.workspace,
        project=owner.project,
    )
    annotation = TraceAnnotation.no_workspace_objects.create(
        trace_id=owned.span.trace_id,
        observation_span_id=OWNER_SPAN,
        annotation_label=label,
        user=owner.user,
        annotation_value_float=80.0,
    )

    _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [
                _observation(OWNER_SPAN, "sender content"),
                _observation(SENDER_SPAN, "sender step"),
            ],
            [
                _score(OWNER_SCORE, OWNER_SPAN, 0.1),
                _score(SENDER_SCORE, OWNER_SPAN, 0.2),
            ],
        ),
    )

    # The owner's span did not move.
    _assert_owner_span_unchanged(owned)
    # The owner's annotation and eval rows still hang on the owner's span and
    # trace, inside the owner's project: none is in another project.
    annotation.refresh_from_db()
    assert (annotation.observation_span_id, annotation.trace_id) == (
        OWNER_SPAN,
        owned.span.trace_id,
    )
    assert (
        not TraceAnnotation.no_workspace_objects.filter(observation_span_id=OWNER_SPAN)
        .exclude(observation_span__project=owner.project)
        .exists()
    )
    on_owner_span = EvalLogger.no_workspace_objects.filter(
        observation_span_id=OWNER_SPAN
    )
    assert set(on_owner_span.values_list("eval_id", flat=True)) == {OWNER_SCORE}
    assert not on_owner_span.exclude(trace__project=owner.project).exists()
    # The sender's score on the owner's span id was not attached anywhere.
    assert not EvalLogger.no_workspace_objects.filter(eval_id=SENDER_SCORE).exists()


def test_the_same_ids_sent_again_into_their_own_project_update_in_place(owner):
    owned = _store_owner_rows(owner)

    _upsert(
        owner,
        _trace(
            OWNER_TRACE,
            "owner question again",
            [_observation(OWNER_SPAN, "owner step again")],
            [_score(OWNER_SCORE, OWNER_SPAN, 0.4)],
        ),
    )

    span = ObservationSpan.no_workspace_objects.get(id=OWNER_SPAN)
    assert (span.project_id, span.trace_id, span.name) == (
        owner.project.id,
        owned.span.trace_id,
        "owner step again",
    )
    root = ObservationSpan.no_workspace_objects.get(id=OWNER_ROOT)
    assert (root.project_id, root.trace_id, root.name) == (
        owner.project.id,
        owned.root.trace_id,
        "owner question again",
    )
    score = EvalLogger.no_workspace_objects.get(eval_id=OWNER_SCORE)
    assert (score.id, score.output_float) == (owned.score.id, 0.4)
    assert Trace.no_workspace_objects.filter(project=owner.project).count() == 1


def test_new_ids_are_created_in_the_senders_project(owner, sender):
    _store_owner_rows(owner)

    _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [_observation(SENDER_SPAN, "sender step")],
            [_score(SENDER_SCORE, SENDER_SPAN, 0.5)],
        ),
    )

    span = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    root = ObservationSpan.no_workspace_objects.get(id=SENDER_ROOT)
    assert span.project_id == root.project_id == sender.project.id
    assert span.parent_span_id == SENDER_ROOT
    score = EvalLogger.no_workspace_objects.get(eval_id=SENDER_SCORE)
    assert score.observation_span_id == SENDER_SPAN
    # The owner's span and root are where they were.
    owners = ObservationSpan.no_workspace_objects.filter(project=owner.project)
    assert set(owners.values_list("id", flat=True)) == {OWNER_SPAN, OWNER_ROOT}


def _post_batch(organization, batch):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=organization.user)
    client.set_workspace(organization.workspace)
    return client.post(reverse("api-public-ingestion"), {"batch": batch}, format="json")


def test_the_live_endpoint_answers_the_refused_event_and_stores_the_rest(owner, sender):
    owned = _store_owner_rows(owner)

    response = _post_batch(
        sender,
        [
            {
                "id": "lfscope-event-trace",
                "type": "trace-create",
                "body": {"id": SENDER_TRACE, "name": "sender question"},
            },
            {
                "id": "lfscope-event-taken",
                "type": "span-create",
                "body": {
                    "id": OWNER_SPAN,
                    "traceId": SENDER_TRACE,
                    "name": "sender content",
                    "startTime": START,
                },
            },
            {
                "id": "lfscope-event-own",
                "type": "span-create",
                "body": {
                    "id": SENDER_SPAN,
                    "traceId": SENDER_TRACE,
                    "name": "sender step",
                    "startTime": START,
                },
            },
        ],
    )

    _assert_owner_span_unchanged(owned)
    # A batch whose other rows are stored keeps its 207. The one event that
    # named the other project's id is answered 409 in ``errors``; the others
    # stay in ``successes`` and are stored.
    assert response.status_code == 207
    assert response.data["errors"] == [
        {"id": "lfscope-event-taken", "status": 409, "message": REFUSED_MESSAGE}
    ]
    assert [event["id"] for event in response.data["successes"]] == [
        "lfscope-event-trace",
        "lfscope-event-own",
    ]
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert own.project_id == sender.project.id


def _batch_naming(span_id):
    """The sender's batch: its trace, a span with ``span_id`` and its own span."""
    return [
        {
            "id": "lfscope-event-trace",
            "type": "trace-create",
            "body": {"id": SENDER_TRACE, "name": "sender question"},
        },
        {
            "id": "lfscope-event-taken",
            "type": "span-create",
            "body": {
                "id": span_id,
                "traceId": SENDER_TRACE,
                "name": "sender content",
                "startTime": START,
            },
        },
        {
            "id": "lfscope-event-own",
            "type": "span-create",
            "body": {
                "id": SENDER_SPAN,
                "traceId": SENDER_TRACE,
                "name": "sender step",
                "startTime": START,
            },
        },
    ]


def test_the_refusal_is_the_same_whichever_project_holds_the_id(owner, sender):
    """A refusal never says who holds the id. An id held by another project
    of the sender's own organization and an id held by another
    organization's project get the same answer, word for word."""
    owned = _store_owner_rows(owner)
    # Another Observe project of the sender's organization, older than the
    # one the endpoint ingests into, holds a span of its own.
    archive = Project.objects.create(
        name="Synthetic sender archive",
        organization=sender.org,
        workspace=sender.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    Project.no_workspace_objects.filter(id=archive.id).update(
        created_at=sender.project.created_at - timedelta(days=1)
    )
    _upsert(
        SimpleNamespace(org=sender.org, workspace=sender.workspace, project=archive),
        _trace(
            ARCHIVE_TRACE,
            "archive question",
            [_observation(ARCHIVE_SPAN, "archive step")],
        ),
    )

    own_organization = _post_batch(sender, _batch_naming(ARCHIVE_SPAN))
    other_organization = _post_batch(sender, _batch_naming(OWNER_SPAN))

    assert own_organization.status_code == other_organization.status_code == 207
    assert own_organization.data == other_organization.data
    assert other_organization.data == {
        "successes": [
            {"id": "lfscope-event-trace", "status": 201},
            {"id": "lfscope-event-own", "status": 201},
        ],
        "errors": [
            {"id": "lfscope-event-taken", "status": 409, "message": REFUSED_MESSAGE}
        ],
    }
    # Neither holder's span changed.
    _assert_owner_span_unchanged(owned)
    held = ObservationSpan.no_workspace_objects.get(id=ARCHIVE_SPAN)
    assert (held.project_id, held.name) == (archive.id, "archive step")


def _sender_archive(sender):
    """Another Observe project of the sender's organization, older than the one
    the endpoint ingests into, holding a span of its own (``ARCHIVE_SPAN``)."""
    archive = Project.objects.create(
        name="Synthetic sender archive",
        organization=sender.org,
        workspace=sender.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    Project.no_workspace_objects.filter(id=archive.id).update(
        created_at=sender.project.created_at - timedelta(days=1)
    )
    _upsert(
        SimpleNamespace(org=sender.org, workspace=sender.workspace, project=archive),
        _trace(
            ARCHIVE_TRACE,
            "archive question",
            [_observation(ARCHIVE_SPAN, "archive step")],
        ),
    )
    return archive


def _batch_scoring(span_id):
    """The sender's batch: its trace, its own span and a score on ``span_id``."""
    return [
        {
            "id": "lfscope-event-trace",
            "type": "trace-create",
            "body": {"id": SENDER_TRACE, "name": "sender question"},
        },
        {
            "id": "lfscope-event-own",
            "type": "span-create",
            "body": {
                "id": SENDER_SPAN,
                "traceId": SENDER_TRACE,
                "name": "sender step",
                "startTime": START,
            },
        },
        {
            "id": "lfscope-event-score",
            "type": "score-create",
            "body": {
                "id": SCORE_ON_HELD,
                "traceId": SENDER_TRACE,
                "name": "helpfulness",
                "value": 0.3,
                "observationId": span_id,
            },
        },
    ]


def test_a_score_on_a_held_span_is_answered_like_the_span(owner, sender):
    """A score on a span another project holds is not stored, and its event is
    answered 409 with the same fixed sentence whichever project holds the
    span."""
    _store_owner_rows(owner)
    _sender_archive(sender)

    own_organization = _post_batch(sender, _batch_scoring(ARCHIVE_SPAN))
    other_organization = _post_batch(sender, _batch_scoring(OWNER_SPAN))

    assert own_organization.status_code == other_organization.status_code == 207
    assert own_organization.data == other_organization.data
    assert other_organization.data == {
        "successes": [
            {"id": "lfscope-event-trace", "status": 201},
            {"id": "lfscope-event-own", "status": 201},
        ],
        "errors": [
            {"id": "lfscope-event-score", "status": 409, "message": REFUSED_MESSAGE}
        ],
    }
    assert not EvalLogger._base_manager.filter(eval_id=SCORE_ON_HELD).exists()


def test_the_live_endpoint_refuses_a_number_id_per_event(owner, sender):
    _upsert(
        owner,
        _trace(
            NUMBER_TRACE,
            "owner numbers",
            [_observation(NUMBER_SPAN, "owner number step")],
        ),
    )

    # The sender's span event names the owner's id as a JSON number.
    response = _post_batch(sender, _batch_naming(int(NUMBER_SPAN)))

    assert response.status_code == 207
    assert response.data["errors"] == [
        {"id": "lfscope-event-taken", "status": 409, "message": REFUSED_MESSAGE}
    ]
    assert [event["id"] for event in response.data["successes"]] == [
        "lfscope-event-trace",
        "lfscope-event-own",
    ]
    span = ObservationSpan.no_workspace_objects.get(id=NUMBER_SPAN)
    assert (span.project_id, span.name) == (owner.project.id, "owner number step")
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert own.project_id == sender.project.id


def test_the_live_endpoint_answers_a_trace_whose_root_is_in_another_project(
    owner, sender
):
    owned = _store_owner_rows(owner)

    response = _post_batch(
        sender,
        [
            {
                "id": "lfscope-event-trace",
                "type": "trace-create",
                "body": {"id": OWNER_TRACE, "name": "sender question"},
            },
            {
                "id": "lfscope-event-own",
                "type": "span-create",
                "body": {
                    "id": SENDER_SPAN,
                    "traceId": OWNER_TRACE,
                    "name": "sender step",
                    "startTime": START,
                },
            },
        ],
    )

    root = ObservationSpan.no_workspace_objects.get(id=OWNER_ROOT)
    assert (root.project_id, root.trace_id, root.name) == (
        owned.root.project_id,
        owned.root.trace_id,
        "owner question",
    )
    # The sender's trace and span are stored in the sender's project; the
    # trace-create event says the trace was stored without its root span.
    assert response.status_code == 207
    assert response.data["errors"] == [
        {
            "id": "lfscope-event-trace",
            "status": 409,
            "message": REFUSED_ROOT_MESSAGE,
        }
    ]
    assert [event["id"] for event in response.data["successes"]] == [
        "lfscope-event-own"
    ]
    assert Trace.no_workspace_objects.filter(
        project=sender.project, external_id=OWNER_TRACE
    ).exists()
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert own.project_id == sender.project.id


def test_an_import_counts_the_refused_row_and_stores_the_rest(owner, sender):
    owned = _store_owner_rows(owner)
    connection = IntegrationConnection.no_workspace_objects.create(
        organization=sender.org,
        workspace=sender.workspace,
        created_by=sender.user,
        platform=IntegrationPlatform.LANGFUSE,
        display_name="Synthetic Langfuse",
        host_url="https://langfuse.example.com",
        encrypted_credentials=b"synthetic",
        project=sender.project,
        external_project_name="synthetic-project",
        status=ConnectionStatus.ACTIVE,
        sync_interval_seconds=300,
        backfill_completed=True,
    )
    langfuse = MagicMock()
    langfuse.fetch_traces.return_value = {
        "traces": [{"id": SENDER_TRACE}],
        "has_more": False,
    }
    langfuse.fetch_trace_detail.side_effect = lambda **kwargs: _trace(
        SENDER_TRACE,
        "sender question",
        [
            _observation(OWNER_SPAN, "sender content"),
            _observation(SENDER_SPAN, "sender step"),
        ],
    )

    with (
        patch("integrations.services.credentials.CredentialManager") as credentials,
        patch(
            "integrations.services.base.get_integration_service",
            return_value=langfuse,
        ),
        patch(
            "integrations.transformers.base.get_transformer",
            return_value=LangfuseTransformer(),
        ),
        patch("time.sleep"),
    ):
        credentials.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        # The activity's wrapper closes the test's database connection; the
        # sync itself is what is under test here.
        sync_integration_connection._original_func(str(connection.id))

    _assert_owner_span_unchanged(owned)
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert own.project_id == sender.project.id
    # The sync log counts what was stored and records the refused row.
    log = SyncLog.objects.get(connection=connection)
    assert log.status == SyncStatus.SUCCESS
    assert (log.traces_fetched, log.spans_synced) == (1, 1)
    assert log.error_details == {"refused_rows": 1}
    assert log.error_message == IMPORT_REFUSED_NOTE
    connection.refresh_from_db()
    assert (connection.total_traces_synced, connection.total_spans_synced) == (1, 1)
    # Sync History reads the sync-logs API: it shows the sync as a success,
    # with the count in its message and in its details.
    row = _sync_history(sender, connection)[str(log.id)]
    assert row["status"] == "success"
    assert row["error_message"] == IMPORT_REFUSED_NOTE
    assert row["error_details"] == {"refused_rows": 1}


def _sync_history(organization, connection):
    """The rows the Sync History page reads for ``connection``, by id."""
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=organization.user)
    client.set_workspace(organization.workspace)
    response = client.get(
        "/integrations/sync-logs/", {"connection_id": str(connection.id)}
    )
    assert response.status_code == 200, response.content
    return {row["id"]: row for row in response.json()["result"]["sync_logs"]}


def test_sync_history_shows_a_refused_count_and_nothing_else(sender):
    connection = IntegrationConnection.no_workspace_objects.create(
        organization=sender.org,
        workspace=sender.workspace,
        created_by=sender.user,
        platform=IntegrationPlatform.LANGFUSE,
        display_name="Synthetic Langfuse",
        host_url="https://langfuse.example.com",
        encrypted_credentials=b"synthetic",
        project=sender.project,
        external_project_name="synthetic-project",
        status=ConnectionStatus.ACTIVE,
        sync_interval_seconds=300,
        backfill_completed=True,
    )
    started = datetime.now(UTC) - timedelta(minutes=10)

    def stored_log(minute, message, details):
        return SyncLog.objects.create(
            connection=connection,
            status=SyncStatus.SUCCESS,
            started_at=started + timedelta(minutes=minute),
            completed_at=started + timedelta(minutes=minute, seconds=30),
            error_message=message,
            error_details=details,
        )

    several = stored_log(
        0,
        "3 rows were not imported: their ids are already used in another project.",
        {"refused_rows": 3},
    )
    # Values that are not a whole count stay out of the answer.
    negative = stored_log(1, None, {"refused_rows": -1})
    text = stored_log(2, None, {"refused_rows": "2"})
    flag = stored_log(3, None, {"refused_rows": True})

    rows = _sync_history(sender, connection)

    assert rows[str(several.id)]["error_message"] == (
        "3 rows were not imported: their ids are already used in another project."
    )
    assert rows[str(several.id)]["error_details"] == {"refused_rows": 3}
    for log in (negative, text, flag):
        assert rows[str(log.id)]["error_details"] == {}


# ---------------------------------------------------------------------------
# Span ids only ClickHouse holds. The collector writes most spans to ClickHouse
# and never to Postgres, so a span id another project holds there has no
# Postgres row for the upsert to find.
# ---------------------------------------------------------------------------

CH_SPAN = "lfscope-clickhouse-span"
CH_OTHER_SPAN = "lfscope-clickhouse-other-span"
CH_TRACE = "lfscope-clickhouse-trace"
CH_ROOT = f"root-{CH_TRACE}"
SECOND_TRACE = "lfscope-second-trace"
SECOND_SPAN = "lfscope-second-span"

# The answers when a trace's span ids could not be checked.
UNCHECKED_MESSAGE = (
    "The batch was not fully stored: its span ids could not be checked. Send it again."
)
UNCHECKED_IMPORT_MESSAGE = (
    "The sync to Langfuse stopped on an unexpected error. It is retried "
    "automatically; Retry tries again now."
)
# Text of the stand-in's errors; no answer and no sync log may carry it.
ERROR_TEXT = "lfscope-error-text: Code: 60. DB::Exception at 10.0.0.9:9000"


class SyntheticQueryError(Exception):
    """A query ClickHouse refused."""


UNREADABLE = [
    pytest.param(ConnectionRefusedError(ERROR_TEXT), id="connection"),
    pytest.param(TimeoutError(ERROR_TEXT), id="timeout"),
    pytest.param(SyntheticQueryError(ERROR_TEXT), id="query"),
]


class ClickHouseStandIn:
    """Stands in for the ClickHouse read client the upsert asks which span ids
    another project holds.

    ``held`` maps a span id to the project holding it in ClickHouse only. The
    stand-in answers from ``params`` (``ids``, ``project_id``) and records
    every read. From read number ``fails_from`` on it raises ``failure``.
    """

    def __init__(self):
        self.held = {}
        self.reads = []
        self.failure = None
        self.fails_from = 1

    def hold(self, span_id, project):
        self.held[span_id] = str(project.id)

    def cannot_be_read(self, failure, *, from_read=1):
        self.failure = failure
        self.fails_from = from_read

    def execute_read(self, query, params=None, timeout_ms=None, settings=None):
        params = dict(params or {})
        self.reads.append((query, params))
        if self.failure is not None and len(self.reads) >= self.fails_from:
            raise self.failure
        asker = params["project_id"]
        rows = [
            (span_id,)
            for span_id in params["ids"]
            if span_id in self.held and self.held[span_id] != asker
        ]
        return rows, [("id", "String")], 0.0

    def asked_ids(self):
        return [set(params["ids"]) for _, params in self.reads]


@pytest.fixture
def clickhouse(monkeypatch):
    """The upsert's ClickHouse reads, answered by a ``ClickHouseStandIn``."""
    stand_in = ClickHouseStandIn()
    # Patched where the upsert looks the client up. raising=False: before the
    # check exists the name is missing, and the cases fail on their asserts.
    monkeypatch.setattr(
        "tracer.utils.langfuse_upsert.get_v2_query_client",
        lambda: stand_in,
        raising=False,
    )
    return stand_in


def test_a_span_id_only_clickhouse_holds_in_another_project_is_refused(
    owner, sender, clickhouse
):
    """The owner's spans exist only in ClickHouse. The sender's span with one
    of their ids is refused and counted, scores on either are refused, the
    rest is stored, and no Postgres span takes either id."""
    clickhouse.hold(CH_SPAN, owner.project)
    clickhouse.hold(CH_OTHER_SPAN, owner.project)

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [
                _observation(CH_SPAN, "sender content"),
                _observation(SENDER_SPAN, "sender step"),
            ],
            [
                # On a span of the batch that is refused.
                _score(SCORE_ON_REFUSED, CH_SPAN, 0.2),
                # On a span the batch does not send, held in ClickHouse.
                _score(SCORE_ON_HELD, CH_OTHER_SPAN, 0.3),
                _score(SENDER_SCORE, SENDER_SPAN, 0.5),
            ],
        ),
    )

    # No Postgres span takes either id, in the sender's project or anywhere.
    assert not ObservationSpan._base_manager.filter(
        id__in=[CH_SPAN, CH_OTHER_SPAN]
    ).exists()
    sender_trace = Trace.no_workspace_objects.get(
        project=sender.project, external_id=SENDER_TRACE
    )
    stored = ObservationSpan.no_workspace_objects.filter(trace=sender_trace)
    assert set(stored.values_list("id", flat=True)) == {SENDER_SPAN, SENDER_ROOT}
    # Refused and counted like an id Postgres holds.
    refused = _refused(result)
    assert refused.observation_ids == (CH_SPAN,)
    assert refused.score_ids == (SCORE_ON_REFUSED, SCORE_ON_HELD)
    assert refused.count == 3
    _, spans_count, scores_count, spans_created, scores_created, _ = result
    assert (spans_count, spans_created) == (1, 1)
    assert (scores_count, scores_created) == (1, 1)
    assert not EvalLogger._base_manager.filter(
        eval_id__in=[SCORE_ON_REFUSED, SCORE_ON_HELD]
    ).exists()
    own = EvalLogger.no_workspace_objects.get(eval_id=SENDER_SCORE)
    assert own.observation_span_id == SENDER_SPAN
    # One ClickHouse read for the whole trace, naming every span id it uses.
    assert clickhouse.asked_ids() == [
        {SENDER_ROOT, CH_SPAN, SENDER_SPAN, CH_OTHER_SPAN}
    ]


def test_a_root_id_only_clickhouse_holds_elsewhere_is_not_stored(
    owner, sender, clickhouse
):
    clickhouse.hold(CH_ROOT, owner.project)

    result = _upsert(
        sender,
        _trace(CH_TRACE, "sender question", [_observation(SENDER_SPAN, "sender step")]),
    )

    # The trace and its span are stored in the sender's project, without a
    # root: no Postgres span takes the root's id.
    assert not ObservationSpan._base_manager.filter(id=CH_ROOT).exists()
    sender_trace = Trace.no_workspace_objects.get(
        project=sender.project, external_id=CH_TRACE
    )
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert (own.project_id, own.trace_id) == (sender.project.id, sender_trace.id)
    assert own.parent_span_id is None
    refused = _refused(result)
    assert refused.root is True
    assert refused.count == 1


def test_the_live_endpoint_answers_an_id_only_clickhouse_holds_elsewhere(
    owner, sender, clickhouse
):
    clickhouse.hold(CH_SPAN, owner.project)

    response = _post_batch(sender, _batch_naming(CH_SPAN))

    # The same answer as for an id Postgres holds: that event is answered 409
    # with the fixed sentence, and the rest of the batch is stored.
    assert response.status_code == 207
    assert response.data == {
        "successes": [
            {"id": "lfscope-event-trace", "status": 201},
            {"id": "lfscope-event-own", "status": 201},
        ],
        "errors": [
            {"id": "lfscope-event-taken", "status": 409, "message": REFUSED_MESSAGE}
        ],
    }
    assert not ObservationSpan._base_manager.filter(id=CH_SPAN).exists()
    own = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert own.project_id == sender.project.id


@pytest.mark.parametrize("failure", UNREADABLE)
def test_the_live_endpoint_stores_nothing_of_a_trace_it_cannot_check(
    sender, clickhouse, failure
):
    clickhouse.cannot_be_read(failure)

    response = _post_batch(sender, _batch_naming(CH_SPAN))

    # The whole request is answered 503, which the Langfuse SDKs send again,
    # as JSON with a fixed sentence and no error text.
    assert response.status_code == 503
    assert response.json()["message"] == UNCHECKED_MESSAGE
    assert ERROR_TEXT not in response.content.decode()
    # Nothing of the trace is stored.
    assert not Trace.no_workspace_objects.filter(project=sender.project).exists()
    assert not ObservationSpan._base_manager.filter(
        id__in=[CH_SPAN, SENDER_SPAN, SENDER_ROOT]
    ).exists()


def _two_trace_batch():
    """The sender's batch: its trace with its own span, and a second trace."""
    return [
        {
            "id": "lfscope-event-trace",
            "type": "trace-create",
            "body": {"id": SENDER_TRACE, "name": "sender question"},
        },
        {
            "id": "lfscope-event-own",
            "type": "span-create",
            "body": {
                "id": SENDER_SPAN,
                "traceId": SENDER_TRACE,
                "name": "sender step",
                "startTime": START,
            },
        },
        {
            "id": "lfscope-event-second",
            "type": "span-create",
            "body": {
                "id": SECOND_SPAN,
                "traceId": SECOND_TRACE,
                "name": "second step",
                "startTime": START,
            },
        },
    ]


def _assert_stored_once(organization, trace_id, span_id):
    trace = Trace.no_workspace_objects.get(
        project=organization.project, external_id=trace_id
    )
    stored = ObservationSpan.no_workspace_objects.filter(trace=trace)
    assert sorted(stored.values_list("id", flat=True)) == sorted(
        [span_id, f"root-{trace_id}"]
    )


def test_a_batch_answered_503_is_stored_once_when_sent_again(sender, clickhouse):
    """The trace checked before the read failed is stored; the one it failed
    on is not. The SDK sends the whole batch again, and then every row is
    stored, once."""
    clickhouse.cannot_be_read(ConnectionRefusedError(ERROR_TEXT), from_read=2)

    first = _post_batch(sender, _two_trace_batch())

    assert first.status_code == 503
    _assert_stored_once(sender, SENDER_TRACE, SENDER_SPAN)
    assert not Trace.no_workspace_objects.filter(
        project=sender.project, external_id=SECOND_TRACE
    ).exists()
    assert not ObservationSpan._base_manager.filter(id=SECOND_SPAN).exists()

    clickhouse.failure = None
    again = _post_batch(sender, _two_trace_batch())

    assert again.status_code == 207
    assert again.data["errors"] == []
    _assert_stored_once(sender, SENDER_TRACE, SENDER_SPAN)
    _assert_stored_once(sender, SECOND_TRACE, SECOND_SPAN)


def _langfuse_connection(organization, *, last_synced_at):
    return IntegrationConnection.no_workspace_objects.create(
        organization=organization.org,
        workspace=organization.workspace,
        created_by=organization.user,
        platform=IntegrationPlatform.LANGFUSE,
        display_name="Synthetic Langfuse",
        host_url="https://langfuse.example.com",
        encrypted_credentials=b"synthetic",
        project=organization.project,
        external_project_name="synthetic-project",
        status=ConnectionStatus.ACTIVE,
        sync_interval_seconds=300,
        backfill_completed=True,
        last_synced_at=last_synced_at,
    )


def _run_import(connection, traces):
    """One Langfuse sync of ``connection`` reading ``traces`` (id -> trace)."""
    langfuse = MagicMock()
    langfuse.fetch_traces.return_value = {
        "traces": [{"id": trace_id} for trace_id in traces],
        "has_more": False,
    }
    langfuse.fetch_trace_detail.side_effect = lambda **kwargs: deepcopy(
        traces[kwargs["trace_id"]]
    )
    with (
        patch("integrations.services.credentials.CredentialManager") as credentials,
        patch(
            "integrations.services.base.get_integration_service",
            return_value=langfuse,
        ),
        patch(
            "integrations.transformers.base.get_transformer",
            return_value=LangfuseTransformer(),
        ),
        patch("time.sleep"),
    ):
        credentials.decrypt.return_value = {"public_key": "pk", "secret_key": "sk"}
        sync_integration_connection._original_func(str(connection.id))


@pytest.mark.parametrize("failure", UNREADABLE)
def test_an_import_stores_nothing_of_a_trace_it_cannot_check_and_fails(
    sender, clickhouse, failure
):
    clickhouse.cannot_be_read(failure)
    synced_until = datetime.now(UTC) - timedelta(hours=1)
    connection = _langfuse_connection(sender, last_synced_at=synced_until)

    _run_import(
        connection,
        {
            SENDER_TRACE: _trace(
                SENDER_TRACE,
                "sender question",
                [_observation(SENDER_SPAN, "sender step")],
            )
        },
    )

    assert not Trace.no_workspace_objects.filter(project=sender.project).exists()
    assert not ObservationSpan._base_manager.filter(
        id__in=[SENDER_SPAN, SENDER_ROOT]
    ).exists()
    # Not a success: the sync failed with a fixed sentence and no error text.
    log = SyncLog.objects.get(connection=connection)
    assert log.status == SyncStatus.FAILED
    assert log.error_message == UNCHECKED_IMPORT_MESSAGE
    # The connection is in Error, which the scheduler syncs again, from the
    # same window.
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.ERROR
    assert connection.last_synced_at == synced_until
    row = _sync_history(sender, connection)[str(log.id)]
    assert row["status"] == "failed"
    assert row["error_message"] == UNCHECKED_IMPORT_MESSAGE
    assert ERROR_TEXT not in json.dumps(row)


def test_an_import_that_stops_on_an_unchecked_trace_reads_it_on_the_next_sync(
    sender, clickhouse
):
    clickhouse.cannot_be_read(ConnectionRefusedError(ERROR_TEXT), from_read=2)
    synced_until = datetime.now(UTC) - timedelta(hours=1)
    connection = _langfuse_connection(sender, last_synced_at=synced_until)
    traces = {
        SENDER_TRACE: _trace(
            SENDER_TRACE, "sender question", [_observation(SENDER_SPAN, "sender step")]
        ),
        SECOND_TRACE: _trace(
            SECOND_TRACE, "second question", [_observation(SECOND_SPAN, "second step")]
        ),
    }

    _run_import(connection, traces)

    # The first trace is stored; the second, whose ids could not be checked,
    # is not, and the sync is partial, not a success.
    _assert_stored_once(sender, SENDER_TRACE, SENDER_SPAN)
    assert not Trace.no_workspace_objects.filter(
        project=sender.project, external_id=SECOND_TRACE
    ).exists()
    log = SyncLog.objects.get(connection=connection)
    assert log.status == SyncStatus.PARTIAL
    row = _sync_history(sender, connection)[str(log.id)]
    assert row["status"] == "partial"
    assert row["error_message"] == (
        f"Partial sync: 1 traces ingested before error. {UNCHECKED_IMPORT_MESSAGE}"
    )
    assert ERROR_TEXT not in json.dumps(row)
    # The connection keeps its last sync time, so the next sync reads the same
    # window again.
    connection.refresh_from_db()
    assert connection.status == ConnectionStatus.ACTIVE
    assert connection.last_synced_at == synced_until

    clickhouse.failure = None
    _run_import(connection, traces)

    latest = SyncLog.objects.filter(connection=connection).order_by("-started_at")
    assert latest.first().status == SyncStatus.SUCCESS
    _assert_stored_once(sender, SENDER_TRACE, SENDER_SPAN)
    _assert_stored_once(sender, SECOND_TRACE, SECOND_SPAN)
    # Each trace is counted once, by the sync that first stored it.
    connection.refresh_from_db()
    assert connection.total_traces_synced == 2


def test_an_id_clickhouse_holds_in_the_senders_own_project_is_stored(
    sender, clickhouse
):
    clickhouse.hold(CH_SPAN, sender.project)

    result = _upsert(
        sender,
        _trace(SENDER_TRACE, "sender question", [_observation(CH_SPAN, "sender step")]),
    )

    span = ObservationSpan.no_workspace_objects.get(id=CH_SPAN)
    assert (span.project_id, span.name) == (sender.project.id, "sender step")
    assert _refused(result).count == 0


def test_a_new_id_is_created_when_clickhouse_holds_it_nowhere(
    owner, sender, clickhouse
):
    clickhouse.hold(CH_SPAN, owner.project)

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [_observation(SENDER_SPAN, "sender step")],
            [_score(SENDER_SCORE, SENDER_SPAN, 0.5)],
        ),
    )

    span = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    root = ObservationSpan.no_workspace_objects.get(id=SENDER_ROOT)
    assert span.project_id == root.project_id == sender.project.id
    assert span.parent_span_id == SENDER_ROOT
    score = EvalLogger.no_workspace_objects.get(eval_id=SENDER_SCORE)
    assert score.observation_span_id == SENDER_SPAN
    assert _refused(result).count == 0


def test_the_same_id_sent_again_updates_in_place_when_clickhouse_holds_it_there(
    sender, clickhouse
):
    """The upsert mirrors its spans to ClickHouse, so a span sent again is held
    there by its own project: that is not a refusal."""
    _upsert(
        sender,
        _trace(
            SENDER_TRACE, "sender question", [_observation(SENDER_SPAN, "sender step")]
        ),
    )
    first = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    clickhouse.hold(SENDER_SPAN, sender.project)
    clickhouse.hold(SENDER_ROOT, sender.project)

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question again",
            [_observation(SENDER_SPAN, "sender step again")],
        ),
    )

    span = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert (span.project_id, span.trace_id, span.name) == (
        sender.project.id,
        first.trace_id,
        "sender step again",
    )
    assert Trace.no_workspace_objects.filter(project=sender.project).count() == 1
    _, _, _, spans_created, _, refused = result
    assert spans_created == 0
    assert refused.count == 0


# ---------------------------------------------------------------------------
# The operator's setting for the ClickHouse check, and the ids it asks about
# ---------------------------------------------------------------------------

# The environment variable the upsert reads at each check. "off", in any case,
# turns the ClickHouse read off; unset, "on" or any other value keeps it on.
CHECK_SETTING = "LANGFUSE_SPAN_ID_CLICKHOUSE_CHECK"
# The line the upsert logs for each check it skips.
SKIPPED_LINE = "langfuse_span_id_check_skipped"
NEW_SPAN = "lfscope-sender-new-span"
SECOND_ROOT = f"root-{SECOND_TRACE}"


def _skipped_lines(logs):
    return [line for line in logs if line["event"] == SKIPPED_LINE]


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("off", id="off"),
        pytest.param("OFF", id="OFF"),
        pytest.param("Off", id="Off"),
    ],
)
def test_with_the_check_off_clickhouse_is_not_read_and_one_line_says_so(
    owner, sender, clickhouse, monkeypatch, value
):
    owned = _store_owner_rows(owner)
    # The owner holds one span in Postgres and one only in ClickHouse.
    clickhouse.hold(CH_SPAN, owner.project)
    clickhouse.reads.clear()
    monkeypatch.setenv(CHECK_SETTING, value)

    with capture_logs() as logs:
        result = _upsert(
            sender,
            _trace(
                SENDER_TRACE,
                "sender question",
                [
                    _observation(OWNER_SPAN, "sender content"),
                    _observation(CH_SPAN, "sender held content"),
                    _observation(SENDER_SPAN, "sender step"),
                ],
            ),
        )

    # ClickHouse is not read.
    assert clickhouse.reads == []
    # The id the owner holds only in ClickHouse is stored in the sender's
    # project, as the Postgres check alone stores it. The id the owner holds
    # in Postgres is still refused, and the owner's span keeps its row.
    held = ObservationSpan.no_workspace_objects.get(id=CH_SPAN)
    assert (held.project_id, held.name) == (sender.project.id, "sender held content")
    refused = _refused(result)
    assert refused.observation_ids == (OWNER_SPAN,)
    assert refused.count == 1
    _assert_owner_span_unchanged(owned)
    # One line says the check was skipped: the project and the number of ids
    # ClickHouse was not asked about (the root, the held id and the new one),
    # and no id.
    skipped = _skipped_lines(logs)
    assert len(skipped) == 1
    assert set(skipped[0]) == {"event", "log_level", "project_id", "span_ids"}
    assert skipped[0]["project_id"] == str(sender.project.id)
    assert skipped[0]["span_ids"] == 3
    for span_id in (SENDER_ROOT, OWNER_SPAN, CH_SPAN, SENDER_SPAN):
        assert span_id not in str(skipped[0])


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="unset"),
        pytest.param("on", id="on"),
        pytest.param("of", id="of"),
        pytest.param("false", id="false"),
    ],
)
def test_with_the_check_unset_on_or_mistyped_the_held_id_is_refused(
    owner, sender, clickhouse, monkeypatch, value
):
    clickhouse.hold(CH_SPAN, owner.project)
    if value is None:
        monkeypatch.delenv(CHECK_SETTING, raising=False)
    else:
        monkeypatch.setenv(CHECK_SETTING, value)

    with capture_logs() as logs:
        result = _upsert(
            sender,
            _trace(
                SENDER_TRACE,
                "sender question",
                [
                    _observation(CH_SPAN, "sender content"),
                    _observation(SENDER_SPAN, "sender step"),
                ],
            ),
        )

    # The check runs: one read, and the id the owner holds there is refused.
    assert len(clickhouse.reads) == 1
    assert _refused(result).observation_ids == (CH_SPAN,)
    assert not ObservationSpan._base_manager.filter(id=CH_SPAN).exists()
    assert _skipped_lines(logs) == []


def test_clickhouse_is_not_asked_about_ids_the_senders_project_holds(
    sender, clickhouse
):
    # The sender's span and its root are stored in the sender's project.
    _upsert(
        sender,
        _trace(
            SENDER_TRACE, "sender question", [_observation(SENDER_SPAN, "sender step")]
        ),
    )
    first = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    clickhouse.reads.clear()

    _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question again",
            [
                _observation(SENDER_SPAN, "sender step again"),
                _observation(NEW_SPAN, "sender new step"),
            ],
            [_score(SENDER_SCORE, SENDER_SPAN, 0.5)],
        ),
    )

    # ClickHouse is asked only about the new id: the span, the root and the
    # scored span the project holds in Postgres are its own rows.
    assert clickhouse.asked_ids() == [{NEW_SPAN}]
    # The span the project holds is updated in place.
    span = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert (span.project_id, span.trace_id, span.name) == (
        sender.project.id,
        first.trace_id,
        "sender step again",
    )


def test_a_trace_whose_ids_the_senders_project_all_holds_reads_no_clickhouse(
    sender, clickhouse
):
    _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [_observation(SENDER_SPAN, "sender step")],
            [_score(SENDER_SCORE, SENDER_SPAN, 0.5)],
        ),
    )
    clickhouse.reads.clear()

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question again",
            [_observation(SENDER_SPAN, "sender step again")],
            [_score(SENDER_SCORE, SENDER_SPAN, 0.4)],
        ),
    )

    # Every span id is the sender's own row in Postgres: ClickHouse is not
    # read, and the rows are updated in place.
    assert clickhouse.reads == []
    assert _refused(result).count == 0
    span = ObservationSpan.no_workspace_objects.get(id=SENDER_SPAN)
    assert (span.project_id, span.name) == (sender.project.id, "sender step again")
    score = EvalLogger.no_workspace_objects.get(eval_id=SENDER_SCORE)
    assert score.output_float == 0.4


# A span id two projects hold: the sender's project in Postgres, and another
# project in ClickHouse only, as when the sender's row was stored first or
# while the check was off. The project that holds the Postgres row keeps it.
SHARED_TRACE = "lfscope-shared-trace"
SHARED_SPAN = "lfscope-shared-span"


def _shared_span(owner, sender, clickhouse):
    """The sender's project stores ``SHARED_SPAN`` in Postgres; then the
    owner's project holds the same id in ClickHouse only. Returns the sender's
    span, with the stand-in's reads cleared."""
    _upsert(
        sender,
        _trace(
            SHARED_TRACE,
            "sender shared question",
            [_observation(SHARED_SPAN, "sender shared step")],
        ),
    )
    clickhouse.hold(SHARED_SPAN, owner.project)
    clickhouse.reads.clear()
    return ObservationSpan.no_workspace_objects.get(id=SHARED_SPAN)


def test_the_senders_postgres_id_is_its_own_while_clickhouse_holds_it_elsewhere(
    owner, sender, clickhouse
):
    first = _shared_span(owner, sender, clickhouse)

    result = _upsert(
        sender,
        _trace(
            SHARED_TRACE,
            "sender shared question again",
            [
                _observation(SHARED_SPAN, "sender shared step again"),
                _observation(NEW_SPAN, "sender new step"),
            ],
        ),
    )

    # ClickHouse is asked only about the new id, never about the id the
    # sender's project holds in Postgres. Nothing is refused, and the sender's
    # span is updated in place.
    asked = [span_id for ids in clickhouse.asked_ids() for span_id in ids]
    assert SHARED_SPAN not in asked
    assert asked == [NEW_SPAN]
    assert _refused(result).count == 0
    span = ObservationSpan.no_workspace_objects.get(id=SHARED_SPAN)
    assert (span.project_id, span.trace_id, span.name) == (
        sender.project.id,
        first.trace_id,
        "sender shared step again",
    )

    # The other project's batch naming that id is refused and counted, and
    # the sender's span keeps its row.
    other = _upsert(
        owner,
        _trace(
            OWNER_TRACE,
            "owner question",
            [
                _observation(SHARED_SPAN, "owner content"),
                _observation(OWNER_SPAN, "owner step"),
            ],
        ),
    )

    refused = _refused(other)
    assert refused.observation_ids == (SHARED_SPAN,)
    assert refused.count == 1
    span = ObservationSpan.no_workspace_objects.get(id=SHARED_SPAN)
    assert (span.project_id, span.name) == (
        sender.project.id,
        "sender shared step again",
    )


def test_the_live_endpoint_answers_409_to_the_clickhouse_holder_of_a_senders_id(
    owner, sender, clickhouse
):
    _shared_span(owner, sender, clickhouse)

    response = _post_batch(
        owner,
        [
            {
                "id": "lfscope-event-trace",
                "type": "trace-create",
                "body": {"id": OWNER_TRACE, "name": "owner question"},
            },
            {
                "id": "lfscope-event-taken",
                "type": "span-create",
                "body": {
                    "id": SHARED_SPAN,
                    "traceId": OWNER_TRACE,
                    "name": "owner content",
                    "startTime": START,
                },
            },
            {
                "id": "lfscope-event-own",
                "type": "span-create",
                "body": {
                    "id": OWNER_SPAN,
                    "traceId": OWNER_TRACE,
                    "name": "owner step",
                    "startTime": START,
                },
            },
        ],
    )

    # The project that holds the id only in ClickHouse is answered as for any
    # id another project holds in Postgres: 409 for that event, and the rest
    # of its batch stored. The sender's span keeps its row.
    assert response.status_code == 207
    assert response.data == {
        "successes": [
            {"id": "lfscope-event-trace", "status": 201},
            {"id": "lfscope-event-own", "status": 201},
        ],
        "errors": [
            {"id": "lfscope-event-taken", "status": 409, "message": REFUSED_MESSAGE}
        ],
    }
    span = ObservationSpan.no_workspace_objects.get(id=SHARED_SPAN)
    assert (span.project_id, span.name) == (sender.project.id, "sender shared step")
    own = ObservationSpan.no_workspace_objects.get(id=OWNER_SPAN)
    assert own.project_id == owner.project.id


def test_two_traces_in_one_request_are_checked_in_one_read_each(sender, clickhouse):
    response = _post_batch(sender, _two_trace_batch())

    assert response.status_code == 207
    assert response.data["errors"] == []
    # One read per trace, each naming only that trace's span ids: never one
    # list for the whole request.
    assert clickhouse.asked_ids() == [
        {SENDER_ROOT, SENDER_SPAN},
        {SECOND_ROOT, SECOND_SPAN},
    ]
    assert {params["project_id"] for _, params in clickhouse.reads} == {
        str(sender.project.id)
    }


# ---------------------------------------------------------------------------
# How many ids one ClickHouse read carries
# ---------------------------------------------------------------------------

# The most ids, and the most bytes of ids, one read carries: every read stays
# within the sizes the cost checks measure.
IDS_PER_READ = 501
ID_BYTES_PER_READ = 200_000
# 1,200 short span ids: their bytes fit in one read, their number does not.
MANY_SHORT_SPANS = [f"lfscope-short-{number:04d}" for number in range(1_200)]


def _statement_bytes(ids):
    """The most bytes ``ids`` can take in a statement: clickhouse-driver writes
    each id quoted and followed by a separator, and escapes some characters
    into two."""
    return sum(2 * len(span_id.encode()) + 4 for span_id in ids)


def test_a_trace_of_many_short_ids_is_read_at_most_501_ids_at_a_time(
    owner, sender, clickhouse
):
    # The owner holds each of those ids only in ClickHouse. Their bytes and the
    # root's fit in one read, so only their number splits the reads.
    for span_id in MANY_SHORT_SPANS:
        clickhouse.hold(span_id, owner.project)
    assert _statement_bytes([SENDER_ROOT, *MANY_SHORT_SPANS]) <= ID_BYTES_PER_READ

    result = _upsert(
        sender,
        _trace(
            SENDER_TRACE,
            "sender question",
            [_observation(span_id, "sender content") for span_id in MANY_SHORT_SPANS],
        ),
    )

    # The root and the 1,200 ids are asked about in three reads, none with
    # more than 501 ids or 200,000 bytes of ids, each about projects other
    # than the sender's.
    reads = [params for _, params in clickhouse.reads]
    assert len(reads) == 3
    for read in reads:
        assert len(read["ids"]) <= IDS_PER_READ
        assert _statement_bytes(read["ids"]) <= ID_BYTES_PER_READ
        assert read["project_id"] == str(sender.project.id)
    # Every span id of the trace is asked about once, and each id the owner
    # holds is refused, whichever read answered it.
    asked = [span_id for read in reads for span_id in read["ids"]]
    assert sorted(asked) == sorted([SENDER_ROOT, *MANY_SHORT_SPANS])
    refused = _refused(result)
    assert set(refused.observation_ids) == set(MANY_SHORT_SPANS)
    assert refused.count == len(MANY_SHORT_SPANS)
    assert not ObservationSpan._base_manager.filter(id__in=MANY_SHORT_SPANS).exists()
