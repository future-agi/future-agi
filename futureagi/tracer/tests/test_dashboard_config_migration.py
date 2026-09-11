import importlib


migration = importlib.import_module(
    "tracer.migrations.0094_canonicalize_dashboard_widget_configs"
)


def test_canonicalizes_known_dashboard_config_aliases():
    query_config = {
        "metrics": [{"name": "latency", "displayName": "Latency"}],
        "breakdowns": [{"name": "status", "displayName": "Status"}],
        "customCamelKey": "preserved",
    }
    chart_config = {
        "chartType": "line",
        "axisConfig": {
            "leftY": {
                "prefixSuffix": "prefix",
                "outOfBounds": "visible",
                "unit": "ms",
            },
            "rightY": {
                "prefix_suffix": "suffix",
                "out_of_bounds": "hidden",
            },
            "xAxis": {"visible": True},
            "seriesAxis": {"0": "right"},
        },
    }

    migrated_query, query_changed = migration._canonicalize_query_config(query_config)
    migrated_chart, chart_changed = migration._canonicalize_chart_config(chart_config)

    assert query_changed is True
    assert migrated_query == {
        "metrics": [{"name": "latency", "display_name": "Latency"}],
        "breakdowns": [{"name": "status", "display_name": "Status"}],
        "customCamelKey": "preserved",
    }
    assert chart_changed is True
    assert migrated_chart == {
        "chart_type": "line",
        "axis_config": {
            "left_y": {
                "prefix_suffix": "prefix",
                "out_of_bounds": "visible",
                "unit": "ms",
            },
            "right_y": {
                "prefix_suffix": "suffix",
                "out_of_bounds": "hidden",
            },
            "x_axis": {"visible": True},
            "series_axis": {"0": "right"},
        },
    }


def test_preserves_canonical_values_when_aliases_coexist():
    query_config = {
        "metrics": [
            {
                "displayName": "Legacy latency",
                "display_name": "Latency",
            }
        ]
    }
    chart_config = {
        "axis_config": {
            "leftY": {
                "prefixSuffix": "prefix",
                "unit": "ms",
            },
            "left_y": {
                "prefix_suffix": "suffix",
                "label": "Latency",
            },
        }
    }

    migrated_query, _ = migration._canonicalize_query_config(query_config)
    migrated_chart, _ = migration._canonicalize_chart_config(chart_config)

    assert migrated_query["metrics"] == [{"display_name": "Latency"}]
    assert migrated_chart["axis_config"]["left_y"] == {
        "prefix_suffix": "suffix",
        "unit": "ms",
        "label": "Latency",
    }


def test_canonicalization_is_idempotent():
    query_config = {"metrics": [{"display_name": "Latency"}]}
    chart_config = {
        "chart_type": "line",
        "axis_config": {
            "left_y": {
                "prefix_suffix": "prefix",
                "out_of_bounds": "visible",
            }
        },
    }

    assert migration._canonicalize_query_config(query_config) == (query_config, False)
    assert migration._canonicalize_chart_config(chart_config) == (chart_config, False)
