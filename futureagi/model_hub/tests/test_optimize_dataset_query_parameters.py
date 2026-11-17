"""Optimisation result reads carry stored values as ClickHouse parameters.

Every read below goes through a stand-in for the shared ClickHouse client that
answers from the parameters it receives. A stored model, environment, version,
date or page reaches ClickHouse as a parameter and never inside the query
text, and a stored name still reads exactly as it did before. A run saved from
the model Optimize drawer stores its environment as the number text "1" to
"4"; it reads the same rows, with the same statement, as it did before.
"""

import ast
import re
import uuid
from pathlib import Path

import pytest
from clickhouse_driver.util.escape import escape_params
from rest_framework import status

from model_hub.models.ai_model import AIModel
from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.tasks.prompt_template_optimizer import (
    gather_data_for_optimization,
    get_template_explore,
    get_template_results,
    get_topk_prompts,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
VIEWS_MODULE = "model_hub/views/optimize_dataset.py"
TASKS_MODULE = "model_hub/tasks/prompt_template_optimizer.py"

START = "2026-01-01 00:00:00"
END = "2026-01-02 00:00:00"
VERSION = "v-1.2"
TRAINING_NUMBER = 1
PRODUCTION_NUMBER = 3
DRAWER_ENVIRONMENT = "3"
UNKNOWN_ENVIRONMENT = "staging-eu"
UNUSUAL_VERSION = "v2 (Dana's)"
UNUSUAL_ENVIRONMENT = "Dana's team (east)"

PAGED_ROWS_STATEMENT = (
    "SELECT UUID, EventDateTime, EvalResults.Key, EvalResults.Value, "
    "arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) "
    "AS input_node_id, "
    "arrayElement(PredictionLabel.Value, indexOf(PredictionLabel.Key, 'node_id')) "
    "AS output_node_id "
    "FROM events WHERE AIModel = '{model}' AND ModelVersion = 'v-1.2' "
    "AND Environment = {environment} AND has(Features.Key,'node_id') "
    "AND deleted = 0 "
    "AND EventDateTime > toDateTime('2026-01-01 00:00:00') "
    "AND EventDateTime < toDateTime('2026-01-02 00:00:00') LIMIT 10 OFFSET 0"
)
PAGED_COUNT_STATEMENT = (
    "SELECT COUNT(*) FROM events WHERE AIModel = '{model}' "
    "AND ModelVersion = 'v-1.2' AND Environment = {environment} "
    "AND has(Features.Key,'node_id') "
    "AND EventDateTime > toDateTime('2026-01-01 00:00:00') "
    "AND EventDateTime < toDateTime('2026-01-02 00:00:00') AND deleted = 0"
)
TEMPLATE_STATEMENT = (
    "WITH arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'optimized_{run}_{metric}_0_score')) AS temp1_score, "
    "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'optimized_{run}_{metric}_1_score')) AS temp2_score, "
    "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'optimized_{run}_{metric}_original')) AS original_score, "
    "if(isNotNull(temp1_score) AND temp1_score != '', toFloat32(temp1_score), -1) "
    "AS t1_score, "
    "if(isNotNull(temp2_score) AND temp2_score != '', toFloat32(temp2_score), -1) "
    "AS t2_score, "
    "if(isNotNull(original_score) AND original_score != '', "
    "toFloat32(original_score), -1) AS og_score "
    "SELECT AVG(t1_score) AS avg_t1_score, AVG(t2_score) AS avg_t2_score, "
    "AVG(og_score) AS avg_og_score FROM events "
    "WHERE AIModel = '{model}' AND ModelVersion = 'v-1.2' "
    "AND Environment = {environment} AND has(Features.Key, 'node_id') "
    "AND deleted = 0 AND t1_score != -1 AND t2_score != -1 AND og_score != -1 "
    "GROUP BY AIModel"
)
TOPK_STATEMENT = (
    "SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) "
    "AS FeatureValue, Tags.Key AS TagKeys, Tags.Value AS TagValues FROM events "
    "WHERE AIModel = '{model}' AND ( ( Environment = {environment} "
    "AND ModelVersion = 'v-1.2' AND EventDate>='2026-01-01' "
    "AND EventDate<='2026-01-02') ) AND has(Features.Key, 'node_id') "
    "AND deleted=0 ORDER BY EventDateTime ASC"
)

PAGED_ROUTES = ["right-answers", "prompt-template-explore"]


class FakeEventsClickHouse:
    """Stands in for the shared ClickHouse client over in-memory events.

    Each event is (model id, version, environment number). A read is answered
    from its parameters the way the server compares them: equal model,
    version and environment, where a NULL environment equals nothing.
    """

    def __init__(self, events=(), aggregate_row=None):
        self.events = list(events)
        self.aggregate_row = aggregate_row
        self.reads = []

    def __call__(self):
        return self

    def execute_read(self, query, params=None, **kwargs):
        self.reads.append((query, params))
        matching = self._matching(params)
        if "COUNT(*)" in query:
            return [(len(matching),)]
        if "AVG(" in query:
            return [self.aggregate_row] if matching and self.aggregate_row else []
        return []

    def _matching(self, params):
        if not params or params.get("environment") is None:
            return []
        wanted = (params.get("model_id"), params.get("version"), params["environment"])
        return [event for event in self.events if event == wanted]


def _render(query, params):
    """The statement the driver sends, with whitespace runs collapsed."""

    text = query if params is None else query % escape_params(params, None)
    return " ".join(text.split())


def _assert_values_stay_out_of_text(reads, *values):
    assert reads, "expected at least one ClickHouse read"
    for query, _params in reads:
        for value in values:
            assert value not in query


def _create_model(organization, workspace):
    return AIModel.all_objects.create(
        user_model_id=f"optimise-model-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _create_metric(
    model, metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT, datasets=None
):
    return Metric.objects.create(
        name=f"Metric {uuid.uuid4()}",
        text_prompt="Score the output.",
        criteria_breakdown=["Score the output."],
        model=model,
        metric_type=metric_type,
        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
        datasets=datasets,
    )


def _create_run(model, metric, organization, workspace, *, environment, version):
    run = OptimizeDataset.no_workspace_objects.create(
        name=f"Optimisation {uuid.uuid4()}",
        model=model,
        organization=organization,
        workspace=workspace,
        optimize_type=OptimizeDataset.OptimizeType.TEMPLATE,
        start_date="2026-01-01T00:00:00Z",
        end_date="2026-01-02T00:00:00Z",
        environment=environment,
        version=version,
        status=OptimizeDataset.StatusType.COMPLETED,
        optimized_k_prompts=["Optimised prompt A", "Optimised prompt B"],
    )
    run.metrics.set([metric])
    return run


@pytest.mark.django_db
@pytest.mark.parametrize("route", PAGED_ROUTES)
def test_paged_results_carry_stored_values_as_parameters(
    auth_client, organization, workspace, monkeypatch, route
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version=VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse(
        events=[
            (model_id, VERSION, TRAINING_NUMBER),
            (model_id, VERSION, TRAINING_NUMBER),
            (model_id, VERSION, TRAINING_NUMBER),
            (model_id, "v-1.3", TRAINING_NUMBER),
            (model_id, VERSION, 3),
        ]
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton", fake
    )

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/{route}/{run.id}/",
        {"page": 1, "limit": 10},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_values_stay_out_of_text(
        fake.reads, VERSION, model_id, "2026-01-0", "Training"
    )
    rows_read, count_read = fake.reads
    assert {model_id, VERSION, TRAINING_NUMBER, START, END, 10, 0} <= set(
        rows_read[1].values()
    )
    assert {model_id, VERSION, TRAINING_NUMBER, START, END} <= set(
        count_read[1].values()
    )
    assert _render(*rows_read) == PAGED_ROWS_STATEMENT.format(
        model=model_id, environment=TRAINING_NUMBER
    )
    assert _render(*count_read) == PAGED_COUNT_STATEMENT.format(
        model=model_id, environment=TRAINING_NUMBER
    )
    body = response.json()
    assert body["count"] == 3
    assert body["total_pages"] == 1
    assert body["results"] == []


@pytest.mark.django_db
def test_template_results_carry_stored_values_as_parameters(
    auth_client, organization, workspace, monkeypatch
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version=VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse(
        events=[(model_id, VERSION, TRAINING_NUMBER)],
        aggregate_row=(0.5, 0.75, 0.25),
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton", fake
    )

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/prompt-template-result/{run.id}/",
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_values_stay_out_of_text(
        fake.reads, VERSION, model_id, str(run.id), "Training"
    )
    ((query, params),) = fake.reads
    assert {model_id, VERSION, TRAINING_NUMBER} <= set(params.values())
    statement = _render(query, params)
    assert (
        f"WHERE AIModel = '{model_id}' AND ModelVersion = '{VERSION}' "
        "AND Environment = 1 AND has(Features.Key, 'node_id') AND deleted = 0"
    ) in statement
    for suffix in ("0_score", "1_score", "original"):
        key = f"optimized_{run.id}_{metric.id}_{suffix}"
        assert f"indexOf(EvalResults.Key, '{key}')" in statement
    assert response.json()["results"] == [
        {"metric_name": metric.name, "templates": [0.5, 0.75], "old_template": 0.25}
    ]


def _assert_environment_parameter_is(reads, number):
    assert reads, "expected at least one ClickHouse read"
    for _query, params in reads:
        assert params["environment"] == number
        assert type(params["environment"]) is int


@pytest.mark.django_db
@pytest.mark.parametrize("route", PAGED_ROUTES)
def test_paged_results_read_runs_whose_environment_was_saved_from_the_drawer(
    auth_client, organization, workspace, monkeypatch, route
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=DRAWER_ENVIRONMENT,
        version=VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse(
        events=[
            (model_id, VERSION, PRODUCTION_NUMBER),
            (model_id, VERSION, PRODUCTION_NUMBER),
            (model_id, VERSION, TRAINING_NUMBER),
        ]
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton", fake
    )

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/{route}/{run.id}/",
        {"page": 1, "limit": 10},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_environment_parameter_is(fake.reads, PRODUCTION_NUMBER)
    rows_read, count_read = fake.reads
    assert _render(*rows_read) == PAGED_ROWS_STATEMENT.format(
        model=model_id, environment=PRODUCTION_NUMBER
    )
    assert _render(*count_read) == PAGED_COUNT_STATEMENT.format(
        model=model_id, environment=PRODUCTION_NUMBER
    )
    assert response.json()["count"] == 2


@pytest.mark.django_db
def test_template_results_read_runs_whose_environment_was_saved_from_the_drawer(
    auth_client, organization, workspace, monkeypatch
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=DRAWER_ENVIRONMENT,
        version=VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse(
        events=[(model_id, VERSION, PRODUCTION_NUMBER)],
        aggregate_row=(0.5, 0.75, 0.25),
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton", fake
    )

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/prompt-template-result/{run.id}/",
        {},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_environment_parameter_is(fake.reads, PRODUCTION_NUMBER)
    ((query, params),) = fake.reads
    assert _render(query, params) == TEMPLATE_STATEMENT.format(
        model=model_id,
        run=run.id,
        metric=metric.id,
        environment=PRODUCTION_NUMBER,
    )
    assert response.json()["results"] == [
        {"metric_name": metric.name, "templates": [0.5, 0.75], "old_template": 0.25}
    ]


@pytest.mark.django_db
@pytest.mark.parametrize("route", [*PAGED_ROUTES, "prompt-template-result"])
def test_unknown_stored_environment_matches_nothing(
    auth_client, organization, workspace, monkeypatch, route
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=UNKNOWN_ENVIRONMENT,
        version=VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse(
        events=[(model_id, VERSION, number) for number in range(5)],
        aggregate_row=(0.5, 0.75, 0.25),
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton", fake
    )
    payload = {} if route == "prompt-template-result" else {"page": 1, "limit": 10}

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/{route}/{run.id}/",
        payload,
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_values_stay_out_of_text(fake.reads, UNKNOWN_ENVIRONMENT)
    for _query, params in fake.reads:
        assert UNKNOWN_ENVIRONMENT not in params.values()
        assert None in params.values()
    body = response.json()
    assert body["results"] == []
    if route != "prompt-template-result":
        assert body["count"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("route", PAGED_ROUTES)
def test_unusual_stored_text_stays_a_parameter(
    auth_client, organization, workspace, monkeypatch, route
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    ordinary = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version=VERSION,
    )
    unusual = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=UNUSUAL_ENVIRONMENT,
        version=UNUSUAL_VERSION,
    )
    unusual_version_only = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version=UNUSUAL_VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse(
        events=[(model_id, VERSION, TRAINING_NUMBER)] * 3
        + [(model_id, UNUSUAL_VERSION, TRAINING_NUMBER)] * 2
    )
    monkeypatch.setattr(
        "model_hub.views.optimize_dataset.ClickHouseClientSingleton", fake
    )

    def read(run):
        fake.reads.clear()
        response = auth_client.post(
            f"/model-hub/optimize-dataset/{model.id}/{route}/{run.id}/",
            {"page": 1, "limit": 10},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        return list(fake.reads), response.json()["count"]

    ordinary_reads, ordinary_count = read(ordinary)
    unusual_reads, unusual_count = read(unusual)
    version_reads, version_count = read(unusual_version_only)

    for reads in (unusual_reads, version_reads):
        _assert_values_stay_out_of_text(reads, UNUSUAL_VERSION, "Dana")
        assert [query for query, _ in reads] == [query for query, _ in ordinary_reads]
        for _query, params in reads:
            assert UNUSUAL_VERSION in params.values()
    for _query, params in unusual_reads:
        assert UNUSUAL_ENVIRONMENT not in params.values()
    assert ordinary_count == 3
    assert unusual_count == 0
    assert version_count == 2


@pytest.mark.django_db
def test_topk_prompt_query_carries_stored_values_as_parameters(
    organization, workspace, monkeypatch
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version=VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse()
    started = []
    monkeypatch.setattr(
        "model_hub.tasks.prompt_template_optimizer.ClickHouseClientSingleton", fake
    )
    monkeypatch.setattr(
        "model_hub.tasks.prompt_template_optimizer.runner_topk_optimizer",
        lambda *args: started.append(args),
    )

    for environment in ("Training", UNKNOWN_ENVIRONMENT):
        get_topk_prompts._original_func(
            model_id,
            environment,
            VERSION,
            [str(metric.id)],
            run.id,
            "2026-01-01",
            "2026-01-02",
        )

    _assert_values_stay_out_of_text(
        fake.reads, VERSION, model_id, "2026-01-0", "Training", UNKNOWN_ENVIRONMENT
    )
    (known_query, known_params), (unknown_query, unknown_params) = fake.reads
    assert unknown_query == known_query
    assert {model_id, VERSION, TRAINING_NUMBER, "2026-01-01", "2026-01-02"} <= set(
        known_params.values()
    )
    assert None in unknown_params.values()
    assert _render(known_query, known_params) == TOPK_STATEMENT.format(
        model=model_id, environment=TRAINING_NUMBER
    )
    assert len(started) == 2


@pytest.mark.django_db
@pytest.mark.parametrize(
    "environment",
    [DRAWER_ENVIRONMENT, PRODUCTION_NUMBER],
    ids=["number-text", "number"],
)
def test_topk_prompt_query_reads_runs_whose_environment_was_saved_from_the_drawer(
    organization, workspace, monkeypatch, environment
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=DRAWER_ENVIRONMENT,
        version=VERSION,
    )
    model_id = str(model.id)
    fake = FakeEventsClickHouse()
    started = []
    monkeypatch.setattr(
        "model_hub.tasks.prompt_template_optimizer.ClickHouseClientSingleton", fake
    )
    monkeypatch.setattr(
        "model_hub.tasks.prompt_template_optimizer.runner_topk_optimizer",
        lambda *args: started.append(args),
    )

    get_topk_prompts._original_func(
        model_id,
        environment,
        VERSION,
        [str(metric.id)],
        run.id,
        "2026-01-01",
        "2026-01-02",
    )

    _assert_environment_parameter_is(fake.reads, PRODUCTION_NUMBER)
    ((query, params),) = fake.reads
    assert _render(query, params) == TOPK_STATEMENT.format(
        model=model_id, environment=PRODUCTION_NUMBER
    )
    assert len(started) == 1


@pytest.mark.django_db
def test_scheduled_scoring_query_carries_dataset_values_as_parameters(
    organization, workspace, monkeypatch
):
    model = _create_model(organization, workspace)
    metric = _create_metric(
        model,
        metric_type=Metric.MetricTypes.STEPWISE_MODEL_INFERENCE,
        datasets=[
            {"environment": "Training", "model_version": VERSION},
            {"environment": UNKNOWN_ENVIRONMENT, "model_version": "v-1.3"},
        ],
    )
    run = _create_run(
        model,
        metric,
        organization,
        workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version=VERSION,
    )
    fake = FakeEventsClickHouse()
    monkeypatch.setattr(
        "model_hub.tasks.prompt_template_optimizer.ClickHouseClientSingleton", fake
    )

    gather_data_for_optimization._original_func()

    _assert_values_stay_out_of_text(
        fake.reads,
        VERSION,
        "v-1.3",
        UNKNOWN_ENVIRONMENT,
        str(model.id),
        str(run.id),
        "2026-01-0",
    )
    ((query, params),) = fake.reads
    assert {
        str(model.id),
        VERSION,
        "v-1.3",
        TRAINING_NUMBER,
        None,
        "2026-01-01",
        "2026-01-02",
    } <= set(params.values())
    statement = _render(query, params)
    assert (
        "( Environment = 1 AND ModelVersion = 'v-1.2' AND EventDate>='2026-01-01' "
        "AND EventDate<='2026-01-02') OR ( Environment = NULL "
        "AND ModelVersion = 'v-1.3'"
    ) in statement
    status_key = f"optimized_{run.id}_{metric.id}_status"
    assert f"NOT has(EvalResults.Key, '{status_key}')" in statement


@pytest.mark.django_db
def test_template_score_helpers_carry_the_optimisation_id_as_a_parameter(
    monkeypatch,
):
    optimization_id = uuid.uuid4()
    fake = FakeEventsClickHouse()
    monkeypatch.setattr(
        "model_hub.tasks.prompt_template_optimizer.ClickHouseClientSingleton", fake
    )

    assert get_template_results(optimization_id) == []
    assert get_template_explore(optimization_id) == []

    _assert_values_stay_out_of_text(fake.reads, str(optimization_id))
    assert len(fake.reads) == 2
    for query, params in fake.reads:
        assert f"%{optimization_id}%" in params.values()
        assert f"evalKey LIKE '%{optimization_id}%'" in _render(query, params)


_STORED_FIELD = re.compile(r"(^|_)(environment|version)(_|$)")
_CLICKHOUSE_CALLS = {"execute", "execute_read", "execute_paginated"}


def _optimisation_reader_files():
    """Backend modules that read optimisation runs and query ClickHouse."""

    readers = set()
    for package in sorted(BACKEND_ROOT.iterdir()):
        if package.name.startswith(".") or not (package / "__init__.py").is_file():
            continue
        for path in package.rglob("*.py"):
            relative = path.relative_to(BACKEND_ROOT)
            if {"tests", "migrations"} & set(relative.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "OptimizeDataset" in text and "ClickHouseClientSingleton" in text:
                readers.add(relative.as_posix())
    return readers


def _names_environment_or_version(expression, aliases=frozenset()):
    for node in ast.walk(expression):
        if isinstance(node, ast.Name):
            label = node.id
            if label in aliases:
                return True
        elif isinstance(node, ast.Attribute):
            label = node.attr
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            label = node.slice.value
        else:
            continue
        if _STORED_FIELD.search(label.lower()):
            return True
    return False


_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)


def _scope_nodes(scope):
    """Nodes of one module or function body, without nested function bodies."""

    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _FUNCTIONS):
            pending.extend(ast.iter_child_nodes(node))


def _names_stored_field(label):
    return bool(_STORED_FIELD.search(label.lower()))


def _is_stored_field_value(expression, aliases):
    """The expression is environment or version itself, or its plain text."""

    if isinstance(expression, ast.Name):
        return expression.id in aliases or _names_stored_field(expression.id)
    if isinstance(expression, ast.Attribute):
        return _names_stored_field(expression.attr)
    if isinstance(expression, ast.Subscript):
        key = expression.slice
        return (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and _names_stored_field(key.value)
        )
    if isinstance(expression, ast.IfExp):
        return any(
            _is_stored_field_value(item, aliases)
            for item in (expression.body, expression.orelse)
        )
    if isinstance(expression, ast.BoolOp):
        return any(_is_stored_field_value(item, aliases) for item in expression.values)
    if isinstance(expression, ast.Call):
        function, arguments = expression.func, expression.args
        if isinstance(function, ast.Name) and function.id in {"str", "repr", "format"}:
            return bool(arguments) and _is_stored_field_value(arguments[0], aliases)
        if isinstance(function, ast.Attribute) and function.attr == "get":
            key = arguments[0] if arguments else None
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and _names_stored_field(key.value)
            ):
                return True
        if isinstance(function, ast.Attribute):
            return _is_stored_field_value(function.value, aliases)
    return False


def _bound_names(target):
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    if isinstance(target, ast.Tuple | ast.List):
        return {name for item in target.elts for name in _bound_names(item)}
    return set()


def _assigned_pairs(target, value):
    sequences = ast.Tuple | ast.List
    if (
        isinstance(target, sequences)
        and isinstance(value, sequences)
        and len(target.elts) == len(value.elts)
    ):
        for item_target, item_value in zip(target.elts, value.elts, strict=True):
            yield from _assigned_pairs(item_target, item_value)
    else:
        yield target, value


def _stored_field_aliases(nodes, inherited):
    """Names in one scope bound to environment, version or another alias."""

    assignments = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assignments += _assigned_pairs(target, node.value)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
            if node.value is not None:
                assignments += _assigned_pairs(node.target, node.value)
    aliases = set(inherited)
    while True:
        found = {
            name
            for target, value in assignments
            if _is_stored_field_value(value, aliases)
            for name in _bound_names(target)
        } - aliases
        if not found:
            return aliases
        aliases |= found


def _formatted_expressions(nodes):
    """Each expression an f-string, %, str.format or + puts into text."""

    for node in nodes:
        if isinstance(node, ast.FormattedValue):
            yield node, node.value
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            yield node, node.right
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            yield node, node.left
            yield node, node.right
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Add | ast.Mod):
            yield node, node.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            for argument in [*node.args, *(item.value for item in node.keywords)]:
                yield node, argument


def _formatted_stored_field_lines(scope, inherited=frozenset()):
    """Lines where text is formatted from environment, version or an alias."""

    nodes = list(_scope_nodes(scope))
    aliases = _stored_field_aliases(nodes, inherited)
    lines = {
        node.lineno
        for node, expression in _formatted_expressions(nodes)
        if _names_environment_or_version(expression, aliases)
    }
    for node in nodes:
        if isinstance(node, _FUNCTIONS):
            lines |= _formatted_stored_field_lines(node, frozenset(aliases))
    return lines


def _parse(relative):
    return ast.parse((BACKEND_ROOT / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        ("def read(run):\n    return f'{run.environment}'\n", {2}),
        ("def read(run):\n    env = run.environment\n    return f'{env}'\n", {3}),
        (
            "def read(run):\n    name, ver = run.name, run.version\n"
            "    return '{}'.format(ver)\n",
            {3},
        ),
        ("def read(run):\n    return 'v' + run.version\n", {2}),
        (
            "def read(run):\n    ver = run.version\n    text = 'v'\n"
            "    text += ver\n    return text\n",
            {4},
        ),
        (
            "def read(run):\n    env = run.environment\n\n"
            "    def label():\n        return 'e' + env\n\n    return label()\n",
            {5},
        ),
        (
            "def read(run):\n    ver = str(run.version).strip()\n"
            "    return '%s' % ver\n",
            {3},
        ),
        (
            "def read(data):\n    env = data.get('environment')\n    return f'{env}'\n",
            {3},
        ),
        (
            "def read(run, index):\n    params = {'environment': run.environment}\n"
            "    params[f'environment_{index}'] = run.version\n"
            "    return f'{index}', params\n",
            set(),
        ),
        (
            "def read(client, run):\n"
            "    rows = client.execute_read('q', {'environment': run.environment})\n"
            "    total = len(rows) + 1\n    return f'{total}'\n",
            set(),
        ),
        (
            "def read(run):\n    env = run.environment\n    return env\n\n\n"
            "def label(env_label):\n    return f'{env_label}'\n",
            set(),
        ),
    ],
    ids=[
        "attribute",
        "alias",
        "tuple-alias-format",
        "concatenation",
        "augmented-concatenation",
        "closure-alias",
        "method-alias",
        "lookup-alias",
        "parameter-dict-stays-clean",
        "read-result-stays-clean",
        "other-function-stays-clean",
    ],
)
def test_census_finds_environment_or_version_formatted_into_text(source, lines):
    assert _formatted_stored_field_lines(ast.parse(source)) == lines


def test_optimisation_readers_never_format_environment_or_version_into_text():
    readers = _optimisation_reader_files()
    assert {VIEWS_MODULE, TASKS_MODULE} <= readers

    formatted = [
        f"{relative}:{line}"
        for relative in sorted(readers)
        for line in sorted(_formatted_stored_field_lines(_parse(relative)))
    ]

    assert formatted == []


_ENVIRONMENT_MAPPINGS = {"convert_to_type", "get_env_num_types"}


def test_optimisation_readers_map_environment_through_one_helper():
    readers = _optimisation_reader_files()
    assert {VIEWS_MODULE, TASKS_MODULE} <= readers

    called = {
        relative: [
            (node.func.attr, node.lineno)
            for node in ast.walk(_parse(relative))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        for relative in sorted(readers)
    }

    assert [
        f"{relative}:{line} {name}"
        for relative, calls in called.items()
        for name, line in calls
        if name in _ENVIRONMENT_MAPPINGS
    ] == []
    for relative in (VIEWS_MODULE, TASKS_MODULE):
        assert "events_number" in {name for name, _line in called[relative]}


@pytest.mark.parametrize(
    ("stored", "number"),
    [
        ("Training", 1),
        ("Validation", 2),
        ("Production", 3),
        ("Corpus", 4),
        ("1", 1),
        ("2", 2),
        ("3", 3),
        ("4", 4),
        (1, 1),
        (4, 4),
        ("0", None),
        ("5", None),
        (" 3", None),
        ("03", None),
        ("3.0", None),
        ("", None),
        ("training", None),
        (UNKNOWN_ENVIRONMENT, None),
        (None, None),
        (0, None),
        (5, None),
        (3.0, None),
        (True, None),
    ],
    ids=[
        "name-training",
        "name-validation",
        "name-production",
        "name-corpus",
        "number-text-1",
        "number-text-2",
        "number-text-3",
        "number-text-4",
        "number-1",
        "number-4",
        "number-text-0",
        "number-text-5",
        "leading-space",
        "leading-zero",
        "decimal-text",
        "blank",
        "lowercase-name",
        "unknown-name",
        "none",
        "number-0",
        "number-5",
        "decimal-number",
        "boolean",
    ],
)
def test_stored_environment_maps_to_its_events_number(stored, number):
    mapped = OptimizeDataset.EnvTypes.events_number(stored)

    assert mapped == number
    assert type(mapped) is type(number)


def test_optimisation_readers_pass_parameters_to_every_clickhouse_read():
    readers = _optimisation_reader_files()
    assert {VIEWS_MODULE, TASKS_MODULE} <= readers

    unparameterised = [
        f"{relative}:{node.lineno}"
        for relative in sorted(readers)
        for node in ast.walk(_parse(relative))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _CLICKHOUSE_CALLS
        and len(node.args) < 2
        and not any(item.arg == "params" for item in node.keywords)
    ]

    assert unparameterised == []
