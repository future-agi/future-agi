"""The import reads Langfuse's newer endpoints when the v1 trace reads answer 404.

A Langfuse version that does not serve this read, such as Langfuse v4 in its
default events_only mode, answers GET /api/public/traces with 404. The import
then lists traces from GET /api/public/v2/observations one cursor page at a
time, importing each page's traces before it reads the next page, and reads
each trace from its observation rows and GET /api/public/v3/scores. A Langfuse
3.x server keeps the v1 path, unchanged.

Both sets of fixture bodies describe the same three traces: 2, 4 and 2
observations, four generations (gpt-4o-mini 123/45/168 at 0.0125;
gpt-4o-mini 211/89/300 at 0.005 and claude-3-5-sonnet-20241022 777/333/1110
at 0.03; gpt-4o 50/20/70 at 0.001) and five scores, one boolean on a
generation and one categorical. The v1 bodies follow the v1 trace reads; the
newer ones follow the Langfuse API reference for observations v2 (I/O as raw
strings) and scores v3 (value typed by dataType, subject by kind). All HTTP
is mocked and every date is fixed.
"""

import gc
import json
import weakref
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
import requests

from integrations.models import ConnectionStatus, SyncLog, SyncStatus
from integrations.services.langfuse_service import LangfuseService
from integrations.temporal.activities import sync_integration_connection
from integrations.transformers.langfuse_transformer import LangfuseTransformer
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace

REQUEST = "integrations.services.langfuse_service.requests.request"
HOST = "https://langfuse.example.com"
CREDS = {"public_key": "pk-lf-test", "secret_key": "sk-lf-test"}
LANGFUSE_PROJECT = "cm3langfuseproject0001"
SCORE_TIME = "2026-11-11T14:41:00.000Z"
ROOT = "synthetic root"

EVENTS_ONLY_BODY = {
    "message": (
        "This endpoint is not available on deployments running in Langfuse v4 "
        "events_only mode. Upgrade to the latest Langfuse SDKs, which send traces "
        "via OpenTelemetry, or send traces directly to POST "
        "/api/public/otel/v1/traces. Scores can still be sent via POST "
        "/api/public/scores."
    )
}

SUPPORT = "4bf92f3577b34da6a3ce929d0e0e4736"
RAG = "5c0a3e6f8d2b4c1e9f7a6b5c4d3e2f10"
RECENT = "6d1b4f7a9e3c5d2f0a8b7c6d5e4f3a21"

