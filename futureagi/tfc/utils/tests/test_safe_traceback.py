import json

from tfc.utils.safe_traceback import sanitized_exception_traceback


def test_suppressed_context_and_exception_notes_are_not_exposed():
    try:
        try:
            raise ValueError("synthetic-private-inner")
        except ValueError:
            error = RuntimeError("synthetic-private-outer")
            error.add_note("synthetic-private-note")
            raise error from None
    except RuntimeError as exc:
        trace = sanitized_exception_traceback(exc)
    assert len(trace) == 1
    assert trace[0]["chained_by"] is None
    assert (
        trace[0]["frames"][-1]["function"]
        == "test_suppressed_context_and_exception_notes_are_not_exposed"
    )
    assert "synthetic-private" not in json.dumps(trace)


def test_implicit_context_is_retained():
    try:
        try:
            raise ValueError("synthetic-private-inner")
        except ValueError:
            # Reproduce implicit chaining in a third-party dependency.
            raise RuntimeError("synthetic-private-outer")  # noqa: B904
    except RuntimeError as exc:
        trace = sanitized_exception_traceback(exc)
    assert [entry["error_type"] for entry in trace] == ["RuntimeError", "ValueError"]
    assert trace[0]["chained_by"] == "context"
    assert "synthetic-private" not in json.dumps(trace)


def test_cyclic_and_long_exception_chains_are_bounded():
    first = RuntimeError("synthetic-private")
    first.__cause__ = first
    assert len(sanitized_exception_traceback(first)) == 1
    last = first
    for _ in range(20):
        error = ValueError("synthetic-private")
        error.__cause__ = last
        last = error
    assert len(sanitized_exception_traceback(last)) == 8


def test_deep_trace_retains_the_origin_with_a_bounded_frame_count():
    def recurse(depth):
        if depth:
            recurse(depth - 1)
        else:
            raise RuntimeError("synthetic-private")

    try:
        recurse(60)
    except RuntimeError as exc:
        trace = sanitized_exception_traceback(exc)
    assert len(trace[0]["frames"]) == 32
    assert trace[0]["frames"][-1]["function"] == "recurse"
    assert "synthetic-private" not in json.dumps(trace)
