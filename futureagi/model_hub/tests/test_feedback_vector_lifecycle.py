import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import transaction

from accounts.models import Organization, User
from agentic_eval.core.database import ch_vector
from agentic_eval.core.embeddings import embedding_manager
from conftest import _open_ch_test_native_client
from model_hub.models.evals_metric import EvalTemplate, Feedback
from model_hub.utils import optimization_feedback
from model_hub.utils.optimization_feedback import DatasetFeedbackVectorDB


@pytest.fixture
def feedback_factory(db):
    organization = Organization.objects.create(name="Vector lifecycle tests")
    user = User.objects.create_user(
        email="vector-lifecycle@example.com",
        password="test-password",
        organization=organization,
    )
    template = EvalTemplate.objects.create(
        name="vector-lifecycle-eval",
        description="Feedback lifecycle regression",
        organization=organization,
        config={"output": "Pass/Fail", "eval_type_id": "test_eval_type"},
    )

    def create_feedback(**overrides):
        fields = {
            "source": "eval_playground",
            "source_id": str(uuid.uuid4()),
            "eval_template": template,
            "organization": organization,
            "user": user,
            "value": "Failed",
            "explanation": "superseded correction",
        }
        fields.update(overrides)
        return Feedback.objects.create(**fields)

    return create_feedback


@pytest.fixture
def vector_manager(monkeypatch):
    database = f"test_feedback_lifecycle_{uuid.uuid4().hex}"
    admin = _open_ch_test_native_client(owned_database=database)
    admin.execute(f"CREATE DATABASE {database}")
    # ClickHouseVectorDB connects with get_clickhouse_client_kwargs(), whose
    # env defaults (clickhouse:9000) CI does not set. Give every vector client
    # the sidecar target the guard just proved for the admin client.
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
        embedding_manager,
        "model_manager",
        SimpleNamespace(_use_serving=True, text_model=lambda text: [1.0, 0.0]),
    )
    monkeypatch.setattr(embedding_manager, "serving_available", lambda: True)
    manager = embedding_manager.EmbeddingManager()
    try:
        manager.db_client.create_table("feedbacks", database=database)
        yield manager
    finally:
        manager.db_client.close()
        admin.execute(f"DROP DATABASE {database} SYNC")
        admin.disconnect()


def embed_feedback(manager, feedback, inputs=("prompt", "response")):
    return manager.data_formatter(
        row_dict={
            "feedback_id": str(feedback.id),
            "prompt": "What is the identifier?",
            "response": "The identifier is ABC.",
            "feedback_comment": feedback.explanation,
            "feedback_value": feedback.value,
        },
        inputs_formater=list(inputs),
        insert=True,
        eval_id=str(feedback.eval_template_id),
        organization_id=str(feedback.organization_id),
    )


def active_metadata(manager):
    rows = manager.db_client.client.execute(
        "SELECT metadata.key, metadata.value FROM feedbacks WHERE deleted = 0"
    )
    return [dict(zip(keys, values, strict=True)) for keys, values in rows]


def formatted_context(manager, feedback):
    examples = manager.retrieve_avg_rag_based_examples(
        eval_id=str(feedback.eval_template_id),
        inputs=["What is the identifier?", "The identifier is ABC."],
        input_cols=["prompt", "response"],
        organization_id=str(feedback.organization_id),
    )
    return str(
        manager.process_examples(
            examples,
            inputs=["prompt", "response"],
            feedback_col_name="feedback_comment",
            corrected_label_col_name="feedback_value",
        )
    )


@pytest.mark.integration
def test_retune_replaces_both_inputs_and_preserves_other_feedback(
    feedback_factory, vector_manager
):
    feedback = feedback_factory()
    unrelated = feedback_factory(explanation="unrelated correction")
    embed_feedback(vector_manager, feedback)
    embed_feedback(vector_manager, unrelated)
    feedback.explanation = "current correction"
    feedback.value = "Passed"
    feedback.save()
    embed_feedback(vector_manager, feedback)
    embed_feedback(vector_manager, feedback)

    rows = active_metadata(vector_manager)
    assert len(rows) == 4
    current = [row for row in rows if row["feedback_id"] == str(feedback.id)]
    assert {row["feedback_input"] for row in current} == {"prompt", "response"}
    assert {row["feedback_comment"] for row in current} == {"current correction"}
    context = formatted_context(vector_manager, feedback)
    assert "current correction" in context
    assert "unrelated correction" in context
    assert "superseded correction" not in context


