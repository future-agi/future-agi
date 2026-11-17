"""Canonical-reference regressions with real normalization, views and worker ORM.

Reuse the adjacent suites' graph and request fixtures, but not their identity
normalizer worker fixture. Only execution/dispatch and external boundaries are
stubbed; reference extraction, scoped queries and refusal persistence are real.
"""

import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from structlog.testing import capture_logs
from temporalio.exceptions import ApplicationError

from ee.agent_opt.utils.template_variables import normalize_prompt_text
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.develop_dataset import Cell
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.models.run_prompt import RunPrompter
from model_hub.tests import test_optimizer_copy_writers_and_worker_refusal as copy_cases
from model_hub.tests import (
    test_optimizer_placeholder_writers_and_updates as writer_cases,
)
from model_hub.tests.test_optimizer_copy_writers_and_worker_refusal import (
    copy_writer as copy_writer,
)
from model_hub.tests.test_optimizer_copy_writers_and_worker_refusal import (
    graph as graph,
)
from model_hub.tests.test_optimizer_placeholder_writers_and_updates import (
    writer as writer,
)
from model_hub.utils.optimizer_prompt_text import (
    COLUMN_REFERENCE_PATTERN,
    optimizer_column_references,
    optimizer_prompt_text,
)
from model_hub.views import run_prompt
from tfc.temporal.dataset_optimization import activities

pytestmark = pytest.mark.django_db


def _messages(column_id, layout, spelling, suffix=""):
    canonical = "{{" + str(column_id) + suffix + "}}"
    if spelling == "compatibility":
        token = "".join(chr(ord(char) + 0xFEE0) for char in canonical)
    else:
        token = canonical[:12] + "​‌‍‎‏﻿" + canonical[12:]
    assert (
        normalize_prompt_text.__module__ == "ee.agent_opt.utils.template_variables"
    ), "SETUP_AQ-XWS-W5G must use the production normalizer"
    assert normalize_prompt_text(token) == canonical, (
        "SETUP_AQ-XWS-W5G raw token must normalize to the canonical reference"
    )
    assert COLUMN_REFERENCE_PATTERN.search(token) is None, (
        "SETUP_AQ-XWS-W5G raw token must evade the pre-fix raw regex"
    )
    # Non-reference Unicode must stay byte-for-byte unchanged in persisted writers.
    text = "Keep Å​ text " + token
    if layout == "whole":
        return [{"role": "user", "content": text}]
    left, right = text[:30], text[30:]
    if layout == "parts":
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": left},
                    {"type": "text", "text": right},
                ],
            }
        ]
    return [{"role": "system", "content": left}, {"role": "user", "content": right}]


@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize("suffix", ["", ".value", "[0]"])
def test_extractor_checks_canonical_fields_and_assembly(
    graph, layout, spelling, suffix
):
    messages = _messages(graph.columns["same"].id, layout, spelling, suffix)
    assert optimizer_column_references(messages) == {graph.columns["same"].id}, (
        "AQ-XWS-W5G: extraction must recognize canonical whole and split UUID references"
    )


def test_extractor_without_ee_keeps_identity_semantics(graph, monkeypatch):
    messages = _messages(graph.columns["same"].id, "whole", "compatibility")
    monkeypatch.setitem(sys.modules, "ee.agent_opt.utils.template_variables", None)
    assert (
        optimizer_column_references(messages),
        optimizer_column_references(copy_cases._messages(graph.columns["same"].id)),
    ) == (set(), {graph.columns["same"].id}), (
        "SETUP_AQ-XWS-W5G absent EE must preserve OSS identity and ASCII-reference checks"
    )


@pytest.mark.parametrize(
    ("operation", "layout"),
    [
        ("legacy", "whole"),
        ("legacy", "strings"),
        ("add", "whole"),
        ("add", "strings"),
        ("add", "parts"),
        ("edit", "whole"),
        ("edit", "strings"),
        ("edit", "parts"),
        ("retained", "whole"),
        ("retained", "strings"),
        ("retained", "parts"),
    ],
)
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize(
    "kind", ["other_dataset", "other_workspace", "legacy", "foreign_org"]
)
def test_persisted_writers_refuse_canonical_foreign_and_missing(
    graph, writer, operation, layout, spelling, kind
):
    observed = []
    for reference in (graph.columns[kind].id, graph.missing):
        messages = _messages(reference, layout, spelling)
        if operation == "retained":
            graph.prompt.messages = messages
            graph.prompt.save(update_fields=["messages"])
        before = writer_cases._snapshot()
        calls = writer.dispatch.call_count
        response = writer.post(
            "edit" if operation == "retained" else operation,
            None if operation == "retained" else messages,
        )
        observed.append(
            (
                writer_cases._shape(response),
                writer_cases._snapshot() == before,
                writer.dispatch.call_count - calls,
            )
        )
    expected = writer_cases._shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert observed == [(expected, True, 0), (expected, True, 0)], (
        "AQ-XWS-W5G: all persisted writers must refuse canonical foreign and missing identically without writes or dispatch"
    )


