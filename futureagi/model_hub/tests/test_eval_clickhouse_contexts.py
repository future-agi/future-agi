from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

from model_hub.selectors.eval_list_charts import read_eval_list_charts
from model_hub.serializers.contracts import (
    EvalTemplateListChartsRequestSerializer,
    LegacyEvalTemplatesRequestSerializer,
)
from model_hub.views.separate_evals import EvalTemplateListChartsView


class _FakeClickHouseClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute_read(self, query, params, *, timeout_ms, settings):
        self.calls.append(
            {
                "query": query,
                "params": params,
                "timeout_ms": timeout_ms,
                "settings": settings,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response, [], 1.0


@pytest.mark.parametrize(
    ("page_size", "current_page_index"),
    [
        (0, 0),
        (101, 0),
        (10, -1),
    ],
)
def test_legacy_eval_list_request_keeps_preexisting_integer_contract(
    page_size,
    current_page_index,
):
    serializer = LegacyEvalTemplatesRequestSerializer(
        data={
            "page_size": page_size,
            "current_page_index": current_page_index,
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["page_size"] == page_size
    assert serializer.validated_data["current_page_index"] == current_page_index


def test_eval_list_charts_request_keeps_preexisting_list_contract():
    serializer = EvalTemplateListChartsRequestSerializer(
        data={"template_ids": [str(uuid4()) for _ in range(101)]}
    )

    assert serializer.is_valid(), serializer.errors
    assert len(serializer.validated_data["template_ids"]) == 101


def test_eval_list_charts_accepts_over_budget_request_and_explicitly_degrades(
    monkeypatch,
):
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts.get_clickhouse_client",
        lambda: pytest.fail("over-budget chart request must not query ClickHouse"),
    )

    request = SimpleNamespace(
        validated_data={"template_ids": [uuid4() for _ in range(101)]},
        organization=SimpleNamespace(id=uuid4()),
        workspace=SimpleNamespace(id=uuid4(), is_default=False),
    )
    response = EvalTemplateListChartsView.post.__wrapped__(
        EvalTemplateListChartsView(),
        request,
    )

    assert response.status_code == 200
    assert response.data["result"] == {
        "charts": {},
        "query_complete": False,
        "query_status": "degraded",
        "query_sampled": False,
        "query_error_code": "template_limit_exceeded",
        "data_stale": False,
    }


def test_eval_list_charts_uses_one_bounded_materialized_clickhouse_aggregate(
    monkeypatch,
):
    template_id = uuid4()
    bucket = datetime.now(UTC)
    client = _FakeClickHouseClient(
        [[(str(template_id), bucket, 4, 1)]],
    )
    cache_writes = []
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_get",
        lambda _cache, _key: None,
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_set",
        lambda _cache, key, value, *, timeout: cache_writes.append(
            (key, value, timeout)
        ),
    )
    organization = SimpleNamespace(id=uuid4())
    workspace = SimpleNamespace(id=uuid4(), is_default=False)

    result = read_eval_list_charts(
        organization,
        workspace,
        [template_id],
    )

    assert result["charts"][str(template_id)]["run_count"] == 4
    assert result["charts"][str(template_id)]["error_rate"][-1]["value"] == 25.0
    assert result["query_status"] == "complete"
    assert result["query_sampled"] is False
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["timeout_ms"] == 2_000
    assert call["settings"]["max_threads"] == 2
    assert "max_rows_to_read" not in call["settings"]
    assert call["settings"]["max_bytes_to_read"] == 36 * 1024 * 1024 * 1024
    assert call["settings"]["max_memory_usage"] == 36 * 1024 * 1024 * 1024
    assert "eval_score" in call["query"]
    assert "eval_output_str" in call["query"]
    assert "JSONExtract" not in call["query"]
    assert "workspace_id = toUUID" in call["query"]
    assert "usage_apicalllog FINAL" not in call["query"]
    assert "ORDER BY _peerdb_version DESC" in call["query"]
    assert "LIMIT 1 BY id" in call["query"]
    assert {entry[2] for entry in cache_writes} == {30, 6 * 60 * 60}


def test_eval_list_charts_returns_stale_result_when_clickhouse_exceeds_budget(
    monkeypatch,
):
    template_id = uuid4()
    stale = {str(template_id): {"chart": [], "error_rate": [], "run_count": 9}}
    client = _FakeClickHouseClient([ReadDeadlineExceeded("budget")])
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_get",
        lambda _cache, key: stale if ":stale:" in key else None,
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_set",
        lambda *_args, **_kwargs: None,
    )

    result = read_eval_list_charts(
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4(), is_default=True),
        [template_id],
    )

    assert result == {
        "charts": stale,
        "query_complete": False,
        "query_status": "stale",
        "query_sampled": False,
        "data_stale": True,
        "query_error_code": "read_budget_exceeded",
    }
    assert client.calls[0]["timeout_ms"] == 2_000