@pytest.mark.integration
def test_retune_revokes_removed_input_fields(feedback_factory, vector_manager):
    feedback = feedback_factory()
    embed_feedback(vector_manager, feedback)
    feedback.explanation = "current correction"
    feedback.save()
    embed_feedback(vector_manager, feedback, inputs=("prompt",))

    rows = active_metadata(vector_manager)
    assert len(rows) == 1
    assert rows[0]["feedback_input"] == "prompt"
    assert rows[0]["feedback_comment"] == "current correction"


@pytest.mark.integration
def test_retune_with_no_inputs_revokes_previous_vectors(
    feedback_factory, vector_manager
):
    feedback = feedback_factory()
    embed_feedback(vector_manager, feedback)

    embed_feedback(vector_manager, feedback, inputs=())

    assert active_metadata(vector_manager) == []


@pytest.mark.integration
@pytest.mark.parametrize("failure", ["exception", "empty", "missing"])
def test_embedding_failure_does_not_remove_previous_vectors(
    feedback_factory, vector_manager, monkeypatch, failure
):
    feedback = feedback_factory()
    embed_feedback(vector_manager, feedback)

    def failing_embedding(text):
        if text == "The identifier is ABC.":
            if failure == "exception":
                raise RuntimeError("embedding service unavailable")
            return [] if failure == "empty" else None
        return [1.0, 0.0]

    monkeypatch.setattr(
        embedding_manager.model_manager, "text_model", failing_embedding
    )
    feedback.explanation = "replacement correction"
    vectors, metadata = embed_feedback(vector_manager, feedback)

    assert vectors == []
    assert metadata == []
    rows = active_metadata(vector_manager)
    assert len(rows) == 2
    assert {row["feedback_comment"] for row in rows} == {"superseded correction"}


@pytest.mark.integration
def test_insert_failure_does_not_remove_previous_vectors(
    feedback_factory, vector_manager, monkeypatch
):
    feedback = feedback_factory()
    embed_feedback(vector_manager, feedback)

    original_insert = vector_manager.insert_embedding
    insert_count = 0

    def fail_on_second_insert(*args, **kwargs):
        nonlocal insert_count
        insert_count += 1
        if insert_count == 2:
            raise RuntimeError("clickhouse insert unavailable")
        return original_insert(*args, **kwargs)

    monkeypatch.setattr(vector_manager, "insert_embedding", fail_on_second_insert)
    feedback.explanation = "replacement correction"
    vectors, metadata = embed_feedback(vector_manager, feedback)

    assert vectors == []
    assert metadata == []
    rows = active_metadata(vector_manager)
    assert len(rows) == 2
    assert {row["feedback_comment"] for row in rows} == {"superseded correction"}


@pytest.mark.integration
def test_retune_does_not_reembed_after_revoking_previous_vectors(
    feedback_factory, vector_manager, monkeypatch
):
    feedback = feedback_factory()
    embedded_inputs = []

    def embedding_once(text):
        assert text not in embedded_inputs
        embedded_inputs.append(text)
        return [1.0, 0.0]

    monkeypatch.setattr(embedding_manager.model_manager, "text_model", embedding_once)
    vectors, metadata = embed_feedback(vector_manager, feedback)

    assert len(vectors) == len(metadata) == len(embedded_inputs) == 2
    assert len(active_metadata(vector_manager)) == 2


@pytest.mark.integration
def test_retune_revokes_legacy_vectors_with_feedback_identity(
    feedback_factory, vector_manager
):
    feedback = feedback_factory()
    vector_manager.db_client.upsert_vector(
        "feedbacks",
        str(feedback.eval_template_id),
        [1.0, 0.0],
        {
            "feedback_id": str(feedback.id),
            "item_id": str(uuid.uuid4()),
            "prompt": "What is the identifier?",
            "feedback_comment": "legacy correction",
            "feedback_value": "Failed",
            "input_type": "text",
            "organization_id": str(feedback.organization_id),
        },
        ["item_id"],
    )
    embed_feedback(vector_manager, feedback, inputs=("prompt",))

    rows = active_metadata(vector_manager)
    assert len(rows) == 1
    assert rows[0]["feedback_comment"] == feedback.explanation


