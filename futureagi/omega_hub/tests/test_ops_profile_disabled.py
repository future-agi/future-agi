"""An explicitly disabled Hub does no operations work; an enabled Hub still pages."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.utils import timezone

from omega_hub import conf
from omega_hub.services import ops

pytestmark = pytest.mark.django_db

FILLED_TOKEN = "made-up-ops-profile-checker-token"
DISABLED_PROFILES = [
    pytest.param("", id="empty-profile"),
    pytest.param("full", id="full-only"),
    pytest.param("hubx", id="hubx-only"),
    pytest.param("full,hubx", id="unrelated-profiles"),
]
ENABLED_PROFILES = [
    pytest.param("hub", id="hub"),
    pytest.param("*", id="wildcard"),
    pytest.param("full,hub", id="full-hub"),
    pytest.param(" full , hub ", id="stripped-hub"),
]
TOKENS = [pytest.param("", id="empty-token"), pytest.param(FILLED_TOKEN, id="filled-token")]
INVALID_TOKENS = [
    pytest.param(None, id="missing-token"),
    pytest.param("", id="empty-token"),
    pytest.param("   ", id="blank-token"),
    pytest.param("local-dev-only-hub-checker-token-replace-me", id="placeholder-token"),
]


@pytest.fixture
def production_ops(hub_settings, monkeypatch):
    hub_settings.ENV_TYPE = "production"
    monkeypatch.delenv("OMEGA_HUB_COMPOSE_PROFILES", raising=False)
    monkeypatch.delenv("OMEGA_HUB_CHECKER_TOKEN", raising=False)

    # Benign results let the unfixed base reach assertions, not mock/DB errors.
    snapshot = Mock(side_effect=lambda **kwargs: {
        "omega_hub_checker_queued_jobs": 0,
        "omega_hub_checker_oldest_queued_age_seconds": 0,
        "omega_hub_checker_configured": float(bool(conf.checker_token())),
        "omega_hub_bundle_locks_overdue": 0,
    })
    report_filter = Mock()
    report_filter.return_value.order_by.return_value.values_list.return_value.first.return_value = None
    reap = Mock(return_value=0)
    reconcile = Mock(return_value={"ready": True, "kind": "object"})
    remember = Mock()
    notify = Mock(return_value=True)
    cache = Mock()
    cache.get.return_value = None
    cache.add.return_value = True
    page = Mock(wraps=ops.page)
    monkeypatch.setattr(ops.metrics, "snapshot", snapshot)
    monkeypatch.setattr(ops.Report.objects, "filter", report_filter)
    monkeypatch.setattr(ops.checks, "reap_expired_leases", reap)
    monkeypatch.setattr(ops.storage_provision, "reconcile", reconcile)
    monkeypatch.setattr(ops.storage_provision, "remember", remember)
    monkeypatch.setattr(ops.notify, "ops_alert", notify)
    monkeypatch.setattr(ops, "cache", cache)
    monkeypatch.setattr(ops, "page", page)
    return SimpleNamespace(
        snapshot=snapshot, report_filter=report_filter, reap=reap,
        reconcile=reconcile, remember=remember, notify=notify, cache=cache, page=page,
    )


def _assert_no_work(calls):
    for operation in (
        calls.snapshot, calls.report_filter, calls.reap, calls.reconcile,
        calls.remember, calls.notify, calls.page,
    ):
        operation.assert_not_called()
    assert calls.cache.mock_calls == []


def _assert_checker_alert(found):
    assert [alert["id"] for alert in found] == ["omega_hub_checker_not_configured"]
    assert found[0]["severity"] == "critical"


@pytest.mark.parametrize("profiles", DISABLED_PROFILES)
@pytest.mark.parametrize("token", TOKENS)
def test_disabled_alerts_do_no_work(production_ops, hub_settings, monkeypatch, profiles, token):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = token
    # An explicit Django override must win over an enabled environment marker.
    monkeypatch.setenv("OMEGA_HUB_COMPOSE_PROFILES", "hub")
    assert conf.checker_token() is None
    assert ops.alerts() == []
    _assert_no_work(production_ops)


@pytest.mark.parametrize("profiles", DISABLED_PROFILES)
@pytest.mark.parametrize("token", TOKENS)
def test_disabled_check_and_page_do_no_work(production_ops, hub_settings, monkeypatch, profiles, token):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = token
    monkeypatch.setenv("OMEGA_HUB_COMPOSE_PROFILES", "hub")
    result = ops.check_and_page()
    assert set(result) == {"reaped", "alerts", "storage", "paged"}
    assert result["reaped"] == 0
    assert result["alerts"] == []
    assert result["paged"] == 0
    assert result["storage"] == {"skipped": True, "reason": "profile_disabled"}
    _assert_no_work(production_ops)


@pytest.mark.parametrize("profiles", ENABLED_PROFILES)
@pytest.mark.parametrize("token", INVALID_TOKENS)
def test_enabled_invalid_token_still_alerts_and_pages(production_ops, hub_settings, monkeypatch, profiles, token):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = token
    monkeypatch.setenv("OMEGA_HUB_COMPOSE_PROFILES", "")
    assert conf.checker_token() is None
    now = timezone.now()
    _assert_checker_alert(ops.alerts(now=now))
    result = ops.check_and_page(now=now)
    _assert_checker_alert(result["alerts"])
    assert result["reaped"] == 0
    assert result["storage"] == production_ops.reconcile.return_value
    assert result["paged"] == 1
    assert production_ops.snapshot.call_count == 2
    assert production_ops.report_filter.call_count == 2
    production_ops.reap.assert_called_once_with(now=now)
    production_ops.reconcile.assert_called_once_with()
    production_ops.remember.assert_called_once_with(result["storage"])
    production_ops.page.assert_called_once_with(result["alerts"])
    production_ops.notify.assert_called_once_with(result["alerts"][0])
    production_ops.cache.set.assert_called_once_with(
        "omega_hub:alert-paged:omega_hub_checker_not_configured", True, timeout=3600,
    )


@pytest.mark.parametrize("token", TOKENS)
def test_absent_marker_keeps_helm_operations_enabled(production_ops, hub_settings, token):
    # None means no settings marker; production_ops also removes the env marker.
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = None
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = token
    assert conf.checker_token() == (token or None)
    now = timezone.now()
    result = ops.check_and_page(now=now)
    if token:
        assert result["alerts"] == []
        assert result["paged"] == 0
        production_ops.notify.assert_not_called()
    else:
        _assert_checker_alert(result["alerts"])
        assert result["paged"] == 1
        production_ops.notify.assert_called_once_with(result["alerts"][0])
    assert result["storage"] == production_ops.reconcile.return_value
    production_ops.snapshot.assert_called_once_with(now=now)
    production_ops.report_filter.assert_called_once()
    production_ops.reap.assert_called_once_with(now=now)
    production_ops.reconcile.assert_called_once_with()
    production_ops.remember.assert_called_once_with(result["storage"])
    production_ops.page.assert_called_once_with(result["alerts"])


@pytest.mark.parametrize("profiles,enabled", [
    pytest.param("", False, id="empty-env"),
    pytest.param("full,hubx", False, id="unrelated-env"),
    pytest.param("hub", True, id="hub-env"),
    pytest.param("*", True, id="wildcard-env"),
    pytest.param(" full , hub ", True, id="stripped-hub-env"),
])
def test_checker_token_preserves_environment_fallback(production_ops, hub_settings, monkeypatch, profiles, enabled):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = None
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = None
    monkeypatch.setenv("OMEGA_HUB_COMPOSE_PROFILES", profiles)
    monkeypatch.setenv("OMEGA_HUB_CHECKER_TOKEN", FILLED_TOKEN)
    assert conf.checker_token() == (FILLED_TOKEN if enabled else None)


@pytest.mark.parametrize("profiles", ENABLED_PROFILES)
def test_enabled_checker_token_is_returned_unchanged(production_ops, hub_settings, profiles):
    hub_settings.OMEGA_HUB_COMPOSE_PROFILES = profiles
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = f" {FILLED_TOKEN} "
    assert conf.checker_token() == f" {FILLED_TOKEN} "
