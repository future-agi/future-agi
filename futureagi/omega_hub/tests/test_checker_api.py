"""The checker's pull endpoints (SPEC §4.1, §9.4): secret, leases, retries and result integrity."""

from datetime import timedelta

import pytest
from django.utils import timezone

from omega_hub.models import CheckJob, Submission
from omega_hub.tests.helpers import (build_report, checker_client, entry_files, post_result, submit, token_client)

pytestmark = pytest.mark.django_db


def queued(world, name="first-agent", version="1.0.0"):
    files = entry_files(name, manifest={"version": version})
    response = submit(token_client(world.token_a), "future-agi", files, name=name, version=version)
    return response.json()["id"], files


def test_checker_needs_its_secret(world, hub_settings):
    from rest_framework.test import APIClient

    assert APIClient().post("/omega-hub/internal/checker/claim").status_code == 401
    assert world.b.post("/omega-hub/internal/checker/claim").status_code == 401
    hub_settings.OMEGA_HUB_CHECKER_TOKEN = None
    assert checker_client().post("/omega-hub/internal/checker/claim").status_code == 503


def test_idle_claim_is_204(world):
    assert checker_client().post("/omega-hub/internal/checker/claim").status_code == 204


def test_claim_hands_out_the_raw_upload_and_hints(world):
    sub_id, files = queued(world)
    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    assert job["submission"] == sub_id and job["hints"] == {"name": "first-agent", "version": "1.0.0"}
    assert job["publisherTier"] == "official" and job["previousVersion"] is None
    raw = checker.get(job["bundleUrl"])
    assert raw.status_code == 200 and raw.content[:2] == b"\x1f\x8b"
    assert Submission.objects.get(pk=sub_id).status == "checking"
    assert checker.post(job["heartbeatUrl"]).status_code == 200


def test_expired_lease_is_requeued_and_errors_after_three_attempts(world):
    sub_id, _ = queued(world)
    checker = checker_client()
    for attempt in (1, 2, 3):
        job = checker.post("/omega-hub/internal/checker/claim").json()
        assert job["attempt"] == attempt
        CheckJob.objects.filter(pk=job["job"]).update(lease_expires_at=timezone.now() - timedelta(minutes=1))
    assert checker.post("/omega-hub/internal/checker/claim").status_code == 204
    assert Submission.objects.get(pk=sub_id).status == "check_error"
    assert CheckJob.objects.get(submission_id=sub_id).status == "errored"
    # a maintainer can run them again
    assert world.b.post(f"/omega-hub/v1/review/submissions/{sub_id}/recheck").status_code == 200
    assert checker.post("/omega-hub/internal/checker/claim").status_code == 200


def test_result_with_another_bundle_is_refused(world):
    _, files = queued(world)
    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    report, _ = build_report(job, files)
    _, other_bundle = build_report(job, entry_files("notes-plugin"))
    response = post_result(checker, job, report, other_bundle)
    assert response.status_code == 400 and response.json()["error"]["code"] == "OMEGA_HUB_BUNDLE_MISMATCH"


def test_result_after_the_lease_moved_is_refused(world):
    _, files = queued(world)
    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    CheckJob.objects.filter(pk=job["job"]).update(leased_by="another-checker")
    report, bundle = build_report(job, files)
    response = post_result(checker, job, report, bundle)
    assert response.status_code == 409 and response.json()["error"]["code"] == "OMEGA_HUB_LEASE_LOST"


def test_an_error_result_requeues(world):
    sub_id, files = queued(world)
    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    report, _ = build_report(job, files, outcome="error")
    report["error"] = "docker daemon unavailable"
    assert post_result(checker, job, report, None).status_code == 200
    assert CheckJob.objects.get(pk=job["job"]).status == "queued"
    assert Submission.objects.get(pk=sub_id).status == "checking"


def test_a_newer_upload_supersedes_the_older_one(world):
    old_id, files = queued(world)
    new_id, _ = queued(world)
    assert Submission.objects.get(pk=old_id).status == "superseded"
    assert Submission.objects.get(pk=old_id).superseded_by_id is not None
    assert Submission.objects.get(pk=new_id).status == "received"


