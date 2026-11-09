"""Dashboard links in Falcon tool output point at this install's UI.

Falcon renders tool output in the browser. A dashboard link on
app.futureagi.com sends a self-hosted install's user to Future AGI Cloud.
Future AGI Cloud itself keeps its UI host through FRONTEND_URL/APP_URL.
"""

from __future__ import annotations

from django.test import override_settings

from ai_tools.formatting import dashboard_link


class TestDashboardLinkBase:
    def test_no_configured_ui_gives_a_root_relative_link(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
        with override_settings(FRONTEND_BASE_URL=""):
            link = dashboard_link("dataset", "abc-123", label="DS")
        assert link == "[DS](/dashboard/develop/abc-123)"

    def test_the_install_ui_is_the_base(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
        with override_settings(FRONTEND_BASE_URL="https://ui.selfhost.test/"):
            link = dashboard_link("project", "p-1", label="P")
        assert link == "[P](https://ui.selfhost.test/dashboard/observe/p-1)"

    def test_dashboard_base_url_env_still_wins(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_BASE_URL", "https://dash.selfhost.test/")
        with override_settings(FRONTEND_BASE_URL="https://ui.selfhost.test"):
            link = dashboard_link("agent", "a-1", label="A")
        assert link == "[A](https://dash.selfhost.test/dashboard/agents/playground/a-1)"

    def test_cloud_keeps_its_ui_host(self, monkeypatch):
        # Cloud sets APP_URL; FRONTEND_BASE_URL is built from it.
        monkeypatch.delenv("DASHBOARD_BASE_URL", raising=False)
        with override_settings(FRONTEND_BASE_URL="https://app.futureagi.com"):
            link = dashboard_link("dataset", "d-1", label="D")
        assert link == "[D](https://app.futureagi.com/dashboard/develop/d-1)"
