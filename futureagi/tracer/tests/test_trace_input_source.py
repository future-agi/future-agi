"""Root-input text is read from the normalised column, not one convention's key.

`get_trace_input_data` used to read `attrs_string["input.value"]` — the
OpenInference key. Measured across production: zero projects emit it, while the
collector's normalised `input` column was populated for 25 of 34 active
projects. So this returned an empty list everywhere, no root-input embedding was
ever written, and success-trace KNN silently produced nothing on every project.

It failed quietly, which is why it survived: the caller logs
`no_root_inputs_found` at INFO and returns 0. Nothing errors.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tracer.models.trace_scan import TraceScanResult, TraceScanStatus
from tracer.queries.scan_clustering import get_trace_input_data

TRACE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _root(**kwargs):
    """A parentless CHSpan-shaped row."""
    base = {
        "trace_id": TRACE_ID,
        "parent_span_id": "",
        "input": "",
        "attrs_string": {},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _scanned(project):
    TraceScanResult.objects.create(
        trace_id=TRACE_ID,
        project_id=project.id,
        status=TraceScanStatus.COMPLETED,
        has_issues=True,
    )


def _run(project, spans):
    reader = MagicMock()
    reader.__enter__ = MagicMock(return_value=reader)
    reader.__exit__ = MagicMock(return_value=False)
    reader.roots_by_trace_ids.return_value = spans
    with patch("tracer.queries.scan_clustering.get_reader", return_value=reader):
        return get_trace_input_data([TRACE_ID], str(project.id))


@pytest.mark.django_db
class TestRootInputComesFromTheNormalisedColumn:
    def test_column_only_root_is_read(self, project):
        """The production shape: collector populated the column, and the
        OpenInference attribute is absent. This found nothing before."""
        _scanned(project)
        out = _run(project, [_root(input="what is my refund status")])

        assert len(out) == 1, "a root with a populated input column was skipped"
        assert out[0].input_text == "what is my refund status"

    def test_aliased_attribute_still_works_when_the_column_is_empty(self, project):
        """Rows written before the column was extracted must keep working."""
        _scanned(project)
        out = _run(project, [_root(attrs_string={"input.value": "legacy text"})])

        assert len(out) == 1
        assert out[0].input_text == "legacy text"

    def test_a_root_with_neither_contributes_nothing(self, project):
        _scanned(project)
        assert _run(project, [_root()]) == []

    def test_non_root_spans_are_ignored(self, project):
        """Only the parentless span carries the trace's input."""
        _scanned(project)
        child = _root(parent_span_id="abc123", input="a child's input")
        assert _run(project, [child]) == []

    def test_unscanned_traces_are_excluded(self, project):
        """No TraceScanResult means has_issues is unknown — such a trace must
        not be embedded as a success-trace comparison point."""
        out = _run(project, [_root(input="never scanned")])
        assert out == []
