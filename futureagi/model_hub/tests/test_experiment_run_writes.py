import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun
from model_hub.services.experiment_run_writes import (
    RunWriteError,
    _canonical,
    _digest,
    claim_run_item,
    finalize_run,
    pin_run_plan,
    record_run_item_result,
    transition_run,
)


def test_canonical_receipt_is_stable_for_mapping_order():
    assert _digest({"b": 2, "a": 1}) == _digest({"a": 1, "b": 2})


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        (1, 2),
        {1: "bad"},
        {"bad\x00": "string"},
        {"bad\ud800": "string"},
        object(),
        pytest.param(10**4301, id="oversized-integer"),
    ],
)
def test_canonical_receipt_rejects_non_json_payloads(value):
    with pytest.raises(RunWriteError):
        _canonical(value)


def test_canonical_receipt_rejects_cycles():
    value = []
    value.append(value)
    with pytest.raises(RunWriteError):
        _canonical(value)


@pytest.fixture
def run_fixture(organization, workspace, user):
    dataset = Dataset.objects.create(
        name="Run write dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.objects.create(dataset_version=version, input={"x": number})
        for number in (1, 2)
    ]
    evaluator_id = str(uuid.uuid4())
    run = ExperimentRun.objects.create(
        dataset_version=version, evaluator_versions=[{"id": evaluator_id}]
    )
    scope = {"organization_id": organization.id, "workspace_id": workspace.id}
    return run, examples, scope, evaluator_id


def _start(run, examples, scope):
    pin_run_plan(
        run_id=run.id,
        expected_items=[(example.example_id, 1) for example in examples],
        **scope,
    )
    transition_run(run_id=run.id, status="queued", **scope)
    transition_run(run_id=run.id, status="running", **scope)


def _claim(run, example, scope):
    return claim_run_item(
        run_id=run.id,
        example_id=example.example_id,
        receipt_key=str(example.id),
        **scope,
    )


@pytest.mark.django_db
def test_claim_membership_and_receipt_are_durable(run_fixture):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    first = _claim(run, examples[0], scope)
    repeated = _claim(run, examples[0], scope)
    assert repeated.pk == first.pk
    assert repeated.input == examples[0].input
    assert run.items.count() == 1
    with pytest.raises(RunWriteError, match="already claimed"):
        claim_run_item(
            run_id=run.id,
            example_id=examples[0].example_id,
            receipt_key="other",
            **scope,
        )


@pytest.mark.django_db(transaction=True)
def test_concurrent_claims_produce_one_running_item(run_fixture):
    from threading import Barrier

    from django.db import close_old_connections

    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    barrier = Barrier(2)

    def attempt(receipt):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            claim_run_item(
                run_id=run.id,
                example_id=examples[0].example_id,
                receipt_key=receipt,
                **scope,
            )
            return "ok"
        except RunWriteError:
            return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["thread-a", "thread-b"]))
    assert results.count("ok") == 1
    assert run.items.filter(example_id=examples[0].example_id).count() == 1
    with pytest.raises(RunWriteError, match="pinned run plan"):
        claim_run_item(
            run_id=run.id, example_id=uuid.uuid4(), receipt_key="absent", **scope
        )


@pytest.mark.django_db
def test_result_is_terminal_write_once_with_identical_receipt_retry(run_fixture):
    run, examples, scope, evaluator_id = run_fixture
    _start(run, examples, scope)
    item = _claim(run, examples[0], scope)
    payload = dict(
        item_id=item.id,
        status="completed",
        receipt_key="result-1",
        output={"answer": 2},
        score_refs=[
            {"evaluator_version_id": evaluator_id, "status": "assessed", "value": 0.8}
        ],
        **scope,
    )
    first = record_run_item_result(**payload)
    first_updated = first.updated_at
    second = record_run_item_result(**payload)
    assert second.pk == first.pk
    assert second.updated_at == first_updated
    assert second.metadata["_futureagi_run_write"]["result_receipt"]["payload_hash"]
    with pytest.raises(RunWriteError, match="cannot be overwritten"):
        record_run_item_result(**{**payload, "output": {"answer": 3}})
    with pytest.raises(RunWriteError, match="cannot be overwritten"):
        record_run_item_result(**{**payload, "receipt_key": "new"})


@pytest.mark.django_db
def test_finalize_requires_all_planned_items(run_fixture):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    first = _claim(run, examples[0], scope)
    record_run_item_result(
        item_id=first.id, status="completed", receipt_key="r1", **scope
    )
    with pytest.raises(RunWriteError, match="pinned plan"):
        finalize_run(run_id=run.id, **scope)
    second = _claim(run, examples[1], scope)
    with pytest.raises(RunWriteError, match="pending or running"):
        finalize_run(run_id=run.id, **scope)
    record_run_item_result(
        item_id=second.id, status="completed", receipt_key="r2", **scope
    )
    finalized = finalize_run(run_id=run.id, **scope)
    assert finalized.status == "completed"
    assert finalized.completed_at >= finalized.started_at


@pytest.mark.django_db
def test_transition_cannot_bypass_finalization_and_cancel_blocks_writes(run_fixture):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    item = _claim(run, examples[0], scope)
    with pytest.raises(RunWriteError, match="cannot transition"):
        transition_run(run_id=run.id, status="completed", **scope)
    transition_run(run_id=run.id, status="cancelled", **scope)
    with pytest.raises(RunWriteError, match="terminal"):
        record_run_item_result(
            item_id=item.id, status="completed", receipt_key="r", **scope
        )
    with pytest.raises(RunWriteError, match="sealed run"):
        transition_run(run_id=run.id, status="running", **scope)


