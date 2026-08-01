from __future__ import annotations

import inspect

import pytest

from tracer.views.project import ProjectView

pytestmark = pytest.mark.unit


def test_project_list_uses_compact_sources_and_subsecond_read_budgets():
    source = inspect.getsource(ProjectView.list_projects)

    assert source.count("FROM traces ") == 2
    assert "FROM spans " not in source
    assert "FROM trace_count_rollup " not in source
    assert "countIf(created_at >= %(since)s) AS vol" in source
    assert "max(start_time) AS last_active" not in source
    assert "timeout_ms=5000" not in source
    assert source.count("timeout_ms=750") >= 2
    assert source.count("settings=project_read_settings") >= 2
