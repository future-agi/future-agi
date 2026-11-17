"""Performance reads keep every request value typed.

The Performance views read through a stand-in for the shared ClickHouse client
that answers from the parameters it receives. The model, organisation,
dataset version and environment, the dates, and each filter's key, values and
metric key reach ClickHouse as parameters and never inside the query text. An
ordinary request sends the same statement it sent before, and unusual ordinary
text reads the rows it names. The environment reads through the shared helper:
a name or the number "1" to "4" is its events number, anything else matches no
event. Filter choices outside the request contract are refused as before.
"""

import ast
import re
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
from model_hub.models.metric import Metric
from model_hub.tests.test_optimize_dataset_query_parameters import (
    _CLICKHOUSE_CALLS,
    _ENVIRONMENT_MAPPINGS,
    _FUNCTIONS,
    _assert_values_stay_out_of_text,
    _assigned_pairs,
    _bound_names,
    _formatted_expressions,
    _names_stored_field,
    _render,
    _scope_nodes,
)
from model_hub.utils.insight import get_insight_details, get_insight_details_count
from model_hub.utils.performance_ch import (
    calculate_performance,
    calculate_performance_processing,
    get_performance_details_query,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PERFORMANCE_MODULE = "model_hub/utils/performance_ch.py"
INSIGHT_MODULE = "model_hub/utils/insight.py"
PERFORMANCE_READERS = (PERFORMANCE_MODULE, INSIGHT_MODULE)

START = "2026-01-01 00:00:00"
END = "2026-01-02 00:00:00"
DAY_AFTER_END = "2026-01-03 00:00:00"
VERSION = "v-1.2"
PRODUCTION_NUMBER = 3
TRAINING_NUMBER = 1
PROPERTY_KEY = "customer_tier"
PROPERTY_VALUE = "vip-gold"
NUMBER_KEY = "latency_ms"
NUMBER_TEXT = "42.5"
NUMBER = 42.5
SCORE_TEXT = "7"
SCORE = 0.7
SCORE_KEY_ID = "55555555-5555-5555-5555-555555555555"
TAG_VALUE = "POSITIVE:helpful"
UNUSUAL_VERSION = "v2 (Dana's)"
UNUSUAL_KEY = "customer's tier (EU)"
UNUSUAL_VALUE = "Dana's team (east)"

# Rendered statements, whitespace runs collapsed, as the driver sends them.
INTERVALS = (
    "WITH toDateTime('2026-01-01 00:00:00') AS start_date, "
    "toDateTime('2026-01-02 00:00:00') AS end_date, "
    "CASE WHEN 'daily' = 'hourly' THEN toStartOfHour(start_date) "
    "WHEN 'daily' = 'daily' THEN toDate(start_date) "
    "WHEN 'daily' = 'weekly' THEN toStartOfWeek(toDate(start_date)) "
    "WHEN 'daily' = 'monthly' THEN toStartOfMonth(start_date) END AS interval_start, "
    "CASE WHEN 'daily' = 'hourly' THEN toStartOfHour(end_date) "
    "WHEN 'daily' = 'daily' THEN toDate(end_date) "
    "WHEN 'daily' = 'weekly' THEN toLastDayOfWeek(toDate(end_date)) "
    "WHEN 'daily' = 'monthly' THEN toStartOfMonth(end_date) END AS interval_end, "
    "CASE WHEN 'daily' = 'hourly' THEN 'hour' WHEN 'daily' = 'daily' THEN 'day' "
    "WHEN 'daily' = 'weekly' THEN 'week' WHEN 'daily' = 'monthly' THEN 'month' "
    "END AS interval_type "
)
TIME_SERIES = (
    "FROM ( SELECT arrayJoin(arrayMap(x -> toDateTime(x), "
    "range(toUInt32(interval_start), toUInt32(interval_end), "
    "CASE WHEN interval_type = 'hour' THEN 3600 "
    "WHEN interval_type = 'day' THEN 86400 "
    "WHEN interval_type = 'week' THEN 604800 "
    "WHEN interval_type = 'month' THEN 2592000 END ) )) AS time_interval "
    ") AS time_series LEFT JOIN ( SELECT agg_time AS time_interval, "
    "COUNT() AS total_events, "
)
AGG_TIME = (
    "FROM ( SELECT UUID, "
    "CASE WHEN 'daily' = 'hourly' THEN toStartOfHour(EventDateTime) "
    "WHEN 'daily' = 'daily' THEN toDate(EventDate) "
    "WHEN 'daily' = 'weekly' THEN toStartOfWeek(EventDate) "
    "WHEN 'daily' = 'monthly' THEN toStartOfMonth(EventDate) END AS agg_time, "
)
EVENTS_IN_RANGE = (
    "FROM events WHERE EventDateTime BETWEEN '2026-01-01 00:00:00' "
    "AND '2026-01-02 00:00:00' "
)
SCOPE = (
    "AND ( AIModel = '{model}' ) AND ( OrgID = '{org}' ) "
    "AND ( ModelVersion = 'v-1.2' ) AND ( Environment = {environment} ) "
    "AND ( EventDateTime BETWEEN '2026-01-01 00:00:00' AND '2026-01-03 00:00:00' ) "
    "AND ( deleted = 0 )"
)
SCORE_ELEMENT = (
    "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'metric_{score_metric}_score'))"
)
FILTER_CONDITIONS = (
    " AND ( arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) "
    "IN (SELECT DISTINCT arrayElement(Features.Value, "
    "indexOf(Features.Key, 'conversation_id')) AS conv_id FROM events "
    "WHERE hasAll(splitByString(';',arrayElement(EvalResults.Value, "
    "indexOf(EvalResults.Key, 'customer_tier'))),['vip-gold']) "
    "AND NOT has(Features.Key, 'node_id') AND deleted = 0) ) "
    "AND ( toFloat32(arrayElement(EvalResults.Value, "
    "indexOf(EvalResults.Key, 'latency_ms'))) > 42.5 ) "
    "AND ( if(isNotNull("
    + SCORE_ELEMENT
    + ") AND "
    + SCORE_ELEMENT
    + " != '', toFloat32("
    + SCORE_ELEMENT
    + "), 0) >= {score} ) "
    "AND ( hasAll(splitByString(';',arrayElement(EvalResults.Value, "
    "indexOf(EvalResults.Key, 'metric_{metric}_tags'))),['POSITIVE:helpful']))"
)
GRAPH_HEAD = (
    INTERVALS + "SELECT time_series.time_interval, "
    "(event_data.correct_predictions / 10) * 100 AS correct_predictions_percentage "
    + TIME_SERIES
    + "AVG(if(isNotNull(metricValue) AND metricValue != '', "
    "toFloat32(metricValue), 0)) AS correct_predictions "
    + AGG_TIME
    + "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'metric_{metric}_score')) AS metricValue " + EVENTS_IN_RANGE
)
GRAPH_TAIL = (
    " AND has(EvalResults.Key, 'metric_{metric}_score') ) GROUP BY time_interval "
    ") AS event_data ON time_series.time_interval = event_data.time_interval "
    "ORDER BY time_series.time_interval"
)
GRAPH_STATEMENT = GRAPH_HEAD + SCOPE + FILTER_CONDITIONS + GRAPH_TAIL
UNFILTERED_GRAPH_STATEMENT = GRAPH_HEAD + SCOPE + GRAPH_TAIL
EXPORT_STATEMENT = (
    "WITH arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'metric_{metric}_score')) AS raw_metric_score SELECT UUID, EventDateTime, "
    "if(isNotNull(raw_metric_score) AND raw_metric_score != '', "
    "toFloat32(raw_metric_score), 0) AS metric_score, "
    "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'metric_{metric}_explanation')) AS metric_explanation, "
    "arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) "
    "AS input_node_id, "
    "arrayElement(PredictionLabel.Value, indexOf(PredictionLabel.Key, 'node_id')) "
    "AS output_node_id, "
    "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, "
    "'metric_{metric}_tags')) AS metric_tags, "
    "arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) "
    "AS conversation_id "
    + EVENTS_IN_RANGE
    + SCOPE
    + FILTER_CONDITIONS
    + " AND has(Features.Key, 'node_id') AND deleted=0"
)
DETAIL_STATEMENT = EXPORT_STATEMENT + " LIMIT 31 OFFSET 0"
ALL_TAGS_STATEMENT = (
    INTERVALS + "SELECT time_series.time_interval, "
    "(event_data.correct_predictions / 1) * 1 AS correct_predictions_percentage "
    + TIME_SERIES
    + "sum(arraySum(metricValue)) AS correct_predictions "
    + AGG_TIME
    + "arrayMap( x -> length(arrayFilter(x -> startsWith(x, '{prefix}'), "
    "splitByString(';', arrayElement(EvalResults.Value, "
    "indexOf(EvalResults.Key, toString(x))) ))), ['metric_{metric}_tags'] ) "
    "AS metricValue "
    + EVENTS_IN_RANGE
    + SCOPE
    + FILTER_CONDITIONS
    + " AND has(EvalResults.Key, 'metric_{metric}_tags') ) GROUP BY time_interval "
    ") AS event_data ON time_series.time_interval = event_data.time_interval "
    "ORDER BY time_series.time_interval"
)
TOP_TAGS_STATEMENT = (
    "SELECT tag, COUNT(*) AS tag_count FROM ( SELECT UUID, arrayJoin( "
    "arrayFlatten( arrayMap( x -> splitByString(';', "
    "arrayElement(EvalResults.Value, indexOf(EvalResults.Key, toString(x)))), "
    "['metric_{metric}_tags'] ) ) ) AS tag FROM events "
    "WHERE startsWith(tag, '{prefix}') "
    + SCOPE
    + FILTER_CONDITIONS
    + " AND has(EvalResults.Key, 'metric_{metric}_tags') ) GROUP BY tag "
    "ORDER BY tag_count DESC LIMIT 10"
)
INSIGHT_COUNT_STATEMENT = (
    "SELECT COUNT() AS total_events FROM events WHERE EventDateTime BETWEEN "
    "'2026-01-01 00:00:00' AND '2026-01-02 00:00:00' "
    "AND ( AIModel = '{model}' ) AND ( OrgID = '{org}' ) "
    "AND ( ModelVersion = 'v-1.2' ) AND ( Environment = {environment} ) "
    "AND ( EventDateTime BETWEEN '2026-01-01 00:00:00' AND '2026-01-02 00:00:00' ) "
    "AND ( deleted = 0 ) "
    "AND ( hasAll(splitByString(';',arrayElement(EvalResults.Value, "
    "indexOf(EvalResults.Key, 'customer_tier'))),['vip-gold']) ) "
    "AND ( toFloat32(arrayElement(EvalResults.Value, "
    "indexOf(EvalResults.Key, 'latency_ms'))) > 42.5)"
)


class FakeEventsClickHouse:
    """Stands in for the shared ClickHouse client over in-memory events.

    Each event is (model id, organisation id, version, environment number). A
    read answers one row with the number of events whose model, organisation,
    version and environment equal its parameters; a NULL environment equals
    nothing, and a read without parameters answers nothing.
    """

    def __init__(self, events=()):
        self.events = list(events)
        self.reads = []

    def __call__(self):
        return self

    def execute_read(self, query, params=None, **kwargs):
        self.reads.append((query, params))
        if not params or params.get("environment") is None:
            return []
        wanted = (
            params.get("model_id"),
            params.get("org_id"),
            params.get("version"),
            params["environment"],
        )
        count = sum(event == wanted for event in self.events)
        return [[START, count]] if count else []


def _string_filter(key=PROPERTY_KEY, value=PROPERTY_VALUE):
    return {
        "type": "property",
        "datatype": "string",
        "operator": "equal",
        "values": [value],
        "key": key,
        "key_id": "",
    }


def _number_filter(value=NUMBER_TEXT):
    return {
        "type": "property",
        "datatype": "number",
        "operator": "greaterThan",
        "values": [value],
        "key": NUMBER_KEY,
        "key_id": "",
    }


def _ordinary_filters():
    return [
        _string_filter(),
        _number_filter(),
        {
            "type": "performanceMetric",
            "datatype": "number",
            "operator": "greaterThanEqualTo",
            "values": [SCORE_TEXT],
            "key": "",
            "key_id": SCORE_KEY_ID,
        },
        {
            "type": "performanceTag",
            "datatype": "string",
            "operator": "equal",
            "values": [TAG_VALUE],
            "key": "",
            "key_id": "",
        },
    ]


def _dataset(metric, environment="Production", version=VERSION):
    return {
        "environment": environment,
        "version": version,
        "metric_id": str(metric.id),
        "filters": [],
    }


def _request(route, model, metric, filters, **dataset_fields):
    """The path and body the Performance page sends for one route."""

    dataset = _dataset(metric, **dataset_fields)
    window = {"filters": filters, "start_date": START, "end_date": END}
    if route == "graph":
        return f"/model-hub/performance/{model.id}/", {
            "datasets": [dataset],
            "breakdown": [],
            "agg_by": "daily",
            **window,
        }
    if route in ("detail", "export"):
        return f"/model-hub/performance/{route}/{model.id}/", {
            "dataset": dataset,
            **({"page": 1} if route == "detail" else {}),
            **window,
        }
    return f"/model-hub/performance/tag-distribution/{model.id}/", {
        "dataset": dataset,
        "agg_by": "daily",
        "graph_type": route,
        **window,
    }


def _create_model(organization, workspace):
    return AIModel.objects.create(
        user_model_id=f"performance-model-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _create_metric(model):
    return Metric.objects.create(
        name=f"Metric {uuid.uuid4()}",
        text_prompt="Score the response.",
        criteria_breakdown=["Score the response."],
        model=model,
        metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT,
        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
        tags=["POSITIVE:helpful", "NEGATIVE:vague"],
    )


def _install(monkeypatch, fake, module="model_hub.utils.performance_ch"):
    monkeypatch.setattr(f"{module}.ClickHouseClientSingleton", fake)


def _statement(template, model, organization, metric, **fields):
    return template.format(
        model=model.id,
        org=organization.id,
        metric=metric.id,
        score_metric=SCORE_KEY_ID,
        **{"environment": PRODUCTION_NUMBER, "score": SCORE, **fields},
    )


def _ordinary_params(model, organization, metric, **extra):
    """Parameters of an ordinary request with the four filter kinds."""

    return {
        "model_id": str(model.id),
        "org_id": str(organization.id),
        "version": VERSION,
        "environment": PRODUCTION_NUMBER,
        "start_date": START,
        "end_date": END,
        "filter_end_date": DAY_AFTER_END,
        "filter_0_key": PROPERTY_KEY,
        "filter_0_values": [PROPERTY_VALUE],
        "filter_1_key": NUMBER_KEY,
        "filter_1_value": NUMBER,
        "filter_2_score_key": f"metric_{SCORE_KEY_ID}_score",
        "filter_2_value": SCORE,
        "metric_tags_key": f"metric_{metric.id}_tags",
        "filter_3_values": [TAG_VALUE],
        **extra,
    }


def _assert_request_values_stay_out_of_text(reads, model, organization, metric):
    _assert_values_stay_out_of_text(
        reads,
        str(model.id),
        str(organization.id),
        str(metric.id),
        VERSION,
        "2026-01-0",
        PROPERTY_KEY,
        PROPERTY_VALUE,
        NUMBER_KEY,
        NUMBER_TEXT,
        SCORE_KEY_ID,
        str(SCORE),
        TAG_VALUE,
    )


def _events(model, organization, *rows):
    return [
        (str(model.id), str(organization.id), version, environment)
        for version, environment, count in rows
        for _ in range(count)
    ]


@pytest.mark.django_db
def test_performance_graph_keeps_request_values_typed(
    auth_client, organization, workspace, monkeypatch
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    fake = FakeEventsClickHouse(
        _events(
            model,
            organization,
            (VERSION, PRODUCTION_NUMBER, 2),
            ("v-1.3", PRODUCTION_NUMBER, 1),
            (VERSION, TRAINING_NUMBER, 1),
        )
    )
    _install(monkeypatch, fake)

    response = auth_client.post(
        *_request("graph", model, metric, _ordinary_filters()), format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_request_values_stay_out_of_text(fake.reads, model, organization, metric)
    ((query, params),) = fake.reads
    assert params == _ordinary_params(
        model,
        organization,
        metric,
        agg_by="daily",
        metric_score_key=f"metric_{metric.id}_score",
    )
    assert type(params["environment"]) is int
    assert type(params["filter_1_value"]) is float
    assert _render(query, params) == _statement(
        GRAPH_STATEMENT, model, organization, metric
    )
    assert response.json() == {"Dataset 1": [[START, 2]]}


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["detail", "export"])
def test_performance_rows_keep_request_values_typed(
    auth_client, organization, workspace, monkeypatch, route
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake)

    response = auth_client.post(
        *_request(route, model, metric, _ordinary_filters()), format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_request_values_stay_out_of_text(fake.reads, model, organization, metric)
    ((query, params),) = fake.reads
    page = {"limit": 31, "offset": 0} if route == "detail" else {}
    assert params == _ordinary_params(
        model,
        organization,
        metric,
        metric_score_key=f"metric_{metric.id}_score",
        metric_explanation_key=f"metric_{metric.id}_explanation",
        **page,
    )
    template = DETAIL_STATEMENT if route == "detail" else EXPORT_STATEMENT
    assert _render(query, params) == _statement(template, model, organization, metric)
    if route == "detail":
        assert response.json()["result"] == []
    else:
        assert response.content.decode().startswith("Model Input,Model Output")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("graph_type", "prefixes"),
    [("all", ["POSITIVE", "NEGATIVE"]), ("good", ["POSITIVE"]), ("bad", ["NEGATIVE"])],
    ids=["all", "good", "bad"],
)
def test_performance_tag_distribution_keeps_request_values_typed(
    auth_client, organization, workspace, monkeypatch, graph_type, prefixes
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    fake = FakeEventsClickHouse(
        _events(model, organization, (VERSION, PRODUCTION_NUMBER, 2))
    )
    _install(monkeypatch, fake)

    response = auth_client.post(
        *_request(graph_type, model, metric, _ordinary_filters()), format="json"
    )

    assert response.status_code == status.HTTP_200_OK
    _assert_request_values_stay_out_of_text(fake.reads, model, organization, metric)
    assert len(fake.reads) == len(prefixes)
    template = ALL_TAGS_STATEMENT if graph_type == "all" else TOP_TAGS_STATEMENT
    extra = {"agg_by": "daily"} if graph_type == "all" else {}
    # The all route reads the good series, then the bad series, with one filter
    # list. Each read divides the score filter's value by ten on that list, so
    # the bad read carries the good read's score divided by ten again. This
    # pins today's behaviour.
    scores = [SCORE, SCORE / 10][: len(prefixes)]
    for (query, params), prefix, score in zip(
        fake.reads, prefixes, scores, strict=True
    ):
        assert params == _ordinary_params(
            model,
            organization,
            metric,
            tag_prefix=prefix,
            filter_2_value=score,
            **extra,
        )
        assert _render(query, params) == _statement(
            template, model, organization, metric, prefix=prefix, score=score
        )
    result = response.json()["result"]
    if graph_type == "all":
        assert result == {"good": [[START, 2]], "bad": [[START, 2]]}
    else:
        assert result == [[START, 2]]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("environment", "number"),
    [
        ("Production", PRODUCTION_NUMBER),
        ("Training", TRAINING_NUMBER),
        ("3", PRODUCTION_NUMBER),
        (3, PRODUCTION_NUMBER),
        ("staging-eu", None),
        ("production", None),
    ],
    ids=[
        "name-production",
        "name-training",
        "number-text",
        "number",
        "unknown-name",
        "lowercase-name",
    ],
)
def test_performance_environment_reads_its_events_number(
    auth_client, organization, workspace, monkeypatch, environment, number
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    fake = FakeEventsClickHouse(
        _events(
            model,
            organization,
            (VERSION, PRODUCTION_NUMBER, 2),
            (VERSION, TRAINING_NUMBER, 1),
        )
    )
    _install(monkeypatch, fake)

    response = auth_client.post(
        *_request("graph", model, metric, [], environment=environment),
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    ((query, params),) = fake.reads
    assert params["environment"] == number
    assert type(params["environment"]) is type(number)
    assert _render(query, params) == _statement(
        UNFILTERED_GRAPH_STATEMENT,
        model,
        organization,
        metric,
        environment="NULL" if number is None else number,
    )
    expected_rows = {PRODUCTION_NUMBER: [[START, 2]], TRAINING_NUMBER: [[START, 1]]}
    assert response.json() == {"Dataset 1": expected_rows.get(number, [])}


@pytest.mark.django_db
def test_unusual_request_text_stays_a_parameter(
    auth_client, organization, workspace, monkeypatch
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    fake = FakeEventsClickHouse(
        _events(
            model,
            organization,
            (VERSION, PRODUCTION_NUMBER, 3),
            (UNUSUAL_VERSION, PRODUCTION_NUMBER, 2),
        )
    )
    _install(monkeypatch, fake)

    def read(version, key, value):
        fake.reads.clear()
        response = auth_client.post(
            *_request(
                "graph", model, metric, [_string_filter(key, value)], version=version
            ),
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        ((query, params),) = fake.reads
        return query, params, response.json()["Dataset 1"]

    ordinary_query, _ordinary, ordinary_rows = read(
        VERSION, PROPERTY_KEY, PROPERTY_VALUE
    )
    unusual_query, unusual, unusual_rows = read(
        UNUSUAL_VERSION, UNUSUAL_KEY, UNUSUAL_VALUE
    )

    assert unusual_query == ordinary_query
    for text in (UNUSUAL_VERSION, UNUSUAL_KEY, UNUSUAL_VALUE, "Dana"):
        assert text not in unusual_query
    assert unusual["version"] == UNUSUAL_VERSION
    assert unusual["filter_0_key"] == UNUSUAL_KEY
    assert unusual["filter_0_values"] == [UNUSUAL_VALUE]
    assert ordinary_rows == [[START, 3]]
    assert unusual_rows == [[START, 2]]


@pytest.mark.django_db
@pytest.mark.parametrize("route", ["graph", "detail", "export", "all"])
@pytest.mark.parametrize(
    ("field", "value"),
    [("operator", "between"), ("type", "column"), ("datatype", "date")],
    ids=["operator", "filter-type", "data-type"],
)
def test_performance_views_refuse_a_filter_choice_outside_the_contract(
    auth_client, organization, workspace, monkeypatch, route, field, value
):
    model = _create_model(organization, workspace)
    metric = _create_metric(model)
    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake)

    response = auth_client.post(
        *_request(route, model, metric, [{**_string_filter(), field: value}]),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"]["filters"] == [
        f'{field}: "{value}" is not a valid choice.'
    ]
    assert fake.reads == []


@pytest.mark.parametrize(
    "request_filter",
    [
        _string_filter(value={"tier": "vip"}),
        _string_filter(value=["vip"]),
        _string_filter(value=True),
        _number_filter(value="fast"),
    ],
    ids=["mapping", "list", "boolean", "number-filter-word"],
)
def test_filter_values_take_text_or_numbers(monkeypatch, request_filter):
    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake)

    with pytest.raises(ValueError):
        get_performance_details_query(
            uuid.uuid4(),
            uuid.uuid4(),
            {"environment": "Production", "version": VERSION, "metric_id": "m"},
            [request_filter],
            "daily",
            START,
            END,
        )

    assert fake.reads == []


def _scored_dataset(environment="Production"):
    return {
        "environment": environment,
        "version": VERSION,
        "start_date": START,
        "end_date": END,
        "agg_by": "daily",
        "tags": [TAG_VALUE],
    }


def _scored_metric():
    return SimpleNamespace(
        id=uuid.uuid4(), metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT
    )


def test_scored_performance_reads_keep_dataset_values_typed(monkeypatch):
    model_id, org_id, metric = uuid.uuid4(), uuid.uuid4(), _scored_metric()
    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake)

    calculate_performance(
        org_id,
        model_id,
        _scored_dataset(),
        metric,
        [
            {
                "comparison": "equalTo",
                "dataType": "string",
                "values": [PROPERTY_VALUE],
                "dataset": {"version": "v-1.3"},
            }
        ],
    )
    calculate_performance_processing(org_id, model_id, _scored_dataset("3"), metric)

    _assert_values_stay_out_of_text(
        fake.reads,
        str(model_id),
        str(org_id),
        str(metric.id),
        VERSION,
        "v-1.3",
        "2026-01-0",
        TAG_VALUE,
        PROPERTY_VALUE,
    )
    (scored_query, scored), (processing_query, processing) = fake.reads
    for params in (scored, processing):
        assert params["environment"] == PRODUCTION_NUMBER
        assert type(params["environment"]) is int
        assert params["version"] == VERSION
        assert params["tags"] == [TAG_VALUE]
    assert {"v-1.3", PROPERTY_VALUE, "daily"} <= {
        value for value in scored.values() if isinstance(value, str)
    }


@pytest.mark.parametrize(
    "change",
    [{"comparison": "between"}, {"dataType": "date"}],
    ids=["comparison", "data-type"],
)
def test_scored_performance_refuses_an_unknown_comparison_or_data_type(
    monkeypatch, change
):
    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake)
    request_filter = {
        "comparison": "equalTo",
        "dataType": "string",
        "values": [PROPERTY_VALUE],
        "dataset": {"version": VERSION},
        **change,
    }

    with pytest.raises(ValueError):
        calculate_performance(
            uuid.uuid4(),
            uuid.uuid4(),
            _scored_dataset(),
            _scored_metric(),
            [request_filter],
        )

    assert fake.reads == []


def _insight_count_filters(operator=">"):
    return [
        {
            "data_type": "string",
            "key": PROPERTY_KEY,
            "value": [PROPERTY_VALUE],
            "operator": "=",
        },
        {
            "data_type": "number",
            "key": NUMBER_KEY,
            "value": [NUMBER_TEXT],
            "operator": operator,
        },
    ]


@pytest.mark.parametrize("environment", ["Production", "3"], ids=["name", "number"])
def test_insight_reads_keep_dataset_values_typed(monkeypatch, environment):
    model_id, org_id, metric_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake, "model_hub.utils.insight")
    dataset = {
        "environment": environment,
        "version": VERSION,
        "start_date": START,
        "end_date": END,
        "agg_by": "daily",
    }

    get_insight_details_count(org_id, model_id, dataset, _insight_count_filters())
    get_insight_details(
        org_id,
        model_id,
        dataset,
        [
            {
                "type": "property",
                "data_type": "string",
                "key": PROPERTY_KEY,
                "value": [PROPERTY_VALUE],
                "operator": "equal",
            },
            {
                "type": "performanceMetric",
                "data_type": "number",
                "key": "",
                "value": [SCORE_TEXT],
                "operator": "greaterThanEqualTo",
            },
        ],
        {
            "type": "performanceMetric",
            "database_id": str(metric_id),
            "filters": [],
        },
    )

    _assert_values_stay_out_of_text(
        fake.reads,
        str(model_id),
        str(org_id),
        str(metric_id),
        VERSION,
        "2026-01-0",
        PROPERTY_KEY,
        PROPERTY_VALUE,
        NUMBER_KEY,
        NUMBER_TEXT,
        str(SCORE),
    )
    (count_query, count_params), (details_query, details_params) = fake.reads
    for params in (count_params, details_params):
        assert params["environment"] == PRODUCTION_NUMBER
        assert type(params["environment"]) is int
    assert _render(count_query, count_params) == INSIGHT_COUNT_STATEMENT.format(
        model=model_id, org=org_id, environment=PRODUCTION_NUMBER
    )
    assert f"metric_{metric_id}_score" in details_params.values()
    assert SCORE in details_params.values()


@pytest.mark.parametrize(
    "operator", ["between", "greaterThan"], ids=["unknown-word", "operator-name"]
)
def test_insight_count_refuses_an_operator_outside_the_comparison_symbols(
    monkeypatch, operator
):
    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake, "model_hub.utils.insight")
    dataset = {
        "environment": "Production",
        "version": VERSION,
        "start_date": START,
        "end_date": END,
    }

    with pytest.raises(ValueError):
        get_insight_details_count(
            uuid.uuid4(), uuid.uuid4(), dataset, _insight_count_filters(operator)
        )

    assert fake.reads == []


def test_performance_reads_send_dates_as_text(monkeypatch):
    """A caller may pass the dates as datetime values; the read sends their text."""

    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake, "model_hub.utils.insight")
    dataset = {
        "environment": "Production",
        "version": VERSION,
        "start_date": datetime(2026, 1, 1),
        "end_date": datetime(2026, 1, 2),
    }

    get_insight_details_count(uuid.uuid4(), uuid.uuid4(), dataset, [])

    ((_query, params),) = fake.reads
    for name, text in (
        ("start_date", START),
        ("end_date", END),
        ("filter_end_date", END),
    ):
        assert type(params[name]) is str
        assert params[name] == text


