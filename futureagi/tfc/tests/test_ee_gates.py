"""Tests for the Turing/Protect runtime gates in `tfc.ee_gates`.

These gates block Turing/Protect model selections that would otherwise route
into ee/turing code the deployment can't serve. The gating decision now runs
through the capability service (`_turing_denied_off_cloud`) rather than a raw
`DeploymentMode.is_oss()` probe, so this covers:

- cloud is a no-op (per-org entitlement is enforced by the usage layer),
- self-hosted denies when `turing_models` isn't licensed (the case the old
  is_oss() probe missed for EE installs without Turing),
- self-hosted allows when it is licensed,
- early-startup falls back to the deployment probe,
- the public `turing_oss_gate_response` / `strip_turing_from_config_options`
  honour that decision.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from tfc import ee_gates
from tfc.licensing.types import DeploymentLocation


def _cfg(location, *, allowed=True):
    """Patch the capability service into a configured state at `location`,
    with check("turing_models") returning `allowed`."""
    return (
        patch("tfc.capabilities.service.is_configured", return_value=True),
        patch(
            "tfc.capabilities.service.get_deployment_location",
            return_value=location,
        ),
        patch(
            "tfc.capabilities.service.check",
            return_value=SimpleNamespace(allowed=allowed),
        ),
    )


# ── _turing_denied_off_cloud() ───────────────────────────────────────────
def test_turing_denied_off_cloud_is_noop_on_cloud():
    cfg, loc, chk = _cfg(DeploymentLocation.CLOUD)
    with cfg, loc, chk as check:
        assert ee_gates._turing_denied_off_cloud() is False
    # Cloud must not consult the license/plan check here — the usage layer
    # owns per-org Turing entitlement.
    check.assert_not_called()


def test_turing_denied_off_cloud_true_when_self_hosted_unlicensed():
    cfg, loc, chk = _cfg(DeploymentLocation.SELF_HOSTED, allowed=False)
    with cfg, loc, chk as check:
        assert ee_gates._turing_denied_off_cloud() is True
    check.assert_called_once_with("turing_models")


def test_turing_denied_off_cloud_false_when_self_hosted_licensed():
    cfg, loc, chk = _cfg(DeploymentLocation.SELF_HOSTED, allowed=True)
    with cfg, loc, chk:
        assert ee_gates._turing_denied_off_cloud() is False


def test_turing_denied_off_cloud_falls_back_to_is_oss_before_configured():
    """Before AppConfig.ready wires the service, fall back to the deployment
    probe rather than the (unconfigured, always-deny) capability service."""
    with patch("tfc.capabilities.service.is_configured", return_value=False):
        with patch("tfc.ee_gates._is_oss", return_value=True):
            assert ee_gates._turing_denied_off_cloud() is True
        with patch("tfc.ee_gates._is_oss", return_value=False):
            assert ee_gates._turing_denied_off_cloud() is False


# ── turing_oss_gate_response() ───────────────────────────────────────────
def test_gate_passes_non_turing_model_regardless():
    # Non-Turing models never route into ee/turing code, so the gate is a
    # no-op even when Turing would be denied.
    with patch("tfc.ee_gates._turing_denied_off_cloud", return_value=True):
        assert ee_gates.turing_oss_gate_response("gpt-4o") is None


def test_gate_402s_turing_model_when_denied():
    with patch("tfc.ee_gates._turing_denied_off_cloud", return_value=True):
        resp = ee_gates.turing_oss_gate_response("turing_large")
    assert resp is not None
    assert resp.status_code == 402
    assert resp.data["feature"] == "turing"
    assert resp.data["upgrade_required"] is True
    assert resp.data["code"] == "ENTITLEMENT_DENIED"


def test_gate_passes_turing_model_when_not_denied():
    with patch("tfc.ee_gates._turing_denied_off_cloud", return_value=False):
        assert ee_gates.turing_oss_gate_response("turing_large") is None


# ── strip_turing_from_config_options() ───────────────────────────────────
def test_strip_removes_turing_when_denied():
    opts = {"model": ["gpt-4o", "turing_large", "claude-3-5-sonnet", "protect"]}
    with patch("tfc.ee_gates._turing_denied_off_cloud", return_value=True):
        out = ee_gates.strip_turing_from_config_options(opts)
    assert out["model"] == ["gpt-4o", "claude-3-5-sonnet"]
    # original not mutated
    assert "turing_large" in opts["model"]


def test_strip_is_noop_when_not_denied():
    opts = {"model": ["gpt-4o", "turing_large"]}
    with patch("tfc.ee_gates._turing_denied_off_cloud", return_value=False):
        out = ee_gates.strip_turing_from_config_options(opts)
    assert out is opts


def test_strip_handles_empty_and_missing_model_key():
    with patch("tfc.ee_gates._turing_denied_off_cloud", return_value=True):
        assert ee_gates.strip_turing_from_config_options(None) == {}
        passthrough = {"temperature": [0.0, 1.0]}
        assert ee_gates.strip_turing_from_config_options(passthrough) is passthrough
