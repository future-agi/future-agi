"""Tests for the edit option-table guard — which rerun action an edit may use."""

from tracer.models.eval_task import RunType
from tracer.services.eval_tasks.edit_options import (
    EDIT_RERUN,
    FRESH_RUN,
    validate_edit_action,
)


def _check(edit_type, *, orig=RunType.HISTORICAL, new=None, evals=False, rows=False):
    return validate_edit_action(
        edit_type,
        original_run_type=orig,
        new_run_type=new,
        evals_changed=evals,
        rows_changed=rows,
    )


class TestEditOptions:
    def test_evals_only_allows_both(self):
        assert _check(EDIT_RERUN, evals=True) is None
        assert _check(FRESH_RUN, evals=True) is None

    def test_rows_only_allows_both(self):
        assert _check(EDIT_RERUN, rows=True) is None
        assert _check(FRESH_RUN, rows=True) is None

    def test_both_axes_requires_delete(self):
        assert _check(EDIT_RERUN, evals=True, rows=True) is not None  # rejected
        assert _check(FRESH_RUN, evals=True, rows=True) is None  # delete allowed

    def test_historical_to_continuous_is_edit_only(self):
        assert (
            _check(EDIT_RERUN, orig=RunType.HISTORICAL, new=RunType.CONTINUOUS) is None
        )
        assert (
            _check(FRESH_RUN, orig=RunType.HISTORICAL, new=RunType.CONTINUOUS)
            is not None
        )  # delete rejected

    def test_continuous_to_historical_allows_both(self):
        assert (
            _check(EDIT_RERUN, orig=RunType.CONTINUOUS, new=RunType.HISTORICAL) is None
        )
        assert (
            _check(FRESH_RUN, orig=RunType.CONTINUOUS, new=RunType.HISTORICAL) is None
        )

    def test_metadata_only_allows_both(self):
        assert _check(EDIT_RERUN) is None
        assert _check(FRESH_RUN) is None
