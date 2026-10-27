from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest

from simulate.models import HarnessDesignDocument, HostedHarnessJob
from simulate.services import harness_designs
from simulate.services.hosted_harness import HostedHarnessError, create_hosted_job
from simulate.services.hosted_harness_conversation import enqueue_message
from simulate.tests.test_hosted_harness_conversation import _payload


def _archive(files: dict[str, bytes]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return raw.getvalue()


def _members(body: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        return {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.isfile()
        }


@pytest.mark.django_db
def test_continuing_a_parked_design_drops_the_previous_attempts_result(
    organization, monkeypatch
):
    # The design parked at its approval, then a later turn checkpointed the workspace with the
    # failed result it parked with. Continuing must ask the question again, not exit at once.
    parked, _ = create_hosted_job(
        organization, _payload(), idempotency_key="parked-design"
    )
    enqueue_message(parked, content="Design it", client_request_id="design-1")
    checkpoint = _archive(
        {
            "design.json": b'{"stage": "interview"}',
            ".futureagi-pending-question.json": b'{"question": "Approve?"}',
            ".futureagi-build-finished.json": b'{"status": "failed"}',
            "design-output/result.json": b'{"status": "failed"}',
            "world/world.sqlite": b"sqlite",
        }
    )
    monkeypatch.setattr(
        "simulate.services.hosted_harness_conversation.load_workspace_archive",
        lambda conversation: checkpoint,
    )
    continued = HostedHarnessJob(
        organization=organization,
        payload={
            "metadata": {"design": {"name": "desk", "resume_from": str(parked.id)}}
        },
    )

    launch = harness_designs.design_launch_for(continued)

    uploads = dict(launch.uploads)
    restored = _members(uploads["/work/authoring.tar.gz"])
    assert sorted(restored) == [
        ".futureagi-pending-question.json",
        "design.json",
        "world/world.sqlite",
    ]
    assert restored["world/world.sqlite"] == b"sqlite"


@pytest.mark.django_db
def test_two_documents_with_one_name_are_refused(organization):
    documents = [
        HarnessDesignDocument.no_workspace_objects.create(
            organization=organization,
            name="policy.txt",
            content_type="text/plain",
            sha256=hashlib.sha256(body).hexdigest(),
            size=len(body),
            object_key=f"designs/{index}",
        )
        for index, body in enumerate(
            (b"returns within 30 days", b"returns within 14 days")
        )
    ]

    with pytest.raises(HostedHarnessError) as refused:
        harness_designs.design_payload(
            organization,
            name="Returns desk",
            requirements="",
            modality="chat",
            document_ids=[one.id for one in documents],
        )

    assert refused.value.code == "design_documents_duplicate_name"
    assert "policy.txt" in json.dumps(refused.value.as_dict())
