"""Tests for the annotation queue export helpers.

Focuses on the pure helpers that the export composition relies on —
``_flatten_score_value``, ``_resolve_recording_link``,
``_slugify_for_header``, and the source-type dispatch in
``_collect_evals_for_item`` / ``_collect_system_metrics_for_item``.
These are the functions where the review found correctness regressions
(falsy collapse, wrong recording field priority, slug collisions, N+1
prefetch bypass).

The HTTP-level shape (CSV/XLSX content type, header row, row count) is
covered by lighter smoke tests that go through ``auth_client``.
"""

from unittest.mock import MagicMock

import pytest

from model_hub.views.annotation_queues import AnnotationQueueViewSet


# ---------------------------------------------------------------------------
# _flatten_score_value
# ---------------------------------------------------------------------------


class TestFlattenScoreValue:
    flatten = staticmethod(AnnotationQueueViewSet._flatten_score_value)

    def test_none_returns_empty_string(self):
        assert self.flatten(None) == ""

    def test_plain_scalars_pass_through(self):
        assert self.flatten(42) == 42
        assert self.flatten(4.5) == 4.5
        assert self.flatten("yes") == "yes"
        assert self.flatten(True) is True
        assert self.flatten(False) is False

    def test_plain_list_joined_with_comma(self):
        assert self.flatten([1, 2, 3]) == "1, 2, 3"
        assert self.flatten(["a", "b"]) == "a, b"

    def test_unwraps_rating_wrapper(self):
        assert self.flatten({"rating": 3}) == 3
        # Falsy real values must NOT collapse to "".
        assert self.flatten({"rating": 0}) == 0

    def test_unwraps_value_wrapper(self):
        assert self.flatten({"value": "yes"}) == "yes"
        assert self.flatten({"value": False}) is False

    def test_unwraps_text_wrapper(self):
        assert self.flatten({"text": "free-form notes"}) == "free-form notes"
        assert self.flatten({"text": ""}) == ""

    def test_joins_selected_list_in_wrapper(self):
        assert self.flatten({"selected": ["a", "b", "c"]}) == "a, b, c"

    def test_wrapper_key_with_none_inner_falls_through(self):
        # rating present-but-None should not short-circuit — the helper
        # must keep looking so partial payloads still surface a value.
        assert self.flatten({"rating": None, "value": "ok"}) == "ok"
        assert self.flatten({"rating": None, "value": None, "text": "fb"}) == "fb"
        assert self.flatten({"rating": None, "selected": ["x"]}) == "x"

    def test_all_wrapper_inners_none_fall_through_to_json(self):
        # No usable inner anywhere → JSON dump of the original payload.
        assert self.flatten({"rating": None}) == '{"rating": null}'

    def test_nested_dict_inner_is_jsonified(self):
        # csv.writer / openpyxl would emit a Python repr otherwise.
        assert self.flatten({"value": {"x": 1}}) == '{"x": 1}'

    def test_unknown_shape_dumped_as_json(self):
        assert self.flatten({"foo": "bar"}) == '{"foo": "bar"}'

    def test_inner_list_in_wrapper_joined(self):
        assert self.flatten({"value": ["a", "b"]}) == "a, b"


# ---------------------------------------------------------------------------
# _resolve_recording_link
# ---------------------------------------------------------------------------


