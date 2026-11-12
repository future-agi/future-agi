import uuid

from model_hub.services.experiment_output_diff import compare_output_items


class Item:
    def __init__(self, *, status="completed", output=None, error=None):
        self.id = uuid.uuid4()
        self.status = status
        self.output = output
        self.error = error


def test_canonical_output_ignores_object_order_and_integral_float_type():
    result = compare_output_items(
        Item(output={"b": 1.0, "a": [True, None]}),
        Item(output={"a": [True, None], "b": 1}),
    )

    assert result.comparison.status == "unchanged"
    assert result.comparison.changed_paths == ()
    assert result.baseline.output_digest == result.candidate.output_digest


def test_output_diff_reports_nested_paths_and_array_order():
    result = compare_output_items(
        Item(output={"answer": "old", "items": ["a", "b"]}),
        Item(output={"answer": "new", "items": ["b", "a", "c"]}),
    )

    assert result.comparison.status == "changed"
    assert result.comparison.changed_paths == (
        "/answer",
        "/items/0",
        "/items/1",
        "/items/2",
    )
    assert result.comparison.changed_path_count == 4


def test_missing_failed_and_completed_null_are_distinct():
    missing = compare_output_items(None, Item(output=None))
    failed = compare_output_items(
        Item(status="failed", error={"message": "x"}), Item(output=None)
    )
    nulls = compare_output_items(Item(output=None), Item(output=None))

    assert missing.baseline.output_state == "missing"
    assert missing.comparison.missing_side == "baseline"
    assert missing.comparison.status == "not_comparable"
    assert failed.baseline.output_state == "unavailable"
    assert failed.comparison.status == "not_comparable"
    assert nulls.comparison.status == "unchanged"
    assert nulls.baseline.output_preview is None
    assert nulls.baseline.preview_omitted is False


def test_output_preview_and_changed_path_bounds_are_explicit():
    large = {str(index): "x" * 500 for index in range(100)}
    result = compare_output_items(
        Item(output=large), Item(output={**large, "new": "x"})
    )
    assert result.baseline.preview_omitted is True
    assert result.candidate.preview_omitted is True

    before = {str(index): 0 for index in range(150)}
    after = {str(index): 1 for index in range(150)}
    bounded = compare_output_items(Item(output=before), Item(output=after))
    assert len(bounded.comparison.changed_paths) == 100
    assert bounded.comparison.changed_path_count == 150
    assert bounded.comparison.truncated is True

    over_limit = Item(output={"value": "x"})
    over_limit._output_over_limit = True
    classified = compare_output_items(over_limit, Item(output={"value": "x"}))
    assert classified.baseline.output_state == "over_limit"
    assert classified.comparison.status == "not_comparable"


def test_boolean_is_distinct_from_number():
    result = compare_output_items(
        Item(output={"value": True}), Item(output={"value": 1})
    )
    assert result.comparison.status == "changed"
    assert result.comparison.changed_paths == ("/value",)
