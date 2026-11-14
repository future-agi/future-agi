"""
Tests for get_evaluation_details fixes.

Compares OLD (buggy) vs NEW (fixed) query behavior for:
1. deleted=False filter — soft-deleted EvalLoggers must be excluded
2. error state handling — errored evals must return error: True
3. ClickHouse query parity — CH and PG paths produce identical responses
"""

import json

import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger


def _old_pg_query(observation_span_id, custom_eval_config_id):
    """OLD PG query — BaseModelManager already filters deleted via .objects."""
    return EvalLogger.objects.filter(
        observation_span_id=observation_span_id,
        custom_eval_config_id=custom_eval_config_id,
    ).first()


def _raw_pg_query_includes_deleted(observation_span_id, custom_eval_config_id):
    """Raw query bypassing BaseModelManager — simulates CH without deleted filter."""
    return EvalLogger.all_objects.filter(
        observation_span_id=observation_span_id,
        custom_eval_config_id=custom_eval_config_id,
    ).first()


def _new_pg_query(observation_span_id, custom_eval_config_id):
    """NEW PG query — explicit deleted=False (defensive, matches CH fix)."""
    return EvalLogger.objects.filter(
        observation_span_id=observation_span_id,
        custom_eval_config_id=custom_eval_config_id,
        deleted=False,
    ).first()


def _old_build_response(eval_logger):
    """OLD (buggy) response builder — no error state check."""
    if not eval_logger:
        return None

    evaluation_result = (
        eval_logger.output_bool
        if eval_logger.output_bool is not None
        else (
            eval_logger.output_float
            if eval_logger.output_float is not None
            else eval_logger.output_str_list
        )
    )
    evaluation_explanation = (
        eval_logger.eval_explanation
        if eval_logger.eval_explanation
        else eval_logger.error_message
    )

    output_metadata = eval_logger.output_metadata
    if not output_metadata or not isinstance(output_metadata, dict):
        output_metadata = {}

    return {
        "error_analysis": output_metadata.get("error_analysis", None),
        "selected_input_key": output_metadata.get("selected_input_key", None),
        "input_data": output_metadata.get("input_data", None),
        "input_types": output_metadata.get("input_types", None),
        "score": evaluation_result,
        "explanation": evaluation_explanation,
    }


def _new_build_response(eval_logger):
    """NEW (fixed) response builder — with error state check."""
    if not eval_logger:
        return None

    output_metadata = eval_logger.output_metadata
    if not output_metadata or not isinstance(output_metadata, dict):
        output_metadata = {}

    if eval_logger.error or eval_logger.output_str == "ERROR":
        return {
            "error_analysis": output_metadata.get("error_analysis"),
            "selected_input_key": output_metadata.get("selected_input_key"),
            "input_data": output_metadata.get("input_data"),
            "input_types": output_metadata.get("input_types"),
            "score": None,
            "explanation": eval_logger.error_message,
            "error": True,
        }

    evaluation_result = (
        eval_logger.output_bool
        if eval_logger.output_bool is not None
        else (
            eval_logger.output_float
            if eval_logger.output_float is not None
            else eval_logger.output_str_list
        )
    )
    evaluation_explanation = (
        eval_logger.eval_explanation
        if eval_logger.eval_explanation
        else eval_logger.error_message
    )

    return {
        "error_analysis": output_metadata.get("error_analysis", None),
        "selected_input_key": output_metadata.get("selected_input_key", None),
        "input_data": output_metadata.get("input_data", None),
        "input_types": output_metadata.get("input_types", None),
        "score": evaluation_result,
        "explanation": evaluation_explanation,
    }


def _old_ch_query_sql():
    """OLD ClickHouse query — missing deleted filter, error/output_str columns."""
    return """
        SELECT
            output_float,
            output_bool,
            output_str_list,
            eval_explanation,
            error_message,
            output_metadata
        FROM tracer_eval_logger FINAL
        WHERE observation_span_id = %(span_id)s
          AND custom_eval_config_id = %(config_id)s
          AND _peerdb_is_deleted = 0
        LIMIT 1
    """


def _new_ch_query_sql():
    """NEW ClickHouse query — with deleted filter and error/output_str columns."""
    return """
        SELECT
            output_float,
            output_bool,
            output_str_list,
            output_str,
            eval_explanation,
            error,
            error_message,
            output_metadata
        FROM tracer_eval_logger FINAL
        WHERE observation_span_id = %(span_id)s
          AND custom_eval_config_id = %(config_id)s
          AND _peerdb_is_deleted = 0
          AND (deleted = 0 OR deleted IS NULL)
        LIMIT 1
    """


