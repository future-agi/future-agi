from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2 import attribute_catalog_reader as reader_module
from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)
from tracer.services.clickhouse.v2.attribute_catalog_reader import (
    CATALOG_MAX_PAGE_SIZE,
    CATALOG_MAX_PROJECTS,
    CATALOG_MAX_VALUE_JSON_BYTES,
    CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES,
    CATALOG_READ_SETTINGS,
    AttributeCatalogReader,
    CatalogActivationStatus,
    CatalogCheckpointStatus,
    CatalogKeyCheckpoint,
    CatalogKeyPage,
    CatalogQualification,
    CatalogUnavailable,
    CatalogValueCheckpoint,
    CatalogValuePage,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)

PROJECT_A = "00000000-0000-4000-8000-000000000001"
PROJECT_B = "00000000-0000-4000-8000-000000000002"
EPOCH = 7
WINDOW_START = datetime(2025, 8, 13, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 13, tzinfo=UTC)


def _micros(value):
    delta = value - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


class RecordingExecutor:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def execute(self, sql, params, *, timeout_ms, settings):
        call = SimpleNamespace(
            sql=sql,
            params=params,
            timeout_ms=timeout_ms,
            settings=settings,
        )
        self.calls.append(call)
        rows = self.responder(call)
        if isinstance(rows, BaseException):
            raise rows
        return SimpleNamespace(data=rows)


def _activation(project_id=PROJECT_A, **overrides):
    row = {
        "project_id": project_id,
        "catalog_epoch": EPOCH,
        "handoff_start": WINDOW_START,
        "handoff_end": WINDOW_END,
        "writer_watermark": WINDOW_END + timedelta(seconds=1),
        "status": CatalogActivationStatus.ACTIVE.value,
        "qualified_at": WINDOW_START,
        "state_version": 10,
        "latest_state_variants": 1,
    }
    row.update(overrides)
    return row


def _source_stream(project_id=PROJECT_A, **overrides):
    stream_id = str(uuid.UUID(int=int(uuid.UUID(project_id)) + 100))
    row = {
        "project_id": project_id,
        "stream_count": 1,
        "open_count": 0,
        "non_terminal_count": 0,
        "declared_gap_count": 0,
        "sequence_invalid_count": 0,
        "digest_invalid_count": 0,
        "missing_frozen_at_count": 0,
        "missing_version_count": 0,
        "version_conflict_count": 0,
        "source_stream_fences": [
            (stream_id, 1, 1, 2, 2, "a" * 64, "b" * 64),
        ],
    }
    row.update(overrides)
    return row


def _coverage(project_id=PROJECT_A, **overrides):
    midpoint = WINDOW_START + (WINDOW_END - WINDOW_START) / 2
    row = {
        "project_id": project_id,
        "checkpoint_count": 2,
        "incomplete_count": 0,
        "declared_gap_count": 0,
        "row_mismatch_count": 0,
        "missing_fence_count": 0,
        "version_conflict_count": 0,
        "coverage_start": WINDOW_START,
        "coverage_end": WINDOW_END,
        "interior_gap_count": 0,
        "checkpoint_fences": [
            (_micros(WINDOW_START), _micros(midpoint), 101, 20),
            (_micros(midpoint), _micros(WINDOW_END), 102, 21),
        ],
    }
    row.update(overrides)
    return row


def _key_row(
    key,
    attribute_type,
    *,
    first_seen=None,
    last_seen=None,
    total_count=1,
):
    ranks = {
        "string": 1,
        "number": 2,
        "boolean": 3,
        "array": 4,
        "map": 5,
        "json": 6,
    }
    return {
        "key_folded": "".join(
            chr(ord(character) + 32) if "A" <= character <= "Z" else character
            for character in key
        ),
        "attribute_key": key,
        "attribute_type": attribute_type,
        "attribute_type_rank": ranks[attribute_type],
        "first_seen": first_seen or WINDOW_START,
        "last_seen": last_seen or WINDOW_END - timedelta(microseconds=1),
        "total_count": total_count,
    }


def _value_row(attribute_type, value, **overrides):
    ranks = {"string": 1, "number": 2, "boolean": 3, "array": 4}
    encoded = encode_catalog_scalar(value)
    row = {
        "attribute_type": attribute_type,
        "attribute_type_rank": ranks.get(attribute_type, 5),
        "value_fingerprint": encoded.fingerprint,
        "value_json": encoded.value_json,
        "value_search_text": encoded.search_text,
        "value_folded": encoded.search_text.lower(),
        "value_json_variants": 1,
        "value_search_variants": 1,
        "first_seen": WINDOW_START,
        "last_seen": WINDOW_END - timedelta(microseconds=1),
    }
    row.update(overrides)
    return row


def _reader(
    responder,
    *,
    project_ids=(PROJECT_A,),
    epoch=EPOCH,
    catalog_database=None,
):
    executor = RecordingExecutor(responder)
    reader = AttributeCatalogReader(
        executor,
        project_ids=project_ids,
        catalog_epoch=epoch,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        catalog_database=catalog_database,
    )
    return reader, executor


