"""TH-5642: the CDC-off table seams (eval_logger_source / end_user_source /
score_source) and that the eval-read query builders actually route through them.

These are revert-failing: hardcoding the legacy table/predicate back into a
builder (the pre-seam state) makes the v2 assertions fail.
"""

import pytest
from django.test import override_settings

from tracer.services.clickhouse.eval_logger_table import (
    end_user_source,
    eval_logger_source,
    score_source,
)


class TestEvalLoggerSourceSeam:
    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger")
    def test_legacy_table_and_peerdb_predicate(self):
        table, pred = eval_logger_source()
        assert table == "tracer_eval_logger"
        assert "_peerdb_is_deleted = 0" in pred
        assert "is_deleted = 0" not in pred.replace("_peerdb_is_deleted = 0", "")

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_v2_table_drops_peerdb_columns(self):
        table, pred = eval_logger_source()
        assert table == "tracer_eval_logger_v2"
        assert pred == "is_deleted = 0"
        assert "_peerdb" not in pred

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_alias_prefixes_predicate(self):
        _, pred = eval_logger_source("e")
        assert pred == "e.is_deleted = 0"


class TestEndUserSourceSeam:
    @override_settings(CH25_END_USER_TABLE="tracer_enduser")
    def test_legacy(self):
        table, id_col, pred = end_user_source()
        assert (table, id_col) == ("tracer_enduser", "id")
        assert "_peerdb_is_deleted = 0" in pred

    @override_settings(CH25_END_USER_TABLE="end_users")
    def test_v2(self):
        table, id_col, pred = end_user_source()
        assert (table, id_col, pred) == ("end_users", "end_user_id", "is_deleted = 0")


class TestScoreSourceSeam:
    @override_settings(CH25_SCORE_TABLE="model_hub_score")
    def test_legacy_default(self):
        assert score_source() == "model_hub_score"

    @override_settings(CH25_SCORE_TABLE="model_hub_score_v2")
    def test_v2(self):
        assert score_source() == "model_hub_score_v2"


@pytest.mark.django_db
class TestBuildEvalQueryRoutesThroughSeam:
    """The list builders' build_eval_query must emit the seam-selected table —
    not the hardcoded legacy table the pre-seam builders used."""

    def _builder(self, cls):
        b = cls(project_id="11111111-1111-4111-8111-111111111111")
        # build_eval_query short-circuits to "" without configured evals.
        b.eval_config_ids = ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]
        return b

    @override_settings(CH25_EVAL_LOGGER_TABLE="tracer_eval_logger_v2")
    def test_v2_table_and_no_peerdb_column(self):
        from tracer.services.clickhouse.query_builders.span_list import (
            SpanListQueryBuilder,
        )
        from tracer.services.clickhouse.query_builders.trace_list import (
            TraceListQueryBuilder,
        )
        from tracer.services.clickhouse.query_builders.voice_call_list import (
            VoiceCallListQueryBuilder,
        )

        for cls in (
            SpanListQueryBuilder,
            TraceListQueryBuilder,
            VoiceCallListQueryBuilder,
        ):
            query, _ = self._builder(cls).build_eval_query(["t1"])
            assert "tracer_eval_logger_v2" in query, f"{cls.__name__} not routed to v2"
            assert "_peerdb_is_deleted" not in query, (
                f"{cls.__name__} still emits the legacy peerdb column"
            )