@pytest.mark.integration
def test_delete_revokes_vectors_from_previous_eval_template(
    feedback_factory, vector_manager, django_capture_on_commit_callbacks
):
    feedback = feedback_factory()
    embed_feedback(vector_manager, feedback)
    feedback.eval_template = EvalTemplate.objects.create(
        name="replacement-template",
        description="Changed feedback target",
        organization=feedback.organization,
        config={"output": "Pass/Fail", "eval_type_id": "test_eval_type"},
    )
    feedback.save()

    with django_capture_on_commit_callbacks(execute=True):
        feedback.delete()

    assert active_metadata(vector_manager) == []


@pytest.mark.integration
@pytest.mark.parametrize("deletion", ["instance", "queryset", "cascade"])
def test_delete_revokes_retrieval_after_commit(
    deletion, feedback_factory, vector_manager, django_capture_on_commit_callbacks
):
    feedback = feedback_factory()
    unrelated = feedback_factory(explanation="unrelated correction")
    embed_feedback(vector_manager, feedback)
    embed_feedback(vector_manager, unrelated)
    feedback_id = str(feedback.id)

    with django_capture_on_commit_callbacks(execute=True):
        if deletion == "instance":
            feedback.delete()
        elif deletion == "queryset":
            Feedback.objects.filter(pk=feedback.pk).delete()
        else:
            separate_user = User.objects.create_user(
                email="cascade-vector@example.com",
                password="test-password",
                organization=feedback.organization,
            )
            feedback.user = separate_user
            feedback.save()
            User.objects.filter(pk=separate_user.pk).delete()

    rows = active_metadata(vector_manager)
    assert all(row["feedback_id"] != feedback_id for row in rows)
    assert len(rows) == 2
    context = formatted_context(vector_manager, unrelated)
    assert "superseded correction" not in context
    assert "unrelated correction" in context


@pytest.mark.parametrize("deletion", ["instance", "queryset"])
def test_rolled_back_delete_does_not_revoke_vectors(feedback_factory, deletion):
    feedback = feedback_factory()
    feedback_id = feedback.pk
    with patch("agentic_eval.core.database.ch_vector.ClickHouseVectorDB") as client:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                if deletion == "instance":
                    feedback.delete()
                else:
                    Feedback.objects.filter(pk=feedback.pk).delete()
                raise RuntimeError("rollback")
        client.assert_not_called()
    assert Feedback.objects.filter(pk=feedback_id).exists()


@pytest.mark.integration
def test_runtime_grounding_retains_feedback_and_tenant_scope(
    feedback_factory, vector_manager, monkeypatch
):
    from evaluations.engine.params import prepare_run_params

    feedback = feedback_factory(explanation="owning tenant correction")
    embed_feedback(vector_manager, feedback)
    # Deliberately give another tenant the same eval/item input shape. Retrieval
    # must still exclude its feedback, even with workspace filtering disabled.
    vector_manager.db_client.upsert_vector(
        "feedbacks",
        str(feedback.eval_template_id),
        [1.0, 0.0],
        {
            "item_id": str(uuid.uuid4()),
            "prompt": "What is the identifier?",
            "response": "The identifier is ABC.",
            "feedback_comment": "other tenant correction",
            "feedback_value": "Passed",
            "input_type": "text",
            "organization_id": str(uuid.uuid4()),
        },
        ["item_id"],
    )
    template = feedback.eval_template
    template.config = {
        "eval_type_id": "AgentEvaluator",
        "required_keys": ["prompt", "response"],
        "optional_keys": ["reference_context"],
    }
    queries = []
    monkeypatch.setattr(
        embedding_manager.model_manager,
        "text_model",
        lambda text: queries.append(text) or [1.0, 0.0],
    )
    monkeypatch.setattr(embedding_manager, "EmbeddingManager", lambda: vector_manager)
    inputs = {
        "trace_context": {"id": "synthetic-trace"},
        "prompt": "What is the identifier?",
        "span_context": {"id": "synthetic-span"},
        "response": "The identifier is ABC.",
        "session_context": {"id": "synthetic-session"},
        "ground_truth_blocks": [{"type": "text", "text": "runtime ground truth"}],
    }
    params = prepare_run_params(
        inputs, template, organization_id=str(feedback.organization_id)
    )
    assert "owning tenant correction" in str(params["few_shots"])
    assert "other tenant correction" not in str(params["few_shots"])
    assert "runtime ground truth" not in str(params["few_shots"])
    assert queries == [
        "What is the identifier?",
        "The identifier is ABC.",
    ]
    for key, value in inputs.items():
        assert params[key] == value


