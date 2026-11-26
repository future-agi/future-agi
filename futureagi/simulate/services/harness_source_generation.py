"""Generate a suite draft from one accepted Environment source snapshot."""

import base64
import json
import re

from django.db import transaction
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.exceptions import TemporalError

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


_DISPATCH_ERROR = "source_generation_dispatch_failed"


def dispatch(identifier, *, attempt=None):
    from simulate.tasks.environment_source_imports import (
        generate_environment_source_scenarios,
    )

    try:
        generate_environment_source_scenarios.apply_async(
            args=[str(identifier)],
            task_id=f"environment-source-scenarios-{identifier}"
            + (f"-{attempt}" if attempt else ""),
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            dispatch_timeout_seconds=30,
        )
    except (OSError, RuntimeError, TemporalError):
        Generation.objects.filter(pk=identifier, state=Generation.State.QUEUED).update(
            state=Generation.State.FAILED, error=_DISPATCH_ERROR
        )
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
    job, _ = Generation.objects.get_or_create(
        review=row,
        defaults={
            "reviewer": user,
            "requested_count": max(1, min(row.owner.scenario_count, 100)),
            "source_digest": receipt["snapshot_digest"],
            "basis": basis,
        },
    )
    if job.state == Generation.State.FAILED and job.error == _DISPATCH_ERROR:
        Generation.objects.filter(
            pk=job.pk, state=job.state, error=_DISPATCH_ERROR
        ).update(state=Generation.State.QUEUED, error="")
        job.refresh_from_db()
    if job.state == Generation.State.QUEUED:
        transaction.on_commit(lambda: dispatch(job.id))
    return job


def state_for_request(request, review_id, *, retry=False):
    from simulate.services.harness_source_reviews import _find, require_source_scope

    row = _find(review_id, require_source_scope(request, write=retry))
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
    if retry:
        from uuid import uuid4

        with transaction.atomic():
            item = Generation.objects.select_for_update().get(pk=item.pk)
            if item.state == Generation.State.STALE:
                raise _conflict()
            if item.state == Generation.State.FAILED:
                if _basis(row.owner) != item.basis:
                    raise _conflict()
                item.state = Generation.State.QUEUED
                item.error = ""
                item.save(update_fields=["state", "error"])
                transaction.on_commit(lambda: dispatch(item.id, attempt=uuid4().hex))
        item.refresh_from_db()
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
                "distinct_source_cases": True,
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
    result["source_task"] = f"Test the behavior of {task}"
    return result, {item["source_id"] for item in receipt["bindings"]}, snapshot


