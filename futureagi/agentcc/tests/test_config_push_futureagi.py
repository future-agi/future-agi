from unittest.mock import MagicMock, patch

import pytest

from agentcc.services.config_push import _inject_fi_credentials


@pytest.mark.parametrize("base_url", [None, "https://platform.example/"])
def test_local_futureagi_check_is_platform_owned(settings, base_url):
    settings.BASE_URL = "https://platform.example"
    cfg = {"api_key": "wrong-key", "secret_key": "wrong-secret", "platform": False}
    if base_url is not None:
        cfg["base_url"] = base_url
    checks = {"futureagi-eval": {"enabled": True, "config": cfg}}
    org_key = MagicMock(api_key="org-key", secret_key="org-secret")

    with patch("accounts.models.user.OrgApiKey.no_workspace_objects.filter") as keys:
        keys.return_value.first.return_value = org_key
        _inject_fi_credentials(checks, "org-1")

    assert cfg["platform"] is True
    assert cfg["api_key"] == "org-key"
    assert cfg["secret_key"] == "org-secret"
    assert cfg["base_url"] == (base_url or settings.BASE_URL)
    assert cfg["call_type"] == "protect"
    keys.assert_called_once_with(
        organization_id="org-1", type="system", enabled=True, deleted=False
    )


@pytest.mark.parametrize("spoof_platform", [False, True])
def test_cross_env_futureagi_check_is_not_platform_owned(settings, spoof_platform):
    settings.BASE_URL = "https://platform.example"
    cfg = {
        "base_url": "https://other.example",
        "api_key": "user-key",
        "secret_key": "user-secret",
        "call_type": "eval",
    }
    if spoof_platform:
        cfg["platform"] = True
    checks = {"futureagi-eval": {"enabled": True, "config": cfg}}

    with patch("accounts.models.user.OrgApiKey.no_workspace_objects.filter") as keys:
        _inject_fi_credentials(checks, "org-1")

    assert cfg["platform"] is False
    assert cfg["base_url"] == "https://other.example"
    assert cfg["api_key"] == "user-key"
    assert cfg["secret_key"] == "user-secret"
    assert cfg["call_type"] == "eval"
    keys.assert_not_called()