@pytest.mark.django_db
def test_score_refs_require_pinned_evaluator_and_valid_status(run_fixture):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    item = _claim(run, examples[0], scope)
    with pytest.raises(RunWriteError, match="not pinned"):
        record_run_item_result(
            item_id=item.id,
            status="completed",
            receipt_key="r",
            score_refs=[{"evaluator_version_id": str(uuid.uuid4()), "value": 0.7}],
            **scope,
        )
    item.refresh_from_db()
    assert item.status == "running"


@pytest.mark.django_db
def test_write_scope_uses_parent_dataset_tenant(run_fixture):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    with pytest.raises(RunWriteError, match="not found"):
        claim_run_item(
            run_id=run.id,
            example_id=examples[0].example_id,
            receipt_key="x",
            **{**scope, "organization_id": uuid.uuid4()},
        )
    with pytest.raises(RunWriteError, match="not found"):
        claim_run_item(
            run_id=run.id,
            example_id=examples[0].example_id,
            receipt_key="x",
            **{**scope, "workspace_id": uuid.uuid4()},
        )
    # String UUIDs are accepted consistently at the service boundary.
    item = claim_run_item(
        run_id=str(run.id),
        example_id=str(examples[0].example_id),
        receipt_key="x",
        **{key: str(value) for key, value in scope.items()},
    )
    assert item.example_id == examples[0].example_id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "first_status,expected", [("failed", "failed"), ("completed", "partial")]
)
def test_final_partial_and_failed_are_sealed_and_finalize_retry_is_noop(
    run_fixture, first_status, expected
):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    for number, example in enumerate(examples):
        item = _claim(run, example, scope)
        record_run_item_result(
            item_id=item.id,
            status=first_status if number == 0 else "failed",
            receipt_key=f"r{number}",
            **scope,
        )
    first = finalize_run(run_id=run.id, **scope)
    repeated = finalize_run(run_id=run.id, **scope)
    assert first.status == expected
    assert first.completed_at == repeated.completed_at
    assert first.updated_at == repeated.updated_at
    with pytest.raises(RunWriteError, match="sealed"):
        transition_run(run_id=run.id, status="running", **scope)
    with pytest.raises(RunWriteError, match="executing"):
        _claim(run, examples[0], scope)


@pytest.mark.django_db
@pytest.mark.parametrize("deleted_parent", ["dataset", "version"])
def test_deleted_parent_rejects_claim_and_result(run_fixture, deleted_parent):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    item = _claim(run, examples[0], scope)
    target = (
        run.dataset_version.dataset
        if deleted_parent == "dataset"
        else run.dataset_version
    )
    type(target).no_workspace_objects.filter(pk=target.pk).update(deleted=True)
    with pytest.raises(RunWriteError, match="not found"):
        _claim(run, examples[1], scope)
    with pytest.raises(RunWriteError, match="not found"):
        record_run_item_result(
            item_id=item.id, status="completed", receipt_key="r", **scope
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "score,status",
    [("0.8", "assessed"), (10**400, "assessed"), (0.8, []), (float("nan"), "assessed")],
)
def test_result_rejects_malformed_score_without_changing_item(
    run_fixture, score, status
):
    run, examples, scope, evaluator_id = run_fixture
    _start(run, examples, scope)
    item = _claim(run, examples[0], scope)
    with pytest.raises(RunWriteError):
        record_run_item_result(
            item_id=item.id,
            status="completed",
            receipt_key="r",
            score_refs=[
                {"evaluator_version_id": evaluator_id, "status": status, "value": score}
            ],
            **scope,
        )
    item.refresh_from_db()
    assert item.status == "running"


@pytest.mark.django_db
def test_plan_freezes_before_running_and_rejects_tampered_digest(run_fixture):
    run, examples, scope, _ = run_fixture
    with pytest.raises(RunWriteError, match="pinned"):
        transition_run(run_id=run.id, status="queued", **scope)
    _start(run, examples, scope)
    with pytest.raises(RunWriteError, match="already pinned"):
        pin_run_plan(
            run_id=run.id, expected_items=[(examples[0].example_id, 1)], **scope
        )
    run.refresh_from_db()
    run.metadata["_futureagi_run_write"]["plan"]["identities"].pop()
    run.save(update_fields=["metadata"])
    with pytest.raises(RunWriteError, match="digest"):
        finalize_run(run_id=run.id, **scope)


@pytest.mark.django_db
def test_completed_result_cannot_contain_error_and_cross_workspace_result_is_hidden(
    run_fixture,
):
    run, examples, scope, _ = run_fixture
    _start(run, examples, scope)
    item = _claim(run, examples[0], scope)
    with pytest.raises(RunWriteError, match="cannot contain an error"):
        record_run_item_result(
            item_id=item.id,
            status="completed",
            error="failed",
            receipt_key="r",
            **scope,
        )
    with pytest.raises(RunWriteError, match="not found"):
        record_run_item_result(
            item_id=item.id,
            status="completed",
            receipt_key="r",
            **{**scope, "workspace_id": uuid.uuid4()},
        )
    with pytest.raises(RunWriteError, match="valid UUID"):
        claim_run_item(run_id=run.id, example_id="invalid", receipt_key="r", **scope)
