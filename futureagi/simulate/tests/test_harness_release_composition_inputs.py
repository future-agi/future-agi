from types import SimpleNamespace

import pytest

from simulate.services import harness_composition_edits as edits
from simulate.services import harness_releases as releases
from simulate.services import harness_run_report as reports
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_designs import _archive


def _job(composition):
    return SimpleNamespace(
        id="test", payload={"metadata": {"release_composition": composition}}
    )


def test_composition_revision_and_workspace_share_the_required_release():
    composition = {"suite": {"digest": "suite-v1"}}
    assert edits.composition_revision(_job(composition)) == "suite-v1"
    for operation in (edits.composition_revision, edits.composition_workspace_archive):
        with pytest.raises(HostedHarnessError) as error:
            operation(_job(None))
        assert error.value.code == "composition_invalid"


def test_release_archive_preserves_empty_and_nested_files():
    files = {"empty.txt": b"", "world/world.sqlite": b"world"}
    assert releases.archive_files(_archive(files)) == files


def test_composition_with_no_environment_releases_has_no_stage_outputs():
    assert releases.composition_stage_outputs(_job({"environment": []})) == []


def test_run_report_handles_a_missing_suite(monkeypatch):
    monkeypatch.setattr(reports, "_visible", lambda *args: None)
    run = _job({"suite": {"digest": "deleted"}})
    with pytest.raises(HostedHarnessError) as error:
        reports.run_report(
            SimpleNamespace(organization=None), run, {"suite": {"digest": "deleted"}}
        )
    assert error.value.code == "release_not_found"
