import io
import json
import tarfile
import uuid
from typing import Any
from uuid import UUID

import structlog
from django.db import DatabaseError
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from simulate.models.agent_setup import AgentVersionSetup
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

logger = structlog.get_logger(__name__)

_STORAGE_ERRORS = (S3Error, HTTPError)
_CONTRACT_MAX_BYTES = 5 * 1_048_576


def contract_object_key(
    organization_id: UUID, agent_definition_id: UUID, agent_version_id: UUID
) -> str:
    return (
        f"agent-contracts/{organization_id}/{agent_definition_id}/"
        f"{agent_version_id}/{uuid.uuid4()}.json"
    )


def store_version_contract(
    *,
    organization_id: UUID,
    agent_version_id: UUID,
    contract: dict[str, Any],
    replace: bool = False,
) -> bool:
    """Keep the raw contract a version was read into, beside its stored agent.

    Written once, like the agent: a version that already has one, or whose agent
    is not stored yet, is left alone, unless ``replace`` says a build seeded from
    the reading brings the contract it finished with. Returns whether this call
    stored it.
    """
    rows = AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=agent_version_id
    )
    if not replace:
        rows = rows.filter(contract_object_key="")
    current = rows.values_list(
        "agent_version__agent_definition_id", "contract_object_key"
    ).first()
    if not contract or current is None:
        return False
    agent_definition_id, previous_key = current
    key = contract_object_key(organization_id, agent_definition_id, agent_version_id)
    body = json.dumps(contract, separators=(",", ":")).encode("utf-8")
    client = get_storage_client()
    try:
        ensure_bucket(client, UPLOAD_BUCKET_NAME)
        client.put_object(
            bucket_name=UPLOAD_BUCKET_NAME,
            object_name=key,
            data=io.BytesIO(body),
            length=len(body),
            content_type="application/json",
        )
    except _STORAGE_ERRORS:
        logger.warning(
            "agent_contract_not_stored",
            agent_version_id=str(agent_version_id),
            exc_info=True,
        )
        return False
    stored = AgentVersionSetup.no_workspace_objects.filter(
        agent_version_id=agent_version_id, contract_object_key=previous_key
    ).update(contract_object_key=key)
    if not stored:
        _remove_object(client, key)
    elif previous_key:
        _remove_object(client, previous_key)
    logger.info(
        "agent_contract_stored",
        agent_version_id=str(agent_version_id),
        size_bytes=len(body),
        stored=bool(stored),
        replaced=bool(stored and previous_key),
    )
    return bool(stored)


def stored_version_contract(agent_version_id: UUID) -> dict[str, Any] | None:
    """The raw contract stored for a version, or None when it has none."""
    key = (
        AgentVersionSetup.no_workspace_objects.filter(agent_version_id=agent_version_id)
        .values_list("contract_object_key", flat=True)
        .first()
    )
    if not key:
        return None
    response = None
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
        contract = json.loads(response.read())
    except _STORAGE_ERRORS:
        logger.warning(
            "agent_contract_unreadable",
            agent_version_id=str(agent_version_id),
            exc_info=True,
        )
        return None
    except ValueError:
        logger.warning("agent_contract_invalid", agent_version_id=str(agent_version_id))
        return None
    finally:
        if response is not None:
            response.close()
            response.release_conn()
    return contract if isinstance(contract, dict) else None


def _remove_object(client, key: str) -> None:
    try:
        client.remove_object(UPLOAD_BUCKET_NAME, key)
    except _STORAGE_ERRORS:
        logger.warning("agent_contract_orphan_left", object_key=key, exc_info=True)


def seeded_from_reading(job) -> bool:
    """Whether the build started from its version's stored reading, so what it
    finished with may stand in for that reading."""
    return bool(((job.payload or {}).get("metadata") or {}).get("seeded_contract"))


def store_build_contract(
    job, *, agent_version_id: UUID | None = None, archive: bytes | None = None
) -> bool:
    """Keep a build's contract on the version the build stored its agent on.

    The build's authoring archive and its agent arrive in either order; whichever
    comes second stores the contract, since it needs both. A build seeded from the
    version's reading replaces the reading's contract with its own.
    """
    from simulate.services.hosted_harness_gateway import authored_contract

    if agent_version_id is None:
        from simulate.services.hosted_harness import frozen_build_pin

        pinned = frozen_build_pin(job)
        agent_version_id = pinned.id if pinned else job.agent_version_id
    if agent_version_id is None:
        return False
    if archive is None:
        archive = _frozen_authoring_archive(job)
    if archive is None:
        return False
    try:
        return store_version_contract(
            organization_id=job.organization_id,
            agent_version_id=agent_version_id,
            contract=authored_contract(archive),
            replace=seeded_from_reading(job),
        )
    except DatabaseError:
        logger.warning(
            "agent_contract_not_stored",
            job_id=str(job.id),
            agent_version_id=str(agent_version_id),
            exc_info=True,
        )
        return False


def store_environment_version_contract(environment_version_id: UUID) -> bool:
    """Keep the contract a rebuilt environment version carries on the agent version
    it was built for, so a version made outside a build (a provider Sync, a prompt
    edit) gets the contract its first build wrote. Written once, like any other."""
    from simulate.models import HarnessEnvironmentVersion
    from simulate.services.harness_versioning.archives import (
        get_archive,
        read_members,
    )

    environment_version = (
        HarnessEnvironmentVersion.no_workspace_objects.filter(id=environment_version_id)
        .values("organization_id", "agent_version_id", "archive_object_key")
        .first()
    )
    if (
        environment_version is None
        or not environment_version["agent_version_id"]
        or not environment_version["archive_object_key"]
    ):
        return False
    try:
        body = get_archive(environment_version["archive_object_key"])
        members = read_members(body, {"contract.json"}, _CONTRACT_MAX_BYTES)
        contract = json.loads(members["contract.json"])
    except (*_STORAGE_ERRORS, tarfile.TarError, KeyError, ValueError) as error:
        logger.warning(
            "environment_contract_unreadable",
            environment_version_id=str(environment_version_id),
            error=type(error).__name__,
        )
        return False
    if not isinstance(contract, dict):
        return False
    try:
        return store_version_contract(
            organization_id=environment_version["organization_id"],
            agent_version_id=environment_version["agent_version_id"],
            contract=contract,
        )
    except DatabaseError:
        logger.warning(
            "agent_contract_not_stored",
            environment_version_id=str(environment_version_id),
            exc_info=True,
        )
        return False


def _frozen_authoring_archive(job) -> bytes | None:
    metadata = (job.payload or {}).get("metadata") or {}
    key = str(metadata.get("authoring_object_key") or "").strip()
    if not key:
        return None
    response = None
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
        return response.read()
    except _STORAGE_ERRORS:
        logger.warning(
            "authoring_archive_unreadable", job_id=str(job.id), exc_info=True
        )
        return None
    finally:
        if response is not None:
            response.close()
            response.release_conn()
