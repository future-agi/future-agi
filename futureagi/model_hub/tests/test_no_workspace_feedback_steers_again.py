"""Feedback saved with no workspace steers evaluations again.

The earlier production build wrote eval-playground feedback, and dataset feedback on
datasets made before workspaces, with no workspace, and read feedback by
organization only. An earlier change made every feedback read workspace-exact, so none
of that feedback reached an evaluation any more.

A read admits feedback of the same organization whose workspace is the run's
workspace or empty. An empty workspace is admitted only for feedback written
before the release cutoff: the new build marks every feedback vector it writes
with no workspace, and a marked vector is never read across workspaces.
Feedback of another organization, and feedback of another workspace, is never
read.

Real readers and vector SQL over the isolated ClickHouse fixture; only
embedding inference is stubbed.
"""

import uuid

import pytest

from agentic_eval.core.embeddings import embedding_manager as em
from model_hub.tests.test_feedback_rag_workspace_scope import (
    NO_WORKSPACE_AFTER_CUTOFF_KEY,
    _read,
    ambient_foreign_workspace,  # noqa: F401 - shared fixture
    feedback_store,  # noqa: F401 - shared fixture
)

pytestmark = pytest.mark.integration

READERS = ["engine", "standalone", "metric"]
SPACES = ["default", "second"]


def _insert_production_shaped(scope, marker, *, organization_id=None):
    """A vector as production's writers stored it: no workspace key at all.

    Playground and dataset feedback both carry a feedback identity.
    """
    feedback_id = str(uuid.uuid4())
    metadata = {
        "item_id": str(uuid.uuid4()),
        "organization_id": organization_id or scope.organization_id,
        "input_type": "text",
        "output": f"{marker}-input",
        "feedback_comment": f"{marker}-comment",
        "feedback_value": f"{marker}-label",
        "feedback_id": feedback_id,
        "feedback_input": "output",
    }
    scope.manager.db_client.upsert_vector(
        table_name=em.FEEDBACK_TABLE_NAME,
        eval_id=scope.eval_id,
        vector=[1.0, 0.0],
        metadata=metadata,
        unique_keys=["item_id"],
        mark_existing_deleted=False,
    )


def _space_ids(scope, space):
    if not hasattr(scope, "second_workspace_id"):
        scope.second_workspace_id = str(uuid.uuid4())
    if space == "default":
        return scope.workspace_id, scope.second_workspace_id
    return scope.second_workspace_id, scope.workspace_id


@pytest.mark.parametrize("space", SPACES)
@pytest.mark.parametrize("reader", READERS)
def test_production_no_workspace_feedback_steers_every_workspace(
    feedback_store, ambient_foreign_workspace, reader, space
):
    scope = feedback_store
    current, sibling = _space_ids(scope, space)
    _insert_production_shaped(scope, "playground-null")
    _insert_production_shaped(scope, "dataset-null")
    scope.insert("local", workspace_id=current)
    scope.insert("sibling", workspace_id=sibling)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert(
        "other-org",
        workspace_id=current,
        organization_id=scope.foreign_organization_id,
    )
    _insert_production_shaped(
        scope, "other-org-null", organization_id=scope.foreign_organization_id
    )
    result = str(_read(reader, scope, workspace_id=current))
    assert "local-comment" in result, "setup: run workspace feedback is read"
    assert "playground-null-comment" in result and "dataset-null-comment" in result, (
        "feedback saved with no workspace steers runs in every workspace"
    )
    assert all(
        marker not in result for marker in ("sibling-", "foreign-", "other-org-")
    ), "another workspace's or organization's feedback is never read"


def test_feedback_count_admits_the_rows_retrieval_reads(feedback_store):
    scope = feedback_store
    second = str(uuid.uuid4())
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("second", workspace_id=second)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)
    scope.insert(
        "other-org",
        workspace_id=scope.workspace_id,
        organization_id=scope.foreign_organization_id,
    )
    scope.insert(
        "other-org-null",
        workspace_id=None,
        organization_id=scope.foreign_organization_id,
    )

    def count(organization_id, workspace_id):
        return scope.manager.get_feedback_count(
            eval_id=scope.eval_id,
            organization_id=organization_id,
            workspace_id=workspace_id,
        )

    assert count(scope.organization_id, scope.workspace_id) == 2, (
        "own workspace + same-org pre-cutoff no-workspace row"
    )
    assert count(scope.organization_id, second) == 2, (
        "a second workspace sees its row + the pre-cutoff row"
    )
    assert count(scope.foreign_organization_id, scope.workspace_id) == 2, (
        "the other organization counts only its own rows"
    )
    assert count(scope.organization_id, str(uuid.uuid4())) == 1, (
        "an empty workspace still sees the pre-cutoff row only"
    )


