"""Recorded baseline of one Vapi call through today's provider-poll path.

The poll stores a call as one exported top span; the voice list, the call
detail endpoint and the drawer read that span back. These tests run the real
code over ``fixtures/voice_baseline/vapi_call.json`` and compare what comes out
against the recorded files beside it, so any drift in the stored span or in
what the readers answer fails here rather than at a customer.

Re-record with ``VOICE_BASELINE_RECORD=1`` (see the README in the fixture
folder); without it the tests only compare.
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent / "fixtures" / "voice_baseline"
RECORD = os.environ.get("VOICE_BASELINE_RECORD") == "1"

# Fixed ids so the deterministic trace and span ids, and every reader output
# keyed by them, are the same on every run.
PROJECT_ID = uuid.UUID("0b5e11e0-0000-4000-8000-0000000000b1")
ORGANIZATION_ID = uuid.UUID("0b5e11e0-0000-4000-8000-0000000000b2")
WORKSPACE_ID = uuid.UUID("0b5e11e0-0000-4000-8000-0000000000b3")
PROVIDER_ROW_ID = uuid.UUID("0b5e11e0-0000-4000-8000-0000000000b4")
PROJECT_NAME = "voice-baseline"
# The collector stamps the wall clock as the row version; any constant works
# for the readers, which only need it to be a non-negative int.
ROW_VERSION = 1
# Invented; only the stubbed gRPC exporter ever sees it.
SYSTEM_KEY_PAIR = ("baseline-ingest-key", "baseline-ingest-secret")


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _as_json(value: Any) -> Any:
    """What the recorded file would hold for *value* (tuples, datetimes, ...)."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def _write(name: str, value: Any) -> None:
    (FIXTURES / name).write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n"
    )


