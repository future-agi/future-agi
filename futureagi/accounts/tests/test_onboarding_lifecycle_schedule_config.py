import json
from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command

from accounts.services.onboarding import lifecycle_schedule_config


def _report(**overrides):
    values = {
        "path": "/tmp/report.json",
        "sha256": "c" * 64,
        "command_name": "run_onboarding_lifecycle_send",
        "cohort": "internal",
        "limit": 25,
        "campaign_group": "first_action",
        "user_id": None,
        "workspace_id": "workspace-1",
        "require_campaign_group_allowlist": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _patch_artifact_loaders(monkeypatch, *, report=None):
    calls = {}
    approval = SimpleNamespace(
        manifest_sha256="a" * 64,
        approval_record_sha256="b" * 64,
    )
    dry_run_review = SimpleNamespace(
        report=report or _report(),
        review_record_sha256="d" * 64,
    )

    monkeypatch.setattr(
        lifecycle_schedule_config,
        "load_lifecycle_preview_approval",
        lambda manifest_path, approval_record_path: approval,
    )
    monkeypatch.setattr(
        lifecycle_schedule_config,
        "load_lifecycle_send_dry_run_report_review",
        lambda **kwargs: dry_run_review,
    )

    def _load_launch_packet(path, **kwargs):
        calls["launch_packet"] = {"path": path, **kwargs}
        return SimpleNamespace(sha256="e" * 64)

    monkeypatch.setattr(
        lifecycle_schedule_config,
        "load_lifecycle_launch_packet",
        _load_launch_packet,
    )
    return calls


def test_schedule_config_env_is_derived_from_validated_launch_artifacts(monkeypatch):
    calls = _patch_artifact_loaders(monkeypatch)

    result = lifecycle_schedule_config.lifecycle_send_schedule_config_result(
        approval_manifest_path="/tmp/previews/manifest.json",
        approval_record_path="/tmp/previews/approval-record.json",
        dry_run_report_path="/tmp/reports/dry-run.json",
        dry_run_report_review_record_path="/tmp/reports/dry-run-review.json",
        launch_packet_path="/tmp/reports/launch-packet.json",
        enable=True,
        max_limit=50,
    )

    assert result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED"] == "true"
    assert result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_COHORT"] == "internal"
    assert result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_LIMIT"] == "25"
    assert result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT"] == "50"
    assert (
        result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_CAMPAIGN_GROUP"]
        == "first_action"
    )
    assert result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_WORKSPACE_ID"] == (
        "workspace-1"
    )
    assert result.artifacts["launch_packet"]["sha256"] == "e" * 64
    assert calls["launch_packet"]["require_ready"] is True
    assert calls["launch_packet"]["dry_run_report_sha256"] == "c" * 64
    assert "ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED=true" in (result.to_env_text())


def test_schedule_config_stays_disabled_by_default(monkeypatch):
    _patch_artifact_loaders(monkeypatch, report=_report(limit=7))

    result = lifecycle_schedule_config.lifecycle_send_schedule_config_result(
        approval_manifest_path="/tmp/manifest.json",
        approval_record_path="/tmp/approval-record.json",
        dry_run_report_path="/tmp/dry-run.json",
        dry_run_report_review_record_path="/tmp/dry-run-review.json",
        launch_packet_path="/tmp/launch-packet.json",
    )

    assert result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_ENABLED"] == "false"
    assert result.env["ONBOARDING_LIFECYCLE_SEND_SCHEDULE_MAX_LIMIT"] == "100"


def test_schedule_config_rejects_limit_above_max(monkeypatch):
    _patch_artifact_loaders(monkeypatch, report=_report(limit=25))

    with pytest.raises(ImproperlyConfigured, match="cannot exceed max limit"):
        lifecycle_schedule_config.lifecycle_send_schedule_config_result(
            approval_manifest_path="/tmp/manifest.json",
            approval_record_path="/tmp/approval-record.json",
            dry_run_report_path="/tmp/dry-run.json",
            dry_run_report_review_record_path="/tmp/dry-run-review.json",
            launch_packet_path="/tmp/launch-packet.json",
            max_limit=10,
        )


def test_schedule_config_rejects_limit_above_default_max(monkeypatch):
    _patch_artifact_loaders(monkeypatch, report=_report(limit=101))

    with pytest.raises(ImproperlyConfigured, match="cannot exceed max limit"):
        lifecycle_schedule_config.lifecycle_send_schedule_config_result(
            approval_manifest_path="/tmp/manifest.json",
            approval_record_path="/tmp/approval-record.json",
            dry_run_report_path="/tmp/dry-run.json",
            dry_run_report_review_record_path="/tmp/dry-run-review.json",
            launch_packet_path="/tmp/launch-packet.json",
        )


def test_schedule_config_command_writes_json(monkeypatch, tmp_path):
    _patch_artifact_loaders(monkeypatch)
    output_path = tmp_path / "schedule-config.json"
    output = StringIO()

    call_command(
        "generate_onboarding_lifecycle_send_schedule_config",
        "--approval-manifest",
        "/tmp/manifest.json",
        "--approval-record",
        "/tmp/approval-record.json",
        "--dry-run-report",
        "/tmp/dry-run.json",
        "--dry-run-report-review-record",
        "/tmp/dry-run-review.json",
        "--launch-packet",
        "/tmp/launch-packet.json",
        "--format",
        "json",
        "--output",
        str(output_path),
        "--enable",
        stdout=output,
    )

    payload = json.loads(output_path.read_text())
    assert payload["source"] == "onboarding_lifecycle_send_schedule_config"
    assert payload["enabled"] is True
    assert payload["env"]["ONBOARDING_LIFECYCLE_SEND_LAUNCH_PACKET"] == (
        "/tmp/launch-packet.json"
    )
    assert "launch_packet_sha256=" in output.getvalue()