TRACES = [
    {
        "id": SUPPORT,
        "name": "support-chat",
        "userId": "user-7",
        "sessionId": "session-42",
        "tags": ["support"],
        "observations": [
            {
                "id": "00f067aa0ba902b7",
                "type": "SPAN",
                "name": "support-chat",
                "parent": None,
                "start": "2026-11-11T13:32:40.598Z",
                "end": "2026-11-11T13:32:42.098Z",
                "latency": 1.5,
                "input": {"question": "Where is my order?"},
                "output": {"answer": "It ships today."},
            },
            {
                "id": "b7ad6b7169203331",
                "type": "GENERATION",
                "name": "llm-answer",
                "parent": "00f067aa0ba902b7",
                "start": "2026-11-11T13:32:40.700Z",
                "end": "2026-11-11T13:32:41.900Z",
                "latency": 1.2,
                "model": "gpt-4o-mini",
                "modelParameters": {"temperature": 0.2},
                "usage": (123, 45, 168),
                "cost": (0.0075, 0.005, 0.0125),
                "input": [{"role": "user", "content": "Where is my order?"}],
                "output": {"role": "assistant", "content": "It ships today."},
            },
        ],
        "scores": [
            {
                "id": "score-support-helpfulness",
                "name": "helpfulness",
                "dataType": "NUMERIC",
                "value": 0.8,
                "observation": None,
            },
            {
                "id": "score-support-grounded",
                "name": "grounded",
                "dataType": "BOOLEAN",
                "value": True,
                "observation": "b7ad6b7169203331",
            },
            {
                "id": "score-support-tone",
                "name": "tone",
                "dataType": "CATEGORICAL",
                "value": "friendly",
                "observation": None,
            },
        ],
    },
    {
        "id": RAG,
        "name": "rag-pipeline",
        "userId": None,
        "sessionId": None,
        "tags": ["rag"],
        "observations": [
            {
                "id": "1a2b3c4d5e6f7081",
                "type": "SPAN",
                "name": "rag-pipeline",
                "parent": None,
                "start": "2026-11-11T13:52:40.674Z",
                "end": "2026-11-11T13:52:45.674Z",
                "latency": 5.0,
                "input": {"query": "refund policy"},
                "output": "Refunds are accepted within 30 days.",
            },
            {
                "id": "2b3c4d5e6f708192",
                "type": "SPAN",
                "name": "retrieve-docs",
                "parent": "1a2b3c4d5e6f7081",
                "start": "2026-11-11T13:52:40.700Z",
                "end": "2026-11-11T13:52:41.200Z",
                "latency": 0.5,
                "input": {"query": "refund policy"},
                "output": {"documents": ["policy.md"]},
            },
            {
                "id": "3c4d5e6f708192a3",
                "type": "GENERATION",
                "name": "rewrite-query",
                "parent": "1a2b3c4d5e6f7081",
                "start": "2026-11-11T13:52:41.300Z",
                "end": "2026-11-11T13:52:42.100Z",
                "latency": 0.8,
                "model": "gpt-4o-mini",
                "modelParameters": {"temperature": 0},
                "usage": (211, 89, 300),
                "cost": (0.003, 0.002, 0.005),
                "input": [{"role": "user", "content": "refund policy"}],
                "output": "refund policy for orders",
            },
            {
                "id": "4d5e6f708192a3b4",
                "type": "GENERATION",
                "name": "answer",
                "parent": "1a2b3c4d5e6f7081",
                "start": "2026-11-11T13:52:42.200Z",
                "end": "2026-11-11T13:52:45.400Z",
                "latency": 3.2,
                "model": "claude-3-5-sonnet-20241022",
                "modelParameters": {"max_tokens": 512},
                "usage": (777, 333, 1110),
                "cost": (0.018, 0.012, 0.03),
                "input": [
                    {"role": "system", "content": "Answer from the documents."},
                    {"role": "user", "content": "refund policy"},
                ],
                "output": {
                    "role": "assistant",
                    "content": "Refunds are accepted within 30 days.",
                },
            },
        ],
        "scores": [
            {
                "id": "score-rag-relevance",
                "name": "relevance",
                "dataType": "NUMERIC",
                "value": 0.65,
                "observation": None,
            },
        ],
    },
    {
        "id": RECENT,
        "name": "recent-qa",
        "userId": None,
        "sessionId": None,
        "tags": [],
        "observations": [
            {
                "id": "5e6f708192a3b4c5",
                "type": "SPAN",
                "name": "recent-qa",
                "parent": None,
                "start": "2026-11-11T14:39:40.695Z",
                "end": "2026-11-11T14:39:41.695Z",
                "latency": 1.0,
                "input": "What is the capital of France?",
                "output": "Paris",
            },
            {
                "id": "6f708192a3b4c5d6",
                "type": "GENERATION",
                "name": "llm-answer",
                "parent": "5e6f708192a3b4c5",
                "start": "2026-11-11T14:39:40.750Z",
                "end": "2026-11-11T14:39:41.650Z",
                "latency": 0.9,
                "model": "gpt-4o",
                "modelParameters": {},
                "usage": (50, 20, 70),
                "cost": (0.0006, 0.0004, 0.001),
                "input": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
                "output": "Paris",
            },
        ],
        "scores": [
            {
                "id": "score-recent-helpfulness",
                "name": "helpfulness",
                "dataType": "NUMERIC",
                "value": 0.95,
                "observation": None,
            },
        ],
    },
]
TRACE_BY_ID = {trace["id"]: trace for trace in TRACES}


# ---------------------------------------------------------------------------
# v1 bodies, as a Langfuse 3.x server returns them
# ---------------------------------------------------------------------------


def _usage(obs):
    if "usage" not in obs:
        return {}
    prompt, completion, total = obs["usage"]
    return {"input": prompt, "output": completion, "total": total}


def _costs(obs):
    if "cost" not in obs:
        return {}
    prompt, completion, total = obs["cost"]
    return {"input": prompt, "output": completion, "total": total}


def _v1_observation(trace, obs):
    return {
        "id": obs["id"],
        "traceId": trace["id"],
        "projectId": LANGFUSE_PROJECT,
        "type": obs["type"],
        "name": obs["name"],
        "parentObservationId": obs["parent"],
        "startTime": obs["start"],
        "endTime": obs["end"],
        "completionStartTime": None,
        "model": obs.get("model"),
        "modelParameters": obs.get("modelParameters", {}),
        "input": obs["input"],
        "output": obs["output"],
        "usageDetails": _usage(obs),
        "costDetails": _costs(obs),
        "calculatedTotalCost": obs["cost"][2] if "cost" in obs else None,
        "latency": obs["latency"],
        "timeToFirstToken": None,
        "level": "DEFAULT",
        "statusMessage": None,
        "version": None,
        "metadata": {},
        "environment": "default",
    }


