"""Bind hosted scenario authoring to the accepted Environment source review."""

import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from simulate.models import HarnessSourceReview, HostedHarnessJob
from simulate.services.harness_versioning import archives
from simulate.services.hosted_harness import HostedHarnessError

SNAPSHOT = "/run/futureagi/reviewed-source-snapshot.json"
PRIVATE_CONTROLLER_JOB = "/run/futureagi/reviewed-source-job.json"
PRIVATE_PERMISSIONS = f"chown svc-control:svc-control {PRIVATE_CONTROLLER_JOB} {SNAPSHOT} && chmod 0600 {PRIVATE_CONTROLLER_JOB} {SNAPSHOT}"


def sourced(job):
    return bool((job.payload.get("metadata") or {}).get("build_source_selection"))


def controller_args(job):
    return (
        f"{PRIVATE_CONTROLLER_JOB} --reviewed-snapshot {SNAPSHOT}"
        if sourced(job)
        else "/work/job.json"
    )


def _invalid():
    return HostedHarnessError(
        "source_snapshot_changed",
        "This build does not match its accepted source review.",
        status_code=409,
    )


def expected_pin(job):
    metadata = job.payload["metadata"]
    pin = metadata.get("build_source_pin") or {}
    row = HarnessSourceReview.objects.filter(
        id=metadata.get("source_review_id"),
        owner=job,
        organization_id=job.organization_id,
        workspace_id=job.workspace_id,
        owner_kind=HarnessSourceReview.Owner.BUILD_JOB,
        state=HarnessSourceReview.State.ACCEPTED,
    ).first()
    if (
        row is None
        or str(row.id) != pin.get("review_id")
        or str(job.id) != pin.get("owner_id")
    ):
        raise _invalid()
    expected = {
        "id": row.receipt["snapshot_id"],
        "digest": row.snapshot_digest,
        "schema_digest": row.basis["schema_digest"],
    }
    if any(pin.get(key) != value for key, value in expected.items()):
        raise _invalid()
    return expected


@dataclass(frozen=True)
class ReviewedLaunch:
    snapshot: bytes
    receipt: dict
    documents: list

    def stage(self, sandbox, shared):
        raw = {
            **shared,
            "metadata": {
                **shared["metadata"],
                "source_review_documents": self.documents,
                "source_review_receipt": self.receipt,
            },
        }
        sandbox.fs.upload_file(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode(),
            PRIVATE_CONTROLLER_JOB,
        )
        sandbox.fs.upload_file(self.snapshot, SNAPSHOT)


def launch_material(job):
    from simulate.services.harness_designs import _documents, serialize_document
    from simulate.services.harness_source_reviews import accepted_source_snapshot

    if not sourced(job):
        return None
    pin = expected_pin(job)
    metadata = job.payload["metadata"]
    snapshot, receipt = accepted_source_snapshot(
        metadata["source_review_id"],
        owner=job,
        owner_kind=HarnessSourceReview.Owner.BUILD_JOB,
    )
    if (receipt["snapshot_id"], receipt["snapshot_digest"]) != (
        pin["id"],
        pin["digest"],
    ):
        raise _invalid()
    documents = _documents(
        job.organization,
        metadata["build_source_selection"]["document_ids"],
        workspace_id=job.workspace_id,
        allow_duplicate_names=True,
    )
    return ReviewedLaunch(
        snapshot, receipt, [serialize_document(one) for one in documents]
    )


def check_archive(job, body):
    pin = expected_pin(job)
    root = "reviewed-source.json"
    bundled = "environment-bundle/"
    files = archives.read_archive(body)
    try:
        if any(json.loads(files[path]) != pin for path in (root, bundled + root)):
            raise _invalid()
        manifest = json.loads(files[bundled + "manifest.json"])
        if (
            manifest["schema_version"] != "futureagi.environment-bundle.v2"
            or manifest["metadata"]["reviewed_source"] != pin
        ):
            raise _invalid()
        entries = manifest["files"]
        published = {
            path.removeprefix(bundled): content
            for path, content in files.items()
            if path.startswith(bundled) and path != bundled + "manifest.json"
        }
        if {entry["path"] for entry in entries} != set(published) or len(
            entries
        ) != len(published):
            raise _invalid()
        for entry in entries:
            content = published[entry["path"]]
            if (len(content), hashlib.sha256(content).hexdigest()) != (
                entry["size"],
                entry["sha256"],
            ):
                raise _invalid()
        scenarios = json.loads(files["scenarios.json"])
        if not isinstance(scenarios, list) or len(scenarios) != job.scenario_count:
            raise _invalid()
        preview = HarnessSourceReview.objects.values_list("preview", flat=True).get(
            pk=job.payload["metadata"]["source_review_id"]
        )
        approved = {one["pin"]["source_id"] for one in preview["sources"]}
        proofs = json.loads(files["proofs.json"])
        seen = set()
        for scenario in scenarios:
            key = scenario["scenario_key"]
            folder = scenario["folder"]
            if folder != "scenarios/" + scenario["name"] or "/" in scenario["name"]:
                raise _invalid()
            path = folder + "/scenario.json"
            document = json.loads(files[path])
            bundled_document = json.loads(published[path])
            case = document["fixture"]["reviewed_source"]
            if (
                scenario["fixture"]["reviewed_source"] != case
                or bundled_document["fixture"]["reviewed_source"] != case
                or bundled_document["scenario_key"] != key
                or document["scenario_key"] != key
            ):
                raise _invalid()
            cited = {citation["source_id"] for citation in case["citations"]}
            case_digest = (
                "sha256:"
                + hashlib.sha256(
                    json.dumps(
                        {k: v for k, v in case.items() if k != "proof_digest"},
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    ).encode()
                ).hexdigest()
            )
            if (
                case["id"] in seen
                or not cited
                or not cited <= approved
                or case["proof_digest"] != case_digest
                or proofs[key]["reviewed_source_digest"] != case_digest
            ):
                raise _invalid()
            seen.add(case["id"])
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise _invalid() from None
    return pin


@transaction.atomic
def complete(attempt, body):
    from simulate.services.hosted_harness_gateway import (
        authoring_stage_outputs_from_archive,
        freeze_authoring_archive,
    )

    job = HostedHarnessJob.no_workspace_objects.select_for_update().get(
        pk=attempt.job_id
    )
    if job.current_attempt_number != attempt.attempt_number or job.terminal_at:
        return job
    try:
        check_archive(job, body)
    except HostedHarnessError as exc:
        job.failure = {"code": exc.code, "message": exc.message}
        job.state = HostedHarnessJob.State.FAILED
    else:
        freeze_authoring_archive(job, body, extending=False)
        job.refresh_from_db()
        job.stage_outputs = authoring_stage_outputs_from_archive(
            body, scenario_limit=job.scenario_count
        )
        job.state = HostedHarnessJob.State.COMPLETED
    job.terminal_at = job.content_updated_at = timezone.now()
    job.current_stage = job.state
    job.save(
        update_fields=[
            "stage_outputs",
            "failure",
            "state",
            "current_stage",
            "terminal_at",
            "content_updated_at",
            "updated_at",
        ]
    )
    return job
