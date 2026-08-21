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
        "handoff_start": WINDOW_START - timedelta(days=2),
        "handoff_end": WINDOW_START - timedelta(days=1),
        "writer_watermark": WINDOW_END + timedelta(seconds=1),
        "status": CatalogActivationStatus.ACTIVE.value,
        "qualified_at": WINDOW_START,
        "state_version": 10,
        "latest_state_variants": 1,
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
        "coverage_start": WINDOW_START - timedelta(seconds=1),
        "coverage_end": WINDOW_END,
        "interior_gap_count": 0,
        "checkpoint_fences": [
            (_micros(WINDOW_START - timedelta(seconds=1)), _micros(midpoint), 101, 20),
            (_micros(midpoint), _micros(WINDOW_END), 102, 21),
        ],
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _exercise_dormant_reader_contract(monkeypatch):
    monkeypatch.setattr(reader_module, "_CONTIGUOUS_SOURCE_FENCE_SUPPORTED", True)


def _key_row(key, attribute_type, *, first_seen=None, last_seen=None):
    ranks = {
        "string": 1,
        "number": 2,
        "boolean": 3,
        "array": 4,
        "map": 5,
        "json": 6,
    }
    return {
        "key_folded": key.lower(),
        "attribute_key": key,
        "attribute_type": attribute_type,
        "attribute_type_rank": ranks[attribute_type],
        "first_seen": first_seen or WINDOW_START,
        "last_seen": last_seen or WINDOW_END - timedelta(microseconds=1),
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


def _reader(responder, *, project_ids=(PROJECT_A,), epoch=EPOCH):
    executor = RecordingExecutor(responder)
    reader = AttributeCatalogReader(
        executor,
        project_ids=project_ids,
        catalog_epoch=epoch,
        window_start=WINDOW_START,
        window_end=WINDOW_END,
    )
    return reader, executor


def _successful_responder(*, key_rows=(), value_rows=(), projects=(PROJECT_A,)):
    def respond(call):
        if "span_attribute_catalog_activations" in call.sql:
            return [_activation(project) for project in projects]
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
        normalized_search="",
        query_fingerprint=reader._key_query_fingerprint(
            normalized_search="", page_size=page_size
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
    assert len(executor.calls) == 2
    assert all(
        call.params["catalog_project_ids"] == projects for call in executor.calls
    )
    assert all(projects[0] not in call.sql for call in executor.calls)
    assert all(call.timeout_ms == 2_000 for call in executor.calls)
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


def test_reader_is_globally_unavailable_without_contiguous_source_fence(
    monkeypatch,
):
    monkeypatch.setattr(reader_module, "_CONTIGUOUS_SOURCE_FENCE_SUPPORTED", False)
    reader, executor = _reader(lambda _call: AssertionError("must not query"))

    assert reader.qualify() == CatalogUnavailable(
        "activation_requires_contiguous_source_fence"
    )
    assert executor.calls == []


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
            "activation_writer_lag",
        ),
    ],
)
def test_activation_admission_failures_are_explicit(activation_rows, reason):
    reader, _ = _reader(lambda call: activation_rows)

    assert reader.qualify() == CatalogUnavailable(reason)


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
        return checkpoint_rows

    reader, _ = _reader(respond)

    assert reader.qualify() == CatalogUnavailable(reason)


def test_checkpoint_query_error_is_explicit():
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        return RuntimeError("checkpoint read failed")

    reader, _ = _reader(respond)

    assert reader.qualify() == CatalogUnavailable("checkpoint_query_error")


def test_qualification_requires_coverage_for_every_project():
    def respond(call):
        if "activations" in call.sql:
            return [_activation(PROJECT_A), _activation(PROJECT_B)]
        return [_coverage(PROJECT_A)]

    reader, _ = _reader(respond, project_ids=(PROJECT_A, PROJECT_B))

    assert reader.qualify() == CatalogUnavailable("checkpoint_missing")


def test_equal_max_version_checkpoint_conflict_fails_closed():
    reader, executor = _reader(
        lambda call: (
            [_activation()]
            if "activations" in call.sql
            else [_coverage(version_conflict_count=1)]
        )
    )

    assert reader.qualify() == CatalogUnavailable("checkpoint_version_conflict")
    assert "uniqExactIf" in executor.calls[1].sql


def test_checkpoint_completion_status_is_named_and_bound():
    reader, executor = _reader(_successful_responder())

    assert isinstance(reader.qualify(), CatalogQualification)
    checkpoint_call = executor.calls[1]
    assert "status != %(catalog_checkpoint_complete_status)s" in checkpoint_call.sql
    assert "status != 'complete'" not in checkpoint_call.sql
    assert checkpoint_call.params["catalog_checkpoint_complete_status"] == (
        CatalogCheckpointStatus.COMPLETE.value
    )


def test_key_multi_page_result_fails_closed_without_immutable_snapshot():
    key_rows = (
        _key_row("Alpha", "string"),
        _key_row("alpha", "number"),
        _key_row("Beta", "boolean"),
    )
    reader, executor = _reader(_successful_responder(key_rows=key_rows))

    result = reader.read_key_candidates(page_size=2)

    assert result == CatalogUnavailable(
        "multi_page_requires_immutable_snapshot",
        "span_attribute_catalog.keys.v1",
    )
    key_call = executor.calls[-1]
    assert key_call.params["catalog_key_search_pattern"] == "%"
    assert key_call.params["catalog_page_limit"] == 3
    assert key_call.settings["max_result_rows"] == 3
    assert "ORDER BY key_folded ASC, attribute_key ASC" in key_call.sql
    assert "key_folded LIKE %(catalog_key_search_pattern)s" in key_call.sql
    assert "AL" not in key_call.sql

    assert len(executor.calls) == 3

    with pytest.raises(ValueError, match="page_size"):
        reader.read_key_candidates(page_size=CATALOG_MAX_PAGE_SIZE + 1)
    with pytest.raises(ValueError, match="must not be empty"):
        reader.read_value_candidates("key", page_size=1, attribute_types=())


