"""Rendering and registration rules for the OpenAPI-generated tools.

Both behaviours here are invisible in normal use and only show up as wrong
answers: a list that arrives cut in half still looks like a valid list, and a
tool pointing at an unmounted route still looks callable.
"""

import json

from ai_tools.generated import (
    RENDER_BUDGET_CHARS,
    _route_is_mounted,
    register_generated_tools,
    render_result,
)


def _datasets(count):
    return [
        {
            "id": f"{i:08d}-aaaa-bbbb-cccc-dddddddddddd",
            "name": f"Falcon QA Dataset {i}",
            "description": "A test dataset created for query validation purposes",
            "created_at": "2026-10-01T12:00:00Z",
            "updated_at": "2026-10-01T12:30:00Z",
            "row_count": 120 + i,
            "column_count": 6,
            "workspace": "default",
        }
        for i in range(count)
    ]


def _page(count):
    return {"result": _datasets(count), "count": count}


def test_short_result_is_passed_through_untouched():
    rendered = render_result(_page(2))
    assert json.loads(rendered)["count"] == 2
    assert "showing" not in rendered


def test_a_full_page_survives_intact():
    """The regression this guards: a 20-row page used to arrive partial."""
    rendered = render_result(_page(20))
    assert len(rendered) <= RENDER_BUDGET_CHARS
    assert len(json.loads(rendered)["result"]) == 20
    assert "showing" not in rendered


def test_oversized_list_keeps_whole_records_and_says_what_is_missing():
    rendered = render_result(_page(500))
    head, _, note = rendered.partition("\n")

    # Whole records only — a mid-record cut would not parse.
    kept = json.loads(head)["result"]
    assert 0 < len(kept) < 500
    assert all(set(row) == set(kept[0]) for row in kept)

    # And the model is told, so it can narrow rather than answer from a slice.
    assert f"showing {len(kept)} of 500" in note


def test_payload_with_no_list_is_left_alone():
    created = {"status": True, "result": "Experiment created. experiment_id=abc"}
    assert json.loads(render_result(created)) == created
    assert render_result(None) == "Done."


def test_cloud_only_routes_are_skipped_without_the_ee_app(monkeypatch):
    """`/usage/` is mounted only under `if has_ee("ee.cloud")`.

    Registering it anyway on a self-hosted install gives the model a tool that
    can only ever 404.
    """
    import tfc.ee_loader as ee_loader

    real = ee_loader.has_ee
    monkeypatch.setattr(
        ee_loader, "has_ee", lambda m: False if m == "ee.cloud" else real(m)
    )

    assert _route_is_mounted("/tracer/trace/") is True
    assert _route_is_mounted("/usage/v2/usage-overview/") is False

    class _Collector:
        def __init__(self):
            self.names = []

        def register(self, tool):
            self.names.append(tool.name)

    collector = _Collector()
    register_generated_tools(target=collector)
    assert "get_usage_overview" not in collector.names
    assert "list_datasets" in collector.names