def test_performance_reads_send_a_number_filter_key_as_text(monkeypatch):
    """A filter key given as a number reads the result key with that text."""

    fake = FakeEventsClickHouse()
    _install(monkeypatch, fake)

    get_performance_details_query(
        uuid.uuid4(),
        uuid.uuid4(),
        {"environment": "Production", "version": VERSION, "metric_id": "m"},
        [_string_filter(key=7)],
        "daily",
        START,
        END,
    )

    ((query, params),) = fake.reads
    assert params["filter_0_key"] == "7"
    assert "indexOf(EvalResults.Key, '7')" in _render(query, params)


# The census: no request value is formatted into ClickHouse text in either
# module. A value counts by its name (the request field names below, or
# environment / version as in the optimisation census), by an alias bound to
# one in the same function, or by a loop over one (an enumerate or .items()
# loop binds the element, never the index or key). A request choice (operator,
# comparison, type, data type, graph type) counts only when read from a mapping
# by its key, or through a name bound to that read, so a choice mapped through
# a fixed table stays clean. Text appended to a list or joined counts as
# formatted text. Text built to be a value in the driver's params mapping is a
# parameter, not query text.
_REQUEST_FIELDS = frozenset(
    {
        "agg_by",
        "database_id",
        "end_date",
        "filter_key",
        "filter_key_id",
        "filter_val",
        "filter_vals",
        "filter_value",
        "id",
        "key",
        "key_id",
        "limit",
        "metric_id",
        "model_id",
        "offset",
        "org_id",
        "start_date",
        "tag_type",
        "tags",
        "value",
        "values",
    }
)
_REQUEST_CHOICE_KEYS = frozenset(
    {"comparison", "data_type", "datatype", "graph_type", "operator", "type"}
)
_CAMEL_HUMP = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TEXT_CALLS = {"str", "repr", "format", "float", "int"}
_COLLECTING_CALLS = {"append", "extend", "insert", "join"}


