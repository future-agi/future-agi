from __future__ import annotations

from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile

from simulate.models import HarnessCredentialFile, HarnessEnvironmentCredentials


PLATFORM_FILE_MANAGER = "harness_environment_file"


def request_scope(request) -> tuple[Any, Any]:
    organization = getattr(request, "organization", None) or getattr(
        request.user, "organization", None
    )
    workspace = getattr(request, "workspace", None)
    return organization, workspace


def store_credential_file(uploaded, environment_name: str, *, organization, workspace):
    content = uploaded.read()
    record = HarnessCredentialFile(
        organization=organization,
        workspace=workspace,
        environment_name=environment_name,
        filename=str(getattr(uploaded, "name", None) or "credential")[:255],
        content_type=str(
            getattr(uploaded, "content_type", None) or "application/octet-stream"
        )[:255],
        size=len(content),
    )
    record.set_content(content)
    record.save()
    return record


def credential_file_ref(record: HarnessCredentialFile) -> dict[str, str]:
    return {
        "manager": PLATFORM_FILE_MANAGER,
        "key": str(record.id),
        "version": str(record.updated_at.timestamp()),
        "purpose": f"RL environment credential file for {record.environment_name}",
    }


def _platform_file_ids(secret_refs: dict[str, dict[str, Any]]) -> list[str]:
    return [
        str(ref.get("key"))
        for ref in secret_refs.values()
        if ref.get("manager") == PLATFORM_FILE_MANAGER and ref.get("key")
    ]


def materialize_secret_refs(
    secret_refs: dict[str, dict[str, Any]], *, organization, client
) -> dict[str, dict[str, Any]]:
    """Replace durable platform file refs with fresh attempt-scoped runner refs."""

    materialized: dict[str, dict[str, Any]] = {}
    for alias, ref in secret_refs.items():
        if ref.get("manager") != PLATFORM_FILE_MANAGER:
            materialized[alias] = ref
            continue
        record = HarnessCredentialFile.objects.get(
            id=ref["key"], organization=organization, deleted=False
        )
        uploaded = SimpleUploadedFile(
            record.filename,
            record.get_content(),
            content_type=record.content_type,
        )
        response = client.upload_secret_file(uploaded, record.environment_name)
        materialized[alias] = response["secret_ref"]
    return materialized


def save_environment_credentials(
    harness_job_id: str,
    *,
    environment_values: dict[str, str],
    secret_refs: dict[str, dict[str, Any]],
    organization,
    workspace,
) -> HarnessEnvironmentCredentials:
    profile, _ = HarnessEnvironmentCredentials.objects.get_or_create(
        harness_job_id=harness_job_id,
        defaults={"organization": organization, "workspace": workspace},
    )
    if profile.organization_id != organization.id:
        raise PermissionError("harness environment belongs to another organization")
    profile.workspace = workspace
    profile.set_environment(environment_values)
    profile.credential_file_ids = _platform_file_ids(secret_refs)
    profile.save()
    return profile


def credentials_for_rerun(
    harness_job_id: str,
    *,
    organization,
    client,
    environment_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    profile = HarnessEnvironmentCredentials.objects.get(
        harness_job_id=harness_job_id,
        organization=organization,
        deleted=False,
    )
    environment = profile.get_environment()
    environment.update(environment_overrides or {})
    durable_refs = {}
    for file_id in profile.credential_file_ids:
        record = HarnessCredentialFile.objects.get(
            id=file_id, organization=organization, deleted=False
        )
        durable_refs[record.environment_name] = credential_file_ref(record)
    refs = materialize_secret_refs(
        durable_refs, organization=organization, client=client
    )
    if environment_overrides:
        profile.set_environment(environment)
        profile.save(update_fields=["encrypted_environment", "updated_at"])
    return environment, refs
