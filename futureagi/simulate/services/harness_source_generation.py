"""Generate a suite draft from one accepted Environment source snapshot."""

import base64
import json
import re

from django.db import transaction
from temporalio.common import WorkflowIDConflictPolicy

from simulate.models import (
    HarnessSourceReview,
    HostedHarnessJob,
)
from simulate.models import (
    HarnessSourceScenarioGeneration as Generation,
)
from simulate.services import harness_sources as sources
from simulate.services.harness_versioning import archives, assembly, drafts, versions
from simulate.services.hosted_harness import HostedHarnessError


def _conflict():
    return HostedHarnessError(
        "source_generation_stale",
        "The Environment or suite changed. Review the current version before generating scenarios.",
        status_code=409,
    )


def _unsealed():
    return HostedHarnessError(
        "source_generation_unsealed",
        "The generated scenarios could not be sealed against this Environment.",
        status_code=422,
    )


def summary(job):
    return {
        "id": str(job.id),
        "review_id": str(job.review_id),
        "state": job.state,
        "source_digest": job.source_digest,
        "generated_scenarios": job.result.get("generated_scenarios", []),
        "suite_draft": job.result.get("suite_draft"),
        "error": job.error or None,
    }


def dispatch(identifier):
    from simulate.tasks.environment_source_imports import (
        generate_environment_source_scenarios,
    )

    try:
        generate_environment_source_scenarios.apply_async(
            args=[str(identifier)],
            task_id=f"environment-source-scenarios-{identifier}",
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            dispatch_timeout_seconds=30,
        )
    except Exception:
        # A queued row survives delivery failure and can be re-delivered by POST or the platform sweep.
        return False
    return True


def reserve(row, user):
    _snapshot, receipt = sources.read_accepted_snapshot(
        row.owner, row.accepted_artifact_digest
    )
    basis = {
        **_basis(row.owner),
        "environment_version_id": row.basis["environment_version_id"],
        "suite_version_id": row.basis["suite_version_id"],
        "environment": row.receipt["draft"],
        "suite": row.basis["suite_draft"],
    }
    job, created = Generation.objects.get_or_create(
        review=row,
        defaults={
            "reviewer": user,
            "requested_count": max(1, min(row.owner.scenario_count, 100)),
            "source_digest": receipt["snapshot_digest"],
            "basis": basis,
        },
    )
    if created or job.state == Generation.State.QUEUED:
        transaction.on_commit(lambda: dispatch(job.id))
    return job


def state_for_request(request, review_id):
    from simulate.services.harness_source_reviews import _find, require_source_scope

    row = _find(review_id, require_source_scope(request, write=False))
    if (
        row.owner_kind != HarnessSourceReview.Owner.ENVIRONMENT
        or row.state != HarnessSourceReview.State.ACCEPTED
    ):
        raise _conflict()
    item = Generation.objects.filter(review=row).first()
    if item is None:
        raise HostedHarnessError(
            "source_generation_not_found",
            "No scenario generation has been started.",
            status_code=404,
        )
    return summary(item)


def _basis(head):
    refs = drafts.draft_refs(head)
    return {
        "agent_version_id": str(head.agent_version_id)
        if head.agent_version_id
        else None,
        "authoring_key": ((head.payload or {}).get("metadata") or {}).get(
            "authoring_object_key"
        ),
        "environment_version_id": str(head.environment_version_id),
        "suite_version_id": str(head.suite_version_id),
        "environment": sources._ref_value(refs["environment"]),
        "suite": sources._ref_value(refs["suite"]),
    }


