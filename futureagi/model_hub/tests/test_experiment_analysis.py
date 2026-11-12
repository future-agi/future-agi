import pytest

from model_hub.services.experiment_analysis import (
    DUPLICATE,
    IMPROVED,
    INVALID,
    MISSING,
    NOT_ASSESSED,
    REGRESSED,
    TIE,
    UNKNOWN,
    AnalysisFilter,
    RunItemRecord,
    compare_run_items,
    summarize_run_items,
)


def test_summarize_applies_split_metadata_and_keeps_unassessed_explicit():
    items = [
        RunItemRecord("a", score=0.8, split_names=("test",), metadata={"kind": "easy"}),
        RunItemRecord("b", score=0.2, split_names=("test",), metadata={"kind": "hard"}),
        RunItemRecord(
            "c",
            scores={"quality": {"status": NOT_ASSESSED}},
            split_names=("train",),
            metadata={"kind": "easy"},
        ),
    ]

    result = summarize_run_items(
        items,
        filters=AnalysisFilter(split_name="test", metadata_equals={"kind": "easy"}),
        passing_score=0.5,
    )

    assert result.item_count == 1
    assert result.score_count == 1
    assert result.score_average == 0.8
    assert result.pass_count == 1
    assert result.pass_rate == 1.0
    assert result.status_counts == {"completed": 1}

    unassessed = summarize_run_items(items, metric="quality")
    assert unassessed.score_count == 0
    assert unassessed.score_average is None


def test_compare_aligns_cases_and_reports_explicit_statuses():
    baseline = [
        RunItemRecord("improve", score=0.2),
        RunItemRecord("regress", score=0.8),
        RunItemRecord("tie", score=0.5),
        RunItemRecord("missing_candidate", score=0.4),
        RunItemRecord("unknown", scores={"quality": {"status": UNKNOWN}}),
        RunItemRecord("duplicate", score=0.1),
        RunItemRecord("duplicate", score=0.2),
        RunItemRecord("invalid", score=float("nan")),
    ]
    candidate = [
        RunItemRecord("improve", score=0.7),
        RunItemRecord("regress", score=0.3),
        RunItemRecord("tie", score=0.5),
        RunItemRecord("missing_baseline", score=0.9),
        RunItemRecord("unknown", scores={"quality": {"status": UNKNOWN}}),
        RunItemRecord("duplicate", score=0.9),
        RunItemRecord("invalid", score=0.4),
    ]

    result = compare_run_items(baseline, candidate, metric="quality")
    # The quality metric is absent on the numeric cases, so they are explicit
    # not_assessed.  The status matrix is still aligned by stable example ID.
    assert result.cases[0].example_id == "duplicate"
    assert result.status_counts[DUPLICATE] == 1
    assert result.status_counts[MISSING] == 2
    assert result.status_counts[NOT_ASSESSED] >= 4
    assert result.status_counts[UNKNOWN] == 1

    result = compare_run_items(baseline, candidate)
    assert result.status_counts[IMPROVED] == 1
    assert result.status_counts[REGRESSED] == 1
    assert result.status_counts[TIE] == 1
    assert result.status_counts[DUPLICATE] == 1
    assert result.status_counts[MISSING] == 2
    assert result.status_counts[INVALID] == 1
    assert result.comparable_count == 3
    by_id = {case.example_id: case for case in result.cases}
    assert by_id["improve"].score_delta == pytest.approx(0.5)
    assert by_id["missing_candidate"].missing_side == "candidate"
    assert by_id["missing_baseline"].missing_side == "baseline"


def test_trials_align_independently_and_only_duplicate_trial_keys_are_invalid():
    result = compare_run_items(
        [
            RunItemRecord("x", score=0.2, trial_number=1),
            RunItemRecord("x", score=0.8, trial_number=2),
        ],
        [
            RunItemRecord("x", score=0.4, trial_number=1),
            RunItemRecord("x", score=0.6, trial_number=2),
            RunItemRecord("x", score=1, trial_number=3),
        ],
    )
    assert [
        (case.example_id, case.trial_number, case.status) for case in result.cases
    ] == [("x", 1, IMPROVED), ("x", 2, REGRESSED), ("x", 3, MISSING)]
    assert result.cases[2].missing_side == "baseline"
    assert result.comparable_count == 2


