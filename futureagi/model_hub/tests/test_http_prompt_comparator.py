"""Append a published prompt to HTTP evidence without replaying the endpoint.

Canonical evidence must come from the HTTP worker and native prompt activities.
Matrix exposes result IDs rather than output text, so those IDs must refer to
the same completed items whose outputs the grid displays.
"""

from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import reverse

from accounts.models.workspace import Workspace
from model_hub.models.experiments import ExperimentPromptConfig
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeRunBinding,
)
from model_hub.services.experiment_submit import state_of
from model_hub.tests import test_experiment_append_variant as prompts
from model_hub.tests.test_http_canonical_evidence import (
    _body,
    _complete,
    _edit,
    _evidence,
    _stage,
)
from model_hub.tests.test_http_experiment_source import entry as http_entry
from model_hub.tests.test_http_experiment_source import tenants as tenants
from model_hub.tests.test_native_prompt_create_bridge import (
    PROVIDER,
    finish,
    row,
    setup,
)
from model_hub.tests.test_native_prompt_create_bridge import (
    own_key_route as own_key_route,
)
from tfc.utils import outbound_destination

# The real worker helpers close old connections; use committed rows, not a
# rollback-only test transaction. Import own_key_route explicitly: importing
# setup/row/finish alone does not register their module's autouse fixture here.
pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]
START = prompts.START


def _payload(world):
    template, version = prompts.prompt(world.actor, "Prompt comparator")
    column = world.dataset.column_set.get()
    version.prompt_config_snapshot["messages"] = [
        {"role": "user", "content": "{{" + str(column.pk) + "}}"}
    ]
    version.save(update_fields=["prompt_config_snapshot"])
    return version, {
        "append_only": True,
        "prompt_config": [prompts.entry(template, version)],
    }


def _put(world, body, **headers):
    return world.actor["client"].put(
        f"/model-hub/experiments/v2/{world.experiment.pk}/",
        body,
        format="json",
        **headers,
    )


def _tables(world):
    return {
        **prompts.snapshot(world.experiment),
        **_evidence(world),
        "examples": list(
            NativeExampleBinding.objects.filter(snapshot__experiment=world.experiment)
            .order_by("id")
            .values()
        ),
    }


def _assert_retained(before, after):
    for kind, old_rows in before.items():
        saved = {item["id"]: item for item in after[kind]}
        assert all(saved.get(item["id"]) == item for item in old_rows), kind


def _assert_refused(world, body, *, message=None, status=400):
    before = _tables(world)
    submission = state_of(world.experiment)
    with patch(START) as start, patch.object(outbound_destination, "request") as send:
        response = _put(world, body, HTTP_IDEMPOTENCY_KEY=str(uuid4()))
    assert response.status_code == status, response.content
    if status == 404:
        assert response.json()["code"] == "not_found"
        assert response.json()["detail"] == "Prompt version not found"
    if message:
        assert message in response.content.decode(), response.content
    start.assert_not_called()
    send.assert_not_called()
    assert _tables(world) == before
    assert state_of(world.experiment) == submission