def test_catalog_database_qualifies_only_closed_catalog_tables():
    reader, executor = _reader(
        _successful_responder(key_rows=[_key_row("alpha", "string")]),
        catalog_database="property_catalog_dev_normal_0813b",
    )

    result = reader.read_key_candidates(page_size=2)

    assert isinstance(result, CatalogKeyPage)
    assert len(executor.calls) == 4
    expected_tables = (
        "span_attribute_catalog_activations",
        "span_attribute_catalog_source_streams",
        "span_attribute_catalog_checkpoints",
        "span_attribute_key_catalog",
    )
    for call, table in zip(executor.calls, expected_tables, strict=True):
        assert f"FROM `property_catalog_dev_normal_0813b`.`{table}`" in call.sql
        assert "FROM spans" not in call.sql


@pytest.mark.parametrize(
    ("sql_name", "table"),
    (
        ("_ACTIVATION_SQL", "span_attribute_catalog_activations"),
        ("_SOURCE_STREAM_SQL", "span_attribute_catalog_source_streams"),
        ("_CHECKPOINT_SQL", "span_attribute_catalog_checkpoints"),
        ("_KEY_PAGE_SQL", "span_attribute_key_catalog"),
        ("_VALUE_PAGE_SQL", "span_attribute_value_catalog"),
    ),
)
def test_catalog_database_allowlist_covers_each_exact_catalog_table(sql_name, table):
    sql = getattr(reader_module, sql_name)

    qualified = reader_module._qualify_catalog_sql(sql, "isolated_catalog_dev")

    assert qualified.count(f"FROM `isolated_catalog_dev`.`{table}`") == 1
    assert reader_module._qualify_catalog_sql(sql, None) == sql


def test_catalog_database_does_not_match_an_allowlisted_table_prefix():
    with pytest.raises(ValueError, match="allowlisted table"):
        reader_module._qualify_catalog_sql(
            "SELECT * FROM span_attribute_key_catalog_backup",
            "isolated_catalog_dev",
        )


def test_catalog_window_bounds_are_explicit_datetime64_microseconds():
    for sql_name in ("_CHECKPOINT_SQL", "_KEY_PAGE_SQL", "_VALUE_PAGE_SQL"):
        sql = getattr(reader_module, sql_name)
        assert (
            "fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC')" in sql
        )
        assert "fromUnixTimestamp64Micro(%(catalog_window_end_us)s, 'UTC')" in sql
        assert "%(catalog_window_start)s" not in sql
        assert "%(catalog_window_end)s" not in sql
    assert "greatest(" in reader_module._CHECKPOINT_SQL
    assert "ifNull(" in reader_module._CHECKPOINT_SQL

    subsecond = WINDOW_START + timedelta(microseconds=123_456)
    assert reader_module._unix_microseconds(subsecond) == _micros(subsecond)


@pytest.mark.parametrize(
    "database",
    ("default; DROP TABLE spans", "system", "information_schema", "has-dash", ""),
)
def test_catalog_database_rejects_unsafe_identifier(database):
    with pytest.raises(ValueError, match="catalog_database"):
        _reader(lambda _call: [], catalog_database=database)

    with pytest.raises(ValueError, match="catalog_database"):
        reader_module._qualify_catalog_sql(reader_module._KEY_PAGE_SQL, database)


def _successful_responder(*, key_rows=(), value_rows=(), projects=(PROJECT_A,)):
    def respond(call):
        if "span_attribute_catalog_activations" in call.sql:
            return [_activation(project) for project in projects]
        if "span_attribute_catalog_source_streams" in call.sql:
            return [_source_stream(project) for project in projects]
        if "span_attribute_catalog_checkpoints" in call.sql:
            return [_coverage(project) for project in projects]
        if "span_attribute_key_catalog" in call.sql:
            return list(key_rows)
        if "span_attribute_value_catalog" in call.sql:
            return list(value_rows)
        raise AssertionError("unexpected query")

    return respond


def _key_checkpoint(reader, *, page_size=1):
    qualification = reader.qualify()
    assert isinstance(qualification, CatalogQualification)
    return CatalogKeyCheckpoint(
        source="span_attribute_catalog.keys.v1",
        catalog_epoch=EPOCH,
        project_scope_fingerprint=reader.project_scope_fingerprint,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        attribute_types=("string", "number", "boolean", "array", "map", "json"),
        normalized_search="",
        query_fingerprint=reader._key_query_fingerprint(
            attribute_types=(
                "string",
                "number",
                "boolean",
                "array",
                "map",
                "json",
            ),
            normalized_search="",
            page_size=page_size,
        ),
        qualification_fingerprint=qualification.qualification_fingerprint,
        key_folded="alpha",
        attribute_key="Alpha",
        attribute_type_rank=1,
    )


def _value_checkpoint(reader, *, page_size=1):
    qualification = reader.qualify()
    assert isinstance(qualification, CatalogQualification)
    types = ("string", "boolean")
    return CatalogValueCheckpoint(
        source="span_attribute_catalog.values.v1",
        catalog_epoch=EPOCH,
        project_scope_fingerprint=reader.project_scope_fingerprint,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
        attribute_key="voice.kind",
        attribute_types=types,
        normalized_search="",
        query_fingerprint=reader._value_query_fingerprint(
            attribute_key="voice.kind",
            attribute_types=types,
            normalized_search="",
            page_size=page_size,
        ),
        qualification_fingerprint=qualification.qualification_fingerprint,
        value_fingerprint="0" * 64,
        attribute_type_rank=1,
    )


