"""F1 / F2 — list/count tools attach a SCOPE LABEL so the model can't echo a
cross-scope number as if it were scoped.

F1: ``list_trace_projects`` returns ``total_count`` for whatever ``project_type``
filter was applied (or BOTH types when none was). Without a label the model
read "Showing N of 47" and reported "47 observe projects", conflating an
all-type, workspace count with an observe-only one. ``_project_list_scope_label``
states the exact scope inline.

F2: ``get_dataset_rows`` paginates — ``table`` is one page but
``metadata.total_rows`` is the full DB count. ``_dataset_rows_scope_label``
surfaces the true total so the model doesn't report the page size as the row
count.

These are pure functions over (params, data); test them directly.
"""

from ai_tools.tools.bridge._datasets import _dataset_rows_scope_label
from tracer.views.project import _project_list_scope_label


class TestProjectListScopeLabel:
    def test_observe_filter_labels_observe(self):
        label = _project_list_scope_label(
            {"project_type": "observe"}, {"total_count": 47}
        )
        assert "47" in label
        assert "observe" in label.lower()
        assert "workspace" in label.lower()
        # Must warn against presenting it as org-wide / cross-type.
        assert "org-wide" in label.lower() or "cross-type" in label.lower()

    def test_experiment_filter_labels_experiment(self):
        label = _project_list_scope_label(
            {"project_type": "experiment"}, {"total_count": 25}
        )
        assert "25" in label
        assert "experiment" in label.lower()

    def test_no_filter_says_both_types(self):
        label = _project_list_scope_label({}, {"total_count": 72})
        assert "72" in label
        assert "both" in label.lower()
        # Nudges the model to scope down for a single-type count.
        assert "project_type" in label

    def test_name_filter_is_surfaced(self):
        label = _project_list_scope_label(
            {"project_type": "observe", "name": "chatbot"}, {"total_count": 3}
        )
        assert "chatbot" in label

    def test_total_from_projects_list_when_total_count_absent(self):
        label = _project_list_scope_label(
            {"project_type": "observe"},
            {"projects": [{"id": "1"}, {"id": "2"}]},
        )
        assert "2" in label

    def test_handles_missing_total_gracefully(self):
        # No total at all — still returns a usable scope sentence (no count).
        label = _project_list_scope_label({"project_type": "observe"}, {})
        assert "observe" in label.lower()
        assert "workspace" in label.lower()

    def test_handles_non_dict_data(self):
        # Must not crash on an unexpected payload shape.
        label = _project_list_scope_label({"project_type": "observe"}, "oops")
        assert "observe" in label.lower()


class TestDatasetRowsScopeLabel:
    def _data(self, total_rows, table_len=3, name="orders_v1"):
        return {
            "metadata": {"dataset_name": name, "total_rows": total_rows},
            "table": [{"row_id": str(i)} for i in range(table_len)],
        }

    def test_surfaces_true_total(self):
        label = _dataset_rows_scope_label(
            {"page_size": 3, "current_page_index": 0}, self._data(65)
        )
        assert "65" in label
        assert "row" in label.lower()
        # Explicitly tells the model not to report the page size as the count.
        assert "page size" in label.lower()

    def test_includes_dataset_name(self):
        label = _dataset_rows_scope_label({}, self._data(10, name="my_eval_set"))
        assert "my_eval_set" in label

    def test_page_window_reported(self):
        label = _dataset_rows_scope_label(
            {"page_size": 3, "current_page_index": 1}, self._data(65, table_len=3)
        )
        assert "page 1" in label

    def test_none_when_no_total(self):
        # column_config_only / schema-only responses have no total_rows — no label.
        assert _dataset_rows_scope_label({}, {"metadata": {}}) is None
        assert _dataset_rows_scope_label({}, {"column_config": []}) is None

    def test_handles_non_dict_data(self):
        assert _dataset_rows_scope_label({}, "oops") is None