def test_completed_http_accepts_prompt_and_compares_real_completed_runs(tenants):
    world = _stage(tenants)
    http_run = _complete(world)
    version, body = _payload(world)
    before = _tables(world)
    with (
        patch(START, return_value="prompt-comparator-workflow") as start,
        patch.object(outbound_destination, "request") as send,
    ):
        # A prompt append uses its existing durable submission/retry contract,
        # not the HTTP mutation's required Idempotency-Key.
        _body(_put(world, body))
        config = ExperimentPromptConfig.no_workspace_objects.get(
            experiment_dataset__experiment=world.experiment, deleted=False
        )
        assert config.prompt_version_id == version.pk
        submission = state_of(world.experiment)
        args = submission["workflow_args"]
        assert submission["state"] == "accepted"
        assert args["rerun_prompt_config_ids"] == [str(config.pk)]
        assert args["rerun_agent_config_ids"] == []
        assert args["rerun_eval_template_ids"] == []
        assert args["column_changed"] is False
        assert set(args["native_runs"]) == {str(config.pk)}
        assert not args.get("http_only") and "http_plan" not in submission
        start.assert_called_once_with(
            experiment_id=str(world.experiment.pk),
            admission_id=submission["admission_id"],
            **args,
        )
        binding = NativeRunBinding.objects.select_related("run").get(
            variant=config.experiment_dataset
        )
        assert str(binding.execution_id) == submission["admission_id"]
        assert binding.run.status == "queued" and not binding.run.items.exists()
        _assert_retained(before, _tables(world))

        context, prepared = setup(world.experiment)
        assert context == args["native_runs"][str(config.pk)]
        assert set(prepared["row_ids"]) == {
            str(link.snapshot_row_id) for link in world.links
        }
        with patch(PROVIDER, autospec=True) as provider:
            for link in world.links:
                question = world.inputs[str(link.source_row_id)]
                provider.return_value = (f"prompt:{question}", {})
                result = row(
                    world.experiment, context, prepared, str(link.snapshot_row_id)
                )
                assert result["status"] == "COMPLETED", result
                assert question in str(provider.call_args.args[0].messages)
            assert provider.call_count == len(world.links)
            finish(context, prepared)
        send.assert_not_called()

    prompt_run = binding.run
    prompt_run.refresh_from_db()
    http_run.refresh_from_db()
    assert prompt_run.pk != http_run.pk
    assert prompt_run.status == http_run.status == "completed"
    assert prompt_run.completed_at is not None and http_run.completed_at is not None
    assert prompt_run.task_kind == "prompt" and http_run.task_kind == "external"
    assert prompt_run.prompt_version_id == version.pk
    assert http_run.prompt_version_id is None
    assert prompt_run.agent_version_id is http_run.agent_version_id is None
    assert prompt_run.dataset_version_id == http_run.dataset_version_id
    assert prompt_run.dataset_version_id == world.links[0].snapshot.dataset_version_id
    assert NativeRunBinding.objects.filter(variant=world.source).count() == 1
    assert (
        NativeRunBinding.objects.filter(snapshot__experiment=world.experiment).count()
        == 2
    )
    assert prompt_run.items.count() == http_run.items.count() == len(world.links)
    _assert_retained(before, _tables(world))

    before_reads = _tables(world)
    client = world.actor["client"]
    catalog = _body(
        client.get(
            reverse("dataset-results-runs", kwargs={"dataset_id": world.dataset.pk}),
            {"experiment_id": str(world.experiment.pk)},
        )
    )
    ids = {str(http_run.pk), str(prompt_run.pk)}
    assert {item["run_id"] for item in catalog["results"]} == ids
    query = {"runs": f"{http_run.pk},{prompt_run.pk}", "baseline": "none"}
    url = reverse("dataset-results", kwargs={"dataset_id": world.dataset.pk})
    grid = _body(client.get(url, query))
    matrix = _body(client.get(url, {**query, "shape": "matrix"}))
    for payload in (grid, matrix):
        headers = {item["run_id"]: item for item in payload["summary"]["runs"]}
        assert set(headers) == ids
        assert headers[str(http_run.pk)]["task_kind"] == "external"
        assert headers[str(prompt_run.pk)]["task_kind"] == "prompt"
        assert headers[str(prompt_run.pk)]["prompt_config_id"] == str(config.pk)
        assert {item["status"] for item in headers.values()} == {"completed"}
        assert {item["dataset_version_id"] for item in headers.values()} == {
            str(http_run.dataset_version_id)
        }
        assert payload["total"] == len(world.links)
    assert set(matrix["columns"]) == ids
    for link in world.links:
        example_id = str(link.version_item.example_id)
        question = world.inputs[str(link.source_row_id)]
        grid_row = next(
            item for item in grid["rows"] if item["example_id"] == example_id
        )
        matrix_row = next(
            item for item in matrix["rows"] if item["row_id"] == example_id
        )
        assert set(grid_row["results"]) == set(matrix_row["cells"]) == ids
        for run, output in (
            (http_run, f"answer:{question}"),
            (prompt_run, f"prompt:{question}"),
        ):
            item = run.items.get(example_id=link.version_item.example_id)
            assert item.status == "completed" and item.trial_number == 1
            assert item.output == output
            cell = grid_row["results"][str(run.pk)]
            assert cell["status"] == "completed" and cell["output"] == output
            assert cell["result_id"] == str(item.pk)
            matrix_cell = matrix_row["cells"][str(run.pk)]
            assert matrix_cell["result_ids"] == [str(item.pk)]
            assert matrix_cell["trial_numbers"] == [1]
    assert _tables(world) == before_reads