def _differences(expected: Any, actual: Any, path: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        out = []
        for key in sorted(set(expected) | set(actual)):
            where = f"{path}[{key!r}]"
            if key not in actual:
                out.append(f"{where}: missing from the fresh run")
            elif key not in expected:
                out.append(f"{where}: new in the fresh run = {actual[key]!r}")
            else:
                out.extend(_differences(expected[key], actual[key], where))
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        out = []
        if len(expected) != len(actual):
            out.append(f"{path}: length {len(expected)} recorded, {len(actual)} now")
        for index, (old, new) in enumerate(zip(expected, actual, strict=False)):
            out.extend(_differences(old, new, f"{path}[{index}]"))
        return out
    if type(expected) is not type(actual) or expected != actual:
        return [f"{path}: recorded {expected!r}, now {actual!r}"]
    return []


def _check_against_file(name: str, fresh: Any) -> None:
    fresh = _as_json(fresh)
    if RECORD:
        _write(name, fresh)
        # A shell that keeps the variable set would otherwise re-record and
        # pass on every run, so a behaviour change could never fail here.
        pytest.fail(
            f"re-recorded {name}; run again without VOICE_BASELINE_RECORD to compare"
        )
    path = FIXTURES / name
    assert path.exists(), f"{path} is missing; record it with VOICE_BASELINE_RECORD=1"
    diffs = _differences(_load(name), fresh)
    assert not diffs, (
        f"{name} no longer matches today's path ({len(diffs)} differences); "
        "if the change is intended, re-record with VOICE_BASELINE_RECORD=1:\n"
        + "\n".join(diffs)
    )


_SQL_SELECT_TOKEN = re.compile(r"[(),]|\bFROM\b")
# A quoted literal is matched first and kept, so a ``--`` inside one survives.
_SQL_LITERAL_OR_LINE_COMMENT = re.compile(r"('(?:[^'\\]|\\.)*')|--[^\n]*")


def _select_names(sql: str) -> set[str]:
    """The output column names of the outermost SELECT in *sql*.

    Line comments are dropped first, because ClickHouse drops a commented-out
    column too. The list is cut at the first ``FROM`` outside parentheses and
    split on commas outside parentheses, so nested sub-selects and function
    arguments never contribute a name. Each item is named by its trailing
    word: the ``AS`` alias, or the bare column when there is none. The
    builders' outer lists hold no string literals, so parentheses and commas
    are structural.
    """
    sql = _SQL_LITERAL_OR_LINE_COMMENT.sub(lambda m: m.group(1) or "", sql)
    head = re.search(r"\bSELECT\b", sql)
    assert head, "the reader's query has no SELECT"
    depth, item_start, items = 0, head.end(), []
    for token in _SQL_SELECT_TOKEN.finditer(sql, head.end()):
        if token.group() == "(":
            depth += 1
        elif token.group() == ")":
            depth -= 1
        elif depth == 0:
            items.append(sql[item_start : token.start()])
            if token.group() == "FROM":
                break
            item_start = token.end()
    return {item.split()[-1] for item in items}


def _assert_columns(name: str, row: dict, projected: set[str]) -> None:
    assert set(row) == projected, (
        f"the {name} built here no longer has the reader's columns; "
        f"only here: {sorted(set(row) - projected)}, "
        f"only in the reader's query: {sorted(projected - set(row))}"
    )


class _SequentialUuid:
    """Stands in for the ``uuid`` module where transcript entries get ids.

    The provider-log processor gives every transcript entry a fresh uuid4, so
    without this the exported transcript attribute and the detail transcript
    would differ on every run.
    """

    def __init__(self) -> None:
        self._issued = 0

    def uuid4(self) -> uuid.UUID:
        self._issued += 1
        return uuid.UUID(int=self._issued)

    def __getattr__(self, name: str) -> Any:
        return getattr(uuid, name)


# ---------------------------------------------------------------------------
# The ClickHouse row the collector writes for the exported dict.
#
# Not today's Python code: a transcription of the collector's rules, because
# the baseline runs in-process without a collector. Its input is the OTLP span
# today's export hands the gRPC exporter (built by
# ``simulate.services.sim_collector_emit.export_sim_spans``, which runs
# ``_otlp_attributes`` and sets the status, parent and resource), then
# ``fi-collector/pkg/adapter/adapter.go`` ``Split`` (:55-108) with its
# prefixes (:32-40, :133-140), ``DeriveHotKeys``
# (:212-271) with its ``providerKeys`` (:289-295) and ``firstString``
# (:317-324), then ``fi-collector/exporter/clickhouse25exporter/converter.go``
# ``spanToRow`` (:271-414), ``resolveObservationType`` (:110-134) with its
# tables (:67-71, :82-104),
# ``statusString`` (:756-765), ``formatDateTime64`` (:793-795),
# ``traceIDToUUIDString`` (:822-826). The column names are not typed here: the
# readers' own statements are asserted against the row below. ``model``, the token counts
# and ``cost`` stay empty because neither reader reads them for a root span,
# and the Go overflow of integers beyond 2**53 and non-finite doubles is left
# out because no exported attribute reaches it.
# ---------------------------------------------------------------------------

_OVERFLOW_PREFIXES = (
    "llm.prompt",
    "llm.completion",
    "llm.messages",
    "input.value",
    "output.value",
    "retrieval.documents",
    "embedding.embeddings",
)
_SPAN_KIND_KEYS = (
    "fi.span.kind",
    "gen_ai.span.kind",
    "llm.request.type",
    "openinference.span.kind",
)
_SPAN_KIND_SYNONYMS = {
    "chat": "llm",
    "generate_content": "llm",
    "text_completion": "llm",
    "embeddings": "embedding",
    "execute_tool": "tool",
    "invoke": "chain",
}
_KNOWN_OBSERVATION_TYPES = {
    "tool",
    "chain",
    "llm",
    "retriever",
    "embedding",
    "agent",
    "reranker",
    "guardrail",
    "evaluator",
    "conversation",
    "unknown",
}
_PROVIDER_KEYS = (
    "gen_ai.provider.name",
    "llm.system",
    "gen_ai.system",
    "llm.vendor",
    "llm.provider",
)


def _first_string(attrs_string: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        if attrs_string.get(key):
            return attrs_string[key]
    return ""


def _ch_datetime(epoch_ns: int) -> datetime:
    # DateTime64(6, 'UTC'): microsecond precision, read back as aware UTC.
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=epoch_ns // 1000)


def _collector_row(readable: Any, *, project_id: str) -> dict:
    from opentelemetry.trace.status import StatusCode

    attrs_string: dict[str, str] = {}
    attrs_number: dict[str, float] = {}
    attrs_bool: dict[str, int] = {}
    overflow: dict[str, Any] = {}
    for key, value in readable.attributes.items():
        if key.startswith(_OVERFLOW_PREFIXES):
            overflow[key] = value
        elif isinstance(value, bool):
            attrs_bool[key] = 1 if value else 0
        elif isinstance(value, str):
            attrs_string[key] = value
        elif isinstance(value, (int, float)):
            attrs_number[key] = float(value)
        else:
            overflow[key] = value

    raw_kind = _first_string(attrs_string, _SPAN_KIND_KEYS)
    if not raw_kind:
        raw_kind = attrs_string.get("gen_ai.operation.name", "")
    observation_type = raw_kind.strip().lower()
    observation_type = _SPAN_KIND_SYNONYMS.get(observation_type, observation_type)
    if observation_type not in _KNOWN_OBSERVATION_TYPES:
        observation_type = "unknown"

    start = _ch_datetime(readable.start_time)
    end = _ch_datetime(readable.end_time) if readable.end_time else None
    latency_ms = 0
    if end is not None:
        latency_ms = max(0, int((end - start) / timedelta(milliseconds=1)))

    def _overflow_text(key: str) -> str:
        value = overflow.get(key)
        if value is None:
            return ""
        return value if isinstance(value, str) else json.dumps(value)

    status_code = readable.status.status_code
    parent = format(readable.parent.span_id, "016x") if readable.parent else ""
    return {
        "project_id": project_id,
        "id": format(readable.context.span_id, "016x"),
        "trace_id": str(uuid.UUID(int=readable.context.trace_id)),
        "parent_span_id": parent,
        "name": readable.name,
        "observation_type": observation_type,
        "start_time": start,
        "end_time": end,
        "input": _overflow_text("input.value"),
        "output": _overflow_text("output.value"),
        "model": None,
        "latency_ms": latency_ms,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cost": None,
        "status": {StatusCode.OK: "OK", StatusCode.ERROR: "ERROR"}.get(
            status_code, "UNSET"
        ),
        "status_message": "",
        "tags": "[]",
        "span_events": "[]",
        "provider": _first_string(attrs_string, _PROVIDER_KEYS),
        # attributes_extra is a String column holding the overflow object as
        # Go's json.Marshal writes it: sorted keys, no spaces.
        "span_attributes": json.dumps(overflow, sort_keys=True, separators=(",", ":")),
        "project_version_id": None,
        "custom_eval_config_id": None,
        "trace_session_id": None,
        "metadata_json": "{}",
        "attrs_string": attrs_string,
        "attrs_number": attrs_number,
        "attrs_bool": attrs_bool,
    }


# ---------------------------------------------------------------------------
# One run of today's path, shared by the tests below.
# ---------------------------------------------------------------------------


def _baseline_project():
    # An unsaved model instance: ``_create_observation_span`` assigns the
    # project to Django foreign keys, which refuse anything but a Project.
    from tracer.models.project import Project

    return Project(
        id=PROJECT_ID,
        name=PROJECT_NAME,
        trace_type="observe",
        organization_id=ORGANIZATION_ID,
        workspace_id=WORKSPACE_ID,
    )


def _run_top_span(patch: pytest.MonkeyPatch, call: dict) -> tuple[dict, Any]:
    """The exported dict and the OTLP span today's export builds from it."""
    from opentelemetry.sdk.trace.export import SpanExportResult

    import simulate.services.sim_collector_emit as sim_emit
    import tracer.selectors
    import tracer.services.collector_ingest as collector_ingest
    import tracer.services.observability_providers as providers
    import tracer.utils.vapi as vapi
    from tracer.utils import observability_provider as op
    from tracer.utils.vapi_recording import VapiRecordingService

    seen: dict[str, Any] = {
        "key_lookups": [],
        "credentials": [],
        "exports": [],
        "exporters": [],
        "usage": [],
    }

    def _no_key(project_id, provider):
        seen["key_lookups"].append((str(project_id), str(provider)))
        return None

    def _leave_url(*, audio_url, **_kwargs):
        return audio_url, 0

    def _system_key(organization_id, workspace_id):
        seen["credentials"].append((organization_id, workspace_id))
        return SYSTEM_KEY_PAIR

    real_export = sim_emit.export_sim_spans

    def _keep_dict(spans, **kwargs):
        seen["exports"].append({"spans": copy.deepcopy(spans), **kwargs})
        return real_export(spans, **kwargs)

    class _KeepingExporter:
        # Stands in for the gRPC exporter only, so everything between the
        # exported dict and the wire runs as in production.
        def __init__(self, **kwargs):
            self.kwargs, self.exported = kwargs, []
            seen["exporters"].append(self)

        def export(self, spans):
            self.exported.extend(spans)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            # Called whatever the export did; nothing stored depends on it.
            pass

    patch.setattr(tracer.selectors, "get_agent_api_key", _no_key)
    patch.setattr(vapi, "convert_audio_url_to_s3_sync", _leave_url)
    patch.setattr(
        VapiRecordingService,
        "fetch_and_parse_call_logs",
        classmethod(lambda cls, **_kwargs: None),
    )
    # The real lookup reads or creates the organisation's system key in Postgres.
    patch.setattr(collector_ingest, "resolve_system_emit_credentials", _system_key)
    patch.setattr(sim_emit, "export_sim_spans", _keep_dict)
    patch.setattr(sim_emit, "OTLPSpanExporter", _KeepingExporter)
    patch.setattr(
        op, "emit_span_ingestion_usage", lambda **kw: seen["usage"].append(kw)
    )
    patch.setattr(providers, "uuid", _SequentialUuid())

    project = _baseline_project()
    provider = SimpleNamespace(provider="vapi", id=PROVIDER_ROW_ID, project=project)
    # The real poll resolves the agent's key; the baseline has none, so the
    # normaliser takes its unauthenticated branch, which the stubs above
    # make harmless.
    outcome = op.process_and_store_logs([call], provider, api_key=None)

    assert outcome == op.StoreOutcome(stored=1, malformed=0, export_failed=0), outcome
    assert seen["key_lookups"] == [(str(PROJECT_ID), "vapi")]
    assert seen["usage"] == []
    assert seen["credentials"] == [(str(ORGANIZATION_ID), str(WORKSPACE_ID))]
    [export] = seen["exports"]
    assert export["project_name"] == PROJECT_NAME
    assert export["project_type"] == "observe"
    assert (export["api_key"], export["secret_key"]) == SYSTEM_KEY_PAIR
    assert export["service_name"] == "fi-provider"
    [exporter] = seen["exporters"]
    assert exporter.kwargs["headers"] == (
        ("x-api-key", SYSTEM_KEY_PAIR[0]),
        ("x-secret-key", SYSTEM_KEY_PAIR[1]),
    )
    [span] = export["spans"]
    [readable] = exporter.exported
    # The list's root identity carries this; the keyword above stops short of it.
    assert readable.resource.attributes["service.name"] == "fi-provider"
    return span, readable


def _run_detail(patch: pytest.MonkeyPatch, row: dict, *, start_us: int) -> dict:
    import tracer.services.observability_providers as providers
    import tracer.views.trace as trace_views
    from tracer.serializers.trace import TraceVoiceCallDetailResponseSerializer
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.v2.trace_detail_reads import (
        PhysicalSpanIdentity,
        TraceDetailReadBuilder,
        read_trace_detail,
    )

    builder = TraceDetailReadBuilder(
        project_ids=[str(PROJECT_ID)], trace_id=row["trace_id"]
    )
    identity_query, identity_params = builder.build_identity_query()
    content_query, _params = builder.build_content_query(
        [
            PhysicalSpanIdentity(
                project_id=row["project_id"],
                trace_id=row["trace_id"],
                span_id=row["id"],
                start_time=row["start_time"],
            )
        ]
    )
    identity_row = {
        "project_id": row["project_id"],
        "trace_id": row["trace_id"],
        "span_id": row["id"],
        "start_time": row["start_time"],
        "latest_is_deleted": 0,
        "latest_version": ROW_VERSION,
    }
    answers = {
        identity_query: ("detail identity row", identity_row),
        content_query: ("detail row", row),
    }
    issued: list[str] = []

    class _Analytics:
        # Answers by statement, so a reordered or extra read fails here
        # instead of being handed the wrong row.
        def execute_ch_query(self, query, params, *, timeout_ms, settings):
            assert query in answers, (
                "the detail read issued a statement this baseline does not "
                f"answer, projecting {sorted(_select_names(query))}"
            )
            name, answer = answers[query]
            issued.append(name)
            if name == "detail identity row":
                assert params == identity_params, params
            else:
                # The identity rows must reach the content read unchanged;
                # the content text is the same whatever identities it binds.
                assert params["detail_span_identities"] == (
                    (row["project_id"], row["trace_id"], row["id"], start_us),
                ), params["detail_span_identities"]
            _assert_columns(name, answer, _select_names(query))
            return QueryResult(
                data=[answer], row_count=1, backend_used="clickhouse", query_time_ms=0.0
            )

    # As the endpoint calls it; the project has no eval configs, so the eval
    # statement is never issued, and the endpoint never reads annotations.
    detail = read_trace_detail(
        analytics=_Analytics(),
        project_ids=[str(PROJECT_ID)],
        trace_id=row["trace_id"],
        eval_config_ids_resolver=lambda _project_id: [],
        include_annotations=False,
        deadline_ms=6000,
    )
    assert issued == ["detail identity row", "detail row"], issued

    lookups: list[dict] = []

    def _no_simulation(**kwargs):
        lookups.append(kwargs)
        return {}

    # Without a simulator id the helper still looks the provider call id up
    # in Postgres; the baseline is a production call, so it answers nothing.
    patch.setattr(trace_views, "_simulation_context_for_voice_call", _no_simulation)
    patch.setattr(providers, "uuid", _SequentialUuid())

    organization = SimpleNamespace(id=ORGANIZATION_ID, pk=ORGANIZATION_ID)
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(pk="baseline-user", organization=organization),
        query_params={},
    )
    response = trace_views.TraceView()._voice_call_detail_clickhouse(
        request, row["trace_id"], detail, []
    )
    assert response.status_code == 200, response.data
    assert len(lookups) == 1
    assert lookups[0]["organization_id"] == ORGANIZATION_ID

    # The whole envelope, as the endpoint's strict response check runs it.
    TraceVoiceCallDetailResponseSerializer(data=response.data).is_valid(
        raise_exception=True
    )
    return response.data["result"]


def _run_list_row(patch: pytest.MonkeyPatch, row: dict, *, service_name: str) -> dict:
    import tracer.services.observability_providers as providers
    import tracer.views.trace as trace_views
    from tracer.selectors import trace_filter_reads
    from tracer.selectors.trace_filter_reads import BoundedFilterPage
    from tracer.services.clickhouse.query_service import QueryResult
    from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
        VoiceCallListQueryBuilderV2,
    )

    start = row["start_time"]
    identity = {
        "project_id": row["project_id"],
        "trace_id": row["trace_id"],
        "root_span_id": row["id"],
        "start_time": start,
        "_root_observation_type": row["observation_type"],
        "_root_service_name": service_name,
        "_root_start_hour": start.replace(minute=0, second=0, microsecond=0),
        "_root_version": ROW_VERSION,
    }
    # The bounded root page read is replaced whole (its statement is built
    # deep inside the shared pager), so only the content hydration statement
    # is the reader's own; it answers the identity and the content columns
    # ``_VoiceTraceReplayBuilder`` projects (call_logs filtered out).
    page_row = {**identity, "end_time": row["end_time"], "provider": row["provider"]}
    content_overflow = {
        k: v for k, v in json.loads(row["span_attributes"]).items() if k != "call_logs"
    }
    content_row = {
        **identity,
        "span_id": row["id"],
        "provider": row["provider"],
        "span_attributes": json.dumps(
            content_overflow, sort_keys=True, separators=(",", ":")
        ),
        "attrs_string": {
            k: v for k, v in row["attrs_string"].items() if k != "call_logs"
        },
        "attrs_number": dict(row["attrs_number"]),
        "attrs_bool": dict(row["attrs_bool"]),
    }
    page = BoundedFilterPage(
        rows=[page_row],
        has_more=False,
        complete=True,
        status="complete",
        error_code=None,
        total_rows_lower_bound=1,
        elapsed_ms=0.0,
        query_count=1,
        rows_returned=1,
        result_payload_bytes=0,
        attempts=(),
    )
    analytics = mock.MagicMock()
    analytics.execute_ch_query.return_value = QueryResult(
        data=[content_row],
        row_count=1,
        backend_used="clickhouse",
        query_time_ms=0.0,
    )

    patch.setattr(trace_views, "get_project_eval_configs", lambda _pid: ([], []))
    patch.setattr(trace_views, "get_annotation_labels_for_project", lambda _pid: [])
    patch.setattr(trace_views, "_build_annotation_map_from_scores", lambda *a, **kw: {})
    builders: list[Any] = []

    def _one_page(**kwargs):
        builders.append(kwargs["builder"])
        return page

    patch.setattr(trace_filter_reads, "read_bounded_filter_page", _one_page)
    patch.setattr(providers, "uuid", _SequentialUuid())

    organization = SimpleNamespace(id=ORGANIZATION_ID, pk=ORGANIZATION_ID)
    request = SimpleNamespace(
        organization=organization,
        user=SimpleNamespace(pk="baseline-user", organization=organization),
        query_params={},
    )
    window = [
        (start - timedelta(hours=1)).isoformat(),
        (start + timedelta(hours=1)).isoformat(),
    ]
    response = trace_views.TraceView()._list_voice_calls_clickhouse(
        request,
        project_id=str(PROJECT_ID),
        validated_data={
            "filters": [
                {
                    "column_id": "created_at",
                    "filter_config": {
                        "filter_type": "datetime",
                        "filter_op": "between",
                        "filter_value": window,
                    },
                }
            ],
            "page": 1,
            "page_size": 30,
        },
        remove_simulation_calls=False,
        analytics=analytics,
    )
    assert response.status_code == 200, response.data
    assert analytics.execute_ch_query.call_count == 1
    # The content statement the list really issued, from its own builder.
    content_query, content_params = analytics.execute_ch_query.call_args.args[:2]
    content_names = _select_names(content_query)
    _assert_columns("list content row", content_row, content_names)
    # The page row's identity must come back as the physical key the content
    # read binds, in the column order of its IN clause.
    page_key = (
        identity["project_id"],
        identity["_root_observation_type"],
        identity["_root_service_name"],
        identity["_root_start_hour"].replace(tzinfo=None),
        identity["trace_id"],
        identity["root_span_id"],
    )
    assert content_params["content_physical_keys"] == (page_key,), content_params[
        "content_physical_keys"
    ]
    [builder] = builders
    assert isinstance(builder, VoiceCallListQueryBuilderV2), type(builder)
    replay = builder._bounded_delegate()
    content_only = {alias for _, alias in replay._root_replay_content_fields()} | {
        _select_names(f"SELECT {item} FROM t").pop()
        for item in replay._root_replay_content_extra_select()
    }
    # Not the page statement's identity, which the replaced page read never
    # issues: the identity the content statement carries beside the content.
    _assert_columns(
        "list identity in the content statement",
        identity,
        content_names - content_only,
    )
    [entry] = response.data["results"]
    return entry


