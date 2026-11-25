"""Tests for tracer.utils.pii_settings."""

from unittest import mock

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure a clean cache for each test."""
    cache.clear()
    yield
    cache.clear()


class TestGetPiiSettingsForProjects:
    def test_cache_hit(self):
        from tracer.utils.pii_settings import get_pii_settings_for_projects

        cache.set("pii_redaction:org1:proj-a", True, 300)
        result = get_pii_settings_for_projects({"proj-a"}, "org1")
        assert result == {"proj-a": True}

    def test_cache_miss_queries_db(self):
        from tracer.utils.pii_settings import get_pii_settings_for_projects

        # Mock the Project model query
        fake_rows = [
            {"name": "proj-a", "metadata": {"pii_redaction_enabled": True}},
            {"name": "proj-b", "metadata": {"pii_redaction_enabled": False}},
        ]
        with mock.patch("tracer.models.project.Project.objects") as mock_objects:
            mock_qs = mock.MagicMock()
            mock_objects.filter.return_value = mock_qs
            mock_qs.values.return_value = fake_rows

            result = get_pii_settings_for_projects({"proj-a", "proj-b"}, "org1")
            assert result["proj-a"] is True
            assert result["proj-b"] is False

    def test_cache_write_failure_preserves_db_settings(self):
        from tracer.utils.pii_settings import get_pii_settings_for_projects

        with (
            mock.patch("tracer.models.project.Project.objects") as projects,
            mock.patch.object(
                cache, "set", side_effect=[RuntimeError("cache down"), True]
            ) as cache_set,
        ):
            projects.filter.return_value.values.return_value = [
                {"name": "enabled", "metadata": {"pii_redaction_enabled": True}},
                {"name": "disabled", "metadata": {"pii_redaction_enabled": False}},
            ]
            result = get_pii_settings_for_projects({"enabled", "disabled"}, "org1")

        assert result == {"enabled": True, "disabled": False}
        cache_set.assert_has_calls(
            [
                mock.call("pii_redaction:org1:enabled", True, 300),
                mock.call("pii_redaction:org1:disabled", False, 300),
            ],
            any_order=True,
        )

    def test_cache_miss_project_not_in_db(self):
        from tracer.utils.pii_settings import get_pii_settings_for_projects

        with mock.patch("tracer.models.project.Project.objects") as mock_objects:
            mock_qs = mock.MagicMock()
            mock_objects.filter.return_value = mock_qs
            mock_qs.values.return_value = []  # no rows

            result = get_pii_settings_for_projects({"proj-missing"}, "org1")
            assert result["proj-missing"] is False

    def test_db_values_are_cached(self):
        from tracer.utils.pii_settings import get_pii_settings_for_projects

        fake_rows = [
            {"name": "proj-a", "metadata": {"pii_redaction_enabled": True}},
        ]
        with mock.patch("tracer.models.project.Project.objects") as mock_objects:
            mock_qs = mock.MagicMock()
            mock_objects.filter.return_value = mock_qs
            mock_qs.values.return_value = fake_rows

            get_pii_settings_for_projects({"proj-a"}, "org1")

        # Second call should hit cache, not DB
        result = get_pii_settings_for_projects({"proj-a"}, "org1")
        assert result["proj-a"] is True

    def test_db_error_redacts_only_unresolved_projects_without_caching(self):
        from tracer.utils.pii_settings import get_pii_settings_for_projects

        cache.set("pii_redaction:org1:disabled", False, 300)
        with mock.patch("tracer.models.project.Project.objects") as mock_objects:
            mock_objects.filter.side_effect = RuntimeError("DB down")
            result = get_pii_settings_for_projects({"unknown", "disabled"}, "org1")

        assert result == {"unknown": True, "disabled": False}
        assert cache.get("pii_redaction:org1:unknown") is None

    def test_null_metadata_defaults_false(self):
        from tracer.utils.pii_settings import get_pii_settings_for_projects

        fake_rows = [{"name": "proj-a", "metadata": None}]
        with mock.patch("tracer.models.project.Project.objects") as mock_objects:
            mock_qs = mock.MagicMock()
            mock_objects.filter.return_value = mock_qs
            mock_qs.values.return_value = fake_rows

            result = get_pii_settings_for_projects({"proj-a"}, "org1")
            assert result["proj-a"] is False


class TestInvalidatePiiCache:
    def test_invalidate(self):
        from tracer.utils.pii_settings import invalidate_pii_cache

        cache.set("pii_redaction:org1:proj-a", True, 300)
        invalidate_pii_cache("org1", "proj-a")
        assert cache.get("pii_redaction:org1:proj-a") is None

    def test_invalidate_nonexistent_key(self):
        from tracer.utils.pii_settings import invalidate_pii_cache

        # Should not raise
        invalidate_pii_cache("org1", "nonexistent")