class TestResolveRecordingLink:
    @staticmethod
    def _item(**call_attrs):
        ce = MagicMock()
        for k, v in call_attrs.items():
            setattr(ce, k, v)
        item = MagicMock()
        item.call_execution = ce
        return item

    def test_returns_empty_when_no_call_execution(self):
        item = MagicMock()
        item.call_execution = None
        assert AnnotationQueueViewSet._resolve_recording_link(item) == ""

    def test_prefers_top_level_recording_url(self):
        item = self._item(
            recording_url="https://s3.example.com/rec.mp3",
            stereo_recording_url="https://s3.example.com/stereo.mp3",
            provider_call_data={
                "vapi": {"recording_url": "https://vapi.example.com/x.mp3"}
            },
        )
        assert (
            AnnotationQueueViewSet._resolve_recording_link(item)
            == "https://s3.example.com/rec.mp3"
        )

    def test_falls_back_to_stereo(self):
        item = self._item(
            recording_url="",
            stereo_recording_url="https://s3.example.com/stereo.mp3",
            provider_call_data={},
        )
        assert (
            AnnotationQueueViewSet._resolve_recording_link(item)
            == "https://s3.example.com/stereo.mp3"
        )

    def test_falls_back_to_vapi_artifact_camelcase(self):
        item = self._item(
            recording_url=None,
            stereo_recording_url=None,
            provider_call_data={
                "vapi": {"artifact": {"recordingUrl": "https://v.example.com/r.mp3"}}
            },
        )
        assert (
            AnnotationQueueViewSet._resolve_recording_link(item)
            == "https://v.example.com/r.mp3"
        )

    def test_falls_back_to_livekit_recording_url(self):
        item = self._item(
            recording_url=None,
            stereo_recording_url=None,
            provider_call_data={
                "livekit": {"recording_url": "https://lk.example.com/r.mp3"}
            },
        )
        assert (
            AnnotationQueueViewSet._resolve_recording_link(item)
            == "https://lk.example.com/r.mp3"
        )

    def test_returns_empty_when_no_url_anywhere(self):
        item = self._item(
            recording_url=None,
            stereo_recording_url=None,
            provider_call_data={},
        )
        assert AnnotationQueueViewSet._resolve_recording_link(item) == ""

    def test_handles_non_dict_provider_call_data(self):
        item = self._item(
            recording_url=None,
            stereo_recording_url=None,
            provider_call_data="not-a-dict",
        )
        assert AnnotationQueueViewSet._resolve_recording_link(item) == ""


# ---------------------------------------------------------------------------
# _slugify_for_header
# ---------------------------------------------------------------------------


class TestSlugifyForHeader:
    slug = staticmethod(AnnotationQueueViewSet._slugify_for_header)

    def test_lowercases_and_keeps_simple_words(self):
        assert self.slug("Latency") == "latency"
        assert self.slug("OverallScore") == "overallscore"

    def test_collapses_special_chars_to_single_underscore(self):
        assert self.slug("Response Quality (5★)") == "response_quality_5"
        assert self.slug("foo!!!bar") == "foo_bar"

    def test_trims_leading_and_trailing_underscores(self):
        assert self.slug("__hello__") == "hello"
        assert self.slug("--world--") == "world"

    def test_empty_or_none_input_falls_back_to_unknown(self):
        assert self.slug("") == "unknown"
        assert self.slug(None) == "unknown"
        assert self.slug("!!!") == "unknown"


# ---------------------------------------------------------------------------
# _collect_system_metrics_for_item
# ---------------------------------------------------------------------------


class TestCollectSystemMetrics:
    def _item(self, source_type, source_obj):
        item = MagicMock()
        item.source_type = source_type
        # _collect_system_metrics_for_item reads via getattr(item, source_type).
        setattr(item, source_type, source_obj)
        return item

    def test_call_execution_pulls_declared_fields(self):
        ce = MagicMock(
            duration_seconds=42,
            message_count=10,
            overall_score=7.5,
            talk_ratio=0.62,
            avg_agent_latency_ms=350,
            user_wpm=120.0,
            bot_wpm=140.0,
            user_interruption_count=2,
            ai_interruption_count=1,
        )
        item = self._item("call_execution", ce)
        out = AnnotationQueueViewSet._collect_system_metrics_for_item(item)
        assert out["duration_seconds"] == 42
        assert out["overall_score"] == 7.5
        assert out["bot_wpm"] == 140.0

    def test_null_fields_become_empty_string(self):
        ce = MagicMock(
            duration_seconds=None,
            message_count=None,
            overall_score=None,
            talk_ratio=None,
            avg_agent_latency_ms=None,
            user_wpm=None,
            bot_wpm=None,
            user_interruption_count=None,
            ai_interruption_count=None,
        )
        item = self._item("call_execution", ce)
        out = AnnotationQueueViewSet._collect_system_metrics_for_item(item)
        assert out["duration_seconds"] == ""
        assert out["overall_score"] == ""

    def test_unmapped_source_type_returns_empty_dict(self):
        item = MagicMock()
        item.source_type = "trace"  # no entry in _SYSTEM_METRICS_BY_SOURCE
        assert AnnotationQueueViewSet._collect_system_metrics_for_item(item) == {}

    def test_missing_source_object_returns_empty_dict(self):
        item = MagicMock()
        item.source_type = "call_execution"
        item.call_execution = None
        assert AnnotationQueueViewSet._collect_system_metrics_for_item(item) == {}


# ---------------------------------------------------------------------------
# _collect_evals_for_item — source dispatch
# ---------------------------------------------------------------------------