def _names_request_field(label):
    snake = _CAMEL_HUMP.sub("_", label).lower()
    return snake in _REQUEST_FIELDS or _names_stored_field(snake)


def _names_request_choice(label):
    return _CAMEL_HUMP.sub("_", label).lower() in _REQUEST_CHOICE_KEYS


def _names_request_key(key):
    """A mapping key that reads a request field or a request choice."""

    return _names_request_field(key) or _names_request_choice(key)


def _string_key(node):
    return (
        node.slice.value
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)
        else None
    )


def _mapping_key(node):
    """The text key a constant subscript or a .get("<key>") call reads, or None."""

    if isinstance(node, ast.Subscript):
        return _string_key(node)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    return None


def _is_request_value(expression, aliases):
    """The expression is a request value itself, or its plain text."""

    if isinstance(expression, ast.Name):
        return expression.id in aliases or _names_request_field(expression.id)
    if isinstance(expression, ast.Attribute):
        return _names_request_field(expression.attr)
    if isinstance(expression, ast.Subscript):
        key = _string_key(expression)
        if key is not None:
            return _names_request_key(key)
        return _is_request_value(expression.value, aliases)
    if isinstance(expression, ast.IfExp):
        return any(
            _is_request_value(item, aliases)
            for item in (expression.body, expression.orelse)
        )
    if isinstance(expression, ast.BoolOp):
        return any(_is_request_value(item, aliases) for item in expression.values)
    if isinstance(expression, ast.Call):
        function, arguments = expression.func, expression.args
        if isinstance(function, ast.Name) and function.id in _TEXT_CALLS:
            return bool(arguments) and _is_request_value(arguments[0], aliases)
        if isinstance(function, ast.Attribute) and function.attr == "get":
            key = arguments[0] if arguments else None
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and _names_request_key(key.value)
            ):
                return True
        if isinstance(function, ast.Attribute):
            return _is_request_value(function.value, aliases)
    return False


