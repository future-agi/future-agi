"""Unit tests for ``TraceDetailJSONRenderer``.

The renderer must give the stock ``JSONRenderer`` bytes for every payload, and
render span trees nested deeper than the interpreter recursion limit.

Deep payloads are compared only as sha256 and length. ``==``, ``json.loads``,
``copy.deepcopy`` and pytest's assertion repr all recurse once per nesting
level and would fail on these trees for reasons unrelated to the renderer.
"""

import datetime
import decimal
import hashlib
import json
import sys
import threading
import uuid
from unittest.mock import patch

import pytest
from rest_framework.renderers import JSONRenderer

from tracer.renderers import TraceDetailJSONRenderer

pytestmark = pytest.mark.unit

# The production and CI interpreter. On 3.11 the C JSON encoder counts nesting
# against sys.getrecursionlimit(), so the stock renderer fails on these depths
# and a raised limit lifts that. Later versions bound it by a separate C limit.
IS_PY311 = sys.version_info[:2] == (3, 11)

_SENTINEL = "__trace_detail_renderer_test_children_sentinel__"
_SENTINEL_JSON = json.dumps(_SENTINEL).encode()

# Every combination of the api_settings JSONRenderer reads at import time
# (COMPACT_JSON, UNICODE_JSON, STRICT_JSON) as its class attributes.
_VARIANTS = [
    {"compact": compact, "ensure_ascii": ensure_ascii, "strict": strict}
    for compact in (True, False)
    for ensure_ascii in (True, False)
    for strict in (True, False)
]


def _variant_pair(attrs):
    stock = type("StockVariant", (JSONRenderer,), dict(attrs))
    fixed = type("TraceDetailVariant", (TraceDetailJSONRenderer,), dict(attrs))
    return stock, fixed


def _digest(body):
    return len(body), hashlib.sha256(body).hexdigest()


def _outcome(render):
    try:
        return "ok", render()
    except Exception as exc:  # noqa: BLE001 - the exception class is the outcome
        return "error", type(exc).__name__


_AT = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


def _span(index):
    return {
        "id": f"span-{index}",
        "name": "step     café \U0001f600",
        "observation_type": "chain",
        "start_time": _AT + datetime.timedelta(microseconds=index),
        "end_time": datetime.date(2026, 1, 2),
        "cost": decimal.Decimal("0.125"),
        "eval_id": uuid.UUID(int=index),
        "latency_ms": index,
        "ratio": 1.5e-7,
        "big": 10**30,
        "ok": True,
        "status_message": None,
        "input": {"messages": [{"role": "user", "content": "hi"}], "": {}},
        "tags": ("a", "b"),
    }


def _entry(index, children):
    return {
        "observation_span": _span(index),
        "children": children,
        "eval_scores": [],
        "annotations": [{"label_id": None, "value": {}}],
        "eval_rollup": {"scope": "span", "evals": []},
    }


def _envelope(observation_spans):
    return {
        "status": True,
        "result": {
            "trace": {"id": "trace-1", "tags": [], "metadata": {}},
            "observation_spans": observation_spans,
            "summary": {
                "total_spans": 1,
                "total_cost": None,
                # None, int and float keys sit outside the span tree.
                "span_type_counts": {None: 1, "chain": 2, 3: 4, 1.5: 0, True: 0},
            },
            "graph": {"nodes": [{"id": "span-1"}], "edges": []},
        },
    }


def _chain(depth, *, payload_depth=0, payload_level=None):
    """A single parent-child chain ``depth`` entries long, built iteratively."""
    node = _entry(depth, [])
    for index in range(depth - 1, 0, -1):
        node = _entry(index, [node])
    if payload_depth:
        target = node
        for _ in range((payload_level or 1) - 1):
            target = target["children"][0]
        nested = {"leaf": "value"}
        for _ in range(payload_depth):
            nested = {"nested": nested}
        target["observation_span"]["input"] = nested
    return _envelope([node])


