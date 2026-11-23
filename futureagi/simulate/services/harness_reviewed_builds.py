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
    metadata = job.payload.get("metadata") or {}
    return bool(
        not getattr(job, "environment_id", None)
        and not metadata.get("simulation_only")
        and metadata.get("build_source_selection")
    )


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


def _trusted_scenarios(job, manifest, pin):
    from simulate.services import harness_sources as sources
    from simulate.services.harness_source_reviews import accepted_source_snapshot

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
    task = metadata.get("agent_name") or job.name or "the connected agent"
    try:
        result = sources._alk_result(
            sources.execute_source_request(
                {
                    "version": sources.REQUEST_VERSION,
                    "operation": "generate_scenarios",
                    "snapshot": json.loads(snapshot),
                    "environment_requirements": {
                        "task": f"Test the behavior of {task}"
                    },
                    "requested_count": job.scenario_count,
                    "bundle_manifest": manifest,
                }
            )
        )
    except HostedHarnessError as exc:
        if exc.retryable:
            raise
        raise _invalid() from None
    if (
        (result.get("snapshot_id"), result.get("snapshot_digest"))
        != (pin["id"], pin["digest"])
        or result.get("simulation_schema_digest") != pin["schema_digest"]
        or result.get("bundle_digest") != manifest.get("digest")
    ):
        raise _invalid()
    expected = result.get("scenarios")
    if not isinstance(expected, list) or len(expected) != job.scenario_count:
        raise _invalid()
    return expected


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
        expected = _trusted_scenarios(job, manifest, pin)
        scenarios = json.loads(files["scenarios.json"])
        if not isinstance(scenarios, list) or len(scenarios) != job.scenario_count:
            raise _invalid()
        proofs = json.loads(files["proofs.json"])
        expected_by_key = {item["native"]["scenario_key"]: item for item in expected}
        if len(expected_by_key) != len(expected):
            raise _invalid()
        seen = set()
        for scenario in scenarios:
            key = scenario["scenario_key"]
            folder = scenario["folder"]
            trusted = expected_by_key.get(key)
            if (
                trusted is None
                or folder != "scenarios/" + key
                or "/" in key
                or scenario != {**trusted["native"], "folder": folder}
            ):
                raise _invalid()
            path = folder + "/scenario.json"
            document = json.loads(files[path])
            bundled_document = json.loads(published[path])
            case = document["fixture"]["reviewed_source"]
            if (
                key in seen
                or document != trusted["native"]
                or bundled_document != trusted["native"]
                or case != trusted["source_case"]
                or scenario["fixture"]["reviewed_source"] != trusted["source_case"]
                or proofs[key]["reviewed_source_digest"]
                != trusted["source_case"]["proof_digest"]
            ):
                raise _invalid()
            seen.add(key)
        if seen != set(expected_by_key) or set(proofs) != set(expected_by_key):
            raise _invalid()
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
        files = archives.read_archive(body)
        scenarios = json.loads(files["scenarios.json"])
        from simulate.services.agent_contracts import store_build_contract
        from simulate.services.harness_scenarios import index_scenarios

        index_scenarios(job, scenarios, prune=True)
        store_build_contract(job, archive=body)
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
