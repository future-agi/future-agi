"""Empty feedback has one list shape without changing non-empty prompt blocks.

Reuse real tenant-scoped ClickHouse readers and the existing engine recorder.
Only inference, provider construction and explicit transport faults are stubbed;
parameter preparation, tenant guards, SQL construction and formatting stay real.
"""

import uuid
from types import SimpleNamespace

import pytest

from agentic_eval.core.embeddings import embedding_manager as em
from evaluations.engine import EvalRequest, run_eval
from evaluations.engine.params import prepare_run_params
from model_hub.tests.test_feedback_engine_scope_field import (
    engine_execution,  # noqa: F401
)
from model_hub.tests.test_feedback_rag_consumer_scope import (
    _django_db_helper,  # noqa: F401 - preserve isolated transactional teardown
    consumer_scope,  # noqa: F401
)
from model_hub.tests.test_feedback_rag_workspace_scope import feedback_store  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


@pytest.fixture(params=["prepare_run_params", "engine"])
def feedback_reader(
    request,
    consumer_scope,  # noqa: F811
    engine_execution,  # noqa: F811
):
    scope = consumer_scope

    def read(*, organization_id, workspace_id):
        inputs = {"output": "current case"}
        if request.param == "prepare_run_params":
            return prepare_run_params(
                inputs,
                scope.template,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )["few_shots"]

        before = len(engine_execution.recorder.calls)
        result = run_eval(
            EvalRequest(
                eval_template=scope.template,
                inputs=inputs,
                organization_id=organization_id,
                workspace_id=scope.workspace_id,
                feedback_workspace_id=workspace_id,
            )
        )
        assert (
            result.value == 1.0 and len(engine_execution.recorder.calls) == before + 1
        ), "SETUP_AQ-XWS-W4G: real engine reached the evaluator successfully"
        assert engine_execution.providers[-1] == scope.workspace_id, (
            "SETUP_AQ-XWS-W4G: feedback scope did not rebind the provider"
        )
        return engine_execution.recorder.calls[-1]["few_shots"]

    return read


@pytest.fixture
def feedback_contract(consumer_scope, feedback_reader):  # noqa: F811
    scope = consumer_scope
    foreign_workspace_id = str(uuid.uuid4())
    missing_workspace_id = str(uuid.uuid4())
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert(
        "foreign",
        workspace_id=foreign_workspace_id,
        organization_id=scope.foreign_organization_id,
    )
    local = feedback_reader(
        organization_id=scope.organization_id, workspace_id=scope.workspace_id
    )
    foreign = feedback_reader(
        organization_id=scope.foreign_organization_id,
        workspace_id=foreign_workspace_id,
    )
    assert isinstance(local, list) and "local-comment" in str(local), (
        "SETUP_AQ-XWS-W4G: same-scope real feedback is available"
    )
    assert isinstance(foreign, list) and "foreign-comment" in str(foreign), (
        "SETUP_AQ-XWS-W4G: foreign feedback exists and is visible in its own scope"
    )
    assert "foreign-comment" not in str(local) and "local-comment" not in str(
        foreign
    ), "SETUP_AQ-XWS-W4G: positive controls retain exact tenant isolation"
    return SimpleNamespace(
        scope=scope,
        read=feedback_reader,
        local=local,
        foreign=foreign,
        foreign_workspace_id=foreign_workspace_id,
        missing_workspace_id=missing_workspace_id,
    )