def _chain_oracle(data, renderer_class=JSONRenderer):
    """Expected bytes for a single-chain envelope, built level by level.

    Each entry is encoded by the stock renderer with its children list swapped
    for a sentinel string; the encoded text is split around that sentinel and
    the pieces are joined. No piece nests deeper than one entry.
    """
    renderer = renderer_class()

    def split(piece):
        body = renderer.render(piece)
        assert body.count(_SENTINEL_JSON) == 1
        prefix, suffix = body.split(_SENTINEL_JSON)
        return prefix, suffix

    result = data["result"]
    envelope_prefix, envelope_suffix = split(
        {**data, "result": {**result, "observation_spans": _SENTINEL}}
    )
    (entry,) = result["observation_spans"]
    prefixes = []
    suffixes = []
    while entry["children"]:
        prefix, suffix = split({**entry, "children": _SENTINEL})
        prefixes.append(prefix)
        suffixes.append(suffix)
        (entry,) = entry["children"]
    parts = [envelope_prefix]
    for prefix in prefixes:
        parts.extend((b"[", prefix))
    parts.extend((b"[", renderer.render(entry), b"]"))
    for suffix in reversed(suffixes):
        parts.extend((suffix, b"]"))
    parts.append(envelope_suffix)
    return b"".join(parts)


def _stock_render_unbounded(data):
    """Stock JSONRenderer bytes with a raised limit in a large-stack thread."""
    outcome = {}

    def run():
        previous = sys.getrecursionlimit()
        sys.setrecursionlimit(50_000)
        try:
            outcome["body"] = JSONRenderer().render(data)
        except Exception as exc:  # noqa: BLE001 - reported to the test thread
            outcome["error"] = repr(exc)
        finally:
            sys.setrecursionlimit(previous)

    previous_size = threading.stack_size(256 * 1024 * 1024)
    try:
        worker = threading.Thread(target=run)
        worker.start()
        worker.join()
    finally:
        threading.stack_size(previous_size)
    assert "error" not in outcome, outcome.get("error")
    return outcome["body"]


def _corpus():
    shared = {"shared": [1, 2]}
    return [
        {},
        [],
        "text  ",
        0,
        1.25,
        True,
        {"status": True, "result": "ok"},
        {"status": False, "result": {"message": "Trace details could not be loaded"}},
        _envelope([]),
        _envelope([_entry(1, [])]),
        _envelope(
            [_entry(1, [_entry(2, []), _entry(3, [_entry(4, [])])]), _entry(5, [])]
        ),
        _envelope([_entry(1, None)]),
        _envelope([{"observation_span": {}, "children": [shared, shared]}]),
        {"result": {"observation_spans": [{"children": ({"a": 1},)}]}},
        {"result": [1, " ", [[]], {}, {"children": [{"children": []}]}]},
        {"status": True, "result": {"observation_spans": "not-a-list"}},
    ]


@pytest.mark.parametrize(
    "accepted_media_type",
    [
        None,
        "application/json",
        "application/json; indent=4",
        "application/json; indent=0",
    ],
)
def test_stock_path_matches_json_renderer(accepted_media_type):
    for data in _corpus():
        expected = JSONRenderer().render(data, accepted_media_type)
        assert TraceDetailJSONRenderer().render(data, accepted_media_type) == expected


def test_renderer_keeps_json_renderer_identity():
    renderer = TraceDetailJSONRenderer()
    assert isinstance(renderer, JSONRenderer)
    assert renderer.media_type == JSONRenderer.media_type
    assert renderer.format == JSONRenderer.format
    assert renderer.charset == JSONRenderer.charset
    assert renderer.render(None) == b""


@pytest.mark.parametrize("attrs", _VARIANTS)
def test_forced_fallback_matches_json_renderer_for_every_setting(attrs):
    stock, fixed = _variant_pair(attrs)
    corpus = [*_corpus(), _chain(40)]
    expected = [_outcome(lambda data=data: stock().render(data)) for data in corpus]
    with patch.object(JSONRenderer, "render", side_effect=RecursionError):
        actual = [_outcome(lambda data=data: fixed().render(data)) for data in corpus]
    assert actual == expected


