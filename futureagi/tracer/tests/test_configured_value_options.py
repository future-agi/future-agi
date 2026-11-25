from tracer.services.configured_value_options import configured_value_options


def test_configured_value_options_preserve_json_types_and_order():
    assert configured_value_options(
        [
            {"label": "Enabled", "value": True},
            {"label": "Two", "value": 2},
            "plain",
        ]
    ) == (
        {"label": "Enabled", "value": True},
        {"label": "Two", "value": 2},
        {"label": "plain", "value": "plain"},
    )


def test_configured_value_options_deduplicate_type_aware_values():
    assert configured_value_options(
        [
            {"label": "First", "value": 1},
            {"label": "Duplicate", "value": 1},
            {"label": "String", "value": "1"},
            {"name": "Fallback"},
            {"label": ""},
            None,
        ]
    ) == (
        {"label": "First", "value": 1},
        {"label": "String", "value": "1"},
        {"label": "Fallback", "value": "Fallback"},
    )


def test_configured_value_options_reject_non_lists_and_non_json_values():
    assert configured_value_options({"value": "not-a-list"}) == ()
    assert configured_value_options([{"value": {1, 2}}]) == ()