def test_constructor_caps_and_canonicalizes_authorized_project_binding():
    projects = tuple(
        str(uuid.UUID(int=index + 1)) for index in range(CATALOG_MAX_PROJECTS)
    )
    reader, executor = _reader(
        _successful_responder(projects=projects),
        project_ids=projects,
    )

    result = reader.qualify()

    assert isinstance(result, CatalogQualification)
    assert len(executor.calls) == 3
    assert all(
        call.params["catalog_project_ids"] == projects for call in executor.calls
    )
    assert all(projects[0] not in call.sql for call in executor.calls)
    assert all(1 <= call.timeout_ms <= 2_000 for call in executor.calls)
    with pytest.raises(ValueError, match="at most 64"):
        _reader(
            lambda _call: [],
            project_ids=tuple(
                str(uuid.UUID(int=index + 1))
                for index in range(CATALOG_MAX_PROJECTS + 1)
            ),
        )
    with pytest.raises(ValueError, match="canonical UUID"):
        _reader(
            lambda _call: [],
            project_ids=("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",),
        )


def test_reader_fails_closed_when_frozen_source_stream_evidence_is_missing():
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return []
        raise AssertionError("checkpoint query must not run")

    reader, executor = _reader(respond)

    assert reader.qualify() == CatalogUnavailable("source_stream_missing")
    assert len(executor.calls) == 2


@pytest.mark.parametrize(
    ("overrides", "reason"),
    (
        ({"stream_count": 0}, "source_stream_missing"),
        ({"open_count": 1}, "source_stream_open"),
        ({"non_terminal_count": 1}, "source_stream_not_frozen"),
        ({"declared_gap_count": 1}, "source_stream_declared_gap"),
        ({"sequence_invalid_count": 1}, "source_stream_sequence_invalid"),
        ({"digest_invalid_count": 1}, "source_stream_digest_invalid"),
        ({"missing_frozen_at_count": 1}, "source_stream_freeze_missing"),
        ({"missing_version_count": 1}, "source_stream_version_missing"),
        ({"version_conflict_count": 1}, "source_stream_version_conflict"),
    ),
)
def test_source_stream_admission_failures_are_explicit(overrides, reason):
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return [_source_stream(**overrides)]
        raise AssertionError("checkpoint query must not run")

    reader, executor = _reader(respond)

    assert reader.qualify() == CatalogUnavailable(reason)
    assert len(executor.calls) == 2


def test_source_stream_query_error_is_explicit():
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        return RuntimeError("source stream read failed")

    reader, executor = _reader(respond)

    assert reader.qualify() == CatalogUnavailable("source_stream_query_error")
    assert len(executor.calls) == 2


def test_project_set_order_has_one_stable_scope_and_binding():
    first, _ = _reader(
        _successful_responder(projects=(PROJECT_A, PROJECT_B)),
        project_ids=(PROJECT_B, PROJECT_A),
    )
    second, _ = _reader(
        _successful_responder(projects=(PROJECT_A, PROJECT_B)),
        project_ids=(PROJECT_A, PROJECT_B),
    )

    assert first.project_ids == (PROJECT_A, PROJECT_B)
    assert first.project_scope_fingerprint == second.project_scope_fingerprint

    with pytest.raises(ValueError, match="positive UInt16"):
        _reader(lambda _call: [], epoch=0)


def test_qualification_fingerprint_is_deterministic_across_project_row_order():
    first, _ = _reader(
        _successful_responder(projects=(PROJECT_B, PROJECT_A)),
        project_ids=(PROJECT_A, PROJECT_B),
    )
    second, _ = _reader(
        _successful_responder(projects=(PROJECT_A, PROJECT_B)),
        project_ids=(PROJECT_A, PROJECT_B),
    )

    first_result = first.qualify()
    second_result = second.qualify()

    assert isinstance(first_result, CatalogQualification)
    assert isinstance(second_result, CatalogQualification)
    assert len(first_result.qualification_fingerprint) == 64
    assert (
        first_result.qualification_fingerprint
        == second_result.qualification_fingerprint
    )


@pytest.mark.parametrize(
    ("activation_rows", "reason"),
    [
        ([], "activation_missing"),
        ([_activation(catalog_epoch=EPOCH + 1)], "activation_epoch_mismatch"),
        ([_activation(catalog_epoch="7")], "activation_invalid"),
        (
            [_activation(status=CatalogActivationStatus.SHADOW.value)],
            "activation_status_not_active",
        ),
        (
            [_activation(handoff_end=WINDOW_START - timedelta(days=3))],
            "activation_handoff_invalid",
        ),
        (
            [_activation(writer_watermark=WINDOW_END - timedelta(microseconds=1))],
            "activation_handoff_invalid",
        ),
    ],
)
def test_activation_admission_failures_are_explicit(activation_rows, reason):
    reader, _ = _reader(lambda call: activation_rows)

    assert reader.qualify() == CatalogUnavailable(reason)


@pytest.mark.parametrize(
    "activation_row",
    (
        _activation(handoff_start=WINDOW_START - timedelta(microseconds=1)),
        _activation(
            handoff_end=WINDOW_END + timedelta(microseconds=1),
            writer_watermark=WINDOW_END + timedelta(seconds=1),
        ),
    ),
)
def test_epoch_global_bounds_require_the_exact_activation_window(activation_row):
    reader, executor = _reader(
        lambda call: [activation_row] if "activations" in call.sql else []
    )

    assert reader.qualify() == CatalogUnavailable("activation_window_not_exact")
    assert len(executor.calls) == 1