def _v1_score(trace, score):
    body = {
        "id": score["id"],
        "traceId": trace["id"],
        "observationId": score["observation"],
        "sessionId": None,
        "datasetRunId": None,
        "name": score["name"],
        "value": score["value"],
        "source": "API",
        "dataType": score["dataType"],
        "timestamp": SCORE_TIME,
        "createdAt": SCORE_TIME,
        "updatedAt": SCORE_TIME,
        "authorUserId": None,
        "comment": None,
        "metadata": {},
        "configId": None,
        "queueId": None,
        "environment": "default",
    }
    if score["dataType"] == "BOOLEAN":
        body["value"] = 1 if score["value"] else 0
        body["stringValue"] = "True" if score["value"] else "False"
    if score["dataType"] == "CATEGORICAL":
        # No score config is linked, so the numeric mapping is 0.
        body["value"] = 0
        body["stringValue"] = score["value"]
    return body


def _v1_trace_summary(trace):
    root = trace["observations"][0]
    total_cost = sum(obs["cost"][2] for obs in trace["observations"] if "cost" in obs)
    return {
        "id": trace["id"],
        "projectId": LANGFUSE_PROJECT,
        "name": trace["name"],
        "timestamp": root["start"],
        "environment": "default",
        "tags": trace["tags"],
        "bookmarked": False,
        "release": None,
        "version": None,
        "userId": trace["userId"],
        "sessionId": trace["sessionId"],
        "public": False,
        "input": root["input"],
        "output": root["output"],
        "metadata": {},
        "createdAt": root["start"],
        "updatedAt": root["end"],
        "htmlPath": f"/project/{LANGFUSE_PROJECT}/traces/{trace['id']}",
        "latency": root["latency"],
        "totalCost": total_cost,
    }


def _v1_trace_detail(trace):
    return {
        **_v1_trace_summary(trace),
        "observations": [_v1_observation(trace, obs) for obs in trace["observations"]],
        "scores": [_v1_score(trace, score) for score in trace["scores"]],
    }


V1_LIST = {
    "data": [_v1_trace_summary(trace) for trace in reversed(TRACES)],
    "meta": {"page": 1, "limit": 100, "totalItems": 3, "totalPages": 1},
}
V1_DETAIL = {trace["id"]: _v1_trace_detail(trace) for trace in TRACES}


# ---------------------------------------------------------------------------
# Newer bodies, as the API reference describes them
# ---------------------------------------------------------------------------


def _raw(value):
    """Observations v2 returns input and output as raw strings."""
    return value if isinstance(value, str) else json.dumps(value)


def _v2_row(trace, obs):
    cost = obs.get("cost")
    return {
        # core
        "id": obs["id"],
        "traceId": trace["id"],
        "startTime": obs["start"],
        "endTime": obs["end"],
        "projectId": LANGFUSE_PROJECT,
        "parentObservationId": obs["parent"],
        "type": obs["type"],
        # basic
        "isRootObservation": obs["parent"] is None,
        "name": obs["name"],
        "level": "DEFAULT",
        "statusMessage": None,
        "version": None,
        "environment": "default",
        "bookmarked": False,
        "public": False,
        "userId": trace["userId"],
        "sessionId": trace["sessionId"],
        # time
        "completionStartTime": None,
        "createdAt": obs["start"],
        "updatedAt": obs["end"],
        # io
        "input": _raw(obs["input"]),
        "output": _raw(obs["output"]),
        # metadata
        "metadata": {},
        # model
        "model": obs.get("model"),
        "internalModelId": None,
        "modelParameters": obs.get("modelParameters", {}),
        # usage
        "usageDetails": _usage(obs),
        "costDetails": _costs(obs),
        "totalCost": cost[2] if cost else None,
        "usagePricingTierName": None,
        # metrics
        "latency": obs["latency"],
        "timeToFirstToken": None,
        # always present, filled only with the model group
        "modelId": None,
        "inputPrice": None,
        "outputPrice": None,
        "totalPrice": None,
        # trace_context
        "traceName": trace["name"],
        "tags": trace["tags"],
        "release": None,
    }


def _v2_core_row(trace, obs):
    row = _v2_row(trace, obs)
    core = ("id", "traceId", "startTime", "endTime", "projectId")
    return {key: row[key] for key in (*core, "parentObservationId", "type")}


