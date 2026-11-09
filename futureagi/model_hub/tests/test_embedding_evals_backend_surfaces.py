"""Embedding Similarity and Semantic List Contains run in the backend on every surface.

Every eval surface reaches the evaluator through one of two shared paths,
and both build it with ``evaluations.engine.instance.create_eval_instance``:

- ``evaluations.engine.runner.run_eval``: Observe and online eval tasks (span,
  trace and session targets) and the SDK evaluate API, sync and async.
- ``EvaluationRunner._run_evaluation``: datasets (whole column and selected
  rows) and experiments (output, base-column and row-wise evals).

The eval playground (``run_eval_func``) uses the runner's instance builder and
is driven through its API. On each path the seeded system template must run
the backend function, never the code sandbox, and a binding's ``params`` must
reach it, as they reached the sandboxed code.

Model serving is a fake ``ModelServingClient``; the code sandbox fails the
test if anything calls it.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from rest_framework import status

from evaluations.engine.runner import EvalRequest, run_eval
from model_hub.management.commands.seed_system_evals import (
    _yaml_to_template_fields,
    load_yaml_evals,
)
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.views.eval_runner import EvaluationRunner

OUTPUT = "Refunds take five days. Shipping is free!"
PHRASES = '["money back"]'
VECTORS = {
    "Refunds take five days": [1.0, 0.0],
    "Shipping is free": [0.0, 1.0],
    # Cosine 0.6 with the first sentence and 0.8 with the second.
    "money back": [3.0, 4.0],
    "refund issued": [1.0, 0.0],
    "money returned": [3.0, 4.0],
}
# The best sentence scores 0.8: a 0.9 threshold only applies if the binding's
# params reach the evaluator (the default, 0.5, would match).
PARAMS = {"similarity_threshold": 0.9}
SEMANTIC_REASON = (
    "Matched 0/1 phrases (threshold=0.9; partial matches allowed). 'money back': 0.8."
)
SEMANTIC_BATCH = ["Refunds take five days", "Shipping is free", "money back"]
EMBEDDING_REASON = "Cosine similarity (text): 0.6000"


class FakeServingClient:
    """``ModelServingClient`` with fixed vectors. Records each /embed batch;
    any other method is missing, so the backend must batch as the sandboxed
    code did."""

    def __init__(self):
        self.batches = []

    def embed_text_batch(self, texts):
        self.batches.append(list(texts))
        return [VECTORS[text] for text in texts]


@pytest.fixture
def serving(monkeypatch):
    from agentic_eval.core.embeddings import serving_client

    client = FakeServingClient()
    monkeypatch.setattr(serving_client, "get_serving_client", lambda: client)
    return client


@pytest.fixture
def no_code_sandbox(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a system embedding eval ran in the code sandbox")

    monkeypatch.setattr(
        "agentic_eval.core_evals.fi_evals.function.functions.CodeExecution.execute",
        refuse,
    )


@pytest.fixture
def system_template(db):
    """The seeded system template, built from its YAML as the seeder does."""
    definitions = {d["name"]: d for d in load_yaml_evals()}

    def make(name):
        fields = _yaml_to_template_fields(copy.deepcopy(definitions[name]))
        return EvalTemplate.no_workspace_objects.create(**fields)

    return make


# ─────────────────────────────────────────────────────────────────────
# run_eval: Observe tasks and the SDK
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_run_eval_scores_semantic_list_contains_with_the_binding_params(
    system_template, serving, no_code_sandbox, organization
):
    result = run_eval(
        EvalRequest(
            eval_template=system_template("semantic_list_contains"),
            inputs={"output": OUTPUT, "expected": PHRASES},
            # CustomEvalConfig.config on Observe; the request config on the SDK.
            runtime_config={"params": PARAMS},
            organization_id=str(organization.id),
        )
    )

    assert result.value == 0.0
    assert result.reason == SEMANTIC_REASON
    assert serving.batches == [SEMANTIC_BATCH]


@pytest.mark.django_db
def test_run_eval_scores_embedding_similarity(
    system_template, serving, no_code_sandbox, organization
):
    result = run_eval(
        EvalRequest(
            eval_template=system_template("embedding_similarity"),
            inputs={"output": "refund issued", "expected": "money returned"},
            organization_id=str(organization.id),
        )
    )

    assert result.value == 0.6
    assert result.reason == EMBEDDING_REASON
    assert serving.batches == [["refund issued", "money returned"]]
    # The result echoes its inputs; serving's internal address is not one.
    assert "_model_serving_url" not in (result.data or {})


# ─────────────────────────────────────────────────────────────────────
# EvaluationRunner: datasets and experiments
# ─────────────────────────────────────────────────────────────────────


def _dataset_row(organization, user, workspace, output, expected):
    dataset = Dataset.objects.create(
        name=f"embedding-evals-{uuid.uuid4().hex[:8]}",
        organization=organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
        workspace=workspace,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    mapping = {}
    for key, value in (("output", output), ("expected", expected)):
        column = Column.objects.create(
            dataset=dataset,
            name=f"{key}-column",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=column,
            value=value,
            status=CellStatus.PASS.value,
        )
        mapping[key] = str(column.id)
    return dataset, row, mapping


def _dataset_runner(template, dataset, binding_config, organization, user, workspace):
    metric = UserEvalMetric.objects.create(
        name=f"embedding-evals-{uuid.uuid4().hex[:8]}",
        dataset=dataset,
        template=template,
        user=user,
        workspace=workspace,
        organization=organization,
        model="turing_large",
        config=binding_config,
    )
    runner = EvaluationRunner(
        user_eval_metric_id=str(metric.id),
        is_only_eval=True,
        # Skips the constructor's metric loading; the run wires it below.
        format_output=True,
        organization_id=organization.id,
        workspace_id=workspace.id,
    )
    runner.user_eval_metric = metric
    runner.eval_template = template
    runner.dataset = dataset
    # Only read for its id while resolving the mapping.
    runner.column = MagicMock(id=uuid.uuid4())
    return runner


@pytest.mark.django_db
def test_a_dataset_row_scores_in_the_backend_with_the_binding_params(
    system_template, serving, no_code_sandbox, organization, user, workspace
):
    dataset, row, mapping = _dataset_row(organization, user, workspace, OUTPUT, PHRASES)
    binding_config = {
        "mapping": mapping,
        # The legacy configure form stores the template's config keys here.
        # The sandboxed code never read them, and the evaluator's
        # constructor takes none: they must not fail or change the run.
        "config": {"similarity_threshold": 0.5, "match_all": True},
        "params": PARAMS,
        "reason_column": True,
    }
    runner = _dataset_runner(
        system_template("semantic_list_contains"),
        dataset,
        binding_config,
        organization,
        user,
        workspace,
    )

    eval_result = runner._run_evaluation(
        row,
        mapping,
        runner.update_config_list_values(binding_config["config"], row),
    )[0].eval_results[0]

    assert eval_result["metrics"][0]["value"] == 0.0
    assert eval_result["reason"] == SEMANTIC_REASON
    assert serving.batches == [SEMANTIC_BATCH]


@pytest.mark.django_db
def test_a_binding_pinned_to_a_pre_switch_version_runs_in_the_backend(
    system_template, serving, no_code_sandbox, organization, user, workspace
):
    """Versions are immutable, so a binding pinned to a system version seeded
    before the switch snapshots the sandboxed code, eval_type_id
    CustomCodeEval included. That code scored the same but cannot reach
    serving: the backend function runs instead."""
    template = system_template("semantic_list_contains")
    legacy = (
        Path(__file__).resolve().parents[1]
        / "system_evals"
        / "tests"
        / "legacy_serving_evals"
        / "semantic_list_contains.yaml.txt"
    )
    pinned = EvalTemplateVersion.objects.create_version(
        eval_template=template,
        config_snapshot=yaml.safe_load(legacy.read_text())["config"],
        criteria=template.criteria or "",
        model="",
    )
    dataset, row, mapping = _dataset_row(organization, user, workspace, OUTPUT, PHRASES)
    runner = _dataset_runner(
        template,
        dataset,
        {"mapping": mapping, "params": PARAMS},
        organization,
        user,
        workspace,
    )
    runner.user_eval_metric.pinned_version = pinned
    runner.user_eval_metric.save(update_fields=["pinned_version"])
    assert runner._get_effective_eval_config()["eval_type_id"] == "CustomCodeEval"

    eval_result = runner._run_evaluation(row, mapping, {})[0].eval_results[0]

    assert eval_result["metrics"][0]["value"] == 0.0
    assert eval_result["reason"] == SEMANTIC_REASON
    assert serving.batches == [SEMANTIC_BATCH]


# ─────────────────────────────────────────────────────────────────────
# The eval playground
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_the_playground_scores_in_the_backend_with_the_params(
    system_template, serving, no_code_sandbox, auth_client
):
    template = system_template("semantic_list_contains")

    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": OUTPUT, "expected": PHRASES},
            "config": {"params": PARAMS},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["output"] == 0.0
    assert response.data["result"]["reason"] == SEMANTIC_REASON
    assert serving.batches == [SEMANTIC_BATCH]