@pytest.mark.parametrize(
    ("operation", "layout"),
    [
        ("legacy", "whole"),
        ("legacy", "strings"),
        ("add", "whole"),
        ("add", "strings"),
        ("add", "parts"),
        ("edit", "whole"),
        ("edit", "strings"),
        ("edit", "parts"),
        ("retained", "whole"),
        ("retained", "strings"),
        ("retained", "parts"),
    ],
)
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
def test_same_dataset_writer_preserves_original_text(
    graph, writer, operation, layout, spelling
):
    messages = _messages(graph.columns["same"].id, layout, spelling)
    if operation == "retained":
        graph.prompt.messages = messages
        graph.prompt.save(update_fields=["messages"])
    response = writer.post(
        "edit" if operation == "retained" else operation,
        None if operation == "retained" else messages,
    )
    stored = RunPrompter.no_workspace_objects.filter(dataset=graph.dataset)
    if operation in {"edit", "retained"}:
        stored = stored.filter(id=graph.prompt.id)
    else:
        stored = stored.exclude(id=graph.prompt.id)
    assert (
        response.status_code,
        any(prompt.messages == messages for prompt in stored),
        writer.dispatch.call_count,
    ) == (200, True, 1), (
        "SETUP_AQ-XWS-W5G same-dataset writers must accept without changing reference or non-reference text"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize("kind", ["other_dataset", "other_workspace", "foreign_org"])
def test_copied_destinations_refuse_canonical_foreign_and_missing(
    graph, copy_writer, operation, layout, spelling, kind
):
    observed = []
    for reference in (graph.columns[kind].id, graph.missing):
        response, before = copy_writer.post(
            operation, _messages(reference, layout, spelling)
        )
        observed.append((copy_cases._shape(response), copy_cases._snapshot() == before))
    expected = copy_cases._shape(
        run_prompt.GeneralMethods().not_found("Column or dataset not found")
    )
    assert observed == [(expected, True), (expected, True)], (
        "AQ-XWS-W5G: both copied destinations must refuse canonical foreign and missing identically and roll back"
    )


@pytest.mark.parametrize("operation", ["create", "add"])
@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
def test_copy_ascii_same_scope_control(graph, copy_writer, operation, layout):
    response, _ = copy_writer.post(
        operation, copy_cases._messages(graph.columns["legacy"].id, layout)
    )
    copied = list(
        RunPrompter.no_workspace_objects.exclude(
            dataset_id__in=[dataset.id for dataset in graph.datasets.values()]
        )
    )
    assert response.status_code == 200 and len(copied) == 1, (
        "SETUP_AQ-XWS-W5G existing same-scope copy remapping must remain accepted"
    )
    assert run_prompt._run_prompt_columns_belong_to_dataset(
        copied[0].messages, copied[0].dataset
    ), "SETUP_AQ-XWS-W5G copied references must bind to the destination dataset"


@pytest.fixture
def canonical_worker(graph, monkeypatch):
    monkeypatch.setattr(
        activities,
        "sync_to_async",
        lambda func, **kwargs: sync_to_async(func, thread_sensitive=True),
    )
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    monkeypatch.setattr(activities, "Heartbeater", lambda *args: nullcontext())
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        activities, "get_api_key_for_model", lambda **kwargs: "w5g-test"
    )
    executions = []
    provider = Mock()

    class OptimizerBoundary:
        def optimize_from_execution(self, **kwargs):
            execution = kwargs["execution_data"]
            executions.append(execution)
            prompt = execution["agent_definition_prompt"]["description"]
            provider(prompt)
            kwargs["on_trial_callback"](
                {"prompt": prompt, "average_score": 0.5}, 0, {}, True
            )
            return SimpleNamespace(history=[{}], final_score=0.5, best_prompt=prompt)

    monkeypatch.setitem(
        sys.modules,
        "ee.agenthub.fix_your_agent.fix_your_agent",
        SimpleNamespace(FixYourAgent=OptimizerBoundary),
    )
    # Deliberately do not replace ee.agent_opt.utils.template_variables.

    def execute(location=None, saved_text="W5G saved"):
        run = OptimizeDataset.no_workspace_objects.create(
            name="W5G run",
            column=graph.output,
            organization=graph.organization,
            workspace=graph.workspace,
            optimizer_algorithm=OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            optimizer_config={"num_variations": 1},
            status=OptimizeDataset.StatusType.PENDING,
            optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
            environment=OptimizeDataset.EnvTypes.TRAINING,
            version="1.0",
        )
        if location:
            DatasetOptimizationTrial.objects.create(
                optimization_run=run,
                trial_number=1,
                average_score=0.5,
                prompt=saved_text if location == "trial" else "W5G saved",
                metadata={
                    "optimizer_state": (
                        {"prompt": saved_text}
                        if location == "state"
                        else {"done": True}
                    )
                },
            )
        before = list(
            DatasetOptimizationTrial.objects.filter(optimization_run=run).values()
        )
        calls = len(executions)
        provider_calls = provider.call_count
        error = None
        non_retryable = None
        result = None
        with capture_logs() as logs:
            try:
                result = async_to_sync(activities.run_optimization_activity)(
                    {"run_id": str(run.id)}
                )
            except ApplicationError as exc:
                error = exc.message
                non_retryable = exc.non_retryable
                async_to_sync(activities.finalize_run_activity)(
                    {"run_id": str(run.id), "status": "completed"}
                )
        run.refresh_from_db()
        after = list(
            DatasetOptimizationTrial.objects.filter(optimization_run=run).values()
        )
        refusal_logs = [
            entry
            for entry in logs
            if entry.get("event") == "Optimization reference not found"
        ]
        refusal = (
            run.status,
            run.error_message,
            error,
            non_retryable,
            before == after,
            len(executions) - calls,
            provider.call_count - provider_calls,
            refusal_logs,
        )
        return SimpleNamespace(run=run, result=result, refusal=refusal)

    return SimpleNamespace(execute=execute, provider=provider, executions=executions)


