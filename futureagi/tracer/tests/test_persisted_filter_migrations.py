import importlib


tracer_migration = importlib.import_module(
    "tracer.migrations.0078_canonicalize_persisted_filter_contracts"
)
automation_migration = importlib.import_module(
    "model_hub.migrations.0101_canonicalize_automation_rule_filter_fields"
)
performance_migration = importlib.import_module(
    "model_hub.migrations.0102_canonicalize_performance_report_filters"
)
simulate_migration = importlib.import_module(
    "simulate.migrations.0074_canonicalize_simulate_eval_config_filters"
)


def _legacy_filter(column_id="createdAt", col_type="SYSTEM_METRIC", op="is"):
    return {
        "id": "ui-row-1",
        "_meta": {"source": "old-panel"},
        "columnId": column_id,
        "displayName": "Created",
        "filterConfig": {
            "colType": col_type,
            "filterType": "datetime",
            "filterOp": op,
            "filterValue": "2026-01-01T00:00:00Z",
        },
    }


def test_saved_view_config_migrates_legacy_keys_and_filter_lists():
    config = {
        "subTab": "traces",
        "compareDateFilter": {"dateOption": "7D"},
        "extraFilters": [_legacy_filter()],
        "compareFilters": [_legacy_filter("latencyMs")],
        "compareExtraFilters": [],
        "filters": {
            "dateFilter": {"dateOption": "3M"},
        },
    }

    migrated, changed = tracer_migration._canonicalize_saved_view_config(config)

    assert changed is True
    assert "subTab" not in migrated
    assert "extraFilters" not in migrated
    assert "compareFilters" not in migrated
    assert "compareDateFilter" not in migrated
    assert migrated["sub_tab"] == "traces"
    assert migrated["compare_date_filter"] == {"dateOption": "7D"}
    assert migrated["display"]["dateFilter"] == {"dateOption": "3M"}
    assert migrated["extra_filters"] == [
        {
            "column_id": "created_at",
            "display_name": "Created",
            "filter_config": {
                "col_type": "SYSTEM_METRIC",
                "filter_type": "datetime",
                "filter_op": "equals",
                "filter_value": "2026-01-01T00:00:00Z",
            },
        }
    ]
    assert migrated["compare_filters"][0]["column_id"] == "latency_ms"


def test_saved_view_config_preserves_canonical_value_when_alias_also_exists():
    canonical_filter = {
        "column_id": "status",
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": "OK",
        },
    }
    config = {
        "extra_filters": [canonical_filter],
        "extraFilters": [_legacy_filter("createdAt")],
    }

    migrated, changed = tracer_migration._canonicalize_saved_view_config(config)

    assert changed is True
    assert "extraFilters" not in migrated
    assert migrated["extra_filters"] == [canonical_filter]


def test_filter_item_does_not_rewrite_span_attribute_ids():
    migrated, changed = tracer_migration._canonicalize_filter_item(
        _legacy_filter("createdAt", col_type="SPAN_ATTRIBUTE")
    )

    assert changed is True
    assert migrated["column_id"] == "createdAt"
    assert migrated["filter_config"]["col_type"] == "SPAN_ATTRIBUTE"


def test_eval_task_filter_wrapper_migrates_alias_keys_and_nested_filters():
    filters = {
        "projectId": "project-1",
        "dateRange": ["2026-01-01", "2026-01-31"],
        "observationType": ["llm"],
        "spanAttributesFilters": [_legacy_filter("customerTier", "SPAN_ATTRIBUTE")],
    }

    migrated, changed = tracer_migration._canonicalize_filter_wrapper(filters)

    assert changed is True
    assert "projectId" not in migrated
    assert "spanAttributesFilters" not in migrated
    assert migrated["project_id"] == "project-1"
    assert migrated["date_range"] == ["2026-01-01", "2026-01-31"]
    assert migrated["observation_type"] == ["llm"]
    assert migrated["span_attributes_filters"][0]["column_id"] == "customerTier"
    assert migrated["span_attributes_filters"][0]["filter_config"]["filter_op"] == (
        "equals"
    )


def test_dashboard_query_config_migrates_top_level_and_metric_filters():
    query_config = {
        "filters": [_legacy_filter("status")],
        "metrics": [
            {
                "name": "Latency",
                "filters": [_legacy_filter("latencyMs")],
            }
        ],
    }

    migrated, changed = tracer_migration._canonicalize_dashboard_query_config(
        query_config
    )

    assert changed is True
    assert migrated["filters"][0]["column_id"] == "status"
    assert migrated["metrics"][0]["filters"][0]["column_id"] == "latency_ms"


def test_automation_rule_conditions_renames_legacy_filters_key():
    conditions = {
        "operator": "and",
        "filters": [_legacy_filter("createdAt")],
    }

    migrated, changed = automation_migration._canonical_conditions(conditions)

    assert changed is True
    assert "filters" not in migrated
    assert migrated["filter"][0]["column_id"] == "created_at"


def test_automation_rule_filter_does_not_rewrite_span_attribute_ids():
    conditions = {
        "filter": [_legacy_filter("createdAt", col_type="SPAN_ATTRIBUTE")],
    }

    migrated, changed = automation_migration._canonical_conditions(conditions)

    assert changed is True
    assert migrated["filter"][0]["column_id"] == "createdAt"
    assert migrated["filter"][0]["filter_config"]["col_type"] == "SPAN_ATTRIBUTE"


def test_performance_report_filters_migrate_legacy_filter_lists():
    filters = [_legacy_filter("latencyMs", op="notBetween")]

    migrated, changed = performance_migration._canonicalize_filter_list(filters)

    assert changed is True
    assert migrated[0]["column_id"] == "latency_ms"
    assert migrated[0]["filter_config"]["filter_op"] == "not_between"


def test_simulate_eval_config_filters_migrate_legacy_filter_lists():
    filters = [_legacy_filter("duration", op="equal_to")]

    migrated, changed = simulate_migration._canonicalize_filter_list(filters)

    assert changed is True
    assert migrated[0]["column_id"] == "duration"
    assert migrated[0]["filter_config"]["filter_op"] == "equals"
