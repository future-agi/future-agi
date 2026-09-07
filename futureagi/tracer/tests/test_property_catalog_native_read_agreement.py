"""Query-agreement contracts only: fake observations, no SQL/server side effects."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import native_write_proof as subject
from tracer.tests.test_property_catalog_native_write_proof import DATABASE, make_proof

BASE_SQL = (
    f"SELECT status, _version FROM `{DATABASE}`.`property_catalog_source_streams` "
    "ORDER BY _version DESC"
)
PARAM_SQL = BASE_SQL + " LIMIT %(row_limit)s"
COLUMNS = (("status", "String"), ("_version", "UInt64"))
A = ("open", 7)
B = ("draining", 7)


def observed_proof(
    tmp_path, left, right, *, left_columns=COLUMNS, right_columns=COLUMNS
):
    proof, _ = make_proof(tmp_path)
    calls = []
    responses = {
        "replica1": (left, left_columns),
        "replica2": (right, right_columns),
    }

    def driver(name):
        def execute_read(sql, params, *, timeout_ms, settings):
            assert 0 < timeout_ms <= 5000
            assert settings["readonly"] == 2
            assert settings["result_overflow_mode"] == "throw"
            assert settings["timeout_overflow_mode"] == "throw"
            assert settings["use_query_cache"] == 0
            assert settings["max_result_rows"] == subject._MAX_ROWS
            assert settings["max_result_bytes"] == subject._MAX_AGREEMENT_BYTES
            calls.append((name, sql, params))
            rows, columns = responses[name]
            if isinstance(rows, Exception):
                raise rows
            return rows, columns, {}

        return SimpleNamespace(execute_read=execute_read)

    proof.connections = tuple(
        replace(c, driver=driver(c.name)) for c in proof.connections
    )
    return proof, calls


def read(proof, *, agreement=subject.STRICT_READ_AGREEMENT, sql=PARAM_SQL, params=None):
    return proof.agreed_read(
        sql,
        {"row_limit": 10} if params is None else params,
        timeout_ms=5000,
        agreement=agreement,
    )


@pytest.mark.parametrize("right", [[B, A], [A, A, B]])
def test_strict_default_preserves_order_and_multiplicity(tmp_path, right):
    proof, calls = observed_proof(tmp_path, [A, B], right)
    with pytest.raises(subject.NativeWriteProofError, match="disagrees"):
        proof.agreed_read(PARAM_SQL, {"row_limit": 10}, timeout_ms=5000)
    assert [c[0] for c in calls] == ["replica1", "replica2"]


@pytest.mark.parametrize("left,right", [([A, A, B], [B, A]), ([B, A], [A, A, B])])
def test_explicit_distinct_agreement_returns_first_order_and_duplicates(
    tmp_path, left, right
):
    proof, calls = observed_proof(tmp_path, left, right)
    result = read(
        proof,
        agreement=subject.NativeReadAgreement.cap_plus_one(limit_parameter="row_limit"),
    )
    assert result == tuple(
        dict(zip(("status", "_version"), row, strict=True)) for row in left
    )
    assert [c[0] for c in calls] == ["replica1", "replica2"]
    assert all(c[1] == PARAM_SQL and c[2] == {"row_limit": 10} for c in calls)


def test_first_native_objects_and_microseconds_survive_comparison(tmp_path):
    first_id, second_id = UUID(int=1), UUID(int=1)
    first_time = datetime(2026, 9, 6, microsecond=123456, tzinfo=UTC)
    second_time = datetime(2026, 9, 6, microsecond=123456, tzinfo=UTC)
    cols = (("build_token", "UUID"), ("updated_at", "DateTime64(6, 'UTC')"))
    proof, _ = observed_proof(
        tmp_path,
        [(first_id, first_time)],
        [(second_id, second_time)],
        left_columns=cols,
        right_columns=cols,
    )
    result = read(
        proof,
        agreement=subject.NativeReadAgreement.cap_plus_one(limit_parameter="row_limit"),
    )
    assert result[0]["build_token"] is first_id
    assert result[0]["updated_at"] is first_time
    assert result[0]["updated_at"].microsecond == 123456


@pytest.mark.parametrize("left,right", [([A], [A, A, A]), ([A, A, A], [A])])
def test_any_members_raw_sentinel_rejects_before_duplicate_reduction(
    tmp_path, left, right
):
    proof, calls = observed_proof(tmp_path, left, right)
    with pytest.raises(subject.NativeWriteProofError, match="raw overflow.*sentinel 3"):
        read(
            proof,
            params={"row_limit": 3},
            agreement=subject.NativeReadAgreement.cap_plus_one(
                limit_parameter="row_limit"
            ),
        )
    assert len(calls) == (1 if len(left) == 3 else 2)


def test_raw_overflow_precedes_value_normalization(tmp_path):
    proof, _ = observed_proof(tmp_path, [A], [(object(), 7)] * 3)
    with pytest.raises(subject.NativeWriteProofError, match="raw overflow on replica2"):
        read(
            proof,
            params={"row_limit": 3},
            agreement=subject.NativeReadAgreement.cap_plus_one(
                limit_parameter="row_limit"
            ),
        )


@pytest.mark.parametrize(
    "right", [[B], [A, B], [("open", 8)], [("open", "7")], [(None, 7)], []]
)
def test_distinct_does_not_hide_conflicting_full_values_or_missing_rows(
    tmp_path, right
):
    proof, _ = observed_proof(tmp_path, [A], right)
    with pytest.raises(subject.NativeWriteProofError, match="disagrees"):
        read(
            proof,
            agreement=subject.NativeReadAgreement.cap_plus_one(
                limit_parameter="row_limit"
            ),
        )


def test_differing_microseconds_are_not_rounded_into_agreement(tmp_path):
    a = datetime(2026, 9, 6, microsecond=123456, tzinfo=UTC)
    b = datetime(2026, 9, 6, microsecond=123457, tzinfo=UTC)
    cols = (("updated_at", "DateTime64(6, 'UTC')"),)
    proof, _ = observed_proof(
        tmp_path, [(a,)], [(b,)], left_columns=cols, right_columns=cols
    )
    with pytest.raises(subject.NativeWriteProofError, match="disagrees"):
        read(
            proof,
            agreement=subject.NativeReadAgreement.cap_plus_one(
                limit_parameter="row_limit"
            ),
        )


@pytest.mark.parametrize(
    "columns",
    [
        (("_version", "UInt64"), ("status", "String")),
        (("status", "Nullable(String)"), ("_version", "UInt64")),
        (("status", "String"), ("other", "UInt64")),
    ],
)
def test_column_order_names_and_native_type_metadata_must_agree(tmp_path, columns):
    proof, _ = observed_proof(tmp_path, [A], [A], right_columns=columns)
    with pytest.raises(subject.NativeWriteProofError, match="disagrees"):
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )


@pytest.mark.parametrize(
    "columns",
    [
        [],
        None,
        (("status", "String"), ("status", "String")),
        ((True, "UInt8"),),
        (("status", None),),
        (("", "String"),),
    ],
)
def test_malformed_columns_are_not_empty_success(tmp_path, columns):
    proof, _ = observed_proof(tmp_path, [A], [], right_columns=columns)
    with pytest.raises(subject.NativeWriteProofError, match="column"):
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )


@pytest.mark.parametrize(
    "rows", [None, [()], [("open",)], [("open", 7, "extra")], ["open"]]
)
def test_malformed_rows_fail_before_comparison(tmp_path, rows):
    proof, _ = observed_proof(tmp_path, [A], rows)
    with pytest.raises(subject.NativeWriteProofError, match="incomplete"):
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )


def test_empty_state_requires_all_members_and_complete_columns(tmp_path):
    proof, calls = observed_proof(tmp_path, [], [])
    assert (
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )
        == ()
    )
    assert len(calls) == 2


@pytest.mark.parametrize("mutation", ["empty", "missing", "duplicate", "reordered"])
def test_changed_member_inventory_fails_before_reads(tmp_path, mutation):
    proof, calls = observed_proof(tmp_path, [A], [A])
    a, b = proof.connections
    proof.connections = {
        "empty": (),
        "missing": (a,),
        "duplicate": (a, a),
        "reordered": (b, a),
    }[mutation]
    with pytest.raises(
        subject.NativeWriteProofError, match="every exact admitted member"
    ):
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )
    assert calls == []


def test_unavailable_member_never_returns_first_members_success(tmp_path):
    proof, calls = observed_proof(tmp_path, [A], ConnectionError("member absent"))
    with pytest.raises(ConnectionError, match="member absent"):
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )
    assert len(calls) == 2


@pytest.mark.parametrize(
    "limit", [None, True, False, 0, 1, -1, 3.0, "3", [], {}, 1 << 64]
)
def test_missing_or_malformed_parameter_limit_fails_before_reads(tmp_path, limit):
    proof, calls = observed_proof(tmp_path, [A], [A])
    params = {} if limit is None else {"row_limit": limit}
    with pytest.raises(subject.NativeWriteProofError, match="limit"):
        read(
            proof,
            params=params,
            agreement=subject.NativeReadAgreement.cap_plus_one(
                limit_parameter="row_limit"
            ),
        )
    assert calls == []


@pytest.mark.parametrize("limit", [True, False, 0, 1, -1, 4.0, "4", [], {}, 1 << 64])
def test_malformed_literal_limits_are_rejected(limit):
    with pytest.raises(subject.NativeWriteProofError, match="limit"):
        subject.NativeReadAgreement.cap_plus_one(limit=limit)


def test_parameter_may_require_an_exact_reviewed_value(tmp_path):
    proof, calls = observed_proof(tmp_path, [A], [A])
    contract = subject.NativeReadAgreement.cap_plus_one(
        limit_parameter="row_limit", limit=4
    )
    with pytest.raises(subject.NativeWriteProofError, match="differs"):
        read(proof, params={"row_limit": 5}, agreement=contract)
    assert calls == []
    assert read(proof, params={"row_limit": 4}, agreement=contract) == (
        {"status": "open", "_version": 7},
    )


def test_literal_follow_cap_rejects_fourth_row_on_nonfirst_member(tmp_path):
    proof, _ = observed_proof(tmp_path, [A], [A] * 4)
    with pytest.raises(subject.NativeWriteProofError, match="raw overflow on replica2"):
        read(
            proof,
            sql=BASE_SQL + " LIMIT 4",
            params={},
            agreement=subject.NativeReadAgreement.cap_plus_one(limit=4),
        )


def test_nested_selection_limit_does_not_override_outer_physical_sentinel(tmp_path):
    sql = f"SELECT status, _version FROM ({BASE_SQL} LIMIT %(logical_limit)s) ORDER BY _version DESC LIMIT %(physical_limit)s"
    proof, _ = observed_proof(tmp_path, [A, A, A], [A])
    result = read(
        proof,
        sql=sql,
        params={"logical_limit": 2, "physical_limit": 4},
        agreement=subject.NativeReadAgreement.cap_plus_one(
            limit_parameter="physical_limit"
        ),
    )
    assert len(result) == 3


@pytest.mark.parametrize(
    "sql",
    [
        BASE_SQL,
        BASE_SQL + " LIMIT %(other_limit)s",
        BASE_SQL + " LIMIT %(row_limit)s OFFSET 1",
        BASE_SQL + " LIMIT %(row_limit)s WITH TIES",
        BASE_SQL + " LIMIT %(row_limit)s BY status",
        BASE_SQL + " LIMIT 3",
        BASE_SQL + " LIMIT %(row_limit)s -- comment",
        f"SELECT * FROM ({BASE_SQL} LIMIT %(row_limit)s)",
        BASE_SQL + " /* LIMIT %(row_limit)s */",
        f"SELECT 'LIMIT %(row_limit)s', status FROM `{DATABASE}`.`property_catalog_source_streams`",
    ],
)
def test_cap_contract_cannot_bind_a_different_nested_or_quoted_limit(tmp_path, sql):
    proof, calls = observed_proof(tmp_path, [A], [A])
    with pytest.raises(subject.NativeWriteProofError, match="terminal outer LIMIT"):
        read(
            proof,
            sql=sql,
            agreement=subject.NativeReadAgreement.cap_plus_one(
                limit_parameter="row_limit"
            ),
        )
    assert calls == []


def test_complete_result_allows_nested_window_but_rejects_outer_limit(tmp_path):
    proof, calls = observed_proof(tmp_path, [A, A], [A])
    contract = subject.NativeReadAgreement.complete_result()
    with pytest.raises(subject.NativeWriteProofError, match="outer LIMIT"):
        read(proof, agreement=contract)
    assert calls == []
    assert (
        len(
            read(
                proof,
                sql=f"SELECT * FROM ({BASE_SQL} LIMIT 4096)",
                params={},
                agreement=contract,
            )
        )
        == 2
    )


def test_ordinary_singleton_selector_still_uses_strict_default(tmp_path):
    proof, _ = observed_proof(tmp_path, [A], [A])
    assert read(proof, sql=BASE_SQL + " LIMIT 1", params={}) == (
        {"status": "open", "_version": 7},
    )


def test_raw_hard_row_bound_applies_before_distinct(tmp_path, monkeypatch):
    proof, _ = observed_proof(tmp_path, [A], [A] * 3)
    monkeypatch.setattr(subject, "_MAX_ROWS", 2)
    with pytest.raises(subject.NativeWriteProofError, match="incomplete"):
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_raw_duplicate_bytes_cannot_be_hidden_by_set_reduction(
    tmp_path, monkeypatch, reverse
):
    row = ("x" * 100, 7)
    left, right = ([row] * 2, [row]) if reverse else ([row], [row] * 2)
    proof, _ = observed_proof(tmp_path, left, right)
    monkeypatch.setattr(subject, "_MAX_AGREEMENT_BYTES", 200)
    with pytest.raises(subject.NativeWriteProofError, match="byte bound"):
        read(
            proof, agreement=subject.NativeReadAgreement.complete_result(), sql=BASE_SQL
        )


@pytest.mark.parametrize(
    "agreement",
    [None, True, "complete_result", {}, SimpleNamespace(kind="complete_result")],
)
def test_untyped_agreement_cannot_enable_relaxed_comparison(tmp_path, agreement):
    proof, calls = observed_proof(tmp_path, [A], [A])
    with pytest.raises(subject.NativeWriteProofError, match="typed contract"):
        read(proof, agreement=agreement)
    assert calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "unknown"},
        {"kind": []},
        {"kind": True},
        {"kind": "strict", "limit": 4},
        {"kind": "complete_result", "limit": 4},
        {"kind": "cap_plus_one"},
        {"kind": "cap_plus_one", "limit_parameter": True},
        {"kind": "cap_plus_one", "limit_parameter": "row_limit) s;"},
    ],
)
def test_malformed_contracts_are_rejected(kwargs):
    with pytest.raises(subject.NativeWriteProofError):
        subject.NativeReadAgreement(**kwargs)


def test_agreement_contract_is_immutable():
    contract = subject.NativeReadAgreement.cap_plus_one(limit=4)
    with pytest.raises(FrozenInstanceError):
        contract.limit = 5