def _expected_worker_refusal():
    return (
        OptimizeDataset.StatusType.FAILED,
        "Optimization reference not found",
        "Optimization reference not found",
        True,
        True,
        0,
        0,
        [{"event": "Optimization reference not found", "log_level": "warning"}],
    )


@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize(
    "kind", ["other_dataset", "other_workspace", "legacy", "foreign_org"]
)
def test_worker_refuses_canonical_foreign_and_missing(
    graph, canonical_worker, layout, spelling, kind
):
    observed = []
    for reference in (graph.columns[kind].id, graph.missing):
        graph.prompt.messages = _messages(reference, layout, spelling)
        graph.prompt.save(update_fields=["messages"])
        observed.append(canonical_worker.execute().refusal)
    assert observed == [_expected_worker_refusal(), _expected_worker_refusal()], (
        "AQ-XWS-W5G: worker must fail canonical foreign and missing identically with a value-free non-retryable refusal before execution"
    )


@pytest.mark.parametrize("layout", ["whole", "strings", "parts"])
@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize("suffix", ["", ".value", "[0]"])
@pytest.mark.parametrize("source", ["prompt", "fallback"])
def test_worker_resolves_canonical_same_dataset(
    graph, canonical_worker, layout, spelling, suffix, source
):
    messages = _messages(graph.columns["same"].id, layout, spelling, suffix)
    graph.prompt.messages = messages if source == "prompt" else []
    graph.prompt.save(update_fields=["messages"])
    if source == "fallback":
        cell = Cell.objects.get(column=graph.output)
        cell.value = optimizer_prompt_text(messages)
        cell.save(update_fields=["value"])
    result = canonical_worker.execute()
    expected = normalize_prompt_text(optimizer_prompt_text(messages)).replace(
        str(graph.columns["same"].id), graph.columns["same"].name
    )
    trial = DatasetOptimizationTrial.objects.filter(optimization_run=result.run).first()
    assert (
        result.result["best_prompt"] if result.result else None,
        trial.prompt if trial else None,
        canonical_worker.provider.call_args.args
        if canonical_worker.provider.called
        else None,
    ) == (expected, expected, (expected,)), (
        "AQ-XWS-W5G: worker must resolve canonical same-dataset UUIDs before execution and trial persistence"
    )


@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
@pytest.mark.parametrize("location", ["trial", "state"])
def test_completed_resume_rechecks_canonical_references(
    graph, canonical_worker, spelling, location
):
    observed = []
    for reference in (graph.columns["other_dataset"].id, graph.missing):
        text = optimizer_prompt_text(_messages(reference, "parts", spelling))
        observed.append(canonical_worker.execute(location, text).refusal)
    assert observed == [_expected_worker_refusal(), _expected_worker_refusal()], (
        "AQ-XWS-W5G: canonical in-flight trial and resume-state references must be rechecked before returning saved results"
    )


@pytest.mark.parametrize("spelling", ["compatibility", "invisible"])
def test_fallback_cell_rechecks_canonical_references(graph, canonical_worker, spelling):
    graph.prompt.messages = []
    graph.prompt.save(update_fields=["messages"])
    cell = Cell.objects.get(column=graph.output)
    observed = []
    for reference in (graph.columns["other_dataset"].id, graph.missing):
        cell.value = optimizer_prompt_text(_messages(reference, "whole", spelling))
        cell.save(update_fields=["value"])
        observed.append(canonical_worker.execute().refusal)
    assert observed == [_expected_worker_refusal(), _expected_worker_refusal()], (
        "AQ-XWS-W5G: canonical fallback-cell references must fail identically before provider execution"
    )