def test_normal_1970_to_now_ui_window_falls_back_from_a_one_year_activation():
    executor = RecordingExecutor(
        lambda call: [_activation()] if "activations" in call.sql else []
    )
    reader = AttributeCatalogReader(
        executor,
        project_ids=(PROJECT_A,),
        catalog_epoch=EPOCH,
        window_start=datetime(1970, 1, 1, tzinfo=UTC),
        window_end=WINDOW_END,
    )

    assert reader.qualify() == CatalogUnavailable("activation_window_not_exact")
    assert len(executor.calls) == 1


def test_activation_query_error_is_explicit_and_does_not_run_checkpoints():
    reader, executor = _reader(lambda _call: RuntimeError("unavailable"))

    assert reader.qualify() == CatalogUnavailable("activation_query_error")
    assert len(executor.calls) == 1


def test_activation_query_collapses_all_epochs_before_configured_epoch_check():
    reader, executor = _reader(
        lambda call: (
            [_activation(catalog_epoch=EPOCH + 1)] if "activations" in call.sql else []
        )
    )

    result = reader.qualify()

    assert result == CatalogUnavailable("activation_epoch_mismatch")
    sql = executor.calls[0].sql
    assert "argMax(" in sql
    assert "_version" in sql
    assert "catalog_epoch = %(catalog_epoch)s" not in sql


def test_equal_max_version_activation_conflict_fails_closed():
    reader, executor = _reader(
        lambda call: (
            [_activation(latest_state_variants=2)] if "activations" in call.sql else []
        )
    )

    assert reader.qualify() == CatalogUnavailable("activation_version_conflict")
    assert "uniqExactIf" in executor.calls[0].sql


@pytest.mark.parametrize(
    ("checkpoint_rows", "reason"),
    [
        ([], "checkpoint_missing"),
        ([_coverage(checkpoint_count=0)], "checkpoint_missing"),
        ([_coverage(incomplete_count=1)], "checkpoint_status_incomplete"),
        ([_coverage(declared_gap_count=1)], "checkpoint_declared_gap"),
        ([_coverage(row_mismatch_count=1)], "checkpoint_row_mismatch"),
        ([_coverage(missing_fence_count=1)], "checkpoint_source_fence_missing"),
        (
            [_coverage(coverage_start=WINDOW_START + timedelta(microseconds=1))],
            "checkpoint_window_gap",
        ),
        (
            [_coverage(coverage_end=WINDOW_END - timedelta(microseconds=1))],
            "checkpoint_window_gap",
        ),
        ([_coverage(interior_gap_count=1)], "checkpoint_window_gap"),
    ],
)
def test_checkpoint_coverage_failures_are_explicit(checkpoint_rows, reason):
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return [_source_stream()]
        return checkpoint_rows

    reader, _ = _reader(respond)

    assert reader.qualify() == CatalogUnavailable(reason)


def test_checkpoint_query_error_is_explicit():
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return [_source_stream()]
        return RuntimeError("checkpoint read failed")

    reader, _ = _reader(respond)

    assert reader.qualify() == CatalogUnavailable("checkpoint_query_error")


def test_qualification_requires_coverage_for_every_project():
    def respond(call):
        if "activations" in call.sql:
            return [_activation(PROJECT_A), _activation(PROJECT_B)]
        if "source_streams" in call.sql:
            return [_source_stream(PROJECT_A), _source_stream(PROJECT_B)]
        return [_coverage(PROJECT_A)]

    reader, _ = _reader(respond, project_ids=(PROJECT_A, PROJECT_B))

    assert reader.qualify() == CatalogUnavailable("checkpoint_missing")


def test_equal_max_version_checkpoint_conflict_fails_closed():
    reader, executor = _reader(
        lambda call: (
            [_activation()]
            if "activations" in call.sql
            else (
                [_source_stream()]
                if "source_streams" in call.sql
                else [_coverage(version_conflict_count=1)]
            )
        )
    )

    assert reader.qualify() == CatalogUnavailable("checkpoint_version_conflict")
    assert "uniqExactIf" in executor.calls[2].sql


def test_checkpoint_completion_status_is_named_and_bound():
    reader, executor = _reader(_successful_responder())

    assert isinstance(reader.qualify(), CatalogQualification)
    checkpoint_call = executor.calls[2]
    assert "status != %(catalog_checkpoint_complete_status)s" in checkpoint_call.sql
    assert "status != 'complete'" not in checkpoint_call.sql
    assert checkpoint_call.params["catalog_checkpoint_complete_status"] == (
        CatalogCheckpointStatus.COMPLETE.value
    )


