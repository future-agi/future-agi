"""Matrix completeness + non-degeneracy guards.

Cheap, runs without ClickHouse if you skip the dual_writer fixture (we don't —
the non-degeneracy guard needs the seeded corpus).
"""

import pytest

from tracer.tests.integration._filter_matrix import (
    COL_TYPES,
    TARGET_TYPES,
    all_cases,
)
from tracer.utils.filter_operators import FILTER_TYPE_ALLOWED_OPS


def test_matrix_covers_every_col_type_per_target():
    by_target_coltype: dict[str, set[str]] = {}
    for case in all_cases():
        by_target_coltype.setdefault(case.target_type, set()).add(case.col_type)
    expected_cols = set(COL_TYPES)
    for tt in TARGET_TYPES:
        # ID is a matrix-internal pseudo col_type not in the contract's
        # columnTypes; every real col_type must still be present.
        assert expected_cols <= by_target_coltype[tt], (
            f"target {tt} missing col_types {expected_cols - by_target_coltype[tt]}"
        )


def test_no_duplicate_case_ids():
    ids = [c.case_id for c in all_cases()]
    dupes = [i for i in set(ids) if ids.count(i) > 1]
    assert not dupes, f"duplicate case_id in matrix: {dupes}"


def test_every_case_has_a_predicate():
    for c in all_cases():
        assert callable(c.expected_predicate), c.case_id


# ---------------------------------------------------------------------------
# Full-contract coverage: every (filter_type, filter_op) the FE/BE contract
# allows must be covered per applicable col_type — contract additions then fail
# this test until a leaf is added. Sourced live from filter_operators so the
# matrix tracks the canonical artifact.
# ---------------------------------------------------------------------------

# (col_type, filter_type) pairs the matrix is responsible for covering in full.
_APPLICABLE_PAIRS = [
    ("SYSTEM_METRIC", "text"),
    ("SYSTEM_METRIC", "number"),
    ("SYSTEM_METRIC", "datetime"),
    ("SYSTEM_METRIC", "categorical"),
    ("SPAN_ATTRIBUTE", "text"),
    ("SPAN_ATTRIBUTE", "number"),
    ("SPAN_ATTRIBUTE", "boolean"),
    ("EVAL_METRIC", "number"),
    ("EVAL_METRIC", "boolean"),
    ("EVAL_METRIC", "array"),
    ("ANNOTATION", "number"),
    ("ANNOTATION", "text"),
    ("ANNOTATION", "thumbs"),
    ("ANNOTATION", "annotator"),
    ("ANNOTATION", "categorical"),
]


def _is_op_coverage_case(case) -> bool:
    """Only 'normal' single-column cases contribute to op coverage — aggregate,
    ID, combo and meta cases carry contract-orthogonal shapes."""
    if case.col_type == "ID":
        return False
    if case.aggregate_predicate is not None:
        return False
    if case.extra_filters:
        return False
    return True


def test_matrix_covers_full_contract():
    covered: dict[tuple[str, str], set[str]] = {}
    for case in all_cases():
        if not _is_op_coverage_case(case):
            continue
        covered.setdefault((case.col_type, case.filter_type), set()).add(case.filter_op)

    missing_report = []
    for col_type, filter_type in _APPLICABLE_PAIRS:
        allowed = FILTER_TYPE_ALLOWED_OPS[filter_type]
        got = covered.get((col_type, filter_type), set())
        if got != allowed:
            missing_report.append(
                f"{col_type} × {filter_type}: missing {sorted(allowed - got)}, "
                f"extra {sorted(got - allowed)}"
            )
    assert not missing_report, "Filter contract not fully covered:\n  " + "\n  ".join(
        missing_report
    )


def test_special_families_present():
    """ID, has_eval, has_annotation, aggregate and combo families each exist."""
    cases = list(all_cases())
    assert any(c.col_type == "ID" for c in cases), "no ID cases"
    assert any(c.col_type == "has_eval" for c in cases), "no has_eval cases"
    assert any(c.col_type == "has_annotation" for c in cases), "no has_annotation cases"
    assert any(c.aggregate_predicate is not None for c in cases), "no aggregate cases"
    assert any(c.extra_filters for c in cases), "no multi-filter combo cases"


# Set of (col_type, filter_op) pairs where a 0-match or all-match result against
# the base corpus is the meaningful assertion (e.g. is_null on a column that's
# always set proves "no false positives"). Keyed by (col_type, op) — the guard
# can't distinguish columns, so an entry covers every column of that type/op.
_ALLOWED_DEGENERATE = {
    ("SYSTEM_METRIC", "is_null"),  # datetime is_null (no null created_at) → 0
    ("SYSTEM_METRIC", "is_not_null"),  # status is_not_null → all rows
    ("SPAN_ATTRIBUTE", "is_null"),  # missing_attr (number) intentionally absent
}


@pytest.mark.django_db
def test_every_case_matches_a_non_trivial_subset(seeded_corpus):
    """Catch degenerate FilterCases — every filter should match >0 and <ALL rows
    in the corpus. Contract-gap, aggregate and ID/late-bound cases are handled
    specially (gaps are xfailed at the endpoint; aggregates and late-bound are
    resolved before checking)."""
    import dataclasses

    seeded = seeded_corpus.rows
    total = len(seeded)
    degenerate = []
    for case in all_cases(
        eval_config_id=seeded_corpus.eval_config_id,
        label_id=seeded_corpus.annotation_label_id,
        choice_eval_config_id=seeded_corpus.choice_eval_config_id,
    ):
        # Contract gaps intentionally diverge from the corpus (xfailed at the
        # endpoint) — don't hold them to the non-degeneracy bar.
        if case.contract_gap:
            continue
        # voiceCalls only has root conversation rows, so most feature filters
        # match 0 there by corpus shape — the endpoint test still locks it.
        if case.target_type == "voiceCalls":
            continue
        # Aggregate cases operate over per-session root spans, not per-row.
        if case.aggregate_predicate is not None:
            groups: dict = {}
            for r in seeded:
                if r.parent_span_id is None:
                    groups.setdefault(r.session_id, []).append(r)
            matched = sum(1 for g in groups.values() if case.aggregate_predicate(g))
            sessions = len(groups)
            if 0 < matched < sessions:
                continue
            # is_null/is_not_null aggregates legitimately select 0/all sessions.
            if case.filter_op in ("is_null", "is_not_null"):
                continue
            degenerate.append((case.case_id, matched, sessions))
            continue
        # ID cases resolve their value + predicate against the corpus.
        if case.late_bound is not None:
            _, predicate = case.late_bound(seeded)
            case = dataclasses.replace(case, expected_predicate=predicate)
        if (case.col_type, case.filter_op) in _ALLOWED_DEGENERATE:
            continue
        matched = sum(1 for r in seeded if case.expected_predicate(r))
        if matched == 0 or matched == total:
            degenerate.append((case.case_id, matched, total))
    assert not degenerate, (
        "These FilterCases are non-discriminating against the seeded corpus "
        "(matched 0 or ALL rows). Either tune the threshold, vary the corpus, "
        "or add to _ALLOWED_DEGENERATE with justification:\n  "
        + "\n  ".join(f"{cid}: matched {m}/{t}" for cid, m, t in degenerate)
    )