@pytest.mark.parametrize("attrs", _VARIANTS)
def test_forced_fallback_nan_follows_strict_setting(attrs):
    stock, fixed = _variant_pair(attrs)
    data = _envelope(
        [_entry(1, [{"observation_span": {"v": float("nan")}, "children": []}])]
    )
    expected = _outcome(lambda: stock().render(data))
    with patch.object(JSONRenderer, "render", side_effect=RecursionError):
        actual = _outcome(lambda: fixed().render(data))
    assert actual == expected
    assert expected[0] == ("error" if attrs["strict"] else "ok")


def test_forced_fallback_never_rerenders_indented_output():
    data = _envelope([_entry(1, [])])
    with patch.object(JSONRenderer, "render", side_effect=RecursionError):
        with pytest.raises(RecursionError):
            TraceDetailJSONRenderer().render(data, "application/json; indent=4")
        with pytest.raises(RecursionError):
            TraceDetailJSONRenderer().render(data, None, {"indent": 2})


def test_forced_fallback_declines_non_string_key_in_tree():
    data = _envelope([_entry(1, [])])
    data["result"]["observation_spans"][0][7] = "int key"
    assert TraceDetailJSONRenderer()._encode_without_recursion(data) is None
    with patch.object(JSONRenderer, "render", side_effect=RecursionError("original")):
        with pytest.raises(RecursionError, match="original"):
            TraceDetailJSONRenderer().render(data)


def test_forced_fallback_detects_circular_tree():
    entry = {"observation_span": {"id": "loop"}, "children": []}
    entry["children"].append(entry)
    with pytest.raises(ValueError, match="Circular reference detected"):
        TraceDetailJSONRenderer()._encode_without_recursion(_envelope([entry]))


def test_shared_non_circular_entry_is_rendered_twice():
    shared = _entry(2, [])
    data = _envelope([_entry(1, [shared, shared])])
    expected = JSONRenderer().render(data)
    with patch.object(JSONRenderer, "render", side_effect=RecursionError):
        assert TraceDetailJSONRenderer().render(data) == expected


@pytest.mark.parametrize("depth", [2, 3, 50])
def test_chain_oracle_matches_stock_renderer(depth):
    data = _chain(depth)
    assert _chain_oracle(data) == JSONRenderer().render(data)


@pytest.mark.parametrize("depth", [999, 1000, 5000])
def test_deep_chain_renders_oracle_bytes(depth):
    data = _chain(depth)
    body = TraceDetailJSONRenderer().render(data)
    assert _digest(body) == _digest(_chain_oracle(data))


@pytest.mark.parametrize("attrs", _VARIANTS)
def test_deep_chain_renders_oracle_bytes_for_every_setting(attrs):
    stock, fixed = _variant_pair(attrs)
    data = _chain(999)
    assert _digest(fixed().render(data)) == _digest(_chain_oracle(data, stock))


def test_deep_chain_with_deep_span_payload_renders_oracle_bytes():
    data = _chain(500, payload_depth=300, payload_level=500)
    body = TraceDetailJSONRenderer().render(data)
    assert _digest(body) == _digest(_chain_oracle(data))


def test_stock_renderer_cannot_render_deep_chain():
    """The bug: on 3.11 the stock renderer fails on a 999-deep chain."""
    outcome = _outcome(lambda: JSONRenderer().render(_chain(999)))
    if IS_PY311:
        assert outcome == ("error", "RecursionError")


def test_deep_chain_with_indent_is_not_rerendered():
    data = _chain(999)
    stock = _outcome(lambda: JSONRenderer().render(data, "application/json; indent=4"))
    fixed = _outcome(
        lambda: TraceDetailJSONRenderer().render(data, "application/json; indent=4")
    )
    assert fixed == stock
    if IS_PY311:
        assert fixed == ("error", "RecursionError")


@pytest.mark.parametrize("depth", [999, 5000])
def test_deep_chain_matches_unbounded_stock_renderer(depth):
    data = _chain(depth)
    body = TraceDetailJSONRenderer().render(data)
    # Only 3.11 lets a raised recursion limit lift the stock encoder's bound.
    expected = _stock_render_unbounded(data) if IS_PY311 else _chain_oracle(data)
    assert _digest(body) == _digest(expected)