def test_frozen_key_epoch_returns_a_continuable_page():
    key_rows = (
        _key_row("Alpha", "string", total_count=3),
        _key_row("alpha", "number", total_count=3),
        _key_row("Beta", "boolean", total_count=3),
    )
    reader, executor = _reader(_successful_responder(key_rows=key_rows))

    result = reader.read_key_candidates(page_size=2)

    assert isinstance(result, CatalogKeyPage)
    assert result.total_count == 3
    assert [(row.attribute_key, row.attribute_type) for row in result.candidates] == [
        ("Alpha", "string"),
        ("alpha", "number"),
    ]
    assert result.has_more is True
    assert result.next_checkpoint is not None
    assert result.query_count == 4
    assert result.next_checkpoint.attribute_key == "alpha"
    key_call = executor.calls[-1]
    assert key_call.params["catalog_key_search_pattern"] == "%"
    assert key_call.params["catalog_key_attribute_types"] == (
        "string",
        "number",
        "boolean",
        "array",
        "map",
        "json",
    )
    assert key_call.params["catalog_page_limit"] == 18
    assert key_call.params["catalog_window_start_us"] == _micros(WINDOW_START)
    assert key_call.params["catalog_window_end_us"] == _micros(WINDOW_END)
    assert key_call.settings["max_result_rows"] == 18
    assert "ORDER BY key_folded ASC, attribute_key ASC" in key_call.sql
    assert "key_folded LIKE %(catalog_key_search_pattern)s" in key_call.sql
    assert "AL" not in key_call.sql

    assert len(executor.calls) == 4

    with pytest.raises(ValueError, match="page_size"):
        reader.read_key_candidates(page_size=CATALOG_MAX_PAGE_SIZE + 1)
    with pytest.raises(ValueError, match="must not be empty"):
        reader.read_value_candidates("key", page_size=1, attribute_types=())


def test_key_type_filter_pages_before_publish_and_binds_continuation():
    filter_types = ("string", "number", "boolean", "array", "map")

    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return [_source_stream()]
        if "checkpoints" in call.sql:
            return [_coverage()]
        assert call.params["catalog_key_attribute_types"] == filter_types
        if call.params["catalog_after_key"]:
            return [_key_row("Beta", "map")]
        return [_key_row("Alpha", "string"), _key_row("Beta", "map")]

    reader, executor = _reader(respond)
    first = reader.read_key_candidates(
        page_size=1,
        attribute_types=filter_types,
    )

    assert isinstance(first, CatalogKeyPage)
    assert [candidate.attribute_key for candidate in first.candidates] == ["Alpha"]
    assert first.has_more is True
    assert first.next_checkpoint is not None
    assert first.next_checkpoint.attribute_types == filter_types
    first_candidate_calls = len(executor.calls)

    second = reader.read_key_candidates(
        page_size=1,
        attribute_types=filter_types,
        after=first.next_checkpoint,
    )

    assert isinstance(second, CatalogKeyPage)
    assert [candidate.attribute_key for candidate in second.candidates] == ["Beta"]
    assert second.has_more is False

    with pytest.raises(ValueError, match="query identity mismatch"):
        reader.read_key_candidates(
            page_size=1,
            attribute_types=(
                "string",
                "number",
                "boolean",
                "array",
                "map",
                "json",
            ),
            after=first.next_checkpoint,
        )
    assert len(executor.calls) == first_candidate_calls + 4


def test_key_type_filter_rejects_a_row_that_escaped_clickhouse_filter():
    reader, _ = _reader(
        _successful_responder(key_rows=(_key_row("eval.only", "json"),))
    )

    result = reader.read_key_candidates(
        page_size=1,
        attribute_types=("string", "number", "boolean", "array", "map"),
    )

    assert result == CatalogUnavailable(
        "key_candidate_query_error",
        "span_attribute_catalog.keys.v1",
    )


def test_eval_mapping_key_types_include_json_rows():
    all_types = ("string", "number", "boolean", "array", "map", "json")
    reader, executor = _reader(
        _successful_responder(key_rows=(_key_row("eval.only", "json"),))
    )

    result = reader.read_key_candidates(
        page_size=1,
        attribute_types=all_types,
    )

    assert isinstance(result, CatalogKeyPage)
    assert [
        (candidate.attribute_key, candidate.attribute_type)
        for candidate in result.candidates
    ] == [("eval.only", "json")]
    assert executor.calls[-1].params["catalog_key_attribute_types"] == all_types


def test_key_checkpoint_binds_normalized_search_and_whole_query_identity():
    reader, executor = _reader(_successful_responder())
    checkpoint = _key_checkpoint(reader)

    equivalent = reader.read_key_candidates(
        page_size=1,
        after=checkpoint,
    )
    assert isinstance(equivalent, CatalogKeyPage)
    assert equivalent.candidates == ()
    assert equivalent.has_more is False

    call_count = len(executor.calls)
    with pytest.raises(ValueError, match="query identity mismatch"):
        reader.read_key_candidates(
            page_size=1,
            search="different",
            after=checkpoint,
        )
    with pytest.raises(ValueError, match="query identity mismatch"):
        reader.read_key_candidates(
            page_size=2,
            after=checkpoint,
        )
    with pytest.raises(ValueError, match="query identity mismatch"):
        reader.read_key_candidates(
            page_size=1,
            after=replace(checkpoint, query_fingerprint="0" * 64),
        )
    assert len(executor.calls) == call_count


