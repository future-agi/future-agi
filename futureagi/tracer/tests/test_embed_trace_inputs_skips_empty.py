"""One unembeddable trace must not take its whole batch down.

`embed_trace_inputs` passed every kevinified text straight to the embedder,
which raises on an empty string. A single trace whose input reduced to nothing
therefore lost the embedding for every OTHER trace in the call — silently, since
the caller only ever sees a count. Observed on a real backfill: three chunks of
200 traces each returned zero because of one bad row apiece.

`kevinified_text` also returns None when the compression module is absent, so
the same guard covers that.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.utils import trace_scanner


def _inp(trace_id, text):
    """A TraceInputData stand-in — kevinified_text is a property on the real one."""
    return SimpleNamespace(
        trace_id=trace_id,
        project_id="p1",
        input_text=text or "",
        has_issues=False,
        kevinified_text=text,
    )


def _run(inputs):
    """Returns (stored, texts_passed_to_embedder, inputs_passed_to_store)."""
    seen = {}

    def fake_embed(texts):
        if any(t is None or not str(t).strip() for t in texts):
            raise ValueError("Text input cannot be empty")
        seen["texts"] = texts
        return [[0.1] * 8 for _ in texts]

    def fake_store(inps, embeddings):
        seen["stored_inputs"] = inps
        assert len(inps) == len(embeddings), "inputs and embeddings misaligned"
        return len(inps)

    with patch.object(trace_scanner, "get_trace_input_data", return_value=inputs), \
         patch.object(trace_scanner, "embed_texts", side_effect=fake_embed), \
         patch.object(trace_scanner, "store_trace_input_embeddings", side_effect=fake_store):
        stored = trace_scanner.embed_trace_inputs(["t1", "t2", "t3"], "p1")
    return stored, seen.get("texts"), seen.get("stored_inputs")


class TestOneBadTraceDoesNotSinkTheBatch:
    def test_empty_text_is_skipped_and_the_rest_survive(self):
        stored, texts, inputs = _run(
            [_inp("t1", "refund status please"), _inp("t2", ""), _inp("t3", "where is my order")]
        )
        assert stored == 2, "the batch died on one empty trace"
        assert texts == ["refund status please", "where is my order"]
        assert [i.trace_id for i in inputs] == ["t1", "t3"]

    def test_whitespace_only_text_is_skipped(self):
        stored, texts, _ = _run([_inp("t1", "   \n  "), _inp("t2", "real question")])
        assert stored == 1
        assert texts == ["real question"]

    def test_none_text_is_skipped(self):
        """kevinified_text returns None when the compression module is absent."""
        stored, texts, _ = _run([_inp("t1", None), _inp("t2", "real question")])
        assert stored == 1
        assert texts == ["real question"]

    def test_all_empty_returns_zero_without_calling_the_embedder(self):
        stored, texts, _ = _run([_inp("t1", ""), _inp("t2", None)])
        assert stored == 0
        assert texts is None, "the embedder was called with nothing to embed"

    def test_no_inputs_at_all_returns_zero(self):
        stored, texts, _ = _run([])
        assert stored == 0
        assert texts is None


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_stored_inputs_stay_aligned_with_their_embeddings(bad):
    """The store call receives the filtered list, not the original — otherwise
    trace N's embedding gets written against trace N+1's id."""
    stored, _, inputs = _run([_inp("keep1", "a"), _inp("drop", bad), _inp("keep2", "b")])
    assert stored == 2
    assert [i.trace_id for i in inputs] == ["keep1", "keep2"]