def test_key_checkpoint_binds_normalized_search_and_whole_query_identity():
    reader, executor = _reader(_successful_responder())
    checkpoint = _key_checkpoint(reader)

    # A matching identity is accepted, then continuation fails closed because
    # schema 025 has no enforceable content fence.
    equivalent = reader.read_key_candidates(
        page_size=1,
        after=checkpoint,
    )
    assert equivalent == CatalogUnavailable(
        "continuation_requires_immutable_snapshot",
        "span_attribute_catalog.keys.v1",
    )

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


def test_value_multi_page_result_fails_closed_and_binds_filters():
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

    assert result == CatalogUnavailable(
        "multi_page_requires_immutable_snapshot",
        "span_attribute_catalog.values.v1",
    )
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
    assert equivalent == CatalogUnavailable(
        "continuation_requires_immutable_snapshot",
        "span_attribute_catalog.values.v1",
    )

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


def test_value_multi_page_unicode_rows_fail_closed_without_snapshot():
    value_rows = (
        _value_row("string", "İstanbul"),
        _value_row("string", "Straße"),
    )
    reader, executor = _reader(_successful_responder(value_rows=value_rows))

    result = reader.read_value_candidates("city", page_size=1)

    assert result == CatalogUnavailable(
        "multi_page_requires_immutable_snapshot",
        "span_attribute_catalog.values.v1",
    )
    assert len(executor.calls) == 3
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


def test_array_numeric_candidate_is_explicitly_unavailable_for_exact_fallback():
    reader, _ = _reader(
        _successful_responder(value_rows=(_value_row("array", Decimal("1.5")),))
    )

    result = reader.read_value_candidates(
        "json.array",
        page_size=1,
        attribute_types=("array",),
    )

    assert result == CatalogUnavailable(
        "unsupported_array_numeric",
        "span_attribute_catalog.values.v1",
    )


def test_candidate_query_error_is_explicit_after_successful_qualification():
    def respond(call):
        if "activations" in call.sql:
            return [_activation()]
        if "checkpoints" in call.sql:
            return [_coverage()]
        return RuntimeError("candidate read failed")

    reader, _ = _reader(respond)

    assert reader.read_key_candidates(page_size=1) == CatalogUnavailable(
        "key_candidate_query_error",
        "span_attribute_catalog.keys.v1",
    )


def test_continuation_fails_closed_before_requery_without_content_fence():
    activation_queries = 0
    candidate_queries = 0

    def respond(call):
        nonlocal activation_queries, candidate_queries
        if "activations" in call.sql:
            activation_queries += 1
            return [_activation(state_version=9 + activation_queries)]
        if "checkpoints" in call.sql:
            return [_coverage()]
        candidate_queries += 1
        return [_key_row("Alpha", "string"), _key_row("Beta", "string")]

    reader, _ = _reader(respond)
    checkpoint = _key_checkpoint(reader)

    second = reader.read_key_candidates(
        page_size=1,
        after=checkpoint,
    )

    assert second == CatalogUnavailable(
        "continuation_requires_immutable_snapshot",
        "span_attribute_catalog.keys.v1",
    )
    assert activation_queries == 1
    assert candidate_queries == 0


def test_value_continuation_fails_closed_before_requery_without_content_fence():
    checkpoint_queries = 0
    candidate_queries = 0

    def respond(call):
        nonlocal checkpoint_queries, candidate_queries
        if "activations" in call.sql:
            return [_activation()]
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
        "continuation_requires_immutable_snapshot",
        "span_attribute_catalog.values.v1",
    )
    assert checkpoint_queries == 1
    assert candidate_queries == 0


def test_qualification_fingerprint_changes_with_activation_or_checkpoint_state():
    activation_version = 10
    source_fence = 101

    def respond(call):
        if "activations" in call.sql:
            return [_activation(state_version=activation_version)]
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
    assert second.qualification_fingerprint != first.qualification_fingerprint

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

    call_count = len(executor.calls)
    assert reader.read_key_candidates(page_size=1, search=needle) == CatalogUnavailable(
        "search_requires_unicode_parity",
        "span_attribute_catalog.keys.v1",
    )
    assert reader.read_value_candidates(
        "key", page_size=1, search=needle
    ) == CatalogUnavailable(
        "search_requires_unicode_parity",
        "span_attribute_catalog.values.v1",
    )
    assert len(executor.calls) == call_count


@pytest.mark.parametrize("search", ["ss", "Straße"])
def test_unicode_casefold_search_fails_closed_without_false_negative(search):
    reader, executor = _reader(lambda _call: AssertionError("must not query"))

    assert reader.read_key_candidates(page_size=1, search=search) == CatalogUnavailable(
        "search_requires_unicode_parity",
        "span_attribute_catalog.keys.v1",
    )
    assert reader.read_value_candidates(
        "city", page_size=1, search=search
    ) == CatalogUnavailable(
        "search_requires_unicode_parity",
        "span_attribute_catalog.values.v1",
    )
    assert executor.calls == []


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
