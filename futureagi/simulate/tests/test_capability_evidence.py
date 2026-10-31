"""Capability evidence a build sealed: stored once per build, and read back as a report."""

import io
import json
import tarfile
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from simulate.models import HarnessCapabilityEvidence, HostedHarnessJob
from simulate.services.capability_evidence import capability_report, record_capability_evidence

_DOCUMENT = {
    "schema_version": "futureagi.capability-evidence.v1",
    "catalog_version": "1",
    "inputs": {"source_digest": "s1", "bundle_digest": "b1"},
    "capabilities": {"start_health": {"finding": "passed", "checks": []}},
}
_CATALOGUE = [{"id": "start_health", "label": "Start and health", "depends_on": ["source"]}]


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture
def job(db, organization, workspace):
    return HostedHarnessJob.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        run_id=uuid.uuid4(),
        idempotency_key=uuid.uuid4().hex,
        request_digest="digest",
        schema_version="1.6",
        seed=1,
        artifact_level="standard",
        max_artifact_bytes=1024,
        deadline_at=timezone.now() + timedelta(hours=1),
        scenario_count=1,
        payload={},
    )


def test_a_build_s_evidence_is_stored_once_and_reads_back_as_a_report(job):
    body = _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()})

    first = record_capability_evidence(job, body)
    again = record_capability_evidence(job, body)

    assert first is not None and again is not None and first.id == again.id
    assert HarnessCapabilityEvidence.no_workspace_objects.filter(job=job).count() == 1
    assert first.capabilities == _DOCUMENT["capabilities"] and first.catalog_version == "1"
    report = capability_report(_CATALOGUE, first, {"source_digest": "s1", "bundle_digest": "b1"})
    assert report["capabilities"][0]["status"] == "supported"
    assert capability_report(_CATALOGUE, first, {"source_digest": "s2"})["capabilities"][0]["status"] == "stale"


def test_a_build_without_evidence_stores_nothing_and_reads_untested(job):
    body = _archive({"generic-harness/certification.json": b"{}"})

    assert record_capability_evidence(job, body) is None
    assert not HarnessCapabilityEvidence.no_workspace_objects.filter(job=job).exists()
    assert capability_report(_CATALOGUE, None, {})["capabilities"][0]["status"] == "untested"


def test_the_environment_report_is_served_and_unknown_environments_are_not_found(job, auth_client):
    body = _archive(
        {
            "generic-harness/capability-evidence.json": json.dumps(
                {**_DOCUMENT, "order": ["start_health"], "labels": {"start_health": "Start and health"}}
            ).encode()
        }
    )
    record_capability_evidence(job, body)

    response = auth_client.get(f"/simulate/api/harness-environments/{job.id}/capabilities/")
    missing = auth_client.get(f"/simulate/api/harness-environments/{uuid.uuid4()}/capabilities/")

    assert response.status_code == 200, response.content
    rows = response.json()["capabilities"]
    assert [(row["id"], row["label"], row["status"]) for row in rows] == [
        ("start_health", "Start and health", "supported")
    ]
    assert missing.status_code == 404


def test_a_later_archive_of_the_same_build_refreshes_its_row(job):
    first = record_capability_evidence(job, _archive({"generic-harness/capability-evidence.json": json.dumps(_DOCUMENT).encode()}))
    final = {**_DOCUMENT, "capabilities": {"start_health": {"finding": "failed", "checks": []}}}
    later = record_capability_evidence(
        job,
        _archive(
            {
                "generic-harness/capability-evidence.json": json.dumps(final).encode(),
                "generic-harness/scenario-requirements.json": json.dumps(
                    {"scenarios": {"refund": {"capabilities": ["start_health"]}}}
                ).encode(),
            }
        ),
    )

    assert later.id == first.id
    assert HarnessCapabilityEvidence.no_workspace_objects.filter(job=job).count() == 1
    assert later.capabilities["start_health"]["finding"] == "failed"
    assert later.scenario_requirements == {"refund": {"capabilities": ["start_health"]}}