def _v3_score(trace, score):
    if score["observation"]:
        subject = {
            "kind": "observation",
            "id": score["observation"],
            "traceId": trace["id"],
        }
    else:
        subject = {"kind": "trace", "id": trace["id"]}
    return {
        "id": score["id"],
        "projectId": LANGFUSE_PROJECT,
        "name": score["name"],
        "value": score["value"],
        "dataType": score["dataType"],
        "source": "API",
        "timestamp": SCORE_TIME,
        "environment": "default",
        "createdAt": SCORE_TIME,
        "updatedAt": SCORE_TIME,
        "comment": None,
        "configId": None,
        "metadata": {},
        "subject": subject,
    }


WINDOW_ROWS = sorted(
    (_v2_core_row(trace, obs) for trace in TRACES for obs in trace["observations"]),
    key=lambda row: row["startTime"],
    reverse=True,
)
V2_LIST_PAGES = {
    None: {"data": WINDOW_ROWS[:4], "meta": {"cursor": "cursor-page-2"}},
    "cursor-page-2": {"data": WINDOW_ROWS[4:], "meta": {}},
}
V2_ROWS_BY_TRACE = {
    trace["id"]: [_v2_row(trace, obs) for obs in trace["observations"]]
    for trace in TRACES
}
V3_SCORES_BY_TRACE = {
    trace["id"]: [_v3_score(trace, score) for score in trace["scores"]]
    for trace in TRACES
}


# ---------------------------------------------------------------------------
# Mocked Langfuse servers
# ---------------------------------------------------------------------------


def _response(status, body):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.json.return_value = body
    if status >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            f"HTTP {status}", response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


NOT_FOUND_BODY = {"message": "Not Found"}
PROJECTS_BODY = {"data": [{"id": LANGFUSE_PROJECT, "name": "my-langfuse-project"}]}


class Langfuse3:
    """A Langfuse 3.x server: the v1 trace reads answer."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, params=None, **kwargs):
        path = urlsplit(url).path
        self.calls.append((path, dict(params or {})))
        if path == "/api/public/traces":
            return _response(200, V1_LIST)
        if path.startswith("/api/public/traces/"):
            trace_id = path.rsplit("/", 1)[1]
            if trace_id in V1_DETAIL:
                return _response(200, V1_DETAIL[trace_id])
        if path == "/api/public/projects":
            return _response(200, PROJECTS_BODY)
        return _response(404, NOT_FOUND_BODY)


class Langfuse4:
    """A Langfuse v4 server in events_only mode: only the newer reads answer."""

    def __init__(self, list_pages=None, rows_by_trace=None):
        self.calls = []
        self.list_pages = list_pages or V2_LIST_PAGES
        self.rows_by_trace = rows_by_trace or V2_ROWS_BY_TRACE

    def __call__(self, method, url, params=None, **kwargs):
        path = urlsplit(url).path
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "/api/public/traces" or path.startswith("/api/public/traces/"):
            return _response(404, EVENTS_ONLY_BODY)
        if path == "/api/public/v2/observations":
            if "traceId" in params:
                rows = self.rows_by_trace.get(params["traceId"], [])
                return _response(200, {"data": rows, "meta": {}})
            return self.list_page(params.get("cursor"))
        if path == "/api/public/v3/scores":
            scores = V3_SCORES_BY_TRACE.get(params.get("traceId"), [])
            return _response(200, {"data": scores, "meta": {"limit": 100}})
        if path == "/api/public/projects":
            return _response(200, PROJECTS_BODY)
        return _response(404, NOT_FOUND_BODY)

    def list_page(self, cursor):
        return _response(200, self.list_pages[cursor])


class _Row(dict):
    """An observation row that a test can hold a weak reference to."""


class Langfuse4OneTracePerPage(Langfuse4):
    """Lists one trace per observations page, newest first, in three pages.

    Every page is built afresh when it is read and is not kept. As each page
    is read, the server notes which traces the import has stored so far and
    how many rows of each earlier page are still held in memory.
    """

    PAGES = {
        None: (RECENT, "cursor-page-2"),
        "cursor-page-2": (RAG, "cursor-page-3"),
        "cursor-page-3": (SUPPORT, None),
    }

    def __init__(self, project):
        super().__init__()
        self.project = project
        self.page_rows = []
        self.on_page_read = {}

    def list_page(self, cursor):
        gc.collect()
        stored = Trace.no_workspace_objects.filter(project=self.project)
        held = [sum(ref() is not None for ref in refs) for refs in self.page_rows]
        self.on_page_read[cursor] = (
            set(stored.values_list("external_id", flat=True)),
            held,
        )
        trace_id, next_cursor = self.PAGES[cursor]
        trace = TRACE_BY_ID[trace_id]
        rows = [_Row(_v2_core_row(trace, obs)) for obs in trace["observations"]]
        self.page_rows.append([weakref.ref(row) for row in rows])
        meta = {"cursor": next_cursor} if next_cursor else {}
        return _response(200, {"data": rows, "meta": meta})


def _newer_calls(server):
    return [call for call in server.calls if "/v2/" in call[0] or "/v3/" in call[0]]


# ---------------------------------------------------------------------------
# The rows a Langfuse 3.x import stores for these three traces: each
# observation plus one synthetic root per trace, and every score
# ---------------------------------------------------------------------------

SCORE_OBSERVATION = {
    score["id"]: score["observation"] for trace in TRACES for score in trace["scores"]
}


def _chain(name, parent, latency_ms):
    """A span's stored row: no model, tokens or cost."""
    return (name, "chain", parent, "", "", 0, 0, 0, 0.0, latency_ms)