def _read_with_failed_transport(contract, monkeypatch, stage):
    """Fail the driver, not the guarded count/vector reader or formatter."""
    client_class = type(contract.scope.manager.db_client.client)
    execute = client_class.execute
    failed_queries = []
    selector = "COUNT(*)" if stage == "count" else "cosineDistance"

    def fail_feedback_query(client, query, *args, **kwargs):
        if "FROM feedbacks" in query and selector in query:
            failed_queries.append(stage)
            raise RuntimeError("SETUP_AQ-XWS-W4G: injected feedback transport failure")
        return execute(client, query, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(client_class, "execute", fail_feedback_query)
        result = contract.read(
            organization_id=contract.scope.organization_id,
            workspace_id=contract.scope.workspace_id,
        )
    assert failed_queries == [stage], (
        "SETUP_AQ-XWS-W4G: the selected real feedback query reached the failing driver"
    )
    return result


def test_all_no_example_outcomes_have_identical_empty_list_shape(
    feedback_contract, monkeypatch
):
    contract = feedback_contract
    scope = contract.scope
    embedding_calls = []

    def distant_embedding(text):
        embedding_calls.append(text)
        return [0.0, 1.0]

    # A real same-scope row exists, but distance 1 exceeds the production cap.
    with monkeypatch.context() as patch:
        patch.setattr(
            em,
            "model_manager",
            SimpleNamespace(_use_serving=True, text_model=distant_embedding),
        )
        zero_match = contract.read(
            organization_id=scope.organization_id, workspace_id=scope.workspace_id
        )
    assert embedding_calls == ["current case"], (
        "SETUP_AQ-XWS-W4G: zero-match path passed the real scoped count and embedded input"
    )

    results = {
        "same_scope_zero_matches": zero_match,
        "foreign_scope": contract.read(
            organization_id=scope.organization_id,
            workspace_id=contract.foreign_workspace_id,
        ),
        "nonexistent_scope": contract.read(
            organization_id=scope.organization_id,
            workspace_id=contract.missing_workspace_id,
        ),
        "explicit_null_scope": contract.read(
            organization_id=scope.organization_id, workspace_id=None
        ),
        "missing_organization": contract.read(
            organization_id=None, workspace_id=scope.workspace_id
        ),
        "count_transport_failure": _read_with_failed_transport(
            contract, monkeypatch, "count"
        ),
        "vector_transport_failure": _read_with_failed_transport(
            contract, monkeypatch, "vector"
        ),
    }
    health_calls = []

    def failed_health_check():
        health_calls.append(True)
        raise RuntimeError("SETUP_AQ-XWS-W4G: injected retrieval dependency failure")

    # This exception propagates to params.py, unlike swallowed driver failures.
    with monkeypatch.context() as patch:
        patch.setattr(em, "serving_available", failed_health_check)
        results["retrieval_exception"] = contract.read(
            organization_id=scope.organization_id, workspace_id=scope.workspace_id
        )
    assert health_calls == [True], (
        "SETUP_AQ-XWS-W4G: scoped retrieval reached the failing dependency"
    )
    assert results == {name: [] for name in results}, (
        "AQ-XWS-W4G: zero-match foreign nonexistent NULL missing-organization "
        "and failed retrieval must all return []"
    )


def test_foreign_and_nonexistent_feedback_have_the_same_empty_shape(feedback_contract):
    contract = feedback_contract
    foreign = contract.read(
        organization_id=contract.scope.organization_id,
        workspace_id=contract.foreign_workspace_id,
    )
    nonexistent = contract.read(
        organization_id=contract.scope.organization_id,
        workspace_id=contract.missing_workspace_id,
    )
    assert foreign == nonexistent == [], (
        "AQ-XWS-W4G: foreign and nonexistent feedback scopes must share the empty-list shape"
    )


def test_real_examples_preserve_formatted_content_blocks(feedback_contract):
    from ee.prompts.eval_prompts import FEEDBACK_FEWSHOT_PROMPT

    for marker, examples in (
        ("local", feedback_contract.local),
        ("foreign", feedback_contract.foreign),
    ):
        assert examples == [
            {"type": "text", "text": FEEDBACK_FEWSHOT_PROMPT},
            {"type": "text", "text": f"{marker}-input"},
            {
                "type": "text",
                "text": f"Feedback: {marker}-comment, Corrected Label: {marker}-label",
            },
        ], "SETUP_AQ-XWS-W4G: real non-empty examples retain exact formatted content"
