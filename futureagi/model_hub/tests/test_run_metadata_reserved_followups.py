"""Root-only reserved metadata follow-ups; execute in the qualified test venue."""
# Imported fixtures are injected by pytest.
# ruff: noqa: F811

from copy import deepcopy
from uuid import uuid4

import pytest
from opentelemetry.trace import NoOpTracer
from rest_framework.exceptions import ValidationError

from model_hub.models.experiments import ExperimentRunItem
from model_hub.serializers.experiment_run_api import ExternalRunCreateRequestSerializer
from model_hub.services.run_metadata_public import (
    public_run_metadata,
    reject_reserved_metadata_keys,
)
from model_hub.tests.test_experiment_run_writes import (
    _claim,
    _start,
    run_fixture,  # noqa: F401
)
from tfc.utils.api_contracts import _validate_serializer
from tfc.utils.api_errors import build_error_envelope


@pytest.mark.parametrize("key", ["native_snapshot", "_futureagi_run_write"])
def test_root_only_reservation_preserves_nested_caller_data(key):
    # P3-1: the native_snapshot case already passes on the ACKed base. The
    # write-namespace case detects the inconsistent root-only key definition.
    nested = {key: {"marker": "nested caller data"}}
    public = {"caller": nested, "contexts": [deepcopy(nested)]}
    before = deepcopy(public)
    reject_reserved_metadata_keys({"metadata": public})
    field = ExternalRunCreateRequestSerializer().fields["metadata"]
    assert field.run_validation(public) == before
    projected = public_run_metadata(public)
    assert projected == before
    assert projected is not public
    assert projected["caller"] is nested
    assert field.to_representation(public) == before

    stored = {**public, key: {"marker": "root private data"}}
    with pytest.raises(ValidationError) as caught:
        reject_reserved_metadata_keys({"metadata": stored})
    assert build_error_envelope(caught.value.detail)["code"] == "reserved_metadata_key"
    assert "root private data" not in str(caught.value.detail)
    assert public_run_metadata(stored) == before
    assert field.to_representation(stored) == before
    assert stored[key] == {"marker": "root private data"}
    assert public == before


def test_reserved_metadata_definition_is_closed_and_shared():
    # P3-2: no replacement names may silently expand the reserved namespace.
    from model_hub.services import experiment_run_writes, run_metadata_public

    assert isinstance(run_metadata_public.RESERVED_METADATA_KEYS, frozenset)
    assert run_metadata_public.RESERVED_METADATA_KEYS == frozenset(
        {"native_snapshot", "_futureagi_run_write"}
    )
    assert run_metadata_public.RESERVED_METADATA_CODE == "reserved_metadata_key"
    assert experiment_run_writes._RUN_KEY == run_metadata_public.RUN_WRITE_METADATA_KEY
    stored = {
        "native_snapshot": {"marker": "private snapshot"},
        "_futureagi_run_write": {"marker": "private receipt"},
        "Native_Snapshot": "ordinary caller key",
        "caller": {"_futureagi_run_write": "ordinary nested value"},
    }
    before = deepcopy(stored)
    assert public_run_metadata(stored) == {
        "Native_Snapshot": "ordinary caller key",
        "caller": {"_futureagi_run_write": "ordinary nested value"},
    }
    assert stored == before


@pytest.mark.parametrize("serializer_name", [
    "ExperimentCreateV2Serializer", "ExperimentUpdateV2Serializer",
    "ExperimentsTableSerializer", "ExperimentsTableUpdateSerializer",
    "ExperimentRunSerializer", "ExperimentRunItemSerializer",
    "ExternalRunCreateRequestSerializer", "ExternalRunResultsRequestSerializer",
])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("value", [None, "private-marker", {}, {"private-marker": []}])
def test_write_namespace_rejected_with_shared_code_before_other_validation(
    serializer_name, partial, value
):
    # P3-2: exercise every existing raw-input guard, including immutable fields.
    from model_hub.serializers import experiment_run_api, experiments

    module = experiment_run_api if serializer_name.startswith("External") else experiments
    _, errors, valid = _validate_serializer(
        getattr(module, serializer_name),
        {"metadata": {"_futureagi_run_write": value}},
        reject_unknown_fields=True,
        partial=partial,
    )
    assert not valid
    envelope = build_error_envelope(errors)
    assert envelope["code"] == "reserved_metadata_key"
    assert "private-marker" not in str(envelope)


def test_external_create_write_namespace_error_is_typed_for_valid_body():
    # P3-2: reach the former validate_metadata special case with valid fields.
    body = {
        "run_id": str(uuid4()),
        "dataset_version_id": str(uuid4()),
        "expected_items": [{"example_id": str(uuid4()), "trial_number": 1}],
        "metadata": {"_futureagi_run_write": None},
    }
    serializer = ExternalRunCreateRequestSerializer(data=body)
    assert not serializer.is_valid()
    assert build_error_envelope(serializer.errors)["code"] == "reserved_metadata_key"
    with pytest.raises(ValidationError) as caught:
        serializer.validate_metadata(body["metadata"])
    assert build_error_envelope(caught.value.detail)["code"] == "reserved_metadata_key"