class TestCollectEvals:
    def test_call_execution_flattens_eval_outputs_payload(self):
        ce = MagicMock()
        ce.eval_outputs = {
            "id-1": {"name": "helpfulness", "output": "Passed"},
            "id-2": {"name": "coherence", "output": 0.87},
            "id-3": {"name": "no-output", "output": None},
            "id-4": "not-a-dict",
        }
        item = MagicMock()
        item.source_type = "call_execution"
        item.call_execution = ce
        out = AnnotationQueueViewSet._collect_evals_for_item(item)
        assert out == {
            "helpfulness": "Passed",
            "coherence": 0.87,
            "no-output": "",
        }

    def test_call_execution_with_no_eval_outputs_returns_empty(self):
        ce = MagicMock()
        ce.eval_outputs = None
        item = MagicMock()
        item.source_type = "call_execution"
        item.call_execution = ce
        assert AnnotationQueueViewSet._collect_evals_for_item(item) == {}

    def test_dataset_row_reads_prefetched_eval_cells(self):
        col = MagicMock()
        col.name = "ground_truth_match"
        col.id = "col-uuid"
        cell = MagicMock(value="Pass", column=col)
        row = MagicMock()
        row.eval_cells = [cell]
        item = MagicMock()
        item.source_type = "dataset_row"
        item.dataset_row = row
        out = AnnotationQueueViewSet._collect_evals_for_item(item)
        assert out == {"ground_truth_match": "Pass"}

    def test_dataset_row_with_no_prefetch_returns_empty(self):
        row = MagicMock(spec=[])  # no eval_cells attribute attached
        item = MagicMock()
        item.source_type = "dataset_row"
        item.dataset_row = row
        assert AnnotationQueueViewSet._collect_evals_for_item(item) == {}

    def test_trace_reads_prefetched_active_eval_logs(self):
        cfg = MagicMock()
        cfg.name = "is_good_summary"
        log = MagicMock(
            output_float=None,
            output_bool=True,
            output_str="",
            output_str_list=[],
            custom_eval_config=cfg,
            eval_type_id=None,
        )
        trace = MagicMock()
        trace.active_eval_logs = [log]
        item = MagicMock()
        item.source_type = "trace"
        item.trace = trace
        out = AnnotationQueueViewSet._collect_evals_for_item(item)
        assert out == {"is_good_summary": True}

    def test_observation_span_reads_prefetched_active_eval_logs(self):
        log = MagicMock(
            output_float=0.42,
            output_bool=None,
            output_str="",
            output_str_list=[],
            custom_eval_config=None,
            eval_type_id="similarity",
        )
        span = MagicMock()
        span.active_eval_logs = [log]
        item = MagicMock()
        item.source_type = "observation_span"
        item.observation_span = span
        out = AnnotationQueueViewSet._collect_evals_for_item(item)
        assert out == {"similarity": 0.42}

    def test_unsupported_source_type_returns_empty(self):
        for src in ("trace_session", "prototype_run", "unknown"):
            item = MagicMock()
            item.source_type = src
            assert AnnotationQueueViewSet._collect_evals_for_item(item) == {}


# ---------------------------------------------------------------------------
# _eval_logger_value — output-field priority
# ---------------------------------------------------------------------------


class TestEvalLoggerValue:
    @staticmethod
    def _log(**kw):
        defaults = dict(
            output_float=None,
            output_bool=None,
            output_str="",
            output_str_list=[],
        )
        defaults.update(kw)
        return MagicMock(**defaults)

    def test_prefers_output_float(self):
        log = self._log(output_float=0.91, output_bool=True, output_str="ignored")
        assert AnnotationQueueViewSet._eval_logger_value(log) == 0.91

    def test_falls_back_to_bool(self):
        log = self._log(output_bool=False)
        # False is a valid value — must not collapse to "".
        assert AnnotationQueueViewSet._eval_logger_value(log) is False

    def test_falls_back_to_str(self):
        log = self._log(output_str="Passed")
        assert AnnotationQueueViewSet._eval_logger_value(log) == "Passed"

    def test_falls_back_to_str_list_joined(self):
        log = self._log(output_str_list=["a", "b"])
        assert AnnotationQueueViewSet._eval_logger_value(log) == "a, b"

    def test_returns_empty_when_nothing_set(self):
        log = self._log()
        assert AnnotationQueueViewSet._eval_logger_value(log) == ""