def test_eval_list_charts_marks_cold_budget_failure_degraded(monkeypatch):
    template_id = uuid4()
    client = _FakeClickHouseClient([ReadDeadlineExceeded("budget")])
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_get",
        lambda _cache, _key: None,
    )

    result = read_eval_list_charts(
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4(), is_default=True),
        [template_id],
    )

    assert result["charts"][str(template_id)]["run_count"] == 0
    assert result == {
        "charts": result["charts"],
        "query_complete": False,
        "query_status": "degraded",
        "query_sampled": False,
        "data_stale": False,
        "query_error_code": "read_budget_exceeded",
    }


def test_eval_list_charts_reraises_clickhouse_query_defect(monkeypatch):
    from clickhouse_driver.errors import ServerException

    template_id = uuid4()
    client = _FakeClickHouseClient(
        [ServerException("private missing-table query text", code=60)]
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_get",
        lambda _cache, _key: None,
    )

    with pytest.raises(ServerException, match="private missing-table"):
        read_eval_list_charts(
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4(), is_default=True),
            [template_id],
        )


def test_eval_list_charts_degrades_without_clickhouse_configuration(
    monkeypatch,
    settings,
):
    template_id = uuid4()
    settings.CLICKHOUSE = {
        "CH_ENABLED": False,
        "CH_HOST": None,
    }
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts.get_clickhouse_client",
        lambda: pytest.fail("disabled chart reads must not create a CH client"),
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_get",
        lambda _cache, _key: None,
    )

    result = read_eval_list_charts(
        SimpleNamespace(id=uuid4()),
        SimpleNamespace(id=uuid4(), is_default=True),
        [template_id],
    )

    assert result["charts"][str(template_id)]["run_count"] == 0
    assert result == {
        "charts": result["charts"],
        "query_complete": False,
        "query_status": "degraded",
        "query_sampled": False,
        "data_stale": False,
        "query_error_code": "query_failed",
    }


@pytest.mark.unit
def test_eval_list_charts_response_contract_accepts_sanitized_query_failure():
    from model_hub.serializers.contracts import (
        EvalTemplateListChartsResponseResultSerializer,
    )

    serializer = EvalTemplateListChartsResponseResultSerializer(
        data={
            "charts": {},
            "query_complete": False,
            "query_status": "degraded",
            "query_sampled": False,
            "query_error_code": "query_failed",
            "data_stale": False,
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["query_error_code"] == "query_failed"


def test_eval_list_charts_does_not_mask_programming_error_with_stale_data(
    monkeypatch,
):
    template_id = uuid4()
    stale = {str(template_id): {"chart": [], "error_rate": [], "run_count": 9}}
    client = _FakeClickHouseClient([RuntimeError("secret malformed chart SQL")])
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts.get_clickhouse_client",
        lambda: client,
    )
    monkeypatch.setattr(
        "model_hub.selectors.eval_list_charts._cache_get",
        lambda _cache, key: stale if ":stale:" in key else None,
    )

    with pytest.raises(RuntimeError, match="secret malformed chart SQL"):
        read_eval_list_charts(
            SimpleNamespace(id=uuid4()),
            SimpleNamespace(id=uuid4(), is_default=True),
            [template_id],
        )
