"""The published list-page contract must state exactness, not only completeness.

Every Observe list page said whether its read finished (``query_complete``)
and none of them said whether the published rows were the exact requested
prefix: ``query_exact`` was absent from every successful page on all four list
endpoints.  These tests pin both halves — the value rule in
``list_page_exactness`` and the rule that no list read path may publish one
half without the other.
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from tracer.services.clickhouse.list_page_contract import list_page_exactness

pytestmark = pytest.mark.unit

# The six ClickHouse read paths behind the four Observe list endpoints.
LIST_READ_PATHS = {
    "tracer/views/trace.py": (
        "_list_traces_of_session_clickhouse",  # Trace list, User-detail Traces
        "_list_voice_calls_clickhouse",  # Voice list
        "_list_traces_clickhouse",  # prototype trace list
    ),
    "tracer/views/observation_span.py": (
        "_list_spans_clickhouse",  # Span list
        "_list_spans_non_observe_clickhouse",  # prototype span list
    ),
    "tracer/views/trace_session.py": (
        "_list_sessions_clickhouse",  # Session list, User-detail Sessions
    ),
}


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _swagger() -> dict:
    path = _backend_root().parent / "api_contracts" / "openapi" / "swagger.json"
    with path.open() as handle:
        return json.load(handle)


def _published_page_dicts(module_path: str, function_name: str) -> list[ast.Dict]:
    """Every dict literal inside ``function_name`` that publishes completeness."""

    tree = ast.parse((_backend_root() / module_path).read_text())
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    return [
        node
        for node in ast.walk(target)
        if isinstance(node, ast.Dict)
        and any(
            isinstance(key, ast.Constant) and key.value == "query_complete"
            for key in node.keys
        )
    ]


def _publishes_exactness(node: ast.Dict) -> bool:
    return any(
        key is None
        and isinstance(value, ast.Call)
        and getattr(value.func, "id", None) == "list_page_exactness"
        for key, value in zip(node.keys, node.values, strict=True)
    )


def test_finished_read_publishes_an_exact_page():
    assert list_page_exactness(complete=True) == {
        "query_exact": True,
        "ordering_exact": True,
    }


def test_unfinished_read_never_claims_exactness():
    assert list_page_exactness(complete=False) == {
        "query_exact": False,
        "ordering_exact": False,
    }


def test_approximate_ordering_source_is_inexact_however_complete_the_read():
    assert list_page_exactness(complete=True, ordering_source_exact=False) == {
        "query_exact": False,
        "ordering_exact": False,
    }


@pytest.mark.parametrize(
    ("module_path", "function_name"),
    [
        (module_path, function_name)
        for module_path, names in LIST_READ_PATHS.items()
        for function_name in names
    ],
)
def test_every_list_page_publishing_completeness_also_publishes_exactness(
    module_path, function_name
):
    published = _published_page_dicts(module_path, function_name)
    assert published, f"{function_name} publishes no list page"
    assert all(_publishes_exactness(node) for node in published), (
        f"{function_name} publishes query_complete without list_page_exactness"
    )


def test_voice_list_counts_the_statements_its_page_cost():
    from tracer.views.trace import TraceView

    source = inspect.getsource(TraceView._list_voice_calls_clickhouse)
    assert '"query_count"' in source, (
        "the voice list must publish its ClickHouse statement count like the "
        "trace and span lists do"
    )


@pytest.mark.parametrize(
    "definition",
    ["TraceObserveListMetadata", "TraceSessionListMetadata", "SpanListMetadata"],
)
def test_documented_list_metadata_declares_exactness(definition):
    properties = _swagger()["definitions"][definition]["properties"]
    assert properties["query_exact"]["type"] == "boolean"
    assert properties["ordering_exact"]["type"] == "boolean"


def test_documented_voice_list_envelope_declares_exactness_and_statement_count():
    definition = _swagger()["definitions"]["TraceVoiceCallListResponse"]
    properties = definition["properties"]
    assert properties["query_exact"]["type"] == "boolean"
    assert properties["ordering_exact"]["type"] == "boolean"
    assert properties["query_count"]["type"] == "integer"


def test_documented_voice_list_additions_do_not_become_required():
    """A client built from this branch must still read a pre-deploy backend.

    The three fields are additive on an envelope that already ships, so making
    them required would turn the generated zod contract non-optional and make
    ``.parse()`` throw on any voice list served before this change deploys.
    """

    definition = _swagger()["definitions"]["TraceVoiceCallListResponse"]
    assert not {"query_exact", "ordering_exact", "query_count"} & set(
        definition.get("required", ())
    )
