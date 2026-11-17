"""Tests for identity-scoped, fail-closed privacy policy resolution."""

from types import SimpleNamespace
from unittest import mock

import pytest
from django.core.cache import cache

from tracer.utils.pii_scrubber import PIIScrubError
from tracer.utils.pii_settings import (
    _cache_key,
    get_pii_settings_for_projects,
    invalidate_pii_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestGetPiiSettingsForProjects:
    def test_cache_hit(self):
        project = SimpleNamespace(id="id-a", metadata={"pii_redaction_enabled": True})
        cache.set(_cache_key("org1", "id-a"), True, 300)
        with (
            mock.patch("tracer.utils.otel.get_or_create_project", return_value=project),
            mock.patch("tracer.utils.pii_settings.cache.set") as write,
        ):
            assert get_pii_settings_for_projects({"proj-a"}, "org1") == {"proj-a": True}
        write.assert_not_called()

    def test_cache_miss_queries_db(self):
        projects = {
            "proj-a": SimpleNamespace(id="id-a", metadata={"pii_redaction_enabled": True}),
            "proj-b": SimpleNamespace(id="id-b", metadata={"pii_redaction_enabled": False}),
        }
        with mock.patch(
            "tracer.utils.otel.get_or_create_project",
            side_effect=lambda name, *args: projects[name],
        ) as resolve:
            result = get_pii_settings_for_projects({"proj-a", "proj-b"}, "org1")
        assert result == {"proj-a": True, "proj-b": False}
        resolve.assert_has_calls([
            mock.call("proj-a", "org1", "experiment", None, None),
            mock.call("proj-b", "org1", "experiment", None, None),
        ], any_order=True)

    def test_unresolved_project_rejects_batch(self):
        with (
            mock.patch("tracer.utils.otel.get_or_create_project", return_value=None),
            pytest.raises(PIIScrubError),
        ):
            get_pii_settings_for_projects({"proj-missing"}, "org1")

    def test_db_values_are_cached(self):
        project = SimpleNamespace(id="id-a", metadata={"pii_redaction_enabled": True})
        with mock.patch("tracer.utils.otel.get_or_create_project", return_value=project):
            get_pii_settings_for_projects({"proj-a"}, "org1")
        assert cache.get(_cache_key("org1", "id-a")) is True

    def test_fail_closed_on_db_error(self):
        with (
            mock.patch("tracer.utils.otel.get_or_create_project", side_effect=RuntimeError("DB down")),
            pytest.raises(PIIScrubError),
        ):
            get_pii_settings_for_projects({"proj-a"}, "org1")

    def test_null_metadata_defaults_false(self):
        # A successfully resolved, genuinely unconfigured project is disabled.
        project = SimpleNamespace(id="id-a", metadata=None)
        with mock.patch("tracer.utils.otel.get_or_create_project", return_value=project):
            assert get_pii_settings_for_projects({"proj-a"}, "org1") == {"proj-a": False}

    @pytest.mark.parametrize("metadata", [["imported"], "production"], ids=["list", "string"])
    @pytest.mark.parametrize("include_modes", [False, True], ids=["boolean", "policy"])
    def test_legacy_non_object_metadata_defaults_disabled(self, metadata, include_modes):
        project = SimpleNamespace(id="id-a", metadata=metadata)
        resolved = {}
        with mock.patch("tracer.utils.otel.get_or_create_project", return_value=project):
            result = get_pii_settings_for_projects(
                {"proj-a"}, "org1", include_modes=include_modes, resolved_projects=resolved,
            )
        expected = {"enabled": False, "mode": "mask_pii"} if include_modes else False
        assert result == {"proj-a": expected}
        assert cache.get(_cache_key("org1", "id-a", include_modes)) == expected
        assert resolved["proj-a"] is project
        assert project.metadata == metadata

    @pytest.mark.parametrize("operation", ["get", "set"])
    def test_cache_failure_keeps_successful_policy(self, operation):
        project = SimpleNamespace(id="id-a", metadata={
            "pii_redaction_enabled": True, "pii_redaction_mode": "hide_io",
        })
        with (
            mock.patch("tracer.utils.otel.get_or_create_project", return_value=project),
            mock.patch(f"tracer.utils.pii_settings.cache.{operation}", side_effect=RuntimeError("cache down")),
        ):
            result = get_pii_settings_for_projects({"proj-a"}, "org1", include_modes=True)
        assert result == {"proj-a": {"enabled": True, "mode": "hide_io"}}

    def test_stale_disabled_cache_cannot_override_resolved_policy(self):
        project = SimpleNamespace(id="id-a", metadata={
            "pii_redaction_enabled": True, "pii_redaction_mode": "hide_io",
        })
        cache.set(_cache_key("org1", "id-a", True), {"enabled": False, "mode": "mask_pii"})
        with mock.patch("tracer.utils.otel.get_or_create_project", return_value=project):
            result = get_pii_settings_for_projects({"proj-a"}, "org1", include_modes=True)
        assert result["proj-a"] == {"enabled": True, "mode": "hide_io"}


class TestInvalidatePiiCache:
    def test_invalidate(self):
        for modes in (False, True):
            cache.set(_cache_key("org1", "id-a", modes), True, 300)
        invalidate_pii_cache("org1", "id-a")
        for modes in (False, True):
            assert cache.get(_cache_key("org1", "id-a", modes)) is None

    def test_invalidate_nonexistent_key(self):
        invalidate_pii_cache("org1", "nonexistent")
