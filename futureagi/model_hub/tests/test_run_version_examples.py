# ruff: noqa: F811
import uuid
from copy import deepcopy

import pytest
from django.db.models.signals import post_init

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.dataset_versions import DatasetVersionItem
from model_hub.models.develop_dataset import Row
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeRunBinding,
)
from model_hub.services.dataset_version_service import (
    DatasetVersionItemInput,
    create_dataset_version,
)
from model_hub.services.experiment_run_creation import create_experiment_run
from model_hub.services.experiment_run_writes import RunWriteError, _digest
from model_hub.services.native_run_binding import (
    BoundExample,
    http_version_examples,
    run_examples,
    snapshot_examples,
)
from model_hub.services.native_run_recording import (
    _bound_run,
    _example,
    finish_native_run,
    mark_native_run,
    trace_context,
)
from model_hub.tests.test_native_run_binding import (
    bind,
    graph_variant,
    legacy_native_world,  # noqa: F401 -- imported fixture
    native_world,  # noqa: F401 -- imported fixture
)
from model_hub.tests.test_native_run_recording import record, start

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("kind", ["prompt", "graph", "legacy"])
def test_native_resolution_preserves_selected_snapshot_examples(kind, request):
    w = request.getfixturevalue(
        "legacy_native_world" if kind == "legacy" else "native_world"
    )
    if kind == "graph":
        w = graph_variant(w, w["dataset"].workspace)
    row_id = next(iter(w["row_map"].values())).pk
    run = bind(w, snapshot_row_ids=[str(row_id)])
    binding = run.native_binding
    link = next(
        x for x in snapshot_examples(binding.snapshot) if x.snapshot_row_id == row_id
    )
    expected = BoundExample(
        link.source_row_id, link.snapshot_row, link.version_item, link.row_order
    )
    assert run_examples(binding, run) == [expected]
    assert _example(binding, run, row_id, 1) == expected
    with pytest.raises(RunWriteError):
        _example(binding, run, row_id, 2)
    mark_native_run(dataset=w["dataset"], run_id=run.pk, status="queued")
    start(w, run, expected)
    saved = record(w, run, expected, output={"answer": "saved"})
    assert (saved.example_id, saved.input, saved.output) == (
        link.version_item.example_id,
        link.version_item.input,
        {"answer": "saved"},
    )
    assert finish_native_run(dataset=w["dataset"], run_id=run.pk).status == "completed"


@pytest.mark.parametrize("kind", ["prompt", "graph", "legacy"])
@pytest.mark.parametrize("deleted_after", ["admission", "claim", "seal"])
def test_unrelated_copied_row_preserves_recording_and_replay(
    kind,
    deleted_after,
    request,
):
    w = request.getfixturevalue(
        "legacy_native_world" if kind == "legacy" else "native_world"
    )
    if kind == "graph":
        w = graph_variant(w, w["dataset"].workspace)
    row_id = next(iter(w["row_map"].values())).pk
    run = bind(w, snapshot_row_ids=[str(row_id)])
    binding = run.native_binding
    links = snapshot_examples(binding.snapshot)
    link = next(x for x in links if x.snapshot_row_id == row_id)
    other = next(x for x in links if x.snapshot_row_id != row_id)
    mark_native_run(dataset=w["dataset"], run_id=run.pk, status="queued")
    if deleted_after != "admission":
        start(w, run, link)
    if deleted_after == "seal":
        record(w, run, link, output="saved")
        assert (
            finish_native_run(
                dataset=w["dataset"],
                run_id=run.pk,
            ).status
            == "completed"
        )
    other.snapshot_row.delete()
    claimed = start(w, run, link)
    saved = record(w, run, link, output="saved")
    assert (saved.pk, saved.example_id, saved.input, saved.output) == (
        claimed.pk,
        link.version_item.example_id,
        link.version_item.input,
        "saved",
    )
    before = deepcopy(saved.metadata)
    assert start(w, run, link).pk == saved.pk
    assert record(w, run, link, output="saved").metadata == before
    assert run.items.count() == 1
    assert trace_context(
        dataset=w["dataset"],
        run_id=run.pk,
        snapshot_row_id=row_id,
    ) == {
        "fi.experiment.run_id": str(run.pk),
        "fi.experiment.example_id": str(link.version_item.example_id),
        "fi.experiment.trial_number": 1,
        "fi.experiment.execution_id": str(binding.execution_id),
    }
    with pytest.raises(RunWriteError):
        run_examples(binding, run)
    with pytest.raises(RunWriteError):
        finish_native_run(dataset=w["dataset"], run_id=run.pk)
    with pytest.raises(RunWriteError):
        bind(w, execution_id=uuid.uuid4(), snapshot_row_ids=[str(row_id)])
    link.snapshot_row.delete()
    with pytest.raises(RunWriteError):
        start(w, run, link)
    with pytest.raises(RunWriteError):
        record(w, run, link, output="saved")


