"""Judge summaries read the same human evidence that queue review edits."""

import uuid

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueLabel,
    QueueItem,
)
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Row
from model_hub.models.score import Score
from model_hub.tests.test_pairwise_judges import start, summary

pytest_plugins = ["model_hub.tests.pairwise_judge_support"]

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
    # Shared through pytest_plugins, so modules opt in instead of autouse.
    pytest.mark.usefixtures("dispatch"),
]


@pytest.fixture
def review(prepared, organization, workspace, user):
    dataset = prepared[0].dataset
    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    DatasetVersionItem.no_workspace_objects.filter(
        dataset_version=prepared[1][0].dataset_version,
        example_id=prepared[2][0],
    ).update(legacy_row=row)
    label = AnnotationsLabels.no_workspace_objects.create(
        name="Overall preference",
        type="categorical",
        organization=organization,
        workspace=workspace,
        settings={
            "kind": "pairwise",
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
            "options": [{"label": "A"}, {"label": "Tie"}, {"label": "B"}],
        },
    )
    queue = AnnotationQueue.no_workspace_objects.create(
        name="Compare saved answers",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        created_by=user,
        status="active",
        auto_assign=True,
    )
    AnnotationQueueLabel.no_workspace_objects.create(queue=queue, label=label)
    item = QueueItem.no_workspace_objects.create(
        queue=queue,
        dataset_row=row,
        source_type="dataset_row",
        organization=organization,
        workspace=workspace,
        assigned_to=user,
        metadata={
            "kind": "experiment_review",
            "mode": "pairwise",
            "run_a": str(prepared[1][1].pk),
            "run_b": str(prepared[1][0].pk),
            "blind": True,
            "left": "B",
        },
    )
    return queue, item, label


def submit_review(client, review, choice):
    queue, item, label = review
    response = client.post(
        f"/model-hub/annotation-queues/{queue.pk}/items/{item.pk}/annotations/submit/",
        {"annotations": [{"label_id": str(label.pk), "value": {"selected": [choice]}}]},
        format="json",
    )
    assert response.status_code == 200, response.data
    return Score.no_workspace_objects.get(queue_item=item, label=label, deleted=False)


def test_review_submission_edit_and_clear_update_judge_agreement(
    auth_client,
    prepared,
    review,
):
    job = start(auth_client, prepared)
    job.items.filter(example_id=prepared[2][0]).update(
        status="completed", winner="a", reason="Clearer"
    )
    # The actual queue workflow stores canonical B, not "left". Its reversed
    # run pair makes that a vote for this job's A, even though B is shown left.
    score = submit_review(auth_client, review, "B")
    assert score.value == {"selected": ["B"]}
    result = summary(auth_client, prepared, job)
    assert result.status_code == 200
    assert result.data["human"] == {
        "a_wins": 1,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 1,
    }
    assert result.data["agreement"] == {"matches": 1, "compared": 1, "rate": 1.0}
    assert summary(auth_client, prepared, job, reverse=True).data["human"] == {
        "a_wins": 0,
        "b_wins": 1,
        "ties": 0,
        "rows_reviewed": 1,
    }
    edited = submit_review(auth_client, review, "A")
    assert edited.pk == score.pk
    assert edited.value == {"selected": ["A"]}
    result = summary(auth_client, prepared, job)
    assert result.data["human"] == {
        "a_wins": 0,
        "b_wins": 1,
        "ties": 0,
        "rows_reviewed": 1,
    }
    assert result.data["agreement"] == {"matches": 0, "compared": 1, "rate": 0.0}
    submit_review(auth_client, review, "Tie")
    assert summary(auth_client, prepared, job).data["human"] == {
        "a_wins": 0,
        "b_wins": 0,
        "ties": 1,
        "rows_reviewed": 1,
    }
    response = auth_client.delete(f"/model-hub/scores/{score.pk}/")
    assert response.status_code == 200, response.data
    result = summary(auth_client, prepared, job)
    assert result.data["human"] == {
        "a_wins": 0,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 0,
    }
    assert result.data["agreement"] == {"matches": 0, "compared": 0, "rate": None}
    assert result.data["judge"] == {
        "a_wins": 1,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 1,
    }


@pytest.mark.parametrize("left", ["A", "B"])
@pytest.mark.parametrize("reverse", [False, True])
def test_blind_review_values_follow_the_recorded_pair_not_the_display_side(
    auth_client,
    prepared,
    review,
    left,
    reverse,
):
    queue, item, label = review
    runs = list(reversed(prepared[1])) if reverse else prepared[1]
    item.metadata.update(run_a=str(runs[0].pk), run_b=str(runs[1].pk), left=left)
    item.save()
    job = start(auth_client, prepared)
    submit_review(auth_client, review, "A")
    result = summary(auth_client, prepared, job)
    assert result.data["human"] == {
        "a_wins": 0 if reverse else 1,
        "b_wins": 1 if reverse else 0,
        "ties": 0,
        "rows_reviewed": 1,
    }


@pytest.mark.parametrize(
    "excluded",
    ["organization", "workspace", "pair", "source", "item", "label", "membership"],
)
def test_unrelated_or_removed_review_evidence_does_not_enter_totals(
    auth_client,
    prepared,
    review,
    organization,
    user,
    excluded,
):
    queue, item, label = review
    job = start(auth_client, prepared)
    score = submit_review(auth_client, review, "B")
    if excluded == "organization":
        Score.no_workspace_objects.filter(pk=score.pk).update(
            organization=Organization.objects.create(name="Other review tenant")
        )
    elif excluded == "workspace":
        other = Workspace.no_workspace_objects.create(
            name="Other review workspace",
            organization=organization,
            created_by=user,
        )
        AnnotationQueue.no_workspace_objects.filter(pk=queue.pk).update(workspace=other)
    elif excluded == "pair":
        item.metadata["run_b"] = str(uuid.uuid4())
        item.save()
    elif excluded == "source":
        Score.no_workspace_objects.filter(pk=score.pk).update(score_source="auto")
    elif excluded == "item":
        QueueItem.no_workspace_objects.filter(pk=item.pk).update(deleted=True)
    elif excluded == "label":
        AnnotationsLabels.no_workspace_objects.filter(pk=label.pk).update(deleted=True)
    else:
        AnnotationQueueLabel.no_workspace_objects.filter(
            queue=queue, label=label
        ).update(deleted=True)
    result = summary(auth_client, prepared, job)
    assert result.status_code == 200
    assert result.data["human"] == {
        "a_wins": 0,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 0,
    }