def _llm(name, parent, model, provider, tokens, cost, latency_ms):
    """A generation's stored row: model, provider, tokens and cost."""
    return (name, "llm", parent, model, provider, *tokens, cost, latency_ms)


EXPECTED_ROWS = {
    SUPPORT: {
        "name": "support-chat",
        "tags": ["langfuse", "support"],
        "input": {"question": "Where is my order?"},
        "output": {"answer": "It ships today."},
        "root": True,
        "observations": {
            "00f067aa0ba902b7": _chain("support-chat", ROOT, 1500),
            "b7ad6b7169203331": _llm(
                "llm-answer",
                "00f067aa0ba902b7",
                "gpt-4o-mini",
                "openai",
                (123, 45, 168),
                0.0125,
                1200,
            ),
        },
        "scores": {
            "score-support-helpfulness": ("helpfulness", 0.8, "", None, None),
            "score-support-grounded": (
                "grounded",
                1.0,
                "True",
                True,
                "b7ad6b7169203331",
            ),
            "score-support-tone": ("tone", 0.0, "friendly", None, None),
        },
    },
    RAG: {
        "name": "rag-pipeline",
        "tags": ["langfuse", "rag"],
        "input": {"query": "refund policy"},
        "output": "Refunds are accepted within 30 days.",
        "root": True,
        "observations": {
            "1a2b3c4d5e6f7081": _chain("rag-pipeline", ROOT, 5000),
            "2b3c4d5e6f708192": _chain("retrieve-docs", "1a2b3c4d5e6f7081", 500),
            "3c4d5e6f708192a3": _llm(
                "rewrite-query",
                "1a2b3c4d5e6f7081",
                "gpt-4o-mini",
                "openai",
                (211, 89, 300),
                0.005,
                800,
            ),
            "4d5e6f708192a3b4": _llm(
                "answer",
                "1a2b3c4d5e6f7081",
                "claude-3-5-sonnet-20241022",
                "anthropic",
                (777, 333, 1110),
                0.03,
                3200,
            ),
        },
        "scores": {
            "score-rag-relevance": ("relevance", 0.65, "", None, None),
        },
    },
    RECENT: {
        "name": "recent-qa",
        "tags": ["langfuse"],
        "input": "What is the capital of France?",
        "output": "Paris",
        "root": True,
        "observations": {
            "5e6f708192a3b4c5": _chain("recent-qa", ROOT, 1000),
            "6f708192a3b4c5d6": _llm(
                "llm-answer",
                "5e6f708192a3b4c5",
                "gpt-4o",
                "openai",
                (50, 20, 70),
                0.001,
                900,
            ),
        },
        "scores": {
            "score-recent-helpfulness": ("helpfulness", 0.95, "", None, None),
        },
    },
}


def _stored_rows(project):
    rows = {}
    for trace in Trace.no_workspace_objects.filter(project=project):
        root_id = f"root-{trace.external_id}"
        spans = list(ObservationSpan.no_workspace_objects.filter(trace=trace))
        observations = {}
        for span in spans:
            if span.id == root_id:
                continue
            parent = ROOT if span.parent_span_id == root_id else span.parent_span_id
            observations[span.id] = (
                span.name,
                span.observation_type,
                parent,
                span.model or "",
                span.provider or "",
                span.prompt_tokens,
                span.completion_tokens,
                span.total_tokens,
                round(float(span.cost or 0), 6),
                span.latency_ms,
            )
        scores = {}
        for score in EvalLogger.no_workspace_objects.filter(trace=trace):
            # A trace-level score is attached to the trace's earliest span,
            # which ties with the synthetic root; only an observation-level
            # score names its span.
            on_observation = (
                score.observation_span_id
                if SCORE_OBSERVATION.get(score.eval_id)
                else None
            )
            scores[score.eval_id] = (
                score.eval_type_id,
                score.output_float,
                score.output_str or "",
                score.output_bool,
                on_observation,
            )
        rows[trace.external_id] = {
            "name": trace.name,
            "tags": sorted(trace.tags or []),
            "input": trace.input,
            "output": trace.output,
            "root": any(span.id == root_id for span in spans),
            "observations": observations,
            "scores": scores,
        }
    return rows


