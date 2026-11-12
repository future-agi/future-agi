"""Guards on the approval and integrity path that ordinary flows never reach (SPEC §3.2, §4.6, §9.2).

A review mutated each of these guards away and the suite still passed, because another check failed
first or no API path builds the state. Each test here isolates one guard, forcing the state through the
ORM where no API path reaches it, so that removing the guard fails exactly this test.
"""

import gzip
import hashlib

import pytest

from omega_hub.errors import HubError
from omega_hub.models import CheckJob, Entry, Submission, Version
from omega_hub.services import states
from omega_hub.tests.flows import disclosure_of, publish
from omega_hub.tests.helpers import (approve_payload, build_report, checker_client, entry_files, post_result,
                                     run_checks, submit, token_client)

pytestmark = pytest.mark.django_db


def queued(world, name="first-agent"):
    files = entry_files(name)
    created = submit(token_client(world.token_a), "future-agi", files, name=name, version="1.0.0")
    assert created.status_code == 201, created.content
    checker = checker_client()
    job = checker.post("/omega-hub/internal/checker/claim").json()
    return created.json()["id"], files, checker, job


def in_review(world):
    files = entry_files("first-agent")
    created = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.0")
    assert created.status_code == 201, created.content
    _, report, result = run_checks(files)
    assert result["submission"] == "in_review", result
    return Submission.objects.get(pk=created.json()["id"]), report


def test_the_state_machine_refuses_approved_without_the_decision_token(world):
    """states.move is the only writer of status; `approved` needs the token only services.review.decide holds."""
    submission, _ = in_review(world)
    for token in (None, object()):
        with pytest.raises(HubError) as refused:
            states.move(submission, Submission.Status.APPROVED, _approve=token)
        assert refused.value.status == 409 and refused.value.code == "OMEGA_HUB_BAD_STATE"
    submission.refresh_from_db()
    assert submission.status == "in_review"
    assert not Version.objects.exists()


def test_a_recompressed_bundle_with_the_same_files_is_refused_by_its_sha256(world):
    """Same files, same manifest, same digest, different bytes: only the SHA-256 re-hash catches it."""
    sub_id, files, checker, job = queued(world)
    report, bundle = build_report(job, files)
    other = gzip.compress(gzip.decompress(bundle), compresslevel=1, mtime=1)
    assert other != bundle and gzip.decompress(other) == gzip.decompress(bundle)
    assert hashlib.sha256(other).hexdigest() != report["bundleSha256"]
    response = post_result(checker, job, report, other)
    assert response.status_code == 400 and response.json()["error"]["code"] == "OMEGA_HUB_BUNDLE_MISMATCH", response.content
    assert "SHA-256" in response.json()["error"]["message"]
    assert Submission.objects.get(pk=sub_id).status == "checking"


def test_a_report_whose_digest_is_not_the_bundles_is_refused(world):
    """Right bundle, right SHA-256, right manifest: only the catalog-digest re-check catches a wrong digest."""
    sub_id, files, checker, job = queued(world)
    report, bundle = build_report(job, files)
    report["digest"] = "0" * 64
    response = post_result(checker, job, report, bundle)
    assert response.status_code == 400 and response.json()["error"]["code"] == "OMEGA_HUB_BUNDLE_MISMATCH", response.content
    assert "digest" in response.json()["error"]["message"]
    assert Submission.objects.get(pk=sub_id).status == "checking"


def test_approve_needs_the_latest_check_job_to_have_passed(world):
    """No API path leaves an in_review submission whose latest job failed. The job keeps its canonical
    bundle (a failing report may carry one), so publish()'s own no-bundle guard does not mask this one."""
    submission, report = in_review(world)
    job = CheckJob.objects.get(submission=submission)
    assert job.canonical_bundle_key
    CheckJob.objects.filter(pk=job.pk).update(status=CheckJob.Status.FAILED)
    response = world.b.post(f"/omega-hub/v1/review/submissions/{submission.pk}/decision",
                            approve_payload(report, disclosure=disclosure_of("first-agent")), format="json")
    assert response.status_code == 409 and response.json()["error"]["code"] == "OMEGA_HUB_CHECKS_NOT_PASSED", response.content
    submission.refresh_from_db()
    assert submission.status == "in_review"
    assert not Version.objects.exists()


def test_a_removed_version_of_a_listed_entry_is_gone_everywhere(world):
    """Removal marks the entry and its versions together; a removed version must 410 even if the entry is listed."""
    entry = publish(world)
    Version.objects.filter(entry=entry, version="1.0.0").update(status=Version.Status.REMOVED)
    assert Entry.objects.get(pk=entry.pk).status == Entry.Status.LISTED
    base = "/omega-hub/v1/entries/future-agi/first-agent/versions/1.0.0"
    for path in (base, f"{base}/bundle", f"{base}/files/README.md", f"{base}/cast"):
        response = world.c.get(path)
        assert response.status_code == 410, (path, response.status_code)