@pytest.mark.parametrize(
    "status", ["failed", "error", "pending", "running", "skipped", "cancelled"]
)
def test_unsuccessful_item_cannot_improve_using_a_numeric_score(status):
    result = compare_run_items(
        [RunItemRecord("x", score=0.1)],
        [RunItemRecord("x", score=0.9, status=status)],
        passing_score=0.5,
    )
    expected = "error" if status in {"failed", "error"} else NOT_ASSESSED
    assert result.cases[0].status == expected
    assert result.cases[0].candidate_status == expected
    assert result.cases[0].candidate_score is None
    assert result.cases[0].score_delta is None
    assert result.comparable_count == 0
    assert result.candidate.score_count == 0
    assert result.candidate.score_average is None
    assert result.candidate.pass_rate is None
    assert result.candidate.score_status_counts == {expected: 1}


@pytest.mark.parametrize(
    "status", ["improved", "regressed", "tie", "completed", "", None, []]
)
def test_malformed_evidence_status_never_creates_a_comparable_case(status):
    result = compare_run_items(
        [RunItemRecord("x", scores={"quality": {"status": status}})],
        [RunItemRecord("x", scores={"quality": 0.8})],
        metric="quality",
    )
    assert result.status_counts == {INVALID: 1}
    assert result.comparable_count == 0
    assert result.cases[0].baseline_score is None
    assert result.cases[0].score_delta is None


@pytest.mark.parametrize("status", [UNKNOWN, NOT_ASSESSED, "unsupported", "error"])
def test_unassessed_evidence_status_is_preserved_even_with_a_value(status):
    result = compare_run_items(
        [RunItemRecord("x", scores={"quality": 0.2})],
        [RunItemRecord("x", scores={"quality": {"value": 0.9, "status": status}})],
        metric="quality",
    )
    assert result.status_counts == {status: 1}
    assert result.candidate.score_status_counts == {status: 1}
    assert result.comparable_count == 0


def test_failure_filter_keeps_the_completed_baseline_and_aggregate_cohort():
    result = compare_run_items(
        [RunItemRecord("x", score=0.5), RunItemRecord("other", score=1.0)],
        [
            RunItemRecord("x", status="failed", score=0.9),
            RunItemRecord("other", score=1.0),
        ],
        filters=AnalysisFilter(item_statuses=frozenset({"failed"})),
    )
    assert len(result.cases) == 1
    assert result.cases[0].status == "error"
    assert result.cases[0].baseline_score == 0.5
    assert result.cases[0].missing_side is None
    assert result.baseline.item_count == result.candidate.item_count == 1
    assert result.baseline.score_average == 0.5
    assert result.candidate.failed_count == 1


def test_filters_require_all_conditions_on_one_side_and_keep_its_pair():
    baseline = [
        RunItemRecord(
            "x", score=0.2, metadata={"language": "en"}, split_names=("train",)
        )
    ]
    candidate = [
        RunItemRecord(
            "x", score=0.8, metadata={"language": "fr"}, split_names=("test",)
        )
    ]
    excluded = compare_run_items(
        baseline,
        candidate,
        filters=AnalysisFilter(split_name="test", metadata_equals={"language": "en"}),
    )
    assert excluded.cases == ()
    assert excluded.baseline.item_count == excluded.candidate.item_count == 0
    selected = compare_run_items(
        baseline,
        candidate,
        filters=AnalysisFilter(split_name="test", metadata_equals={"language": "fr"}),
    )
    assert selected.cases[0].status == IMPROVED
    assert selected.baseline.score_average == 0.2
    assert selected.candidate.score_average == 0.8


def test_duplicate_records_cannot_be_hidden_by_filters_or_bias_score_averages():
    baseline = [
        RunItemRecord("x", score=0.1, metadata={"review": False}),
        RunItemRecord("x", score=0.9, metadata={"review": True}),
        RunItemRecord("other", score=0.5),
    ]
    result = compare_run_items(
        baseline,
        [RunItemRecord("x", score=0.2)],
        filters=AnalysisFilter(metadata_equals={"review": True}),
    )
    assert result.status_counts == {DUPLICATE: 1}
    assert result.baseline.item_count == 2
    assert result.baseline.score_count == 0
    assert result.baseline.score_status_counts == {DUPLICATE: 2}
    assert result.candidate.score_count == 1
    aggregate = summarize_run_items(baseline)
    assert aggregate.score_count == 1
    assert aggregate.score_average == 0.5


@pytest.mark.parametrize("invalid_id", [None, "", "  ", False, 123])
def test_invalid_example_identity_is_rejected_instead_of_aligned(invalid_id):
    with pytest.raises(ValueError, match="example_id"):
        compare_run_items(
            [{"example_id": invalid_id, "score": 1}],
            [{"example_id": invalid_id, "score": 1}],
        )
    with pytest.raises(ValueError, match="example_id"):
        summarize_run_items([RunItemRecord(invalid_id, score=1)])