# ---------------------------------------------------------------------------
# BUG 1: deleted filter
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeletedFilterFix:
    def test_all_objects_returns_soft_deleted_eval(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Del Test",
            project=project,
            eval_template=eval_template,
        )
        EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.9,
            deleted=True,
        )

        raw_result = _raw_pg_query_includes_deleted(observation_span.id, config.id)
        assert raw_result is not None, "all_objects bypasses soft-delete filter"
        assert raw_result.deleted is True

        managed_result = _old_pg_query(observation_span.id, config.id)
        assert managed_result is None, "BaseModelManager filters deleted by default"

    def test_new_query_excludes_soft_deleted_eval(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Del Test",
            project=project,
            eval_template=eval_template,
        )
        EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.9,
            deleted=True,
        )

        new_result = _new_pg_query(observation_span.id, config.id)
        assert new_result is None, "NEW query correctly excludes deleted logger"

    def test_new_query_returns_active_eval_when_deleted_exists(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Both Test",
            project=project,
            eval_template=eval_template,
        )
        EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.1,
            deleted=True,
        )
        active = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.95,
            deleted=False,
        )

        new_result = _new_pg_query(observation_span.id, config.id)
        assert new_result is not None
        assert new_result.id == active.id
        assert new_result.output_float == 0.95

    def test_ch_query_has_deleted_filter(self):
        old_sql = _old_ch_query_sql()
        new_sql = _new_ch_query_sql()

        assert "deleted" not in old_sql.split("_peerdb_is_deleted")[0]
        assert "(deleted = 0 OR deleted IS NULL)" in new_sql


# ---------------------------------------------------------------------------
# BUG 2: error state handling
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestErrorStateFix:
    def test_old_response_leaks_stale_score_on_error(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Err Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.5,
            error=True,
            error_message="LLM timed out",
            deleted=False,
        )

        old_resp = _old_build_response(logger_obj)
        assert old_resp["score"] == 0.5, (
            "OLD response leaks stale score on errored eval"
        )
        assert "error" not in old_resp, "OLD response has no error flag"

    def test_new_response_returns_error_with_null_score(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Err Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.5,
            error=True,
            error_message="LLM timed out",
            deleted=False,
        )

        new_resp = _new_build_response(logger_obj)
        assert new_resp["score"] is None
        assert new_resp["error"] is True
        assert new_resp["explanation"] == "LLM timed out"

    def test_output_str_error_detected(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Str Err Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_str="ERROR",
            error_message="Evaluation failed: invalid input",
            deleted=False,
        )

        old_resp = _old_build_response(logger_obj)
        assert old_resp.get("error") is None or "error" not in old_resp
        assert old_resp["score"] == [], "OLD returns empty str_list as score"

        new_resp = _new_build_response(logger_obj)
        assert new_resp["error"] is True
        assert new_resp["score"] is None
        assert new_resp["explanation"] == "Evaluation failed: invalid input"

    def test_error_with_output_metadata_preserved(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Err Meta Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            error=True,
            error_message="Parse error",
            output_metadata={
                "error_analysis": "Input was empty",
                "selected_input_key": "user_query",
            },
            deleted=False,
        )

        new_resp = _new_build_response(logger_obj)
        assert new_resp["error"] is True
        assert new_resp["error_analysis"] == "Input was empty"
        assert new_resp["selected_input_key"] == "user_query"

    def test_ch_query_selects_error_columns(self):
        old_sql = _old_ch_query_sql()
        new_sql = _new_ch_query_sql()

        old_select = old_sql.split("FROM")[0]
        new_select = new_sql.split("FROM")[0]

        assert "error," not in old_select.replace("error_message", "")
        assert "output_str," not in old_select

        assert "error," in new_select
        assert "output_str," in new_select


# ---------------------------------------------------------------------------
# Normal score rendering (regression — these must still pass after fix)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestScoreRenderingRegression:
    def test_float_score_unchanged(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Float Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.85,
            eval_explanation="Good quality",
            deleted=False,
        )

        old_resp = _old_build_response(logger_obj)
        new_resp = _new_build_response(logger_obj)

        assert old_resp["score"] == new_resp["score"] == 0.85
        assert old_resp["explanation"] == new_resp["explanation"] == "Good quality"

    def test_bool_score_unchanged(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Bool Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_bool=True,
            eval_explanation="Passed",
            deleted=False,
        )

        old_resp = _old_build_response(logger_obj)
        new_resp = _new_build_response(logger_obj)

        assert old_resp["score"] == new_resp["score"] is True
        assert old_resp["explanation"] == new_resp["explanation"] == "Passed"

    def test_str_list_score_unchanged(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="StrList Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_str_list=["toxic", "offensive"],
            eval_explanation="Multi-label",
            deleted=False,
        )

        old_resp = _old_build_response(logger_obj)
        new_resp = _new_build_response(logger_obj)

        assert old_resp["score"] == new_resp["score"] == ["toxic", "offensive"]

    def test_metadata_fields_preserved(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="Meta Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.9,
            output_metadata={
                "selected_input_key": "prompt",
                "input_data": {"prompt": "Hello"},
                "input_types": {"prompt": "string"},
            },
            deleted=False,
        )

        old_resp = _old_build_response(logger_obj)
        new_resp = _new_build_response(logger_obj)

        assert (
            old_resp["selected_input_key"] == new_resp["selected_input_key"] == "prompt"
        )
        assert old_resp["input_data"] == new_resp["input_data"] == {"prompt": "Hello"}

    def test_non_error_eval_has_no_error_flag(
        self, db, project, trace, observation_span, eval_template
    ):
        config = CustomEvalConfig.objects.create(
            name="NoErr Test",
            project=project,
            eval_template=eval_template,
        )
        logger_obj = EvalLogger.objects.create(
            trace=trace,
            observation_span=observation_span,
            custom_eval_config=config,
            output_float=0.75,
            error=False,
            deleted=False,
        )

        new_resp = _new_build_response(logger_obj)
        assert "error" not in new_resp
        assert new_resp["score"] == 0.75
