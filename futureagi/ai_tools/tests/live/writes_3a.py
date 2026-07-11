# ruff: noqa: E501
"""Phase 3A live write checks — confirmation-gated destructive bridges.

Each entry seeds THROWAWAY rows via ORM with FIXED UUIDs (so the static
``args`` dict can reference them), calls the destructive tool through the
harness two-phase flow (verify_writes._safe_run auto re-calls with
confirm=true after the CONFIRMATION_REQUIRED preview), asserts the ORM
side effect, and hard-deletes any residue to keep the account net-zero.

Covers the design §1.11 minimum set: #1 bulk_delete_prompt_templates,
#3 bulk_remove_queue_items, #5 bulk_delete_test_executions,
#6 bulk_delete_eval_templates, #9 bulk_delete_annotations,
#12 remove_blocklist_words.

Run: docker exec ws1-backend python -m ai_tools.tests.verify_writes
"""

from django.utils import timezone

# Fixed throwaway ids — stable across runs, always reaped by compensate.
PT_ID_1 = "3a000000-0000-4000-8000-000000000101"
PT_ID_2 = "3a000000-0000-4000-8000-000000000102"
AQ_ID = "3a000000-0000-4000-8000-000000000201"
QI_ID_1 = "3a000000-0000-4000-8000-000000000211"
QI_ID_2 = "3a000000-0000-4000-8000-000000000212"
RT_ID = "3a000000-0000-4000-8000-000000000301"
TE_ID_1 = "3a000000-0000-4000-8000-000000000311"
TE_ID_2 = "3a000000-0000-4000-8000-000000000312"
ET_ID_1 = "3a000000-0000-4000-8000-000000000401"
ET_ID_2 = "3a000000-0000-4000-8000-000000000402"
DS_ID = "3a000000-0000-4000-8000-000000000501"
AN_ID_1 = "3a000000-0000-4000-8000-000000000511"
AN_ID_2 = "3a000000-0000-4000-8000-000000000512"
BL_ID = "3a000000-0000-4000-8000-000000000601"


# --- #1 bulk_delete_prompt_templates ---------------------------------------


def _setup_prompt_templates(ctx):
    from model_hub.models.run_prompt import PromptTemplate

    PromptTemplate.all_objects.filter(id__in=[PT_ID_1, PT_ID_2]).delete()
    for pid, name in ((PT_ID_1, "3a-writecheck-pt-1"), (PT_ID_2, "3a-writecheck-pt-2")):
        PromptTemplate.objects.create(
            id=pid,
            name=name,
            organization=ctx.organization,
            workspace=ctx.workspace,
            created_by=ctx.user,
        )


def _assert_prompt_templates_gone(ctx, result):
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.objects.filter(id__in=[PT_ID_1, PT_ID_2]).count() == 0


def _reap_prompt_templates(ctx, result):
    from model_hub.models.run_prompt import PromptTemplate

    PromptTemplate.all_objects.filter(id__in=[PT_ID_1, PT_ID_2]).delete()


# --- #3 bulk_remove_queue_items --------------------------------------------


def _setup_queue_items(ctx):
    from model_hub.models.annotation_queues import (
        AnnotationQueue,
        AnnotationQueueAnnotator,
        QueueItem,
    )
    from model_hub.models.choices import AnnotatorRole, QueueItemSourceType

    QueueItem.all_objects.filter(id__in=[QI_ID_1, QI_ID_2]).delete()
    AnnotationQueueAnnotator.all_objects.filter(queue_id=AQ_ID).delete()
    AnnotationQueue.all_objects.filter(id=AQ_ID).delete()
    queue = AnnotationQueue.objects.create(
        id=AQ_ID,
        name="3a-writecheck-queue",
        organization=ctx.organization,
        workspace=ctx.workspace,
    )
    AnnotationQueueAnnotator.objects.create(
        queue=queue,
        user=ctx.user,
        role=AnnotatorRole.MANAGER.value,
        roles=[AnnotatorRole.MANAGER.value],
    )
    for qid in (QI_ID_1, QI_ID_2):
        QueueItem.objects.create(
            id=qid,
            queue=queue,
            organization=ctx.organization,
            source_type=QueueItemSourceType.DATASET_ROW.value,
        )


def _assert_queue_items_removed(ctx, result):
    from model_hub.models.annotation_queues import QueueItem

    return (
        QueueItem.objects.filter(
            id__in=[QI_ID_1, QI_ID_2], deleted=False
        ).count()
        == 0
    )


def _reap_queue(ctx, result):
    from model_hub.models.annotation_queues import (
        AnnotationQueue,
        AnnotationQueueAnnotator,
        QueueItem,
    )

    QueueItem.all_objects.filter(id__in=[QI_ID_1, QI_ID_2]).delete()
    AnnotationQueueAnnotator.all_objects.filter(queue_id=AQ_ID).delete()
    AnnotationQueue.all_objects.filter(id=AQ_ID).delete()


# --- #5 bulk_delete_test_executions -----------------------------------------


def _setup_test_executions(ctx):
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import TestExecution

    TestExecution.all_objects.filter(id__in=[TE_ID_1, TE_ID_2]).delete()
    RunTest.all_objects.filter(id=RT_ID).delete()
    run_test = RunTest.objects.create(
        id=RT_ID,
        name="3a-writecheck-runtest",
        organization=ctx.organization,
        workspace=ctx.workspace,
    )
    now = timezone.now()
    for tid in (TE_ID_1, TE_ID_2):
        # TestExecution has no org/workspace FKs — tenancy rides run_test.
        TestExecution.objects.create(
            id=tid,
            run_test=run_test,
            status=TestExecution.ExecutionStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            total_scenarios=0,
            total_calls=0,
            completed_calls=0,
            execution_metadata={},
        )