def _loop_pairs(target, iterable):
    """The loop target paired with the value its elements come from.

    enumerate(x) and x.items() bind the second name to an element of x, never
    the index or the key; x.values() binds each value.
    """

    pair = isinstance(target, ast.Tuple | ast.List) and len(target.elts) == 2
    if isinstance(iterable, ast.Call):
        function, arguments = iterable.func, iterable.args
        if isinstance(function, ast.Name) and function.id == "enumerate" and arguments:
            return _loop_pairs(target.elts[1] if pair else target, arguments[0])
        if isinstance(function, ast.Attribute) and not arguments:
            if function.attr == "items":
                return _loop_pairs(target.elts[1] if pair else target, function.value)
            if function.attr == "values":
                return _loop_pairs(target, function.value)
    return [(target, iterable)]


def _request_value_aliases(nodes, inherited):
    """Names in one scope bound to a request value or another alias."""

    assignments = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assignments += _assigned_pairs(target, node.value)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
            if node.value is not None:
                assignments += _assigned_pairs(node.target, node.value)
        elif isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            assignments += _loop_pairs(node.target, node.iter)
    aliases = set(inherited)
    while True:
        found = {
            name
            for target, value in assignments
            if _is_request_value(value, aliases)
            for name in _bound_names(target)
        } - aliases
        if not found:
            return aliases
        aliases |= found