@pytest.mark.parametrize(
    "case",
    [
        "empty",
        "multiple",
        "missing_version",
        "draft",
        "mismatch",
        "existing_id",
        "mixed_edit",
        "mixed_http",
        "prompt_and_rows",
        "null",
    ],
)
def test_malformed_or_mixed_prompt_append_is_atomic(tenants, case):
    world = _stage(tenants)
    _complete(world)
    version, body = _payload(world)
    entry = body["prompt_config"][0]
    if case == "empty":
        body["prompt_config"] = []
    elif case == "multiple":
        body["prompt_config"].append(deepcopy(entry))
    elif case == "missing_version":
        del entry["prompt_version"]
    elif case == "draft":
        version.is_draft = True
        version.save(update_fields=["is_draft"])
    elif case == "mismatch":
        _, other = prompts.prompt(world.actor, "Different template")
        entry["prompt_version"] = str(other.pk)
    elif case == "existing_id":
        entry["id"] = str(world.source.pk)
    elif case == "mixed_edit":
        body["column_id"] = None
    elif case == "mixed_http":
        http = http_entry()
        http["variable_mapping"] = {"question": str(world.dataset.column_set.get().pk)}
        body["prompt_config"].append(http)
    elif case == "prompt_and_rows":
        body["row_ids"] = [str(world.links[0].source_row_id)]
    elif case == "null":
        body["prompt_config"] = None
    _assert_refused(world, body, status=404 if case == "mismatch" else 400)


@pytest.mark.parametrize("scope", ["organization", "workspace"])
@pytest.mark.parametrize("reference", ["pair", "prompt", "version"])
def test_foreign_prompt_references_refused_with_owner_control(
    tenants, scope, reference
):
    world = _stage(tenants)
    _complete(world)
    _, valid = _payload(world)
    actor = tenants["b"]
    if scope == "workspace":
        workspace = Workspace.objects.create(
            name="Other comparator workspace",
            organization=world.actor["org"],
            created_by=world.actor["user"],
            is_default=False,
        )
        actor = {**world.actor, "workspace": workspace}
    template, version = prompts.prompt(actor, "Foreign comparator")
    body = deepcopy(valid)
    entry = body["prompt_config"][0]
    if reference in {"pair", "prompt"}:
        entry["prompt_id"] = str(template.pk)
    if reference in {"pair", "version"}:
        entry["prompt_version"] = str(version.pk)
    _assert_refused(world, body, status=404)
    with patch(START, return_value="owner-comparator-workflow") as start:
        _body(_put(world, valid))
    start.assert_called_once()


@pytest.mark.parametrize("pending", [False, True])
def test_prompt_append_cannot_overlap_http_execution(tenants, pending):
    world = _stage(tenants, pending=pending)
    _, body = _payload(world)
    _assert_refused(
        world,
        body,
        message="unconfirmed" if pending else "current execution",
    )