def test_key_checkpoint_is_frozen_to_source_epoch_scope_and_window():
    reader, _ = _reader(_successful_responder())
    checkpoint = _key_checkpoint(reader)

    for changed in (
        replace(checkpoint, source="wrong"),
        replace(checkpoint, catalog_epoch=EPOCH + 1),
        replace(checkpoint, project_scope_fingerprint="0" * 64),
        replace(checkpoint, window_end=WINDOW_END + timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError, match="frozen scope"):
            reader.read_key_candidates(page_size=1, after=changed)


def test_key_row_outside_frozen_window_fails_closed():
    reader, _ = _reader(
        _successful_responder(
            key_rows=(
                _key_row(
                    "old",
                    "string",
                    first_seen=WINDOW_START - timedelta(days=2),
                    last_seen=WINDOW_START - timedelta(microseconds=1),
                ),
            )
        )
    )

    assert reader.read_key_candidates(page_size=1) == CatalogUnavailable(
        "key_candidate_query_error",
        "span_attribute_catalog.keys.v1",
    )


def test_frozen_value_epoch_returns_a_continuable_page_and_binds_filters():
    value_rows = (
        _value_row("string", "Alpha"),
        _value_row("number", Decimal("1.25")),
        _value_row("boolean", False),
        _value_row("array", "zeta"),
    )
    reader, executor = _reader(_successful_responder(value_rows=value_rows))

    result = reader.read_value_candidates(
        "voice.kind",
        page_size=3,
        attribute_types=("string", "number", "boolean", "array"),
    )

    assert isinstance(result, CatalogValuePage)
    assert [row.value for row in result.candidates] == [
        "Alpha",
        Decimal("1.25"),
        False,
    ]
    assert result.has_more is True
    assert result.next_checkpoint is not None
    assert result.query_count == 4
    value_call = executor.calls[-1]
    assert value_call.params["catalog_attribute_key"] == "voice.kind"
    assert value_call.params["catalog_value_search_pattern"] == "%"
    assert value_call.params["catalog_attribute_types"] == (
        "string",
        "number",
        "boolean",
        "array",
    )
    assert value_call.params["catalog_page_limit"] == 4
    assert "voice.kind" not in value_call.sql
    assert "uniqExact(raw_value_json)" in value_call.sql


def test_value_checkpoint_binds_key_types_search_and_page_identity():
    reader, executor = _reader(_successful_responder())
    checkpoint = _value_checkpoint(reader)
    assert checkpoint.attribute_types == ("string", "boolean")

    equivalent = reader.read_value_candidates(
        "voice.kind",
        page_size=1,
        attribute_types=("string", "boolean"),
        after=checkpoint,
    )
    assert isinstance(equivalent, CatalogValuePage)
    assert equivalent.candidates == ()
    assert equivalent.has_more is False

    call_count = len(executor.calls)
    mismatches = (
        {"attribute_key": "other"},
        {"attribute_types": ("string",)},
        {"search": "different"},
        {"page_size": 2},
    )
    for override in mismatches:
        kwargs = {
            "attribute_key": "voice.kind",
            "page_size": 1,
            "attribute_types": ("boolean", "string"),
            "search": None,
            **override,
        }
        key = kwargs.pop("attribute_key")
        with pytest.raises(ValueError, match="query identity mismatch"):
            reader.read_value_candidates(
                key,
                after=checkpoint,
                **kwargs,
            )
    assert len(executor.calls) == call_count


def test_frozen_value_epoch_paginates_unicode_rows():
    value_rows = sorted(
        (
            _value_row("string", "İstanbul"),
            _value_row("string", "Straße"),
        ),
        key=lambda row: (row["attribute_type_rank"], row["value_fingerprint"]),
    )
    reader, executor = _reader(_successful_responder(value_rows=value_rows))

    result = reader.read_value_candidates("city", page_size=1)

    assert isinstance(result, CatalogValuePage)
    assert len(result.candidates) == 1
    assert result.has_more is True
    assert result.next_checkpoint is not None
    assert len(executor.calls) == 4
    call = executor.calls[-1]
    assert "lowerUTF8" not in call.sql
    assert "ORDER BY\n    attribute_type_rank ASC" in call.sql


@pytest.mark.parametrize(
    "bad_row",
    [
        _value_row("string", "alpha", value_fingerprint="0" * 64),
        _value_row(
            "string",
            "alpha",
            value_fingerprint=encode_catalog_scalar("alpha").fingerprint.upper(),
        ),
        _value_row("string", "alpha", value_json='"\\u0061lpha"'),
        {
            **_value_row("number", 1),
            "attribute_type": "string",
            "attribute_type_rank": 1,
        },
        _value_row("string", "alpha", value_json_variants=2),
        _value_row("string", "alpha", value_search_variants=2),
        _value_row("string", "alpha", value_search_text="different"),
        _value_row(
            "string",
            "alpha",
            last_seen=WINDOW_START - timedelta(microseconds=1),
        ),
    ],
)
def test_invalid_scalar_payload_or_fingerprint_fails_closed(bad_row):
    reader, _ = _reader(_successful_responder(value_rows=(bad_row,)))

    result = reader.read_value_candidates("voice.kind", page_size=1)

    assert result == CatalogUnavailable(
        "value_candidate_query_error",
        "span_attribute_catalog.values.v1",
    )


def test_array_numeric_candidate_preserves_its_scalar_kind():
    reader, _ = _reader(
        _successful_responder(value_rows=(_value_row("array", Decimal("1.5")),))
    )

    result = reader.read_value_candidates(
        "json.array",
        page_size=1,
        attribute_types=("array",),
    )

    assert isinstance(result, CatalogValuePage)
    assert len(result.candidates) == 1
    assert result.candidates[0].attribute_type == "array"
    assert result.candidates[0].scalar_kind == "number"
    assert result.candidates[0].value == Decimal("1.5")


def test_candidate_query_error_is_explicit_after_successful_qualification():
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return [_source_stream()]
        if "checkpoints" in call.sql:
            return [_coverage()]
        return RuntimeError("candidate read failed")

    reader, _ = _reader(respond)

    assert reader.read_key_candidates(page_size=1) == CatalogUnavailable(
        "key_candidate_query_error",
        "span_attribute_catalog.keys.v1",
    )


def test_frozen_key_continuation_survives_idempotent_activation_replay():
    activation_queries = 0
    candidate_queries = 0

    def respond(call):
        nonlocal activation_queries, candidate_queries
        if "activations" in call.sql:
            activation_queries += 1
            return [_activation(state_version=9 + activation_queries)]
        if "source_streams" in call.sql:
            return [_source_stream()]
        if "checkpoints" in call.sql:
            return [_coverage()]
        candidate_queries += 1
        return [_key_row("Beta", "string")]

    reader, _ = _reader(respond)
    checkpoint = _key_checkpoint(reader)

    second = reader.read_key_candidates(
        page_size=1,
        after=checkpoint,
    )

    assert isinstance(second, CatalogKeyPage)
    assert [candidate.attribute_key for candidate in second.candidates] == ["Beta"]
    assert activation_queries == 2
    assert candidate_queries == 1


def test_value_continuation_fails_closed_when_checkpoint_fence_changes():
    checkpoint_queries = 0
    candidate_queries = 0

    def respond(call):
        nonlocal checkpoint_queries, candidate_queries
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return [_source_stream()]
        if "checkpoints" in call.sql:
            checkpoint_queries += 1
            row = _coverage()
            if checkpoint_queries == 2:
                fences = list(row["checkpoint_fences"])
                fences[0] = (*fences[0][:2], fences[0][2] + 1, fences[0][3])
                row["checkpoint_fences"] = fences
            return [row]
        candidate_queries += 1
        return [_value_row("string", "alpha"), _value_row("string", "beta")]

    reader, _ = _reader(respond)
    checkpoint = _value_checkpoint(reader)

    second = reader.read_value_candidates(
        "voice.kind",
        page_size=1,
        attribute_types=("string", "boolean"),
        after=checkpoint,
    )

    assert second == CatalogUnavailable(
        "qualification_changed",
        "span_attribute_catalog.values.v1",
    )
    assert checkpoint_queries == 2
    assert candidate_queries == 0


def test_qualification_fingerprint_changes_with_activation_or_checkpoint_state():
    activation_version = 10
    source_fence = 101

    def respond(call):
        if "activations" in call.sql:
            return [_activation(state_version=activation_version)]
        if "source_streams" in call.sql:
            return [_source_stream()]
        row = _coverage()
        fences = list(row["checkpoint_fences"])
        fences[0] = (*fences[0][:2], source_fence, fences[0][3])
        row["checkpoint_fences"] = fences
        return [row]

    reader, _ = _reader(respond)
    first = reader.qualify()
    assert isinstance(first, CatalogQualification)

    activation_version += 1
    second = reader.qualify()
    assert isinstance(second, CatalogQualification)
    assert second.qualification_fingerprint == first.qualification_fingerprint

    source_fence += 1
    third = reader.qualify()
    assert isinstance(third, CatalogQualification)
    assert third.qualification_fingerprint != second.qualification_fingerprint


def test_catalog_search_uses_exact_ngram_index_expressions_and_bound_literals():
    needle = "X%_' OR 1=1 --\\tail"
    reader, executor = _reader(
        _successful_responder(
            key_rows=(_key_row("key", "string"),),
            value_rows=(_value_row("string", "value"),),
        )
    )

    assert isinstance(reader.read_key_candidates(page_size=1), CatalogKeyPage)
    key_call = executor.calls[-1]
    assert "key_folded LIKE %(catalog_key_search_pattern)s" in key_call.sql
    assert "OR length(key_folded) != lengthUTF8(key_folded)" in key_call.sql
    assert key_call.sql.index("key_folded LIKE") < key_call.sql.index("GROUP BY")
    assert needle not in key_call.sql
    assert key_call.params["catalog_key_search_pattern"] == "%"

    assert isinstance(
        reader.read_value_candidates("key", page_size=1),
        CatalogValuePage,
    )
    value_call = executor.calls[-1]
    assert (
        "lower(value_search_text) LIKE %(catalog_value_search_pattern)s"
        in value_call.sql
    )
    assert value_call.sql.index("lower(value_search_text) LIKE") < value_call.sql.index(
        "GROUP BY"
    )
    assert (
        "OR length(value_search_text) != lengthUTF8(value_search_text)"
        in value_call.sql
    )
    assert needle not in value_call.sql
    assert value_call.params["catalog_value_search_pattern"] == "%"

    key_result = reader.read_key_candidates(page_size=1, search=needle)
    assert isinstance(key_result, CatalogKeyPage)
    assert key_result.candidates == ()
    key_call = executor.calls[-1]
    assert needle not in key_call.sql
    assert key_call.params["catalog_key_search_pattern"] == (
        "%x\\%\\_' or 1=1 --\\\\tail%"
    )

    value_result = reader.read_value_candidates("key", page_size=1, search=needle)
    assert isinstance(value_result, CatalogValuePage)
    assert value_result.candidates == ()
    value_call = executor.calls[-1]
    assert needle not in value_call.sql
    assert value_call.params["catalog_value_search_pattern"] == (
        "%x\\%\\_' or 1=1 --\\\\tail%"
    )


def test_catalog_value_search_filters_raw_rows_before_aggregate_aliases():
    reader, executor = _reader(
        _successful_responder(value_rows=(_value_row("string", "value"),))
    )

    assert isinstance(
        reader.read_value_candidates("key", page_size=1, search="value"),
        CatalogValuePage,
    )
    value_sql = executor.calls[-1].sql
    source_sql, grouped_sql = value_sql.split("), grouped_values AS", maxsplit=1)

    assert "FROM span_attribute_value_catalog" in source_sql
    assert (
        "WHERE lower(value_search_text) LIKE %(catalog_value_search_pattern)s"
        in source_sql
    )
    assert "value_search_text AS raw_value_search_text" in source_sql
    assert "value_json AS raw_value_json" in source_sql
    assert "min(value_search_text) AS value_search_text" not in source_sql
    assert "FROM source_values" in grouped_sql
    assert "min(raw_value_search_text) AS value_search_text" in grouped_sql
    assert "min(raw_value_json) AS value_json" in grouped_sql
    assert "WHERE lower(value_search_text)" not in grouped_sql


@pytest.mark.parametrize("search", ["ss", "Straße"])
def test_unicode_casefold_search_matches_strasse_and_rechecks_false_positives(search):
    key_rows = sorted(
        (
            _key_row("Café", "string"),
            _key_row("Straße", "string"),
        ),
        key=lambda row: (
            row["key_folded"],
            row["attribute_key"],
            row["attribute_type_rank"],
        ),
    )
    value_rows = sorted(
        (
            _value_row("string", "Café"),
            _value_row("string", "Straße"),
        ),
        key=lambda row: (row["attribute_type_rank"], row["value_fingerprint"]),
    )
    reader, executor = _reader(
        _successful_responder(key_rows=key_rows, value_rows=value_rows)
    )

    key_page = reader.read_key_candidates(page_size=1, search=search)
    assert isinstance(key_page, CatalogKeyPage)
    assert [candidate.attribute_key for candidate in key_page.candidates] == ["Straße"]
    assert key_page.has_more is False

    value_page = reader.read_value_candidates("city", page_size=1, search=search)
    assert isinstance(value_page, CatalogValuePage)
    assert [candidate.value for candidate in value_page.candidates] == ["Straße"]
    assert value_page.has_more is False
    assert executor.calls[-1].params["catalog_value_search_pattern"] == (
        f"%{search.casefold()}%"
    )


def test_catalog_page_reports_every_search_scan_statement():
    false_positives = [_key_row(f"é{index:04d}", "string") for index in range(512)]
    candidate_queries = 0

    def respond(call):
        nonlocal candidate_queries
        if "activations" in call.sql:
            return [_activation()]
        if "source_streams" in call.sql:
            return [_source_stream()]
        if "checkpoints" in call.sql:
            return [_coverage()]
        candidate_queries += 1
        return (
            false_positives if candidate_queries == 1 else [_key_row("😀ss", "string")]
        )

    reader, executor = _reader(respond)

    page = reader.read_key_candidates(page_size=1, search="ss")

    assert isinstance(page, CatalogKeyPage)
    assert [candidate.attribute_key for candidate in page.candidates] == ["😀ss"]
    assert candidate_queries == 2
    assert page.query_count == 5
    assert page.query_count == len(executor.calls)


def test_every_catalog_statement_uses_latest_state_without_final():
    reader, executor = _reader(
        _successful_responder(
            key_rows=(_key_row("key", "string"),),
            value_rows=(_value_row("string", "value"),),
        )
    )

    assert isinstance(reader.read_key_candidates(page_size=1), CatalogKeyPage)
    assert isinstance(
        reader.read_value_candidates("key", page_size=1), CatalogValuePage
    )

    sql = "\n".join(call.sql for call in executor.calls)
    assert "FINAL" not in sql.upper()
    assert sql.count("argMax(") >= 4
    assert "argMax(" in sql and "_version" in sql
    assert "arraySort(" in sql
    assert "source_version_fence" in sql
    assert "checkpoint_state_version" in sql


def test_catalog_value_bounds_cover_one_maximum_picker_page():
    assert CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES == TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    assert CATALOG_MAX_VALUE_JSON_BYTES >= 6 * CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES
    assert CATALOG_READ_SETTINGS["max_result_bytes"] >= (
        CATALOG_MAX_PAGE_SIZE * CATALOG_MAX_VALUE_JSON_BYTES
    )


def test_catalog_accepts_the_maximum_suggestible_typed_string():
    value = "x" * TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    reader, _ = _reader(
        _successful_responder(value_rows=(_value_row("string", value),))
    )

    page = reader.read_value_candidates("key", page_size=1)

    assert isinstance(page, CatalogValuePage)
    assert [candidate.value for candidate in page.candidates] == [value]