def _span_attributes(project):
    return {
        span.id: span.span_attributes
        for span in ObservationSpan.no_workspace_objects.filter(trace__project=project)
    }


# ---------------------------------------------------------------------------
# Import through the activity
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestImportStoresEveryTraceSpanAndScore:
    @patch("time.sleep")
    def test_langfuse_3_v1_path_stores_every_trace_span_and_score(
        self, mock_sleep, integration_connection
    ):
        server = Langfuse3()
        with patch(REQUEST, side_effect=server):
            sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        log = SyncLog.objects.filter(connection=integration_connection).latest(
            "started_at"
        )
        assert integration_connection.status == ConnectionStatus.ACTIVE
        assert log.status == SyncStatus.SUCCESS
        assert (log.traces_fetched, log.traces_created) == (3, 3)
        assert (log.spans_synced, log.scores_synced) == (8, 5)
        assert _stored_rows(integration_connection.project) == EXPECTED_ROWS
        assert _newer_calls(server) == []

    @patch("time.sleep")
    def test_events_only_server_stores_the_same_rows_through_the_newer_reads(
        self, mock_sleep, integration_connection
    ):
        server = Langfuse4()
        with patch(REQUEST, side_effect=server):
            sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        log = SyncLog.objects.filter(connection=integration_connection).latest(
            "started_at"
        )
        assert integration_connection.status == ConnectionStatus.ACTIVE
        assert integration_connection.status_message == ""
        assert log.status == SyncStatus.SUCCESS
        assert (log.traces_fetched, log.traces_created) == (3, 3)
        assert (log.spans_synced, log.scores_synced) == (8, 5)
        assert _stored_rows(integration_connection.project) == EXPECTED_ROWS

        listing = [
            params
            for path, params in server.calls
            if path == "/api/public/v2/observations" and "traceId" not in params
        ]
        assert [params.get("cursor") for params in listing] == [None, "cursor-page-2"]
        assert listing[0]["fields"] == "core"
        assert listing[0]["limit"] == 1000
        # A first sync reads from the import's backfill start.
        assert listing[0]["fromStartTime"] == "2020-01-01T00:00:00.000Z"
        assert listing[0]["toStartTime"].endswith("Z")

    @patch("time.sleep")
    def test_newer_reads_keep_the_five_minute_overlap_cursor(
        self, mock_sleep, integration_connection
    ):
        integration_connection.last_synced_at = datetime(
            2026, 11, 11, 14, 39, 1, 384985, tzinfo=UTC
        )
        integration_connection.save(update_fields=["last_synced_at"])
        server = Langfuse4()
        with patch(REQUEST, side_effect=server):
            sync_integration_connection(str(integration_connection.id))

        listing = [
            params
            for path, params in server.calls
            if path == "/api/public/v2/observations" and "traceId" not in params
        ]
        assert [params.get("cursor") for params in listing] == [None, "cursor-page-2"]
        # Every page of the listing is read with the same window.
        starts = {params["fromStartTime"] for params in listing}
        ends = {params["toStartTime"] for params in listing}
        assert starts == {"2026-11-11T14:34:01.384Z"}
        assert len(ends) == 1
        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ACTIVE

    @patch("time.sleep")
    def test_both_trace_lists_answering_404_fail_the_sync(
        self, mock_sleep, integration_connection
    ):
        def nothing(method, url, **kwargs):
            return _response(404, NOT_FOUND_BODY)

        with patch(REQUEST, side_effect=nothing):
            sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        log = SyncLog.objects.filter(connection=integration_connection).latest(
            "started_at"
        )
        assert integration_connection.status == ConnectionStatus.ERROR
        assert log.status == SyncStatus.FAILED
        assert log.error_details["http_status"] == 404
        assert not Trace.no_workspace_objects.filter(
            project=integration_connection.project
        ).exists()

    @patch("time.sleep")
    def test_newer_reads_store_the_same_span_attributes_as_the_v1_path(
        self, mock_sleep, integration_connection
    ):
        with patch(REQUEST, side_effect=Langfuse3()):
            sync_integration_connection(str(integration_connection.id))
        from_v1 = _span_attributes(integration_connection.project)

        # Re-import the same traces through the newer reads: every row is
        # rewritten in place, so a field mapped differently would change.
        integration_connection.refresh_from_db()
        integration_connection.last_synced_at = None
        integration_connection.save(update_fields=["last_synced_at"])
        with patch(REQUEST, side_effect=Langfuse4()):
            sync_integration_connection(str(integration_connection.id))

        integration_connection.refresh_from_db()
        assert integration_connection.status == ConnectionStatus.ACTIVE
        assert _span_attributes(integration_connection.project) == from_v1
        assert _stored_rows(integration_connection.project) == EXPECTED_ROWS

    @patch("time.sleep")
    def test_each_observations_page_is_imported_before_the_next_is_read(
        self, mock_sleep, integration_connection
    ):
        server = Langfuse4OneTracePerPage(integration_connection.project)
        with patch(REQUEST, side_effect=server):
            sync_integration_connection(str(integration_connection.id))

        assert list(server.on_page_read) == [None, "cursor-page-2", "cursor-page-3"]
        stored, _ = server.on_page_read["cursor-page-2"]
        assert stored == {RECENT}
        # When page 3 is read, the traces of pages 1 and 2 are stored and the
        # rows of page 1 are released: the listing holds a page, not the window.
        stored, held = server.on_page_read["cursor-page-3"]
        assert stored == {RECENT, RAG}
        assert held[0] == 0

        integration_connection.refresh_from_db()
        log = SyncLog.objects.filter(connection=integration_connection).latest(
            "started_at"
        )
        assert integration_connection.status == ConnectionStatus.ACTIVE
        assert (log.traces_fetched, log.traces_created) == (3, 3)
        assert _stored_rows(integration_connection.project) == EXPECTED_ROWS