def test_prompt_append_pending_retry_reuses_variant_run_and_dispatch(tenants):
    world = _stage(tenants)
    _complete(world)
    _, body = _payload(world)
    original = _tables(world)
    with (
        patch(START, side_effect=RuntimeError("transport unavailable")) as failed,
        patch.object(outbound_destination, "request") as send,
        patch(PROVIDER) as provider,
    ):
        response = _put(world, body)
    assert response.status_code == 503, response.content
    assert response.json()["code"] == "experiment_admission_unconfirmed"
    failed.assert_called_once()
    send.assert_not_called()
    provider.assert_not_called()
    pending = state_of(world.experiment)
    assert pending["state"] == "pending"
    config = ExperimentPromptConfig.no_workspace_objects.get(
        experiment_dataset__experiment=world.experiment
    )
    binding = NativeRunBinding.objects.select_related("run").get(
        variant=config.experiment_dataset
    )
    assert binding.run.status == "draft" and not binding.run.items.exists()
    _assert_retained(original, _tables(world))
    different = deepcopy(body)
    different["prompt_config"][0]["name"] = "Another comparator"
    _assert_refused(world, different, message="unconfirmed")
    with (
        patch(START, return_value="recovered-comparator-workflow") as resumed,
        patch.object(outbound_destination, "request") as send,
        patch(PROVIDER) as provider,
    ):
        _body(_put(world, body))
        assert resumed.call_args == failed.call_args
        assert state_of(world.experiment)["admission_id"] == pending["admission_id"]
        binding.run.refresh_from_db()
        assert binding.run.status == "queued"
        accepted = _tables(world)
        _body(_put(world, body))
        assert _tables(world) == accepted
        resumed.assert_called_once()
    send.assert_not_called()
    provider.assert_not_called()
    assert (
        NativeRunBinding.objects.filter(variant=config.experiment_dataset).count() == 1
    )
    assert NativeRunBinding.objects.filter(variant=world.source).count() == 1
    assert (
        ExperimentPromptConfig.no_workspace_objects.filter(
            experiment_dataset__experiment=world.experiment
        ).count()
        == 1
    )
    _assert_retained(original, _tables(world))


def test_http_edit_and_http_append_keep_the_http_path(tenants):
    world = _stage(tenants)
    _complete(world)
    _edit(world)
    before = _tables(world)
    entry = http_entry()
    entry["variable_mapping"] = {"question": str(world.dataset.column_set.get().pk)}
    body = {"append_only": True, "prompt_config": [entry]}
    with (
        patch(START, return_value="another-http-workflow") as start,
        patch.object(outbound_destination, "request") as send,
    ):
        _body(_put(world, body, HTTP_IDEMPOTENCY_KEY=str(uuid4())))
    start.assert_called_once()
    send.assert_not_called()
    submission = state_of(world.experiment)
    assert submission["workflow_args"]["http_only"] is True
    assert not submission["workflow_args"].get("native_runs")
    targets = submission["http_plan"]["targets"]
    assert len(targets) == 1 and str(world.source.pk) not in targets
    assert not ExperimentPromptConfig.no_workspace_objects.filter(
        experiment_dataset__experiment=world.experiment
    ).exists()
    assert NativeRunBinding.objects.filter(variant=world.source).count() == 1
    _assert_retained(before, _tables(world))


def test_plain_prompt_append_keeps_its_existing_dispatch(tenants):
    actor = tenants["a"]
    experiment, old, template, version, url = prompts.prepare(actor)
    before = prompts.snapshot(experiment)
    body = {"append_only": True, "prompt_config": [prompts.entry(template, version)]}
    with patch(START, return_value="plain-prompt-workflow") as start:
        _body(actor["client"].put(url, body, format="json"))
    start.assert_called_once()
    config = (
        ExperimentPromptConfig.no_workspace_objects.filter(
            experiment_dataset__experiment=experiment
        )
        .exclude(pk=old.pk)
        .get()
    )
    assert start.call_args.kwargs["rerun_prompt_config_ids"] == [str(config.pk)]
    assert not start.call_args.kwargs.get("http_only")
    _assert_retained(before, prompts.snapshot(experiment))
