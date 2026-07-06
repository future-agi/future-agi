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


def test_matrix_covers_every_col_type_per_target():
    by_target_coltype: dict[str, set[str]] = {}
    for case in all_cases():
        by_target_coltype.setdefault(case.target_type, set()).add(case.col_type)
    expected_cols = set(COL_TYPES)
    for tt in TARGET_TYPES:
        assert by_target_coltype[tt] == expected_cols, (
            f"target {tt} missing col_types {expected_cols - by_target_coltype[tt]}"
        )


def test_no_duplicate_case_ids():
    ids = [c.case_id for c in all_cases()]
    dupes = [i for i in set(ids) if ids.count(i) > 1]
    assert not dupes, f"duplicate case_id in matrix: {dupes}"


def test_every_case_has_a_predicate():
    for c in all_cases():
        assert callable(c.expected_predicate), c.case_id


# Set of (col_type, filter_op) pairs where a 0-match or all-match result is the
# meaningful assertion (e.g. is_null on a column that's always set proves
# "no false positives" — and the matrix's job is to cover these explicitly).
_ALLOWED_DEGENERATE = {
    ("SYSTEM_METRIC", "is_null"),  # cost is always set in corpus
    ("SYSTEM_METRIC", "is_not_null"),  # ditto, inverse
    ("SPAN_ATTRIBUTE", "is_null"),  # ``missing_attr`` is intentionally absent
}


@pytest.mark.django_db
def test_every_case_matches_a_non_trivial_subset(seeded_corpus):
    """Catch degenerate FilterCases — every filter should match >0 and <ALL rows
    in the corpus (otherwise the case is undiscriminating and a bug in the
    matrix could slip through). Explicit allowlist for is_null-style filters."""
    seeded = seeded_corpus.rows
    total = len(seeded)
    degenerate = []
    for case in all_cases(
        eval_config_id=seeded_corpus.eval_config_id,
        label_id=seeded_corpus.annotation_label_id,
        choice_eval_config_id=seeded_corpus.choice_eval_config_id,
    ):
        if (case.col_type, case.filter_op) in _ALLOWED_DEGENERATE:
            continue
        # has_eval/has_annotation with value=False legitimately matches the
        # complement; both subsets are non-empty in the corpus, so don't skip.
        matched = sum(1 for r in seeded if case.expected_predicate(r))
        # voiceCalls only has 3 root rows AND voice roots have sp_idx=0, which
        # carry no eval / annotation in the corpus. Most filters touching
        # those features therefore match 0 voice rows — that's not a matrix
        # bug, it's the corpus shape. Skip the non-degeneracy guard here; the
        # parametrized tests still assert against the predicate-derived count
        # (0 or otherwise) and lock the contract.
        if case.target_type == "voiceCalls":
            continue
        if matched == 0 or matched == total:
            degenerate.append((case.case_id, matched, total))
    assert not degenerate, (
        "These FilterCases are non-discriminating against the seeded corpus "
        "(matched 0 or ALL rows). Either tune the threshold, vary the corpus, "
        "or add to _ALLOWED_DEGENERATE with justification:\n  "
        + "\n  ".join(f"{cid}: matched {m}/{t}" for cid, m, t in degenerate)
    )
