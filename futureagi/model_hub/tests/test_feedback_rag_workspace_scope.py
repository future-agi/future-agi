"""Feedback tenancy regressions using real readers, vector SQL and formatting.

Only embedding inference is stubbed. The ClickHouse fixture follows
``test_feedback_vector_lifecycle`` and requires the guarded test sidecar.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agentic_eval.core.database import ch_vector
from agentic_eval.core.embeddings import embedding_manager as em
from conftest import _open_ch_test_native_client
from evaluations.engine.params import prepare_run_params
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = pytest.mark.integration

# Metadata key the new build writes on a feedback vector saved with no
# workspace. Vectors saved before the release cutoff never carry it.
NO_WORKSPACE_AFTER_CUTOFF_KEY = "written_without_workspace"


@pytest.fixture
def feedback_store(monkeypatch):
    database = f"test_feedback_scope_{uuid.uuid4().hex}"
    admin = _open_ch_test_native_client(owned_database=database)
    admin.execute(f"CREATE DATABASE {database}")
    host, port = admin.connection.hosts[0]
    client_kwargs = {
        "host": host,
        "port": port,
        "user": admin.connection.user,
        "password": admin.connection.password,
        "database": database,
    }
    monkeypatch.setattr(
        ch_vector, "get_clickhouse_client_kwargs", lambda: dict(client_kwargs)
    )
    monkeypatch.setenv("CH_DATABASE", database)
    monkeypatch.setattr(
        em,
        "model_manager",
        SimpleNamespace(_use_serving=True, text_model=lambda text: [1.0, 0.0]),
    )
    monkeypatch.setattr(em, "serving_available", lambda: True)
    manager = em.EmbeddingManager()
    scope = SimpleNamespace(
        organization_id=str(uuid.uuid4()),
        workspace_id=str(uuid.uuid4()),
        foreign_workspace_id=str(uuid.uuid4()),
        foreign_organization_id=str(uuid.uuid4()),
        eval_id=str(uuid.uuid4()),
        manager=manager,
    )

    def insert(
        marker,
        *,
        workspace_id,
        organization_id=None,
        eval_id=None,
        written_after_cutoff=False,
    ):
        metadata = {
            "item_id": str(uuid.uuid4()),
            "organization_id": organization_id or scope.organization_id,
            "input_type": "text",
            "output": f"{marker}-input",
            "feedback_comment": f"{marker}-comment",
            "feedback_value": f"{marker}-label",
        }
        if workspace_id is not None:
            metadata["workspace_id"] = workspace_id
        if written_after_cutoff:
            metadata[NO_WORKSPACE_AFTER_CUTOFF_KEY] = "1"
        manager.db_client.upsert_vector(
            table_name=em.FEEDBACK_TABLE_NAME,
            eval_id=eval_id or scope.eval_id,
            vector=[1.0, 0.0],
            metadata=metadata,
            unique_keys=["item_id"],
            mark_existing_deleted=False,
        )

    scope.insert = insert
    try:
        manager.db_client.create_table(em.FEEDBACK_TABLE_NAME, database=database)
        yield scope
    finally:
        manager.db_client.close()
        admin.execute(f"DROP DATABASE {database} SYNC")
        admin.disconnect()


@pytest.fixture
def ambient_foreign_workspace(feedback_store, urlconf_loaded_without_workspace_context):
    scope = feedback_store
    # Default workspace context must not include default/NULL metadata rows.
    set_workspace_context(
        workspace=SimpleNamespace(
            id=scope.foreign_workspace_id,
            organization_id=scope.organization_id,
            is_default=True,
        ),
        organization=SimpleNamespace(id=scope.organization_id),
    )
    try:
        yield
    finally:
        clear_workspace_context()


def _template(eval_id):
    # SYSTEM/shared templates do not establish the executing run's workspace.
    return SimpleNamespace(
        id=eval_id,
        organization_id=None,
        workspace_id=None,
        config={"eval_type_id": "CustomPromptEvaluator", "required_keys": ["output"]},
    )


def _runner(scope, workspace_id, *, dataset_bound=False):
    from model_hub.views.eval_runner import EvaluationRunner

    runner = EvaluationRunner(
        user_eval_metric_id=None,
        format_output=True,
        organization_id=scope.organization_id,
        workspace_id=workspace_id,
    )
    runner.eval_template = _template(scope.eval_id)
    if dataset_bound:
        runner.dataset = SimpleNamespace(
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
        )
        runner.user_eval_metric_id = str(uuid.uuid4())
        runner.user_eval_metric = SimpleNamespace(
            id=runner.user_eval_metric_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
            dataset=runner.dataset,
        )
    return runner


def _read(reader, scope, *, workspace_id, eval_id=None):
    if reader == "engine":
        return prepare_run_params(
            {"output": "current case"},
            _template(eval_id or scope.eval_id),
            organization_id=scope.organization_id,
            workspace_id=workspace_id,
        )["few_shots"]
    runner = _runner(
        scope,
        None if reader == "metric-derived" else workspace_id,
        dataset_bound=reader in ("metric", "metric-derived"),
    )
    if runner.user_eval_metric is not None:
        runner.user_eval_metric.workspace_id = workspace_id
        runner.dataset.workspace_id = workspace_id
    runner.eval_template = _template(eval_id or scope.eval_id)
    return runner.get_few_shot_examples(["current case"], ["output"])


@pytest.mark.parametrize("reader", ["engine", "standalone", "metric", "metric-derived"])
def test_readers_only_inject_run_workspace_feedback(
    feedback_store, ambient_foreign_workspace, reader
):
    scope = feedback_store
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
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
    result = _read(reader, scope, workspace_id=scope.workspace_id)
    assert "local-comment" in str(result), "SETUP_AQ-XWS-FB: local positive control"
    # Same-organization feedback saved with no workspace before the release
    # cutoff is admitted.
    assert "legacy-null-comment" in str(result), (
        "same-org pre-cutoff no-workspace feedback steers the run"
    )
    assert all(
        marker not in str(result)
        for marker in ("foreign-", "other-org-", "after-cutoff-null-")
    ), "readers must never inject another workspace's or org's feedback"


@pytest.mark.parametrize("reader", ["engine", "standalone", "metric", "metric-derived"])
def test_foreign_feedback_and_nonexistent_feedback_have_same_shape(
    feedback_store, ambient_foreign_workspace, reader
):
    scope = feedback_store
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    visible = _read(reader, scope, workspace_id=scope.foreign_workspace_id)
    assert "foreign-comment" in str(visible), "SETUP_AQ-XWS-FB: foreign row exists"
    foreign = _read(reader, scope, workspace_id=scope.workspace_id)
    nonexistent = _read(
        reader, scope, workspace_id=scope.workspace_id, eval_id=str(uuid.uuid4())
    )
    assert not foreign and foreign == nonexistent, (
        "AQ-XWS-FB: foreign and nonexistent feedback must have identical empty shape"
    )


@pytest.mark.parametrize("reader", ["engine", "standalone", "metric", "metric-derived"])
@pytest.mark.parametrize("missing_workspace", [None, ""])
def test_readers_fail_closed_without_run_workspace(
    feedback_store, ambient_foreign_workspace, reader, missing_workspace
):
    scope = feedback_store
    scope.insert("local", workspace_id=scope.workspace_id)
    visible = _read(reader, scope, workspace_id=scope.workspace_id)
    assert "local-comment" in str(visible), "SETUP_AQ-XWS-FB: scoped run works"
    result = _read(reader, scope, workspace_id=missing_workspace)
    assert result == [], "AQ-XWS-FB: unbound runs must proceed without few-shots"


@pytest.mark.parametrize(
    "broken_binding",
    [
        "run-workspace",
        "dataset-workspace",
        "execution-dataset-workspace",
        "metric-workspace",
        "missing-metric",
    ],
)
def test_dataset_reader_rechecks_persisted_binding(
    feedback_store, ambient_foreign_workspace, broken_binding
):
    scope = feedback_store
    scope.insert("local", workspace_id=scope.workspace_id)
    runner = _runner(scope, scope.workspace_id, dataset_bound=True)
    visible = runner.get_few_shot_examples(["current case"], ["output"])
    assert "local-comment" in str(visible), "SETUP_AQ-XWS-FB: bound metric works"
    if broken_binding == "run-workspace":
        runner.workspace_id = scope.foreign_workspace_id
    elif broken_binding == "dataset-workspace":
        runner.dataset.workspace_id = scope.foreign_workspace_id
    elif broken_binding == "execution-dataset-workspace":
        runner.dataset = SimpleNamespace(
            organization_id=scope.organization_id,
            workspace_id=scope.foreign_workspace_id,
        )
    elif broken_binding == "metric-workspace":
        runner.user_eval_metric.workspace_id = None
    else:
        runner.user_eval_metric = None
    result = runner.get_few_shot_examples(["current case"], ["output"])
    assert result == [], "AQ-XWS-FB: inconsistent or missing metric binding must refuse"


def test_template_ownership_cannot_supply_missing_run_binding(
    feedback_store, ambient_foreign_workspace
):
    from model_hub.views.eval_runner import EvaluationRunner

    scope = feedback_store
    scope.insert("local", workspace_id=scope.workspace_id)
    template = _template(scope.eval_id)
    template.organization = SimpleNamespace(id=scope.organization_id)
    template.workspace = SimpleNamespace(id=scope.workspace_id)
    template.organization_id = scope.organization_id
    template.workspace_id = scope.workspace_id
    bound = _runner(scope, scope.workspace_id)
    visible = bound.map_fields(["output"], ["current case"], eval_template=template)
    assert "local-comment" in str(visible), "SETUP_AQ-XWS-FB: bound template run works"
    unbound = EvaluationRunner(user_eval_metric_id=None, format_output=True)
    result = unbound.map_fields(["output"], ["current case"], eval_template=template)
    assert result["few_shots"] == [], (
        "AQ-XWS-FB: template ownership must not supply missing run tenancy"
    )


@pytest.mark.parametrize("method", ["average", "single", "count"])
@pytest.mark.parametrize("missing_field", ["organization_id", "workspace_id"])
@pytest.mark.parametrize("missing_value", [None, ""])
def test_embedding_entry_points_refuse_incomplete_feedback_scope(
    feedback_store, monkeypatch, method, missing_field, missing_value
):
    scope = feedback_store
    scope.insert("local", workspace_id=scope.workspace_id)
    tenancy = {
        "organization_id": scope.organization_id,
        "workspace_id": scope.workspace_id,
    }

    def retrieve():
        if method == "average":
            return scope.manager.retrieve_avg_rag_based_examples(
                eval_id=scope.eval_id,
                inputs=["current case"],
                input_cols=["output"],
                **tenancy,
            )
        if method == "single":
            return scope.manager.retrieve_rag_based_examples(
                query="current case",
                table_name=em.FEEDBACK_TABLE_NAME,
                eval_id=scope.eval_id,
                meta_data_col="output",
                **tenancy,
            )
        return scope.manager.get_feedback_count(eval_id=scope.eval_id, **tenancy)

    assert retrieve(), "SETUP_AQ-XWS-FB: embedding entry point has a visible row"
    tenancy[missing_field] = missing_value
    log = Mock()
    monkeypatch.setattr(em, "logger", log)
    result = retrieve()
    expected = 0 if method == "count" else []
    reason = (
        "feedback_retrieval_missing_organization"
        if missing_field == "organization_id"
        else "feedback_retrieval_missing_workspace"
    )
    assert (
        result == expected
        and log.warning.call_count == 1
        and log.warning.call_args.args == (reason,)
        and log.warning.call_args.kwargs == {}
    ), "AQ-XWS-FB: incomplete embedding scope must refuse with a value-free reason"


def test_manager_same_workspace_positive_and_foreign_absence_control(feedback_store):
    scope = feedback_store
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    result = scope.manager.retrieve_avg_rag_based_examples(
        eval_id=scope.eval_id,
        inputs=["current case"],
        input_cols=["output"],
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    # Only the run workspace's rows and same-org pre-cutoff rows come back.
    assert result and {
        item.get("workspace_id") for group in result for item in group
    } == {scope.workspace_id, None}, "real vector SQL scopes workspaces"
    assert "legacy-null-comment" in str(result), (
        "same-org pre-cutoff no-workspace row is retrieved"
    )
    assert "local-comment" in str(result), "SETUP_AQ-XWS-FB: local row is retrieved"
    assert "foreign-comment" not in str(result), (
        "SETUP_AQ-XWS-FB: foreign row is hidden"
    )
