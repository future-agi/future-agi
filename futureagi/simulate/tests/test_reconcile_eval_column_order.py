"""Unit tests for reconcile_eval_column_order.

Guards the invariant that column_order stays in sync with the run_test's
current SimulateEvalConfig set across add / soft-delete / rename / template
edit, so pre-existing executions don't render stale grids.
"""

from dataclasses import dataclass
from types import SimpleNamespace

from simulate.utils.test_execution_utils import (
    _build_eval_column,
    reconcile_eval_column_order,
)


@dataclass
class _EC:
    """Duck-typed SimulateEvalConfig for the reconciliation contract."""

    id: str
    name: str
    template_config: dict

    @property
    def eval_template(self):
        return SimpleNamespace(config=self.template_config)


BASE_COLS = [
    {"id": "call_details", "column_name": "Call Details", "type": "system"},
    {"id": "scenario", "column_name": "Scenario", "type": "scenario"},
]


def _eval_cols(order):
    return [c for c in order if isinstance(c, dict) and c.get("type") == "evaluation"]


def test_no_evals_and_no_columns_is_noop():
    reconciled, changed = reconcile_eval_column_order(column_order=[], eval_configs=[])
    assert reconciled == []
    assert changed is False


def test_no_evals_preserves_non_eval_columns():
    reconciled, changed = reconcile_eval_column_order(
        column_order=list(BASE_COLS), eval_configs=[]
    )
    assert reconciled == BASE_COLS
    assert changed is False


def test_appends_missing_eval_column():
    e1 = _EC(id="e1", name="toxicity", template_config={"a": 1})
    reconciled, changed = reconcile_eval_column_order(
        column_order=list(BASE_COLS), eval_configs=[e1]
    )
    assert changed is True
    assert reconciled[: len(BASE_COLS)] == BASE_COLS
    assert _eval_cols(reconciled) == [_build_eval_column(e1)]


def test_soft_deleted_eval_column_is_dropped():
    e1 = _EC(id="e1", name="toxicity", template_config={"a": 1})
    e2 = _EC(id="e2", name="bias", template_config={"b": 2})
    starting = list(BASE_COLS) + [_build_eval_column(e1), _build_eval_column(e2)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting, eval_configs=[e2]  # e1 soft-deleted
    )
    assert changed is True
    assert reconciled[: len(BASE_COLS)] == BASE_COLS
    assert [c["id"] for c in _eval_cols(reconciled)] == ["e2"]


def test_rename_refreshes_column_name_in_place():
    e1_old = _EC(id="e1", name="toxicity", template_config={"a": 1})
    e1_renamed = _EC(id="e1", name="toxicity_v2", template_config={"a": 1})
    starting = list(BASE_COLS) + [_build_eval_column(e1_old)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting, eval_configs=[e1_renamed]
    )
    assert changed is True
    eval_cols = _eval_cols(reconciled)
    assert len(eval_cols) == 1
    assert eval_cols[0]["id"] == "e1"
    assert eval_cols[0]["column_name"] == "toxicity_v2"


def test_template_config_change_is_reflected():
    e1_v1 = _EC(id="e1", name="toxicity", template_config={"threshold": 0.5})
    e1_v2 = _EC(id="e1", name="toxicity", template_config={"threshold": 0.9})
    starting = list(BASE_COLS) + [_build_eval_column(e1_v1)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting, eval_configs=[e1_v2]
    )
    assert changed is True
    assert _eval_cols(reconciled)[0]["eval_config"] == {"threshold": 0.9}


def test_idempotent_when_columns_match_configs():
    e1 = _EC(id="e1", name="toxicity", template_config={"a": 1})
    starting = list(BASE_COLS) + [_build_eval_column(e1)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting, eval_configs=[e1]
    )
    assert changed is False
    assert reconciled == starting


def test_preserves_position_of_surviving_evals():
    e1 = _EC(id="e1", name="toxicity", template_config={})
    e2 = _EC(id="e2", name="bias", template_config={})
    e3 = _EC(id="e3", name="quality", template_config={})
    # User reordered so evals sit before the base "scenario" column.
    starting = [
        BASE_COLS[0],
        _build_eval_column(e2),
        _build_eval_column(e1),
        BASE_COLS[1],
    ]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting, eval_configs=[e1, e2, e3]  # e3 is new
    )
    assert changed is True
    assert [c.get("id") for c in reconciled] == [
        "call_details",
        "e2",  # surviving eval position preserved
        "e1",  # surviving eval position preserved
        "scenario",
        "e3",  # new eval appended
    ]


def test_add_delete_rename_in_one_pass():
    e1_old = _EC(id="e1", name="toxicity", template_config={})
    e2 = _EC(id="e2", name="bias", template_config={})
    starting = list(BASE_COLS) + [_build_eval_column(e1_old), _build_eval_column(e2)]
    e1_renamed = _EC(id="e1", name="toxicity_final", template_config={})
    e3_new = _EC(id="e3", name="quality", template_config={})
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e1_renamed, e3_new],  # e2 deleted, e1 renamed, e3 added
    )
    assert changed is True
    eval_cols = _eval_cols(reconciled)
    assert [c["id"] for c in eval_cols] == ["e1", "e3"]
    assert [c["column_name"] for c in eval_cols] == ["toxicity_final", "quality"]


def test_skips_non_dict_entries_gracefully():
    e1 = _EC(id="e1", name="toxicity", template_config={})
    starting = ["legacy_string_entry", None] + list(BASE_COLS)
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting, eval_configs=[e1]
    )
    assert changed is True
    assert "legacy_string_entry" in reconciled and None in reconciled
    assert _eval_cols(reconciled)[0]["id"] == "e1"