def test_dataset_feedback_search_without_eligible_rows_runs_no_query():
    with patch(
        "agentic_eval.core.database.ch_vector.clickhouse_driver.Client"
    ) as client:
        empty = DatasetFeedbackVectorDB([])
        scoped = DatasetFeedbackVectorDB([str(uuid.uuid4())])
        eval_id = str(uuid.uuid4())
        assert empty.vector_similarity_search("feedbacks", [1.0], eval_id=eval_id) == []
        assert (
            scoped.vector_similarity_search("embeddings", [1.0], eval_id=eval_id) == []
        )
        assert scoped.vector_similarity_search("feedbacks", [1.0]) == []
        assert (
            scoped.vector_similarity_search_with_threshold(
                "feedbacks", [1.0], dataset_id=eval_id, threshold=1.0
            )
            == []
        )
    client.return_value.execute.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("selection", ["own", "empty", "missing", "many", "batched"])
def test_feedback_identity_filter_runs_before_ranking(
    feedback_factory, vector_manager, monkeypatch, selection
):
    own = feedback_factory(explanation="selected correction")
    others = [feedback_factory(explanation="other correction") for _ in range(11)]
    # Other feedback matches the query exactly; the selected feedback is farther
    # away but within the retrieval threshold. The 22 closer vectors fill each
    # per-input LIMIT of 20, so the selection has to apply before ranking.
    monkeypatch.setattr(
        embedding_manager.model_manager,
        "text_model",
        lambda text: [0.8, 0.6] if text.startswith("Selected") else [1.0, 0.0],
    )
    for other in others:
        embed_feedback(vector_manager, other)
    vector_manager.data_formatter(
        row_dict={
            "feedback_id": str(own.id),
            "prompt": "Selected identifier question?",
            "response": "Selected identifier answer.",
            "feedback_comment": own.explanation,
            "feedback_value": own.value,
        },
        inputs_formater=["prompt", "response"],
        insert=True,
        eval_id=str(own.eval_template_id),
        organization_id=str(own.organization_id),
    )
    identities = {
        "own": [str(own.id)],
        "empty": [],
        "missing": [str(uuid.uuid4())],
        "many": [str(uuid.uuid4()) for _ in range(1000)] + [str(own.id)],
        "batched": [str(own.id)],
    }[selection]
    if selection == "batched":
        # The selected feedback has two vectors: one search per vector row.
        monkeypatch.setattr(optimization_feedback, "ID_BATCH_SIZE", 1)
    original_client = vector_manager.db_client
    vector_manager.db_client = DatasetFeedbackVectorDB(identities)
    try:
        examples = vector_manager.retrieve_avg_rag_based_examples(
            eval_id=str(own.eval_template_id),
            inputs=["What is the identifier?", "The identifier is ABC."],
            input_cols=["prompt", "response"],
            organization_id=str(own.organization_id),
            top_k=1,
        )
    finally:
        vector_manager.db_client.close()
        vector_manager.db_client = original_client
    if selection in ("empty", "missing"):
        assert examples == []
        return
    context = str(
        vector_manager.process_examples(
            examples,
            inputs=["prompt", "response"],
            feedback_col_name="feedback_comment",
            corrected_label_col_name="feedback_value",
        )
    )
    assert "selected correction" in context
    assert "other correction" not in context