def _seal(
    owner,
    environment,
    suite,
    *,
    snapshot,
    result,
    schema_digest,
    generated_keys,
    requested_count,
):
    base = drafts._default_authoring(owner)
    if base is None:
        raise _unsealed()
    previous_digest, agent_source = assembly.manifest_facts(base.sealed)
    base_has_certificate = assembly.CERTIFICATE in base.sealed
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
                "distinct_case_budget": requested_count,
                "writer_proved": True,
                "source_task": result["source_task"],
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
    if (
        {k: v for k, v in split.environment.items() if k != "reviewed-source.json"}
        != {k: v for k, v in environment.items() if k != "reviewed-source.json"}
        or split.environment.get("reviewed-source.json") != sources._canonical(pin)
        or split.suite != suite
        or not digest
        or digest == previous_digest
        or digest != response.get("bundle_digest")
        or resealed_agent_source != agent_source
        or (base_has_certificate and not assembly.certifies_bundle(split.sealed))
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
    from simulate.services.harness_source_writer import author_additions

    cases = {r["source_case"]["id"]: r["source_case"] for r in result["scenarios"]}
    if not 1 <= len(cases) == len(result["scenarios"]) <= generation.requested_count:
        raise _unsealed()
    schema_digest = generation.review.basis["schema_digest"]
    pin = {
        "id": result["snapshot_id"],
        "digest": result["snapshot_digest"],
        "schema_digest": schema_digest,
    }
    patch = author_additions(
        owner, {**sides["environment"], **files}, pin, cases.values()
    )
    try:
        goals = patch.pop("added-sub-goals.json", None)
        generated = _merge_additions(files, existing, patch, cases, approved_ids)
        if goals:
            catalogue = json.loads(sides["environment"]["sub_goals.json"])
            known = {goal["name"] for goal in catalogue["sub_goals"]}
            used = {g for row in existing for g in row.get("sub_goals", [])}
            for goal in json.loads(goals):
                if goal["name"] in known or goal["name"] not in used:
                    raise _unsealed()
                known.add(goal["name"])
                catalogue["sub_goals"].append(goal)
            sides["environment"]["sub_goals.json"] = sources._canonical(catalogue)
    except (KeyError, AttributeError, TypeError, ValueError):
        raise _unsealed() from None
    archive, digest = _seal(
        owner,
        sides["environment"],
        files,
        snapshot=snapshot,
        result=result,
        schema_digest=generation.review.basis["schema_digest"],
        generated_keys=[one["scenario_key"] for one in generated],
        requested_count=generation.requested_count,
    )
    return archive, digest, generated


def _merge_additions(files, existing, patch, cases, approved_ids):
    from simulate.services.harness_reviewed_builds import _semantic_source_digest

    generated = []
    proofs = json.loads(files.get("proofs.json", b"{}"))
    additions = json.loads(patch.pop("scenarios.json"))
    added_proofs = json.loads(patch.pop("proofs.json"))
    held = {row.get("scenario_key") for row in existing}
    for row in additions:
        key, name = row["scenario_key"], row["name"]
        folder = "scenarios/" + name + "/"
        if (
            not re.fullmatch(r"[a-zA-Z0-9_-]+", name)
            or name.startswith(("source-case-", "source_case_"))
            or key in held
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", key)
            or row["folder"] != folder[:-1]
        ):
            raise _unsealed()
        held.add(key)
        body = json.loads(patch[folder + "scenario.json"])
        if row != {
            **body,
            "steps": len(body.get("solution") or []),
            "folder": folder[:-1],
        }:
            raise _unsealed()
        case = body["fixture"]["reviewed_source"]
        proof = added_proofs.pop(key)
        if (
            cases.pop(case["id"], None) != case
            or any(c["source_id"] not in approved_ids for c in case["citations"])
            or proof.get("reviewed_source_digest")
            != _semantic_source_digest(
                case, body, patch[folder + "setup.py"], patch[folder + "ready.py"]
            )
        ):
            raise _unsealed()
        if folder + "scenario.json" in files or key in proofs:
            raise _unsealed()
        for path in [path for path in patch if path.startswith(folder)]:
            rel = path[len(folder) :]
            if rel not in {
                "setup.py",
                "ready.py",
                "scenario.json",
            } and not re.fullmatch(r"checks/[a-zA-Z0-9_-]+\.py", rel):
                raise _unsealed()
            files[path] = patch.pop(path)
        proofs[key] = proof
        generated.append(
            {"name": name, "scenario_key": key, "citations": case["citations"]}
        )
        existing.append(row)
    if cases or added_proofs or patch:
        raise _unsealed()
    files["proofs.json"] = sources._canonical(proofs)
    files["scenarios.json"] = sources._canonical(existing)
    return generated


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
        pieces={"suite", "environment"},
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


def process(generation_id, *, retry_available=False):
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
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, HostedHarnessError)
            else "source_generation_failed"
        )
        retryable = not isinstance(exc, HostedHarnessError) or exc.retryable
        running = Generation.objects.filter(pk=job.pk, state=Generation.State.RUNNING)
        if retryable and retry_available:
            running.update(state=Generation.State.QUEUED, error="")
            raise
        state = (
            Generation.State.STALE
            if code in {"source_generation_stale", "draft_changed"}
            else Generation.State.FAILED
        )
        running.update(state=state, error=code)
        if retryable:
            raise
    job.refresh_from_db()
    return summary(job)