@pytest.fixture(scope="module")
def baseline():
    from django.db.backends.base.base import BaseDatabaseWrapper

    opened: list[str] = []

    def _refuse(self, *args, **kwargs):
        opened.append(self.alias)
        raise RuntimeError("the voice baseline must not open a database connection")

    call = _load("vapi_call.json")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(BaseDatabaseWrapper, "ensure_connection", _refuse)
        # The normaliser keeps the call object as raw_log and the transcript
        # step rewrites its messages in place; hand it a copy.
        top_span, readable = _run_top_span(patch, copy.deepcopy(call))
        row = _collector_row(readable, project_id=str(PROJECT_ID))
        # The collector keeps the OTLP nanoseconds to the microsecond.
        detail = _run_detail(
            patch, copy.deepcopy(row), start_us=readable.start_time // 1000
        )
        list_row = _run_list_row(
            patch,
            copy.deepcopy(row),
            service_name=readable.resource.attributes["service.name"],
        )
    assert opened == [], f"database connections attempted: {opened}"
    return SimpleNamespace(
        call=call, top_span=top_span, row=row, detail=detail, list_row=list_row
    )


def test_top_span_matches_recording(baseline):
    _check_against_file("vapi_top_span.json", baseline.top_span)


def test_call_detail_matches_recording(baseline):
    _check_against_file("vapi_detail.json", baseline.detail)


def test_voice_list_row_matches_recording(baseline):
    _check_against_file("vapi_list_row.json", baseline.list_row)


def test_the_facts_the_readers_rely_on(baseline):
    span = baseline.top_span
    attributes = span["attributes"]
    assert span["parent_span_id"] is None
    assert span["name"] == "Vapi Call Log"
    assert attributes["gen_ai.span.kind"] == "CONVERSATION"
    assert attributes["gen_ai.system"] == "vapi"
    assert isinstance(attributes["raw_log"], str)
    assert json.loads(attributes["raw_log"])["id"] == baseline.call["id"]

    assert baseline.row["observation_type"] == "conversation"
    assert baseline.row["parent_span_id"] == ""

    root = baseline.detail["observation_span"][0]
    assert root["observation_type"] == "conversation"
    assert root["id"] == span["span_id"]
    assert baseline.detail["provider_call_id"] == baseline.call["id"]
    assert baseline.list_row["observation_span"] == [
        {
            "id": span["span_id"],
            "observation_type": "conversation",
            "parent_span_id": None,
        }
    ]