def _names_request_value(expression, aliases):
    for node in ast.walk(expression):
        if isinstance(node, ast.Name):
            label = node.id
            if label in aliases:
                return True
        elif isinstance(node, ast.Attribute):
            label = node.attr
        else:
            label = _mapping_key(node)
            if label is None:
                continue
            if _names_request_choice(label):
                return True
        if _names_request_field(label):
            return True
    return False


def _text_expressions(nodes):
    """Pair A's formatted expressions, and each item a list or a join takes in."""

    yield from _formatted_expressions(nodes)
    for node in nodes:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _COLLECTING_CALLS
        ):
            items = node.args[1:] if node.func.attr == "insert" else node.args
            for item in items:
                yield node, item


def _parameter_value_nodes(nodes):
    """Nodes that build a value the scope writes into its params mapping."""

    building = set()
    for node in nodes:
        if not isinstance(node, ast.Assign):
            continue
        if all(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "params"
            for target in node.targets
        ):
            building.update(id(item) for item in ast.walk(node.value))
        elif isinstance(node.value, ast.Dict) and any(
            isinstance(target, ast.Name) and target.id == "params"
            for target in node.targets
        ):
            for value in node.value.values:
                building.update(id(item) for item in ast.walk(value))
    return building


def _formatted_request_value_lines(scope, inherited=frozenset()):
    """Lines where query text is formatted from a request value or an alias."""

    nodes = list(_scope_nodes(scope))
    aliases = _request_value_aliases(nodes, inherited)
    parameter_values = _parameter_value_nodes(nodes)
    lines = {
        node.lineno
        for node, expression in _text_expressions(nodes)
        if id(node) not in parameter_values
        and _names_request_value(expression, aliases)
    }
    for node in nodes:
        if isinstance(node, _FUNCTIONS):
            lines |= _formatted_request_value_lines(node, frozenset(aliases))
    return lines