@pytest.mark.parametrize("space", SPACES)
def test_manager_reads_only_same_org_and_run_or_pre_cutoff_rows(
    feedback_store, space
):
    scope = feedback_store
    current, sibling = _space_ids(scope, space)
    scope.insert("local", workspace_id=current)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)
    scope.insert("sibling", workspace_id=sibling)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert(
        "other-org",
        workspace_id=current,
        organization_id=scope.foreign_organization_id,
    )
    scope.insert(
        "other-org-null",
        workspace_id=None,
        organization_id=scope.foreign_organization_id,
    )
    rows = scope.manager.retrieve_rag_based_examples(
        query="current case",
        table_name=em.FEEDBACK_TABLE_NAME,
        eval_id=scope.eval_id,
        meta_data_col="output",
        organization_id=scope.organization_id,
        workspace_id=current,
    )
    metadata = [row[-2] for row in rows]
    comments = sorted(item["feedback_comment"] for item in metadata)
    assert comments == ["legacy-null-comment", "local-comment"], (
        "the SQL returns the run workspace's row and the same-org "
        "pre-cutoff no-workspace row, nothing else"
    )
    for item in metadata:
        assert item["organization_id"] == scope.organization_id
        assert item.get("workspace_id") in (current, None)
        if "workspace_id" not in item:
            assert NO_WORKSPACE_AFTER_CUTOFF_KEY not in item


def _active_metadata(scope):
    rows = scope.manager.db_client.client.execute(
        "SELECT metadata.key, metadata.value FROM feedbacks WHERE deleted = 0"
    )
    return [dict(zip(keys, values, strict=True)) for keys, values in rows]


def _write_through_formatter(scope, marker, workspace_id):
    return scope.manager.data_formatter(
        row_dict={
            "feedback_id": str(uuid.uuid4()),
            "output": f"{marker}-input",
            "feedback_comment": f"{marker}-comment",
            "feedback_value": f"{marker}-label",
        },
        inputs_formater=["output"],
        insert=True,
        eval_id=scope.eval_id,
        organization_id=scope.organization_id,
        workspace_id=workspace_id,
    )


@pytest.mark.parametrize("space", SPACES)
def test_feedback_written_without_workspace_after_cutoff_stays_out(
    feedback_store, ambient_foreign_workspace, space
):
    scope = feedback_store
    current, _sibling = _space_ids(scope, space)
    vectors, _ = _write_through_formatter(scope, "new-null", None)
    assert vectors, "setup: the formatter stored a vector"
    stored = [
        item
        for item in _active_metadata(scope)
        if item.get("feedback_comment") == "new-null-comment"
    ]
    assert stored and all(
        "workspace_id" not in item and item.get(NO_WORKSPACE_AFTER_CUTOFF_KEY) == "1"
        for item in stored
    ), "the new build marks feedback it writes with no workspace"
    scope.insert("legacy-null", workspace_id=None)
    result = str(_read("engine", scope, workspace_id=current))
    assert "legacy-null-comment" in result, "setup: pre-cutoff row is read"
    assert "new-null-comment" not in result, (
        "feedback written with no workspace after the cutoff is never "
        "read across workspaces"
    )


def test_feedback_written_with_a_workspace_carries_no_cutoff_marker(feedback_store):
    scope = feedback_store
    vectors, _ = _write_through_formatter(scope, "scoped", scope.workspace_id)
    assert vectors, "setup: the formatter stored a vector"
    stored = [
        item
        for item in _active_metadata(scope)
        if item.get("feedback_comment") == "scoped-comment"
    ]
    assert stored and all(
        item.get("workspace_id") == scope.workspace_id
        and NO_WORKSPACE_AFTER_CUTOFF_KEY not in item
        for item in stored
    )


def test_cutoff_marker_key_is_the_one_the_reader_checks():
    assert getattr(em, "FEEDBACK_NO_WORKSPACE_AFTER_CUTOFF_KEY", None) == (
        NO_WORKSPACE_AFTER_CUTOFF_KEY
    ), "writer and reader share one marker key"
