import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

EXPECTED_ARTIFACTS = {
    "approval_manifest": "previews/manifest.json",
    "approval_record": "previews/approval-record.json",
    "dry_run_report": "welcome-dry-run-report.json",
    "dry_run_report_review": "welcome-dry-run-report-review.json",
    "launch_packet": "launch-packet.json",
    "send_evidence_report": "send-evidence-report.json",
    "launch_review": "launch-review.json",
}


def _generate_fixture_pack(output_dir, *extra_args):
    output = StringIO()
    call_command(
        "generate_onboarding_lifecycle_evidence_fixture_pack",
        "--output-dir",
        str(output_dir),
        "--now",
        "2026-05-31T10:00:00Z",
        *extra_args,
        stdout=output,
    )
    return output.getvalue()


@pytest.mark.django_db
def test_lifecycle_evidence_fixture_pack_writes_reviewable_passed_artifacts(tmp_path):
    output_dir = tmp_path / "fixture-pack"

    output = _generate_fixture_pack(output_dir)

    assert f"output_dir={output_dir}" in output
    assert "status=passed" in output

    expected_paths = {
        key: output_dir / relative_path
        for key, relative_path in EXPECTED_ARTIFACTS.items()
    }
    for path in expected_paths.values():
        assert path.exists(), path

    summary_path = output_dir / "fixture-summary.json"
    summary = json.loads(summary_path.read_text())
    send_evidence = json.loads(expected_paths["send_evidence_report"].read_text())
    launch_review = json.loads(expected_paths["launch_review"].read_text())

    assert summary["status"] == "passed"
    assert summary["source"] == "onboarding_lifecycle_evidence_fixture_pack"
    assert summary["generated_at"] == "2026-05-31T10:09:00+00:00"
    assert set(summary["artifacts"]) == set(EXPECTED_ARTIFACTS)
    assert len(summary["send_log_ids"]) == 3

    assert send_evidence["status"] == "passed"
    assert send_evidence["missing_requirements"] == []
    assert send_evidence["send_log_count"] == 3
    assert all(send_evidence["requirements"].values())
    assert all(send_evidence["aggregate_evidence"].values())
    assert send_evidence["aggregate_evidence"]["receipt_backed"] is True
    assert any(
        item["receipt"]["is_receipt_backed"] for item in send_evidence["send_logs"]
    )

    assert launch_review["status"] == "passed"
    assert launch_review["missing_checks"] == []
    assert launch_review["checks"]["send_evidence_report_passed"] is True
    assert launch_review["checks"]["send_evidence_references_launch_packet"] is True
    assert all(launch_review["evidence"]["requirements"].values())
    assert all(launch_review["evidence"]["aggregate_evidence"].values())

    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in output_dir.rglob("*")
        if path.is_file()
    )
    assert "lifecycle-fixture+" not in artifact_text
    assert "Lifecycle Fixture" not in artifact_text


@pytest.mark.django_db
def test_lifecycle_evidence_fixture_pack_force_overwrites_artifacts(tmp_path):
    output_dir = tmp_path / "fixture-pack"
    _generate_fixture_pack(output_dir)

    output = _generate_fixture_pack(output_dir, "--force")

    assert "status=passed" in output
    assert (output_dir / "fixture-summary.json").exists()


@override_settings(TESTING=False)
def test_lifecycle_evidence_fixture_pack_refuses_non_test_database(tmp_path):
    with pytest.raises(CommandError, match="writes synthetic rows"):
        _generate_fixture_pack(tmp_path / "fixture-pack")