def test_row_lookup_loads_only_the_requested_identity(
    native_world,
    django_assert_num_queries,
):
    run = bind(native_world)
    binding = run.native_binding
    link = snapshot_examples(binding.snapshot)[0]
    loaded = {NativeExampleBinding: [], Row: [], DatasetVersionItem: []}

    def capture(sender, instance, **kwargs):
        loaded[sender].append(instance.pk)

    for model in loaded:
        post_init.connect(capture, sender=model)
    try:
        with django_assert_num_queries(1):
            resolved = _example(binding, run, link.snapshot_row_id, 1)
    finally:
        for model in loaded:
            post_init.disconnect(capture, sender=model)
    assert resolved == BoundExample(
        link.source_row_id,
        link.snapshot_row,
        link.version_item,
        link.row_order,
    )
    assert loaded == {
        NativeExampleBinding: [link.pk],
        Row: [link.snapshot_row_id],
        DatasetVersionItem: [link.version_item_id],
    }


@pytest.fixture
def mapped_run(native_world):
    w = native_world
    snapshot = bind(w).native_binding.snapshot
    links = snapshot_examples(snapshot)
    version = create_dataset_version(
        dataset=w["dataset"],
        created_by=w["created_by"],
        parent_version=snapshot.dataset_version,
        items=[
            DatasetVersionItemInput(
                input=x.version_item.input,
                metadata=x.version_item.metadata,
                example_id=x.version_item.example_id,
                legacy_row_id=x.source_row_id,
            )
            for x in links
        ],
    )
    members = {x.example_id: x for x in version.items.all()}
    entries = [
        {
            "snapshot_row_id": str(x.snapshot_row_id),
            "source_row_id": str(x.source_row_id),
            "version_item_id": str(members[x.version_item.example_id].pk),
            "example_id": str(x.version_item.example_id),
            "row_order": x.row_order,
            "content_hash": members[x.version_item.example_id].content_hash,
        }
        for x in links
    ]
    variant = w["variant"]
    variant.legacy_prompt_config = {"target_type": "http"}
    variant.save()
    execution = uuid.uuid4()
    identity = {
        "execution_mode": "http",
        "snapshot_id": str(snapshot.pk),
        "variant_id": str(variant.pk),
        "native_config_id": str(variant.pk),
        "output_column_id": str(w["output_column"].pk),
        "execution_id": str(execution),
        "snapshot_row_ids": [x["snapshot_row_id"] for x in entries],
        "http_examples": entries,
    }
    run = create_experiment_run(
        run_id=uuid.uuid4(),
        dataset_version=version,
        created_by=w["created_by"],
        task_kind="external",
        experiment=w["experiment"],
        expected_items=[(x.version_item.example_id, 1) for x in links],
        metadata={"native_identity": identity},
    )
    binding = NativeRunBinding.objects.create(
        run=run,
        snapshot=snapshot,
        variant=variant,
        execution_id=execution,
        native_config_id=variant.pk,
        output_column=w["output_column"],
        request_hash=_digest(identity),
    )
    return binding, run, links


def test_http_map_survives_source_deletion_without_read_writes(mapped_run):
    binding, run, links = mapped_run
    before = deepcopy(run.metadata)
    for link in links:
        link.version_item.legacy_row.delete()
    resolved = run_examples(binding, run)
    assert [x.source_row_id for x in resolved] == [x.source_row_id for x in links]
    assert [x.version_item.dataset_version_id for x in resolved] == [
        run.dataset_version_id,
        run.dataset_version_id,
    ]
    assert [x.version_item.input for x in resolved] == [
        x.version_item.input for x in links
    ]
    assert http_version_examples(binding, run) == resolved
    run.refresh_from_db()
    assert run.metadata == before


@pytest.mark.parametrize("swap_order", [False, True])
def test_http_map_rejects_crossed_valid_copied_rows(mapped_run, swap_order):
    binding, run, links = mapped_run
    assert [x.snapshot_row_id for x in run_examples(binding, run)] == [
        x.snapshot_row_id for x in links
    ]
    identity = run.metadata["native_identity"]
    first, second = identity["http_examples"]
    first["snapshot_row_id"], second["snapshot_row_id"] = (
        second["snapshot_row_id"],
        first["snapshot_row_id"],
    )
    if swap_order:
        first["row_order"], second["row_order"] = (
            second["row_order"],
            first["row_order"],
        )
    binding.request_hash = _digest(identity)
    with pytest.raises(
        RunWriteError,
        match="HTTP example member is unavailable or inconsistent",
    ):
        run_examples(binding, run)


@pytest.mark.parametrize(
    "field",
    [
        "source_row_id",
        "snapshot_row_id",
        "version_item_id",
        "example_id",
        "content_hash",
        "row_order",
    ],
)
def test_http_map_rejects_mismatched_member_identity(mapped_run, field):
    binding, run, _ = mapped_run
    entry = run.metadata["native_identity"]["http_examples"][0]
    entry[field] = None if field == "row_order" else str(uuid.uuid4())
    binding.request_hash = _digest(run.metadata["native_identity"])
    with pytest.raises(RunWriteError):
        run_examples(binding, run)