@pytest.mark.parametrize("opaque_reserved", [False, True])
def test_python_task_context_projects_before_copying_reserved_values(opaque_reserved):
    # P3-3: use an existing/legacy item, independently of claim sanitization.
    from model_hub.services.python_task_adapter import _execute

    class NoCopy:
        def __deepcopy__(self, memo):
            raise AssertionError("reserved metadata was copied")

        def __str__(self):
            raise AssertionError("reserved metadata was stringified")

    private = NoCopy() if opaque_reserved else {"marker": "private root data"}
    public = {"caller": [{"native_snapshot": "nested", "_futureagi_run_write": [1]}]}
    stored = {
        **deepcopy(public), "native_snapshot": private, "_futureagi_run_write": private,
    }
    payload = {"native_snapshot": "arbitrary task input"}
    expected = {"_futureagi_run_write": "arbitrary expected value"}
    item = ExperimentRunItem(
        run_id=uuid4(), example_id=uuid4(), trial_number=1,
        input=deepcopy(payload), expected=deepcopy(expected), metadata=stored,
    )
    observed = []

    def task(value, context):
        observed.append(deepcopy(context.metadata))
        assert value == payload
        assert context.expected == expected
        context.metadata["caller"][0]["_futureagi_run_write"].append(2)
        value["native_snapshot"] = "callback mutation"
        context.expected["_futureagi_run_write"] = "callback mutation"
        return {"native_snapshot": "arbitrary task output"}

    result = _execute(item, task, NoOpTracer(), [], {})
    assert result["status"] == "completed"
    assert result["output"] == {"native_snapshot": "arbitrary task output"}
    assert observed == [public]
    assert item.metadata["caller"] == public["caller"]
    assert item.metadata["native_snapshot"] is private
    assert item.metadata["_futureagi_run_write"] is private
    assert item.input == payload
    assert item.expected == expected


@pytest.mark.django_db
def test_claim_strips_all_reserved_roots_but_preserves_server_receipt(run_fixture):
    # P3-4: only the inherited metadata root is sanitized, not example payloads.
    run, examples, scope, _ = run_fixture
    example = examples[0]
    public = {"caller": [{"native_snapshot": "nested", "_futureagi_run_write": [1]}]}
    inherited = {
        **deepcopy(public),
        "native_snapshot": {"marker": "caller snapshot"},
        "_futureagi_run_write": {"claim_receipt": {"receipt_key": "caller receipt"}},
    }
    example.metadata = deepcopy(inherited)
    example.input = {"native_snapshot": "example input"}
    example.expected = {"_futureagi_run_write": "example expected"}
    example.save(update_fields=["metadata", "input", "expected"])
    _start(run, examples, scope)
    item = _claim(run, example, scope)
    item.refresh_from_db()
    assert item.metadata == {
        **public,
        "_futureagi_run_write": {"claim_receipt": {"receipt_key": str(example.id)}},
    }
    assert item.input == example.input
    assert item.expected == example.expected
    replay = _claim(run, example, scope)
    assert replay.pk == item.pk
    assert replay.metadata == item.metadata
    example.refresh_from_db()
    assert example.metadata == inherited


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["native_snapshot", "_futureagi_run_write"])
def test_create_rejects_reserved_caller_root_before_persistence_and_hash(
    run_fixture, user, key, monkeypatch
):
    # P3-4: creation rejects either reserved root before hashing or persistence;
    # claim sanitization remains separate, and caller input is never mutated.
    from unittest.mock import Mock

    from model_hub.models.experiments import ExperimentRun
    from model_hub.services import experiment_run_creation
    from model_hub.services.experiment_run_writes import RunWriteError

    fixture_run, examples, _, _ = run_fixture
    public = {"caller": [{"native_snapshot": "nested", "_futureagi_run_write": [1]}]}
    supplied = {**deepcopy(public), key: {"marker": "caller private root"}}
    before = deepcopy(supplied)
    kwargs = {
        "run_id": uuid4(), "dataset_version": fixture_run.dataset_version,
        "created_by": user, "task_kind": "external",
        "expected_items": [(example.example_id, 1) for example in examples],
    }
    run_count = ExperimentRun.all_objects.count()
    request_hash = Mock(wraps=experiment_run_creation._digest)
    monkeypatch.setattr(experiment_run_creation, "_digest", request_hash)

    with pytest.raises(RunWriteError, match="reserved run namespace"):
        experiment_run_creation.create_experiment_run(**kwargs, metadata=supplied)

    request_hash.assert_not_called()
    assert not ExperimentRun.all_objects.filter(pk=kwargs["run_id"]).exists()
    assert ExperimentRun.all_objects.count() == run_count
    assert supplied == before