def test_three_uploads_waiting_is_the_limit(world):
    for version in ("1.0.0", "1.0.1", "1.0.2"):
        queued(world, version=version)
    files = entry_files("first-agent", manifest={"version": "1.0.3"})
    fourth = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.3")
    assert fourth.status_code == 429 and fourth.json()["error"]["code"] == "OMEGA_HUB_TOO_MANY_OPEN"


def test_upload_must_be_gzip_or_zip_and_small(world):
    from django.core.files.uploadedfile import SimpleUploadedFile

    client = token_client(world.token_a)
    bad = client.post("/omega-hub/v1/submissions", {"publisher": "future-agi",
                                                    "bundle": SimpleUploadedFile("x.txt", b"hello")}, format="multipart")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "OMEGA_HUB_BUNDLE_TYPE"
    big = client.post("/omega-hub/v1/submissions", {"publisher": "future-agi",
                                                    "bundle": SimpleUploadedFile("x.tgz", b"\x1f\x8b" + b"0" * (2 * 1024 * 1024))},
                      format="multipart")
    assert big.status_code == 413


def test_upload_and_publish_lock_the_publisher_without_blocking_claims(world):
    """A claim's audit insert takes FOR KEY SHARE on the publisher row. An upload holding FOR UPDATE on
    that row while supersede_older waited on the submission the claim had just moved deadlocked (seen
    as a 500 on /internal/checker/claim during a 27-entry import). FOR NO KEY UPDATE does not conflict
    with FOR KEY SHARE and still serialises uploads per publisher."""
    from django.db import connection, reset_queries
    from django.test.utils import CaptureQueriesContext

    from omega_hub.tests.flows import disclosure_of
    from omega_hub.tests.helpers import approve_payload, run_checks

    def publisher_locks(action):
        reset_queries()  # the query log is a bounded deque; start empty so the slice is this action's
        with CaptureQueriesContext(connection) as captured:
            result = action()
        sql = [q["sql"] for q in captured.captured_queries]
        return result, [q for q in sql if 'FROM "omega_hub_publisher"' in q and " FOR " in q]

    (sub_id, files), upload = publisher_locks(lambda: queued(world))
    _, report, _ = run_checks(files)
    decided, approve = publisher_locks(lambda: world.b.post(
        f"/omega-hub/v1/review/submissions/{sub_id}/decision",
        approve_payload(report, disclosure=disclosure_of("first-agent")), format="json"))
    assert decided.status_code == 200, decided.content
    assert any("LIMIT 1 FOR" in q for q in upload), upload  # intake.receive's lock
    assert any("LIMIT 21 FOR" in q for q in approve), approve  # publish()'s lock
    for locks in (upload, approve):
        assert all("FOR NO KEY UPDATE" in q for q in locks), locks


def test_a_paused_hub_drains_a_running_check_and_starts_no_new_one(world):
    """READINESS 3.13 kill switch: while the Hub is paused no claim and no heartbeat (the running
    check ends within the lease it holds), but a check that finishes reports once; restoring needs
    no restart and the queue moves again."""
    from omega_rollout.models import ProductRollout

    _, files = queued(world)
    queued(world, name="notes-plugin")
    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    ProductRollout.objects.filter(product="hub").update(paused=True)
    refused = checker.post("/omega-hub/internal/checker/claim")
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "product_paused"
    assert checker.post(job["heartbeatUrl"]).status_code == 503
    assert checker.get(job["bundleUrl"]).status_code == 200
    report, bundle = build_report(job, files)
    assert post_result(checker, job, report, bundle).status_code == 200
    assert CheckJob.objects.get(pk=job["job"]).attempt == 1
    ProductRollout.objects.filter(product="hub").update(paused=False)
    nxt = checker.post("/omega-hub/internal/checker/claim")
    assert nxt.status_code == 200 and nxt.json()["job"] != job["job"]