# ---------------------------------------------------------------------------
# The service on its own
# ---------------------------------------------------------------------------


TRACE_FIELDS = ("id", "name", "userId", "sessionId", "tags", "input", "output")


def _ordered(rows, key):
    return sorted(rows, key=lambda row: row[key])


@pytest.mark.unit
class TestNewerReads:
    def test_newer_trace_detail_transforms_like_the_v1_detail(self):
        tf = LangfuseTransformer()
        with patch(REQUEST, side_effect=Langfuse4()):
            for trace in TRACES:
                newer = LangfuseService().fetch_trace_detail(HOST, CREDS, trace["id"])
                v1 = V1_DETAIL[trace["id"]]

                for field in TRACE_FIELDS:
                    assert newer[field] == v1[field], field
                assert tf.transform_trace(newer, "p") == tf.transform_trace(v1, "p")
                spans = tf.transform_observations(newer, "trace", "p")
                v1_spans = tf.transform_observations(v1, "trace", "p")
                assert _ordered(spans, "id") == _ordered(v1_spans, "id")
                scores = tf.transform_scores(newer, "trace")
                v1_scores = tf.transform_scores(v1, "trace")
                key = "langfuse_score_id"
                assert _ordered(scores, key) == _ordered(v1_scores, key)

    def test_newer_score_values_read_as_the_v1_score_values(self):
        with patch(REQUEST, side_effect=Langfuse4()):
            newer = LangfuseService().fetch_trace_detail(HOST, CREDS, SUPPORT)

        scores = {score["name"]: score for score in newer["scores"]}
        grounded, tone = scores["grounded"], scores["tone"]
        assert (grounded["value"], grounded["stringValue"]) == (1, "True")
        assert grounded["observationId"] == "b7ad6b7169203331"
        assert (tone["value"], tone["stringValue"]) == (0, "friendly")
        assert tone["observationId"] is None
        assert scores["helpfulness"]["value"] == 0.8

    def test_each_newer_list_call_reads_one_page_and_lists_a_trace_once(self):
        server = Langfuse4()
        service = LangfuseService()
        window = {
            "from_timestamp": "2026-11-11T13:00:00+00:00",
            "to_timestamp": "2026-11-11T15:00:00.123456+00:00",
        }
        with patch(REQUEST, side_effect=server):
            first = service.fetch_traces(HOST, CREDS, page=1, limit=100, **window)
            second = service.fetch_traces(
                HOST, CREDS, page=first["next_page"], limit=100, **window
            )

        assert [trace["id"] for trace in first["traces"]] == [RECENT, RAG]
        assert first["has_more"] is True
        # The import loop adds one to the page it holds as a fallback.
        assert first["next_page"] + 1 == 3
        # The rag trace's rows span both pages; only the first lists it.
        assert [trace["id"] for trace in second["traces"]] == [SUPPORT]
        assert second["has_more"] is False
        listing = [params for path, params in server.calls if "/v2/" in path]
        assert listing == [
            {
                "fields": "core",
                "limit": 1000,
                "fromStartTime": "2026-11-11T13:00:00.000Z",
                "toStartTime": "2026-11-11T15:00:00.123Z",
            },
            {
                "fields": "core",
                "limit": 1000,
                "fromStartTime": "2026-11-11T13:00:00.000Z",
                "toStartTime": "2026-11-11T15:00:00.123Z",
                "cursor": "cursor-page-2",
            },
        ]

    def test_a_repeated_cursor_ends_the_listing(self):
        row = WINDOW_ROWS[-1]
        pages = {
            None: {"data": [row], "meta": {"cursor": "same"}},
            "same": {"data": [row], "meta": {"cursor": "same"}},
        }
        server = Langfuse4(list_pages=pages)
        service = LangfuseService()
        with patch(REQUEST, side_effect=server):
            first = service.fetch_traces(HOST, CREDS, page=1, limit=100)
            second = service.fetch_traces(
                HOST, CREDS, page=first["next_page"], limit=100
            )

        assert [trace["id"] for trace in first["traces"]] == [SUPPORT]
        assert (second["traces"], second["has_more"]) == ([], False)
        assert len(_newer_calls(server)) == 2

    def test_a_page_without_a_new_trace_is_read_past_in_the_same_call(self):
        pages = {
            None: {"data": [], "meta": {"cursor": "cursor-after-empty"}},
            "cursor-after-empty": {"data": WINDOW_ROWS[-2:], "meta": {}},
        }
        server = Langfuse4(list_pages=pages)
        with patch(REQUEST, side_effect=server):
            result = LangfuseService().fetch_traces(HOST, CREDS, page=1, limit=100)

        assert [trace["id"] for trace in result["traces"]] == [SUPPORT]
        assert result["has_more"] is False
        assert len(_newer_calls(server)) == 2

    def test_the_root_is_the_marked_row_then_a_parentless_row_then_the_earliest(self):
        span, generation = (dict(row) for row in V2_ROWS_BY_TRACE[SUPPORT])
        # The generation starts before the span that is the trace's root.
        generation["startTime"] = "2026-11-11T13:32:40.500Z"
        span_input = {"question": "Where is my order?"}
        generation_input = [{"role": "user", "content": "Where is my order?"}]

        def root_input(*rows):
            server = Langfuse4(rows_by_trace={SUPPORT: list(rows)})
            with patch(REQUEST, side_effect=server):
                detail = LangfuseService().fetch_trace_detail(HOST, CREDS, SUPPORT)
            return detail["input"]

        # A root the SDK marked may keep a parent from another service.
        marked = {**span, "parentObservationId": "remote-parent-span"}
        assert root_input(marked, generation) == span_input
        # Without the mark, the row with no parent is the root.
        unmarked = [
            {key: value for key, value in row.items() if key != "isRootObservation"}
            for row in (span, generation)
        ]
        assert root_input(*unmarked) == span_input
        # With neither, the earliest row is the root.
        orphan = {**unmarked[0], "parentObservationId": "remote-parent-span"}
        assert root_input(orphan, unmarked[1]) == generation_input

    def test_v1_answer_never_calls_the_newer_reads(self):
        server = Langfuse3()
        with patch(REQUEST, side_effect=server):
            result = LangfuseService().fetch_traces(
                HOST,
                CREDS,
                from_timestamp="2026-11-11T13:00:00+00:00",
                to_timestamp="2026-11-11T15:00:00+00:00",
                page=1,
                limit=100,
            )
            detail = LangfuseService().fetch_trace_detail(HOST, CREDS, SUPPORT)

        assert len(result["traces"]) == 3
        assert detail == V1_DETAIL[SUPPORT]
        assert server.calls == [
            (
                "/api/public/traces",
                {
                    "page": 1,
                    "limit": 100,
                    "fromTimestamp": "2026-11-11T13:00:00+00:00",
                    "toTimestamp": "2026-11-11T15:00:00+00:00",
                },
            ),
            (f"/api/public/traces/{SUPPORT}", {}),
        ]

    def test_a_trace_missing_from_both_reads_keeps_the_v1_404(self):
        with patch(REQUEST, side_effect=Langfuse4()):
            with pytest.raises(requests.exceptions.HTTPError) as caught:
                LangfuseService().fetch_trace_detail(HOST, CREDS, "0" * 32)

        assert caught.value.response.status_code == 404