def _generate(generation, owner):
    review = generation.review
    raw, receipt = sources.read_accepted_snapshot(
        owner, review.accepted_artifact_digest
    )
    if receipt["review_id"] != str(review.id) or (
        generation.source_digest
        and receipt["snapshot_digest"] != generation.source_digest
    ):
        raise _conflict()
    snapshot = json.loads(raw)
    task = (
        (owner.payload.get("metadata") or {}).get("agent_name")
        or owner.name
        or "the connected agent"
    )
    result = sources._alk_result(
        sources.execute_source_request(
            {
                "version": sources.REQUEST_VERSION,
                "operation": "generate_scenarios",
                "snapshot": snapshot,
                "environment_requirements": {"task": f"Test the behavior of {task}"},
                "requested_count": generation.requested_count,
            }
        )
    )
    if (result.get("snapshot_id"), result.get("snapshot_digest")) != (
        receipt["snapshot_id"],
        receipt["snapshot_digest"],
    ):
        raise _conflict()
    if not isinstance(result.get("scenarios"), list):
        raise HostedHarnessError(
            "source_generation_invalid",
            "The scenario processor returned an invalid result.",
            status_code=422,
        )
    return result, {item["source_id"] for item in receipt["bindings"]}, snapshot


def _seal(
    owner, environment, suite, *, snapshot, result, schema_digest, generated_keys
):
    base = drafts._default_authoring(owner)
    if base is None:
        raise _unsealed()
    previous_digest, agent_source = assembly.manifest_facts(base.sealed)
    if not schema_digest or previous_digest is None:
        raise _unsealed()
    tree = archives.compose(environment, suite, base.sealed)
    if sum(map(len, tree.values())) > archives.MAX_ARCHIVE_INFLATED_BYTES:
        raise _unsealed()
    pin = {
        "id": result["snapshot_id"],
        "digest": result["snapshot_digest"],
        "schema_digest": schema_digest,
    }
    response = sources._alk_result(
        sources.execute_source_request(
            {
                "version": sources.REQUEST_VERSION,
                "operation": "reseal_scenarios",
                "mode": "existing_environment",
                "generated_scenario_keys": generated_keys,
                "schema_digest": schema_digest,
                "snapshot": snapshot,
                "authoring_archive_base64": base64.b64encode(
                    archives.pack_archive(tree)
                ).decode("ascii"),
            }
        )
    )
    try:
        output = archives.read_archive(
            base64.b64decode(response["authoring_archive_base64"], validate=True)
        )
    except (KeyError, TypeError, ValueError):
        raise _unsealed() from None
    split = archives.split_authoring(output)
    digest, resealed_agent_source = assembly.manifest_facts(split.sealed)
    metadata = drafts._sealed_metadata(split) if digest else {}
    generated_scripts = {
        f"scenarios/{key}/{name}"
        for key in generated_keys
        for name in ("setup.py", "ready.py")
    }
    if (
        split.environment != environment
        or any(split.suite.get(path) != content for path, content in suite.items())
        or not (split.suite.keys() - suite.keys()) <= generated_scripts
        or not digest
        or digest == previous_digest
        or digest != response.get("bundle_digest")
        or resealed_agent_source != agent_source
        or not assembly.certifies_bundle(split.sealed)
        or response.get("reviewed_source") != pin
        or metadata.get("reviewed_source") != pin
        or split.sealed.get("environment-bundle/reviewed-source.json")
        != sources._canonical(pin)
        or metadata.get("sealed_from") != drafts.sealed_from(output)
    ):
        raise _unsealed()
    return archives.pack_archive(output), digest


