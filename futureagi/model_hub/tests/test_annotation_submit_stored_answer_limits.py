"""A queue submit checks a label's limits only for an answer the annotator changed.

An answer stored before its label's limits were tightened stays as it is and
never blocks the annotator from saving the item's other labels. A changed
answer, or a first answer, is checked against the label's current limits.
"""

from types import SimpleNamespace

import pytest
from rest_framework import status

from accounts.models import User
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
    AnnotatorRole,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.score import Score
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context

QUEUE_URL = "/model-hub/annotation-queues/"

OLD_COMMENT = "short note"
TONE_OPTIONS = [{"label": "positive"}, {"label": "negative"}, {"label": "neutral"}]


def _submit(client, queue, item, *answers):
    return client.post(
        f"{QUEUE_URL}{queue.id}/items/{item.id}/annotations/submit/",
        {
            "annotations": [
                {"label_id": str(label.id), "value": value} for label, value in answers
            ]
        },
        format="json",
    )


def _tighten(label, **settings):
    # QuerySet.update leaves the stored answers alone, as a label edit does.
    AnnotationsLabels.objects.filter(pk=label.pk).update(
        settings={**label.settings, **settings}
    )


def _stored(item, label, user):
    return Score.objects.get(
        queue_item=item, label=label, annotator=user, deleted=False
    ).value


@pytest.fixture
def s(auth_client, organization, workspace, user):
    set_workspace_context(workspace=workspace, organization=organization)
    dataset = Dataset.objects.create(
        name="Stored answers", organization=organization, workspace=workspace
    )
    rows = [Row.objects.create(dataset=dataset, order=i) for i in range(2)]
    comment = AnnotationsLabels.objects.create(
        name="Comment",
        type=AnnotationTypeChoices.TEXT.value,
        settings={"placeholder": "", "min_length": 0, "max_length": 1000},
        organization=organization,
        workspace=workspace,
    )
    accuracy = AnnotationsLabels.objects.create(
        name="Accuracy",
        type=AnnotationTypeChoices.NUMERIC.value,
        settings={"min": 0, "max": 10, "step_size": 1, "display_type": "slider"},
        organization=organization,
        workspace=workspace,
    )
    tone = AnnotationsLabels.objects.create(
        name="Tone",
        type=AnnotationTypeChoices.CATEGORICAL.value,
        settings={
            "options": TONE_OPTIONS,
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        organization=organization,
        workspace=workspace,
    )
    queue = AnnotationQueue.objects.create(
        name="Stored answers queue",
        organization=organization,
        workspace=workspace,
        created_by=user,
        status=AnnotationQueueStatusChoices.ACTIVE.value,
    )
    AnnotationQueueAnnotator.objects.get_or_create(
        queue=queue, user=user, defaults={"role": AnnotatorRole.MANAGER.value}
    )
    for order, label in enumerate((comment, accuracy, tone)):
        AnnotationQueueLabel.objects.create(
            queue=queue, label=label, order=order, required=True
        )
    items = [
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.DATASET_ROW.value,
            dataset_row=row,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
            order=order,
        )
        for order, row in enumerate(rows)
    ]

    first = _submit(
        auth_client,
        queue,
        items[0],
        (comment, {"text": OLD_COMMENT}),
        (accuracy, {"value": 8}),
        (tone, {"selected": ["neutral"]}),
    )
    assert first.status_code == status.HTTP_200_OK, first.data

    return SimpleNamespace(
        queue=queue,
        item=items[0],
        fresh_item=items[1],
        comment=comment,
        accuracy=accuracy,
        tone=tone,
    )


@pytest.mark.django_db
class TestStoredAnswerNoLongerFits:
    def test_old_text_answer_does_not_block_a_change_to_another_label(
        self, auth_client, user, s
    ):
        _tighten(s.comment, min_length=50)

        resp = _submit(
            auth_client,
            s.queue,
            s.item,
            (s.comment, {"text": OLD_COMMENT}),
            (s.accuracy, {"value": 8}),
            (s.tone, {"selected": ["positive"]}),
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _stored(s.item, s.comment, user) == {"text": OLD_COMMENT}
        assert _stored(s.item, s.tone, user) == {"selected": ["positive"]}

    def test_changing_the_old_text_answer_to_another_short_text_is_refused(
        self, auth_client, user, s
    ):
        _tighten(s.comment, min_length=50)

        resp = _submit(
            auth_client,
            s.queue,
            s.item,
            (s.comment, {"text": "still far too short."}),
            (s.tone, {"selected": ["positive"]}),
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert resp.data["message"] == "Comment must be at least 50 characters."
        assert _stored(s.item, s.comment, user) == {"text": OLD_COMMENT}
        assert _stored(s.item, s.tone, user) == {"selected": ["neutral"]}

    def test_old_number_outside_a_narrower_range_does_not_block_another_label(
        self, auth_client, user, s
    ):
        _tighten(s.accuracy, max=5)

        resp = _submit(
            auth_client,
            s.queue,
            s.item,
            (s.comment, {"text": OLD_COMMENT}),
            (s.accuracy, {"value": 8}),
            (s.tone, {"selected": ["negative"]}),
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _stored(s.item, s.accuracy, user) == {"value": 8}
        assert _stored(s.item, s.tone, user) == {"selected": ["negative"]}

    def test_changing_the_old_number_to_another_number_out_of_range_is_refused(
        self, auth_client, user, s
    ):
        _tighten(s.accuracy, max=5)

        resp = _submit(
            auth_client,
            s.queue,
            s.item,
            (s.accuracy, {"value": 9}),
            (s.tone, {"selected": ["negative"]}),
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert resp.data["message"] == "Accuracy must be between 0 and 5."
        assert _stored(s.item, s.accuracy, user) == {"value": 8}
        assert _stored(s.item, s.tone, user) == {"selected": ["neutral"]}

    def test_old_choice_whose_option_was_removed_does_not_block_another_label(
        self, auth_client, user, s
    ):
        _tighten(s.tone, options=TONE_OPTIONS[:2])

        resp = _submit(
            auth_client,
            s.queue,
            s.item,
            (s.comment, {"text": "a longer comment written today"}),
            (s.tone, {"selected": ["neutral"]}),
        )

        assert resp.status_code == status.HTTP_200_OK, resp.data
        assert _stored(s.item, s.tone, user) == {"selected": ["neutral"]}
        assert _stored(s.item, s.comment, user) == {
            "text": "a longer comment written today"
        }

    def test_a_first_answer_that_breaks_the_limit_is_still_refused(
        self, auth_client, user, s
    ):
        _tighten(s.comment, min_length=50)

        resp = _submit(
            auth_client, s.queue, s.fresh_item, (s.comment, {"text": OLD_COMMENT})
        )

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert resp.data["message"] == "Comment must be at least 50 characters."
        assert not Score.objects.filter(
            queue_item=s.fresh_item, label=s.comment, deleted=False
        ).exists()

    def test_another_annotators_stored_answer_does_not_exempt_mine(
        self, auth_client, user, organization, s
    ):
        _tighten(s.comment, min_length=50)
        # The stored answers now belong to another person, so this
        # annotator's identical text is a first answer.
        other = User.objects.create_user(
            email="stored-answer-other@futureagi.com",
            password="testpassword123",
            name="Other Annotator",
            organization=organization,
            organization_role=OrganizationRoles.MEMBER,
        )
        Score.objects.filter(queue_item=s.item, annotator=user).update(annotator=other)

        resp = _submit(auth_client, s.queue, s.item, (s.comment, {"text": OLD_COMMENT}))

        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.data
        assert resp.data["message"] == "Comment must be at least 50 characters."
