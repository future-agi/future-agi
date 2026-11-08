"""Bounded, retained Dataset-to-case review. No source answer becomes ground truth."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

from django.db import connection, transaction
from django.utils import timezone

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.skills_hub_r3 import HubSkillIntakePlan
from model_hub.models.skills_hub_v12 import HubSkillContextOperation
from model_hub.selectors.skills_hub_v12 import get_resource
from model_hub.services.dataset_table_snapshot import (
    DatasetTableReadDeadline,
    assert_dataset_table_cells_within_limits,
    begin_repeatable_read_snapshot,
    capture_dataset_table_revision,
)
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256
from model_hub.services.skills_hub.v11.inventory import (
    canonical_json,
    fingerprint,
    secret_findings,
)
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
)
from model_hub.services.skills_hub.v11.suites import save_suite, validate_cases
from model_hub.services.skills_hub.v11.types import SkillPrincipal
from model_hub.services.skills_hub.v12.types import ResourceKind

CASE_FIELDS = {
    "id",
    "title",
    "source_group_id",
    "prompt",
    "fixtures",
    "expected_trigger",
    "checks",
    "reviewed",
}


def purge_expired_case_previews(*, batch_size: int = 100) -> int:
    """Called by the existing retention worker; no scheduler or source re-read."""
    if type(batch_size) is not int or not 1 <= batch_size <= 100:
        raise ValueError("Use a bounded retention batch of 1–100 previews.")
    with transaction.atomic():
        ids = list(
            HubSkillIntakePlan.objects.select_for_update(skip_locked=True)
            .filter(
                kind="case_import",
                expires_at__lte=timezone.now(),
                payload__isnull=False,
            )
            .order_by("expires_at", "id")
            .values_list("id", flat=True)[:batch_size]
        )
        return HubSkillIntakePlan.objects.filter(pk__in=ids).update(payload=None)


def read_dataset_source(
    *,
    principal: SkillPrincipal,
    dataset_id: UUID,
    row_id: UUID,
    prompt_column_id: UUID,
    fixture_column_ids: list[UUID],
) -> dict:
    """One bounded MVCC read using the existing exact-dataset revision authority."""
    require_private_principal(principal=principal)
    if connection.in_atomic_block:
        raise RuntimeError(
            "Read the Dataset snapshot before starting the destination transaction."
        )
    selected = [prompt_column_id, *fixture_column_ids]
    if len(selected) > 20 or len(set(selected)) != len(selected):
        raise SkillsHubError(
            "invalid_test_config",
            "Choose at most 20 distinct text columns.",
            status=400,
        )
    deadline = DatasetTableReadDeadline.start()
    with transaction.atomic():
        snapshot = begin_repeatable_read_snapshot(deadline)
        require_scope_access(principal=principal, write=False)
        dataset = Dataset.no_workspace_objects.filter(
            pk=dataset_id,
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
        ).first()
        if (
            dataset is None
            or not Row.no_workspace_objects.filter(pk=row_id, dataset=dataset).exists()
        ):
            raise SkillsHubError(
                "resource_not_found", "Dataset row not found.", status=404
            )
        revision = capture_dataset_table_revision(
            dataset_id=str(dataset_id), snapshot=snapshot, deadline=deadline
        )
        columns = list(
            Column.no_workspace_objects.filter(dataset=dataset, pk__in=selected).values(
                "id", "name", "data_type", "source"
            )
        )
        # Only user-supplied text is eligible; generated outputs/evaluator reasoning
        # are deliberately not mapped to prompts, fixtures or expected answers.
        if len(columns) != len(selected) or any(
            column["data_type"] != "text" or column["source"] != "OTHERS"
            for column in columns
        ):
            raise SkillsHubError(
                "capture_unsafe",
                "Select supplied text columns, not generated output or reasoning.",
                status=422,
            )
        assert_dataset_table_cells_within_limits(
            row_ids=[str(row_id)],
            column_ids=[str(value) for value in selected],
            deadline=deadline,
        )
        deadline.set_statement_timeout()
        cells = list(
            Cell.no_workspace_objects.filter(
                row_id=row_id, column_id__in=selected, dataset=dataset
            ).values("id", "column_id", "value")
        )
        by_column = {cell["column_id"]: cell for cell in cells}
        if (
            len(cells) != len(selected)
            or len(by_column) != len(cells)
            or any(not isinstance(cell["value"], str) for cell in cells)
        ):
            raise SkillsHubError(
                "capture_unsafe",
                "Every selected column needs exactly one text cell.",
                status=422,
            )
        texts: dict[UUID, str] = {}
        for cell in cells:
            text = cell["value"]
            assert isinstance(text, str)
            texts[cell["column_id"]] = text
        if sum(len(text.encode()) for text in texts.values()) > 65536:
            raise SkillsHubError(
                "payload_too_large", "Selected case input exceeds 64 KiB.", status=413
            )
        names = {column["id"]: column["name"] for column in columns}
        source = {
            "provider": "dataset",
            "dataset_id": str(dataset_id),
            "row_id": str(row_id),
            "revision": revision.revision,
            "prompt_column_id": str(prompt_column_id),
            "fixture_column_ids": [str(value) for value in fixture_column_ids],
            "hashes": {str(key): file_sha256(text) for key, text in texts.items()},
        }
        deadline.checkpoint()
        return {
            "source": source,
            "source_fingerprint": fingerprint(source),
            "prompt": texts[prompt_column_id],
            "fixtures": [
                {
                    "id": str(by_column[key]["id"]),
                    "name": names[key],
                    "text": texts[key],
                }
                for key in fixture_column_ids
            ],
        }


def preview_dataset_case(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    dataset_id: UUID,
    row_id: UUID,
    prompt_column_id: UUID,
    fixture_column_ids: list[UUID],
) -> dict:
    require_private_principal(principal=principal)
    assert principal.user_id is not None
    require_scope_access(principal=principal, write=True)
    get_resource(principal=principal, resource_id=resource_id, kind=kind)
    selected = read_dataset_source(
        principal=principal,
        dataset_id=dataset_id,
        row_id=row_id,
        prompt_column_id=prompt_column_id,
        fixture_column_ids=fixture_column_ids,
    )
    case_id = str(uuid4())
    group = fingerprint(
        {key: selected["source"][key] for key in ("provider", "dataset_id", "row_id")}
    )
    findings = secret_findings(
        selected["prompt"], location={"kind": "case_prompt", "case_id": case_id}
    )
    for fixture in selected["fixtures"]:
        findings.extend(
            secret_findings(
                fixture["text"],
                location={
                    "kind": "fixture",
                    "case_id": case_id,
                    "fixture_id": fixture["id"],
                },
            )
        )
    with transaction.atomic():
        plan = HubSkillIntakePlan.objects.create(
            organization_id=principal.scope.organization_id,
            workspace_id=principal.scope.workspace_id,
            owner_id=principal.user_id,
            kind="case_import",
            fingerprint=selected["source_fingerprint"],
            inventory={
                "resource_id": str(resource_id),
                "kind": kind,
                "case_id": case_id,
                "source_group_id": group,
            },
            payload=selected,
            expires_at=timezone.now() + timedelta(minutes=15),
        )
    return {
        "plan_id": str(plan.id),
        "expires_at": plan.expires_at,
        "source_fingerprint": plan.fingerprint,
        "source": selected["source"],
        "case": {
            "id": case_id,
            "source_group_id": group,
            "prompt": selected["prompt"],
            "fixtures": selected["fixtures"],
            "expected_trigger": None,
            "checks": [],
            "reviewed": False,
            "title": "Dataset regression",
        },
        "outbound_findings": findings,
        "visibility_on_accept": "workspace",
        "replay_scope": "text_only",
    }


def accept_dataset_case(
    *,
    principal: SkillPrincipal,
    resource_id: UUID,
    kind: ResourceKind,
    plan_id: UUID,
    source_fingerprint: str,
    suite_revision: int,
    case: dict,
    acknowledge_workspace_sharing: bool,
    idempotency_key: UUID,
) -> dict:
    require_private_principal(principal=principal)
    assert principal.user_id is not None
    require_scope_access(principal=principal, write=True)
    get_resource(principal=principal, resource_id=resource_id, kind=kind)
    identity = {
        "organization_id": principal.scope.organization_id,
        "workspace_id": principal.scope.workspace_id,
        "principal_key": f"user:{principal.user_id}",
        "operation": "dataset_case",
        "idempotency_key": idempotency_key,
    }
    digest = fingerprint(
        {
            "resource_id": resource_id,
            "kind": kind,
            "plan_id": plan_id,
            "source_fingerprint": source_fingerprint,
            "suite_revision": suite_revision,
            "case": case,
            "acknowledge_workspace_sharing": acknowledge_workspace_sharing,
        }
    )

    def replay() -> dict | None:
        operation = HubSkillContextOperation.objects.filter(**identity).first()
        if operation is None:
            return None
        if operation.request_digest != digest:
            raise SkillsHubError(
                "idempotency_conflict",
                "Operation key already used with another request.",
                status=409,
            )
        response: dict = operation.response
        return response

    existing = replay()
    if existing is not None:
        return existing
    plan = HubSkillIntakePlan.objects.filter(
        pk=plan_id,
        owner_id=principal.user_id,
        kind="case_import",
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
    ).first()
    if (
        plan is None
        or plan.inventory["resource_id"] != str(resource_id)
        or plan.inventory["kind"] != kind
    ):
        raise SkillsHubError(
            "resource_not_found", "Case preview not found.", status=404
        )
    if plan.expires_at <= timezone.now() or plan.payload is None:
        raise SkillsHubError(
            "capture_expired", "Preview expired; review the source again.", status=410
        )
    if acknowledge_workspace_sharing is not True:
        raise SkillsHubError(
            "capture_unsafe",
            "Confirm workspace disclosure of the edited case.",
            status=422,
        )
    reviewed = validate_cases([case], require_reviewed=True)[0]
    if (
        reviewed["id"] != plan.inventory["case_id"]
        or reviewed["source_group_id"] != plan.inventory["source_group_id"]
    ):
        raise SkillsHubError(
            "invalid_test_config",
            "Keep the retained case and source-group identities.",
            status=400,
        )
    if kind == "agent_context" and reviewed["expected_trigger"] is not None:
        raise SkillsHubError(
            "invalid_test_config",
            "Standing context has no root activation expectation.",
            status=400,
        )
    if secret_findings(
        canonical_json(reviewed), location={"kind": "case", "case_id": reviewed["id"]}
    ):
        raise SkillsHubError(
            "capture_unsafe",
            "Redact possible credentials before workspace sharing.",
            status=422,
        )
    source = plan.payload["source"]
    current = read_dataset_source(
        principal=principal,
        dataset_id=UUID(source["dataset_id"]),
        row_id=UUID(source["row_id"]),
        prompt_column_id=UUID(source["prompt_column_id"]),
        fixture_column_ids=[UUID(value) for value in source["fixture_column_ids"]],
    )
    if (
        source_fingerprint != plan.fingerprint
        or current["source_fingerprint"] != plan.fingerprint
    ):
        raise SkillsHubError(
            "source_conflict",
            "Dataset revision changed; review a new preview.",
            status=409,
        )
    from model_hub.models.skills_hub_v11 import HubSkillTestSuite

    with transaction.atomic():
        get_resource(principal=principal, resource_id=resource_id, kind=kind, lock=True)
        existing = replay()
        if existing is not None:
            return existing
        if plan.expires_at <= timezone.now():
            raise SkillsHubError(
                "capture_expired", "Preview expired before acceptance.", status=410
            )
        suite = HubSkillTestSuite.objects.filter(skill_id=resource_id).first()
        previous = (
            [{key: row[key] for key in CASE_FIELDS} for row in suite.cases]
            if suite
            else []
        )
        if any(row["id"] == reviewed["id"] for row in previous):
            raise SkillsHubError(
                "test_suite_conflict", "This source case is already saved.", status=409
            )
        saved = save_suite(
            principal=principal,
            skill_id=resource_id,
            revision=suite_revision,
            cases=[*previous, reviewed],
        )
        saved.cases[-1]["source"] = {
            **source,
            "source_fingerprint": plan.fingerprint,
            "sanitized_digest": fingerprint(reviewed),
            "replay_scope": "text_only",
        }
        # Keep previously accepted provenance when appending a different source.
        for index, old in enumerate(suite.cases if suite else []):
            if "source" in old:
                saved.cases[index]["source"] = old["source"]
        saved.digest = fingerprint(saved.cases)
        saved.save(update_fields=["cases", "digest"])
        response = {
            "suite_revision": saved.revision,
            "suite_digest": saved.digest,
            "case_id": reviewed["id"],
            "source_fingerprint": plan.fingerprint,
        }
        HubSkillContextOperation.objects.create(
            **identity,
            request_digest=digest,
            result_resource_id=resource_id,
            response=response,
            completed_at=timezone.now(),
        )
        plan.payload = None
        plan.save(update_fields=["payload"])
        return response
