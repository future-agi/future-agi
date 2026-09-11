"""Unit tests for reconcile_eval_column_order."""

from dataclasses import dataclass
from types import SimpleNamespace

from simulate.utils.test_execution_utils import (
    build_eval_column,
    reconcile_eval_column_order,
)


@dataclass
class _EC:
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


def _evaluated(*ecs):
    return {ec.id for ec in ecs}


def test_no_evals_and_no_columns_is_noop():
    reconciled, changed = reconcile_eval_column_order(
        column_order=[], eval_configs=[], evaluated_eval_ids=set()
    )
    assert reconciled == []
    assert changed is False


def test_no_evals_preserves_non_eval_columns():
    reconciled, changed = reconcile_eval_column_order(
        column_order=list(BASE_COLS), eval_configs=[], evaluated_eval_ids=set()
    )
    assert reconciled == BASE_COLS
    assert changed is False


def test_appends_missing_eval_column_when_evaluated():
    e1 = _EC(id="e1", name="toxicity", template_config={"a": 1})
    reconciled, changed = reconcile_eval_column_order(
        column_order=list(BASE_COLS),
        eval_configs=[e1],
        evaluated_eval_ids=_evaluated(e1),
    )
    assert changed is True
    assert reconciled[: len(BASE_COLS)] == BASE_COLS
    assert _eval_cols(reconciled) == [build_eval_column(e1)]


def test_added_but_not_evaluated_stays_out_of_column_order():
    """Adding an eval on the run_test without running it against this TE
    must not add a phantom column - the grid would show a blank column."""
    e1 = _EC(id="e1", name="toxicity", template_config={"a": 1})
    reconciled, changed = reconcile_eval_column_order(
        column_order=list(BASE_COLS),
        eval_configs=[e1],
        evaluated_eval_ids=set(),
    )
    assert changed is False
    assert reconciled == BASE_COLS
    assert _eval_cols(reconciled) == []


def test_soft_deleted_eval_column_is_dropped():
    e1 = _EC(id="e1", name="toxicity", template_config={"a": 1})
    e2 = _EC(id="e2", name="bias", template_config={"b": 2})
    starting = list(BASE_COLS) + [build_eval_column(e1), build_eval_column(e2)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e2],
        evaluated_eval_ids=_evaluated(e2),
    )
    assert changed is True
    assert reconciled[: len(BASE_COLS)] == BASE_COLS
    assert [c["id"] for c in _eval_cols(reconciled)] == ["e2"]


def test_rename_refreshes_column_name_in_place():
    e1_old = _EC(id="e1", name="toxicity", template_config={"a": 1})
    e1_renamed = _EC(id="e1", name="toxicity_v2", template_config={"a": 1})
    starting = list(BASE_COLS) + [build_eval_column(e1_old)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e1_renamed],
        evaluated_eval_ids=_evaluated(e1_renamed),
    )
    assert changed is True
    eval_cols = _eval_cols(reconciled)
    assert len(eval_cols) == 1
    assert eval_cols[0]["id"] == "e1"
    assert eval_cols[0]["column_name"] == "toxicity_v2"


def test_template_config_change_is_reflected():
    e1_v1 = _EC(id="e1", name="toxicity", template_config={"threshold": 0.5})
    e1_v2 = _EC(id="e1", name="toxicity", template_config={"threshold": 0.9})
    starting = list(BASE_COLS) + [build_eval_column(e1_v1)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e1_v2],
        evaluated_eval_ids=_evaluated(e1_v2),
    )
    assert changed is True
    assert _eval_cols(reconciled)[0]["eval_config"] == {"threshold": 0.9}


def test_idempotent_when_columns_match_configs():
    e1 = _EC(id="e1", name="toxicity", template_config={"a": 1})
    starting = list(BASE_COLS) + [build_eval_column(e1)]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e1],
        evaluated_eval_ids=_evaluated(e1),
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
        build_eval_column(e2),
        build_eval_column(e1),
        BASE_COLS[1],
    ]
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e1, e2, e3],
        evaluated_eval_ids=_evaluated(e1, e2, e3),
    )
    assert changed is True
    assert [c.get("id") for c in reconciled] == [
        "call_details",
        "e2",
        "e1",
        "scenario",
        "e3",
    ]


def test_add_delete_rename_in_one_pass():
    e1_old = _EC(id="e1", name="toxicity", template_config={})
    e2 = _EC(id="e2", name="bias", template_config={})
    starting = list(BASE_COLS) + [build_eval_column(e1_old), build_eval_column(e2)]
    e1_renamed = _EC(id="e1", name="toxicity_final", template_config={})
    e3_new = _EC(id="e3", name="quality", template_config={})
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e1_renamed, e3_new],
        evaluated_eval_ids=_evaluated(e1_renamed, e3_new),
    )
    assert changed is True
    eval_cols = _eval_cols(reconciled)
    assert [c["id"] for c in eval_cols] == ["e1", "e3"]
    assert [c["column_name"] for c in eval_cols] == ["toxicity_final", "quality"]


def test_late_added_eval_only_appears_after_it_has_been_evaluated():
    """Two originals in column_order, a 3rd added on the run_test later.
    First reconcile (before rerun) keeps the grid at 2. After the rerun
    populates eval_outputs, the 3rd is appended in position after the
    originals."""
    e_orig1 = _EC(id="e-task", name="task_c", template_config={})
    e_orig2 = _EC(id="e-prompt", name="prompt_conformance", template_config={})
    e_added = _EC(id="e-tox", name="toxicity", template_config={})
    snapshotted = list(BASE_COLS) + [
        build_eval_column(e_orig1),
        build_eval_column(e_orig2),
    ]

    before_rerun, changed_before = reconcile_eval_column_order(
        column_order=snapshotted,
        eval_configs=[e_orig1, e_orig2, e_added],
        evaluated_eval_ids=_evaluated(e_orig1, e_orig2),  # e-tox not yet evaluated
    )
    assert changed_before is False
    assert [c["id"] for c in _eval_cols(before_rerun)] == ["e-task", "e-prompt"]

    after_rerun, changed_after = reconcile_eval_column_order(
        column_order=before_rerun,
        eval_configs=[e_orig1, e_orig2, e_added],
        evaluated_eval_ids=_evaluated(e_orig1, e_orig2, e_added),
    )
    assert changed_after is True
    assert [c["id"] for c in _eval_cols(after_rerun)] == [
        "e-task",
        "e-prompt",
        "e-tox",
    ]
    assert after_rerun[-1]["id"] == "e-tox"


def test_errored_or_skipped_output_still_counts_as_evaluated():
    """Any eval_outputs entry (error, skipped, completed) means the eval
    was attempted; the column must be shown so users can see the state."""
    e1 = _EC(id="e1", name="toxicity", template_config={})
    reconciled, changed = reconcile_eval_column_order(
        column_order=list(BASE_COLS),
        eval_configs=[e1],
        evaluated_eval_ids={"e1"},  # entry present, status irrelevant
    )
    assert changed is True
    assert [c["id"] for c in _eval_cols(reconciled)] == ["e1"]


def test_skips_non_dict_entries_gracefully():
    e1 = _EC(id="e1", name="toxicity", template_config={})
    starting = ["legacy_string_entry", None] + list(BASE_COLS)
    reconciled, changed = reconcile_eval_column_order(
        column_order=starting,
        eval_configs=[e1],
        evaluated_eval_ids=_evaluated(e1),
    )
    assert changed is True
    assert "legacy_string_entry" in reconciled and None in reconciled
    assert _eval_cols(reconciled)[0]["id"] == "e1"