@pytest.mark.parametrize(
    "field",
    [
        "source_row_id",
        "snapshot_row_id",
        "version_item_id",
        "example_id",
    ],
)
def test_http_map_refuses_ambiguous_selected_identities(mapped_run, field):
    binding, run, _ = mapped_run
    entries = run.metadata["native_identity"]["http_examples"]
    entries[1][field] = entries[0][field]
    binding.request_hash = _digest(run.metadata["native_identity"])
    with pytest.raises(RunWriteError):
        run_examples(binding, run)


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "unsealed",
        "plan",
        "member",
        "foreign_row",
        "native",
        "binding",
        "uncaptured_row",
    ],
)
def test_http_map_requires_sealed_exact_version_and_plan(mapped_run, change):
    binding, run, links = mapped_run
    identity = run.metadata["native_identity"]
    if change == "missing":
        del identity["http_examples"]
    elif change == "unsealed":
        binding.request_hash = "invalid"
    elif change == "plan":
        identity["http_examples"].pop()
        identity["snapshot_row_ids"].pop()
    elif change in {"foreign_row", "uncaptured_row"}:
        foreign = str(links[0].source_row_id)
        if change == "uncaptured_row":
            foreign = str(
                Row.objects.create(
                    dataset_id=binding.snapshot.snapshot_dataset_id,
                    order=30,
                ).pk
            )
        identity["http_examples"][0]["snapshot_row_id"] = foreign
        identity["snapshot_row_ids"][0] = foreign
    elif change == "native":
        run.task_kind = "prompt"
    elif change == "binding":
        binding.run_id = uuid.uuid4()
    else:
        identity["http_examples"][0]["version_item_id"] = str(links[0].version_item_id)
    if change != "unsealed":
        binding.request_hash = _digest(identity)
    with pytest.raises(RunWriteError):
        run_examples(binding, run)


@pytest.mark.parametrize("field", ["organization_id", "workspace_id"])
def test_http_map_refuses_foreign_version_tenant(mapped_run, user, field):
    binding, run, _ = mapped_run
    foreign = Organization.objects.create(name="Other version organization")
    workspace = Workspace.objects.create(
        name="Other version workspace",
        organization=run.experiment.dataset.organization,
        created_by=user,
    )
    setattr(
        run.dataset_version,
        field,
        foreign.pk if field == "organization_id" else workspace.pk,
    )
    with pytest.raises(RunWriteError):
        run_examples(binding, run)


def test_http_original_version_without_map_keeps_existing_resolution(native_world):
    from unittest.mock import patch

    from model_hub.services.http_run_recording import bind_http_run

    w = native_world
    snapshot = bind(w).native_binding.snapshot
    w["variant"].legacy_prompt_config = {
        "target_type": "http",
        "_http_execution": {},
    }
    w["variant"].save()
    run = bind_http_run(
        w["experiment"],
        w["variant"],
        snapshot,
        w["output_column"],
        uuid.uuid4(),
        {},
    )
    expected = [
        BoundExample(x.source_row_id, x.snapshot_row, x.version_item, x.row_order)
        for x in snapshot_examples(snapshot)
    ]
    assert run_examples(run.native_binding, run) == expected
    assert run.metadata["native_identity"]["http_examples"] == [
        {
            "snapshot_row_id": str(x.snapshot_row_id),
            "source_row_id": str(x.source_row_id),
            "version_item_id": str(x.version_item_id),
            "example_id": str(x.version_item.example_id),
            "content_hash": x.version_item.content_hash,
            "row_order": x.row_order,
        }
        for x in expected
    ]
    assert run.native_binding.request_hash == _digest(run.metadata["native_identity"])

    def historical_run(**kwargs):
        # Omit the map before creation so both creation and binding seals are genuine.
        kwargs["metadata"]["native_identity"].pop("http_examples", None)
        return create_experiment_run(**kwargs)

    with patch(
        "model_hub.services.http_run_recording.create_experiment_run",
        side_effect=historical_run,
    ):
        run = bind_http_run(
            w["experiment"],
            w["variant"],
            snapshot,
            w["output_column"],
            uuid.uuid4(),
            {},
        )
    assert run_examples(run.native_binding, run) == expected
    assert run.metadata["native_identity"].get("http_examples") is None
    assert run.native_binding.request_hash == _digest(run.metadata["native_identity"])


def test_append_version_recording_binds_the_sealed_map(mapped_run):
    binding, run, _ = mapped_run
    _, bound_run, bound = _bound_run(run.experiment.dataset, run.pk)
    assert (bound.pk, bound_run.pk) == (binding.pk, run.pk)
    entries = run.metadata["native_identity"]["http_examples"]
    assert [
        (str(x.snapshot_row_id), str(x.version_item_id))
        for x in run_examples(bound, bound_run)
    ] == [(e["snapshot_row_id"], e["version_item_id"]) for e in entries]
    assert run.items.count() == 0