def _assert_test_executions_gone(ctx, result):
    from simulate.models.test_execution import TestExecution

    return TestExecution.objects.filter(id__in=[TE_ID_1, TE_ID_2]).count() == 0


def _reap_run_test(ctx, result):
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import TestExecution

    TestExecution.all_objects.filter(id__in=[TE_ID_1, TE_ID_2]).delete()
    RunTest.all_objects.filter(id=RT_ID).delete()


# --- #6 bulk_delete_eval_templates -------------------------------------------


def _setup_eval_templates(ctx):
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    EvalTemplate.all_objects.filter(id__in=[ET_ID_1, ET_ID_2]).delete()
    for tid, name in ((ET_ID_1, "3a-writecheck-et-1"), (ET_ID_2, "3a-writecheck-et-2")):
        EvalTemplate.objects.create(
            id=tid,
            name=name,
            organization=ctx.organization,
            workspace=ctx.workspace,
            owner=OwnerChoices.USER.value,
        )


def _assert_eval_templates_gone(ctx, result):
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.objects.filter(id__in=[ET_ID_1, ET_ID_2]).count() == 0


def _reap_eval_templates(ctx, result):
    from model_hub.models.evals_metric import EvalTemplate

    EvalTemplate.all_objects.filter(id__in=[ET_ID_1, ET_ID_2]).delete()


# --- #9 bulk_delete_annotations ----------------------------------------------


def _setup_annotations(ctx):
    from model_hub.models.develop_annotations import Annotations
    from model_hub.models.develop_dataset import Dataset

    Annotations.all_objects.filter(id__in=[AN_ID_1, AN_ID_2]).delete()
    Dataset.all_objects.filter(id=DS_ID).delete()
    dataset = Dataset.objects.create(
        id=DS_ID,
        name="3a-writecheck-dataset",
        organization=ctx.organization,
        workspace=ctx.workspace,
    )
    for aid, name in ((AN_ID_1, "3a-writecheck-an-1"), (AN_ID_2, "3a-writecheck-an-2")):
        annotation = Annotations.objects.create(
            id=aid,
            name=name,
            organization=ctx.organization,
            workspace=ctx.workspace,
            dataset=dataset,
        )
        annotation.assigned_users.add(ctx.user)


def _assert_annotations_gone(ctx, result):
    from model_hub.models.develop_annotations import Annotations

    return Annotations.objects.filter(id__in=[AN_ID_1, AN_ID_2]).count() == 0


def _reap_annotations(ctx, result):
    from model_hub.models.develop_annotations import Annotations
    from model_hub.models.develop_dataset import Dataset

    Annotations.all_objects.filter(id__in=[AN_ID_1, AN_ID_2]).delete()
    Dataset.all_objects.filter(id=DS_ID).delete()


# --- #12 remove_blocklist_words ----------------------------------------------


def _setup_blocklist(ctx):
    from agentcc.models.blocklist import AgentccBlocklist

    AgentccBlocklist.all_objects.filter(id=BL_ID).delete()
    AgentccBlocklist.no_workspace_objects.create(
        id=BL_ID,
        name="3a-writecheck-blocklist",
        organization=ctx.organization,
        words=["3a-writecheck-keep", "3a-writecheck-banned"],
    )


def _assert_blocklist_word_removed(ctx, result):
    from agentcc.models.blocklist import AgentccBlocklist

    bl = AgentccBlocklist.no_workspace_objects.get(id=BL_ID)
    return "3a-writecheck-banned" not in bl.words and "3a-writecheck-keep" in bl.words


def _reap_blocklist(ctx, result):
    from agentcc.models.blocklist import AgentccBlocklist

    AgentccBlocklist.all_objects.filter(id=BL_ID).delete()


ROUNDTRIPS = [
    {
        "tool": "bulk_delete_prompt_templates",
        "args": {"ids": [PT_ID_1, PT_ID_2]},
        "setup": _setup_prompt_templates,
        "assert_orm": _assert_prompt_templates_gone,
        "compensate": _reap_prompt_templates,
    },
    {
        "tool": "bulk_remove_queue_items",
        "args": {"queue_id": AQ_ID, "item_ids": [QI_ID_1, QI_ID_2]},
        "setup": _setup_queue_items,
        "assert_orm": _assert_queue_items_removed,
        "compensate": _reap_queue,
    },
    {
        "tool": "bulk_delete_test_executions",
        "args": {
            "run_test_id": RT_ID,
            "test_execution_ids": [TE_ID_1, TE_ID_2],
            "select_all": False,
        },
        "setup": _setup_test_executions,
        "assert_orm": _assert_test_executions_gone,
        "compensate": _reap_run_test,
    },
    {
        "tool": "bulk_delete_eval_templates",
        "args": {"template_ids": [ET_ID_1, ET_ID_2]},
        "setup": _setup_eval_templates,
        "assert_orm": _assert_eval_templates_gone,
        "compensate": _reap_eval_templates,
    },
    {
        "tool": "bulk_delete_annotations",
        "args": {"annotation_ids": [AN_ID_1, AN_ID_2]},
        "setup": _setup_annotations,
        "assert_orm": _assert_annotations_gone,
        "compensate": _reap_annotations,
    },
    {
        "tool": "remove_blocklist_words",
        "args": {"id": BL_ID, "words": ["3a-writecheck-banned"]},
        "setup": _setup_blocklist,
        "assert_orm": _assert_blocklist_word_removed,
        "compensate": _reap_blocklist,
    },
]