def _prepare(generation, owner, result, approved_ids, snapshot):
    sides = drafts.edit_sides(owner)
    files = sides["suite"]
    existing = versions.suite_documents(files)
    generated = []
    held = {item.get("scenario_key") for item in existing}
    seen_cases = set()
    for draft in result["scenarios"]:
        if not isinstance(draft, dict):
            raise HostedHarnessError(
                "source_generation_invalid",
                "The scenario processor returned an invalid result.",
                status_code=422,
            )
        native = draft.get("native")
        case = draft.get("source_case")
        if not isinstance(native, dict) or not isinstance(case, dict):
            raise HostedHarnessError(
                "source_generation_invalid",
                "The scenario processor returned an invalid result.",
                status_code=422,
            )
        key = native.get("scenario_key")
        citations = case.get("citations") or []
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", key)
            or key in held
            or not isinstance(native.get("name"), str)
            or not isinstance(case.get("id"), str)
            or case.get("id") in seen_cases
            or not isinstance(native.get("fixture"), dict)
            or (native.get("fixture") or {}).get("reviewed_source") != case
            or not isinstance(citations, list)
            or not citations
            or any(
                not isinstance(item, dict) or item.get("source_id") not in approved_ids
                for item in citations
            )
        ):
            raise HostedHarnessError(
                "source_generation_invalid",
                "The scenario processor returned an unverified source case.",
                status_code=422,
            )
        held.add(key)
        seen_cases.add(case.get("id"))
        folder = "scenarios/" + key
        if folder + "/scenario.json" in files:
            raise _conflict()
        files[folder + "/scenario.json"] = sources._canonical(native)
        existing.append({**native, "folder": folder})
        generated.append(
            {"scenario_key": key, "name": native["name"], "citations": citations}
        )
    if len(generated) != generation.requested_count:
        raise HostedHarnessError(
            "source_generation_incomplete",
            "The processor did not return every requested grounded scenario.",
            status_code=422,
        )
    files["scenarios.json"] = sources._canonical(existing)
    archive, digest = _seal(
        owner,
        sides["environment"],
        files,
        snapshot=snapshot,
        result=result,
        schema_digest=generation.review.basis["schema_digest"],
        generated_keys=[one["scenario_key"] for one in generated],
    )
    return archive, digest, generated


@transaction.atomic
def _apply(generation_id, archive, digest, generated):
    generation = (
        Generation.objects.select_for_update()
        .select_related("review")
        .get(pk=generation_id)
    )
    if generation.state == Generation.State.COMPLETED:
        return
    owner = HostedHarnessJob.no_workspace_objects.select_for_update().get(
        pk=generation.review.owner_id
    )
    if owner.deleted or _basis(owner) != generation.basis:
        raise _conflict()
    write = drafts.record_draft(
        owner,
        archive,
        origin="manual",
        user=generation.reviewer,
        expected={
            piece: sources._ref_from(generation.basis[piece])
            for piece in ("environment", "suite")
        },
        pieces={"suite"},
        checked={"environment"},
    )
    if write is None or write.suite is None:
        raise _conflict()
    if not write.suite.documents.get("workspace_sealed", {}).get("resealed"):
        raise _unsealed()
    generation.state = Generation.State.COMPLETED
    generation.error = ""
    generation.result = {
        "generated_scenarios": generated,
        "bundle_digest": digest,
        "suite_draft": {"id": str(write.suite.id), "revision": write.suite.revision},
    }
    generation.save(update_fields=["state", "result", "error"])


def process(generation_id):
    job = Generation.objects.select_related("review__owner").get(pk=generation_id)
    if job.state not in {Generation.State.QUEUED, Generation.State.RUNNING}:
        return summary(job)
    claimed = Generation.objects.filter(
        pk=job.pk, state=Generation.State.QUEUED
    ).update(state=Generation.State.RUNNING, error="")
    if not claimed:
        job.refresh_from_db()
        if job.state != Generation.State.RUNNING:
            return summary(job)
    try:
        owner = job.review.owner
        if _basis(owner) != job.basis:
            raise _conflict()
        result, approved, snapshot = _generate(job, owner)
        archive, digest, generated = _prepare(job, owner, result, approved, snapshot)
        _apply(job.id, archive, digest, generated)
    except HostedHarnessError as exc:
        if exc.retryable:
            Generation.objects.filter(pk=job.pk, state=Generation.State.RUNNING).update(
                state=Generation.State.QUEUED
            )
            raise
        Generation.objects.filter(pk=job.pk, state=Generation.State.RUNNING).update(
            state=(
                Generation.State.STALE
                if exc.code in {"source_generation_stale", "draft_changed"}
                else Generation.State.FAILED
            ),
            error=exc.code,
        )
    except Exception:
        Generation.objects.filter(pk=job.pk, state=Generation.State.RUNNING).update(
            state=Generation.State.QUEUED
        )
        raise
    job.refresh_from_db()
    return summary(job)