def _parse(relative):
    return ast.parse((BACKEND_ROOT / relative).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("source", "lines"),
    [
        (
            "def read(start_date, end_date):\n"
            "    return f\"BETWEEN '{start_date}' AND '{end_date}'\"\n",
            {2},
        ),
        ("def read(item):\n    return \"indexOf(Key, '%s')\" % item['key']\n", {2}),
        ("def read(filter_value):\n    return 'x > ' + str(filter_value[0])\n", {2}),
        (
            "def read(dataset):\n    metricId = dataset['metric_id']\n"
            "    return 'metric_{}'.format(metricId)\n",
            {3},
        ),
        (
            "def read(item):\n    text = ''\n    for each in item['values']:\n"
            "        text += each\n    return text\n",
            {4},
        ),
        ("def read(limit):\n    return f'LIMIT {limit + 1}'\n", {2}),
        (
            "def read(tag_type):\n"
            "    return f\"startsWith(x, {'P' if tag_type == 'good' else 'N'})\"\n",
            {2},
        ),
        (
            "def read(version, parts):\n    parts[0] = f\"'{version}'\"\n"
            "    return parts\n",
            {2},
        ),
        (
            "def read(item, index, params):\n"
            "    params[f'filter_{index}_key'] = f\"metric_{item['key_id']}_score\"\n"
            "    return f'%(filter_{index}_key)s'\n",
            set(),
        ),
        (
            "def read(metric_id):\n"
            "    params = {'metric_key': f'metric_{metric_id}_score'}\n"
            "    return '%(metric_key)s', params\n",
            set(),
        ),
        (
            "def read(item):\n    operator = OPERATORS[item['operator']]\n"
            "    return f'x {operator} %(value)s'\n",
            set(),
        ),
        ("def read(item):\n    return f\"x {item['operator']} %(value)s\"\n", {2}),
        (
            "def read(item):\n    comparison = item.get('comparison')\n"
            "    return f'x {comparison} %(value)s'\n",
            {3},
        ),
        (
            "def read(item):\n    operator = item['operator']\n"
            "    symbol = COMPARISONS[operator]\n"
            "    return f'x {symbol} %(value)s'\n",
            set(),
        ),
        (
            "def read(item):\n    text = ''\n"
            "    for index, each in enumerate(item['values']):\n"
            "        text += f' %(value_{index})s'\n"
            "        text += each\n    return text\n",
            {5},
        ),
        (
            "def read(item):\n    text = ''\n"
            "    for name, each in item['values'].items():\n"
            "        text += f' %({name})s'\n"
            "        text += each\n    return text\n",
            {5},
        ),
        (
            "def read(item, parts):\n    parts.append(item['key'])\n"
            "    return ' AND '.join(parts)\n",
            {2},
        ),
    ],
    ids=[
        "dates",
        "filter-key",
        "filter-value",
        "camel-case-alias",
        "loop-alias",
        "page-size",
        "choice-from-request-value",
        "list-item-is-text",
        "parameter-value-stays-clean",
        "parameter-dict-stays-clean",
        "fixed-operator-stays-clean",
        "operator-by-key",
        "comparison-alias",
        "mapped-operator-stays-clean",
        "enumerate-element",
        "items-value",
        "appended-text",
    ],
)
def test_census_finds_request_values_formatted_into_text(source, lines):
    assert _formatted_request_value_lines(ast.parse(source)) == lines


def test_performance_readers_never_format_request_values_into_text():
    formatted = [
        f"{relative}:{line}"
        for relative in PERFORMANCE_READERS
        for line in sorted(_formatted_request_value_lines(_parse(relative)))
    ]

    assert formatted == []


def test_performance_readers_pass_parameters_to_every_clickhouse_read():
    unparameterised = [
        f"{relative}:{node.lineno}"
        for relative in PERFORMANCE_READERS
        for node in ast.walk(_parse(relative))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _CLICKHOUSE_CALLS
        and len(node.args) < 2
        and not any(item.arg == "params" for item in node.keywords)
    ]

    assert unparameterised == []


def test_performance_readers_map_environment_through_the_shared_helper():
    called = {
        relative: [
            (node.func.attr, node.lineno)
            for node in ast.walk(_parse(relative))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        for relative in PERFORMANCE_READERS
    }

    assert [
        f"{relative}:{line} {name}"
        for relative, calls in called.items()
        for name, line in calls
        if name in _ENVIRONMENT_MAPPINGS
    ] == []
    assert "events_number" in {name for name, _line in called[PERFORMANCE_MODULE]}
