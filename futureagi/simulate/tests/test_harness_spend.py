"""The harness's own cost, as the platform records it from the guest's ledger."""

from __future__ import annotations

import pytest

from simulate.models import HostedHarnessJob
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.hosted_harness_gateway import _record_harness_spend
from simulate.tests.test_hosted_harness_channels import _payload


def _job(organization, metadata=None):
    job, _ = create_hosted_job(
        organization,
        _payload(metadata=metadata or {}),
        idempotency_key="harness-spend",
    )
    return job


def _row(job):
    # The base manager, so a test that patches the named manager's `get` still reads the table.
    return HostedHarnessJob._base_manager.get(id=job.id)


def _spend(job):
    return ((_row(job).payload or {}).get("metadata") or {}).get("harness_spend")


@pytest.mark.django_db
def test_the_total_and_its_stages_land_on_the_job(organization):
    job = _job(organization)
    _record_harness_spend(
        job,
        {
            "total_usd": 0.8123,
            "unpriced_turns": 2,
            "stages": [{"stage": "understand-agent", "usd": 0.8123, "turns": 9}],
        },
    )

    assert _spend(job)["total_usd"] == 0.8123
    assert _spend(job)["unpriced_turns"] == 2
    assert _spend(job)["attempts"]["1"]["stages"] == [
        {"stage": "understand-agent", "usd": 0.8123, "turns": 9}
    ]
    # The caller's own object sees what was written.
    assert job.payload == _row(job).payload


@pytest.mark.django_db
def test_a_later_read_never_lowers_the_bill(organization):
    """A half-written or reset ledger must not erase what a previous poll already saw."""
    job = _job(
        organization,
        {
            "harness_spend": {
                "total_usd": 1.5,
                "unpriced_turns": 0,
                "attempts": {
                    "1": {"total_usd": 1.5, "unpriced_turns": 0, "stages": []}
                },
            }
        },
    )
    before = _row(job)
    _record_harness_spend(job, {"total_usd": 0.2, "stages": []})

    after = _row(job)
    assert _spend(job)["total_usd"] == 1.5
    assert (after.payload, after.updated_at) == (before.payload, before.updated_at)


@pytest.mark.django_db
def test_a_growing_total_replaces_the_earlier_one(organization):
    job = _job(
        organization,
        {
            "harness_spend": {
                "total_usd": 0.5,
                "unpriced_turns": 0,
                "attempts": {
                    "1": {"total_usd": 0.5, "unpriced_turns": 0, "stages": []}
                },
            }
        },
    )
    _record_harness_spend(
        job, {"total_usd": 0.9, "stages": [{"stage": "x", "usd": 0.9}]}
    )

    assert _spend(job)["total_usd"] == 0.9


@pytest.mark.django_db
def test_nothing_readable_is_left_alone(organization):
    """An absent ledger is the ordinary case early in a run, not an error."""
    job = _job(organization)
    before = _row(job)
    for unreadable in (None, "", [], {"total_usd": "not a number"}):
        _record_harness_spend(job, unreadable)
        after = _row(job)
        assert _spend(job) is None
        assert (after.payload, after.updated_at) == (before.payload, before.updated_at)


@pytest.mark.django_db
def test_a_dying_sandbox_is_read_before_it_is_deleted(organization, monkeypatch):
    """The pull has to happen while the sandbox still exists, whatever ended the attempt."""
    from simulate.services import hosted_harness_gateway as gateway

    job = _job(organization)
    read = {}

    class _Files:
        def download_file(self, path):
            read["path"] = path
            return b'{"total_usd": 0.42, "unpriced_turns": 0, "stages": []}'

    class _Sandbox:
        fs = _Files()

    class _Attempt:
        id = "attempt-1"
        job_id = "job-1"
        attempt_number = 1

    monkeypatch.setattr(
        gateway.HostedHarnessJob.no_workspace_objects, "get", lambda **_: job
    )
    gateway._read_harness_spend(_Attempt(), _Sandbox())

    assert read["path"] == "/work/authoring/cost.json"
    assert _spend(job)["total_usd"] == 0.42


def test_a_sandbox_that_cannot_be_read_never_blocks_its_own_deletion(monkeypatch):
    """A leaked sandbox costs more than the figure it was holding."""
    from simulate.services import hosted_harness_gateway as gateway

    class _Files:
        def download_file(self, path):
            raise RuntimeError("sandbox gone")

    class _Sandbox:
        fs = _Files()

    class _Attempt:
        id = "attempt-2"
        job_id = "job-2"
        attempt_number = 1

    gateway._read_harness_spend(_Attempt(), _Sandbox())


@pytest.mark.django_db
def test_a_retry_adds_to_the_bill_instead_of_replacing_it(organization):
    """A retry's ledger starts at zero, so attempts are summed rather than replaced."""
    job = _job(organization)
    _record_harness_spend(job, {"total_usd": 1.2, "unpriced_turns": 1, "stages": []}, 1)
    _record_harness_spend(job, {"total_usd": 0.8, "unpriced_turns": 0, "stages": []}, 2)

    spend = _spend(job)
    assert spend["total_usd"] == 2.0
    assert spend["unpriced_turns"] == 1
    assert sorted(spend["attempts"]) == ["1", "2"]


@pytest.mark.django_db
def test_one_attempt_growing_does_not_disturb_another(organization):
    job = _job(organization)
    _record_harness_spend(job, {"total_usd": 1.0, "stages": []}, 1)
    _record_harness_spend(job, {"total_usd": 0.3, "stages": []}, 2)
    _record_harness_spend(job, {"total_usd": 0.9, "stages": []}, 2)

    spend = _spend(job)
    assert spend["attempts"]["1"]["total_usd"] == 1.0
    assert spend["attempts"]["2"]["total_usd"] == 0.9
    assert spend["total_usd"] == 1.9


def test_the_ledger_is_read_before_the_only_delete_that_exists(monkeypatch):
    """Pins the ordering, so a reordered or second deletion path fails here."""
    from simulate.services import hosted_harness_gateway as gateway

    order = []

    class _Client:
        name = "test-provider"

        # **kwargs so a new option on the real call (request_timeout, say) does not read as a
        # broken ordering invariant.
        def get(self, ref, **kwargs):
            return "sandbox"

        def delete(self, sandbox, **kwargs):
            order.append("delete")

    class _Attempt:
        id = "attempt-3"
        job_id = "job-3"
        attempt_number = 1
        provider_ref = "ref-3"
        # Cleanup only captures diagnostics when a real attempt has not already
        # finalized them. This lightweight test double exercises spend/delete
        # ordering, so model the normal terminal-poll path explicitly.
        diagnostics_final = True

    monkeypatch.setattr(
        gateway, "_read_harness_spend", lambda *_: order.append("read_spend")
    )
    monkeypatch.setattr(gateway, "record_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(
        "simulate.services.harness_usage.record_sandbox_runtime",
        lambda *args, **kwargs: None,
    )

    driver = gateway.HostedHarnessGateway.__new__(gateway.HostedHarnessGateway)
    driver.client = _Client()
    monkeypatch.setattr(driver, "_cleanup_conversation_runtime", lambda job: None)
    driver._delete_and_record(_Attempt())

    assert order == ["read_spend", "delete"]