def test_missing_example_identity_is_rejected():
    with pytest.raises(ValueError, match="example_id"):
        compare_run_items([{"score": 1}], [{"score": 1}])


@pytest.mark.parametrize("invalid_trial", [None, 0, -1, True, 1.5, "1"])
def test_invalid_trial_identity_is_rejected(invalid_trial):
    with pytest.raises(ValueError, match="trial_number"):
        summarize_run_items(
            [{"example_id": "x", "trial_number": invalid_trial, "score": 1}]
        )


def test_uuid_identities_align_with_serialized_ids():
    from uuid import UUID

    example_id = UUID("ec466b9c-1b1e-441f-852c-2e48c621ca07")
    result = compare_run_items(
        [RunItemRecord(example_id, score=0.2)],
        [{"example_id": str(example_id), "score": 0.5}],
    )
    assert result.cases[0].example_id == str(example_id)
    assert result.status_counts == {IMPROVED: 1}


def test_null_metadata_filter_requires_the_key_to_exist():
    result = summarize_run_items(
        [
            RunItemRecord("missing", score=0),
            RunItemRecord("null", score=1, metadata={"language": None}),
        ],
        filters=AnalysisFilter(metadata_equals={"language": None}),
    )
    assert result.item_count == 1
    assert result.score_average == 1.0


def test_extreme_numeric_inputs_do_not_overflow_or_emit_nonfinite_results():
    import json
    from dataclasses import asdict

    aggregate = summarize_run_items(
        [RunItemRecord("x", score=1e308), RunItemRecord("y", score=1e308)]
    )
    assert aggregate.score_average == 1e308
    result = compare_run_items(
        [RunItemRecord("x", score=-1e308)], [RunItemRecord("x", score=1e308)]
    )
    assert result.status_counts == {INVALID: 1}
    assert result.cases[0].score_delta is None
    assert result.comparable_count == 0
    json.dumps(asdict(aggregate), allow_nan=False)
    json.dumps(asdict(result), allow_nan=False)
    oversized = summarize_run_items([RunItemRecord("x", score=10**400)])
    assert oversized.score_count == 0
    assert oversized.score_status_counts == {INVALID: 1}


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), True, 10**400])
def test_invalid_pass_threshold_is_rejected(threshold):
    with pytest.raises(ValueError, match="passing_score"):
        summarize_run_items([RunItemRecord("x", score=1)], passing_score=threshold)
    with pytest.raises(ValueError, match="passing_score"):
        compare_run_items([], [], passing_score=threshold)


def test_lower_is_better_applies_to_comparisons_and_pass_thresholds():
    result = compare_run_items(
        [
            RunItemRecord("x", scores={"fid": 20}),
            RunItemRecord("y", scores={"fid": 10}),
        ],
        [
            RunItemRecord("x", scores={"fid": 10}),
            RunItemRecord("y", scores={"fid": 20}),
        ],
        metric="fid",
        passing_score=15,
        higher_is_better=False,
    )
    assert [(case.status, case.score_delta) for case in result.cases] == [
        (IMPROVED, -10.0),
        (REGRESSED, 10.0),
    ]
    assert result.baseline.pass_rate == result.candidate.pass_rate == 0.5
    aggregate = summarize_run_items(
        [RunItemRecord("x", score=10)], passing_score=15, higher_is_better=False
    )
    assert aggregate.pass_count == 1


def test_default_score_uses_named_envelope_and_preserves_explicit_zero():
    result = summarize_run_items(
        [
            RunItemRecord("x", scores={"score": {"value": 0.8, "status": "assessed"}}),
            RunItemRecord("y", score=0, scores={"score": 1}),
        ]
    )
    assert result.score_count == 2
    assert result.score_average == 0.4


def test_empty_inputs_have_no_invented_score_or_pass_rate():
    result = compare_run_items([], [], passing_score=0.5)
    assert result.cases == ()
    assert result.status_counts == {}
    assert result.baseline.item_count == result.candidate.item_count == 0
    assert result.baseline.score_average is None
    assert result.candidate.pass_rate is None


@pytest.mark.parametrize("field", ["scores", "metadata"])
@pytest.mark.parametrize("malformed", [[], False, 0, "bad"])
def test_mapping_adapter_rejects_malformed_mapping_fields(field, malformed):
    item = {"example_id": "x", field: malformed}
    with pytest.raises(ValueError, match=f"{field}"):
        summarize_run_items([item])


def test_mapping_adapter_allows_explicit_null_mapping_defaults():
    result = summarize_run_items(
        [{"example_id": "x", "score": 0.5, "scores": None, "metadata": None}]
    )
    assert result.score_count == 1
    assert result.score_average == 0.5
