"""Scenario editing on a composition (`environment-releases.md` §7.8).

A composition's chat runs the environment's builder on a workspace assembled here from what the
composition holds: its release archives, its binding and proof, and the agent's proved
trajectories. Saving in the chat seals a child `SuiteRelease` of the composition's suite in the
sandbox and leaves it, with the binding re-proved against it, under `composition-change/` in the
checkpoint. Publishing registers that suite for the organization, makes it the composition's suite,
and re-indexes the scenarios a Run can select. The world, tools, policy and binding never change;
Runs already made keep the manifest they froze.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from simulate.models import (
    HarnessEnvironmentRelease,
    HostedHarnessConversation,
    HostedHarnessConversationLease,
    HostedHarnessJob,
)
from simulate.services.harness_releases import (
    _checked_binding,
    _checked_suite,
    _download,
    _error,
    _hex,
    _owned_by,
    _store_suite,
    _visible,
    archive_files,
    composition_of,
    scenario_documents,
)
from simulate.services.hosted_harness import HostedHarnessError

WORKSPACE_SCHEMA = "futureagi.composition-workspace.v1"
CHANGE_SCHEMA = "futureagi.composition-change.v1"
CHANGE = "composition-change"


def composition_revision(job: HostedHarnessJob) -> str:
    """The revision a composition chat edits: its current suite."""
    return str(composition_of(job)["suite"]["digest"])


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def composition_workspace_archive(job: HostedHarnessJob) -> bytes:
    """The workspace a composition chat opens on (contract §7.8 layout)."""
    composition = composition_of(job)
    files: list[tuple[str, bytes]] = []
    for entry in composition["environment"]:
        prefix = f"releases/environment/{_hex(entry['digest'])}"
        files += [
            (f"{prefix}/{name}", body)
            for name, body in archive_files(_download(entry["object_key"])).items()
        ]
    files += [
        (f"releases/suite/{name}", body)
        for name, body in archive_files(
            _download(composition["suite"]["object_key"])
        ).items()
    ]
    binding = composition["binding"]
    files.append(("binding.json", _json(binding)))
    if isinstance(composition.get("binding_proof"), dict):
        files.append(("binding-proof.json", _json(composition["binding_proof"])))
    files.append(("trajectories.json", _json(composition.get("trajectories") or {})))
    files.append(
        (
            "composition.json",
            _json(
                {
                    "schema_version": WORKSPACE_SCHEMA,
                    "composition_id": str(job.id),
                    "environment": [
                        {"digest": entry["digest"], "name": entry["name"]}
                        for entry in composition["environment"]
                    ],
                    "suite": {
                        "digest": composition["suite"]["digest"],
                        "name": composition["suite"]["name"],
                    },
                    "binding": {"digest": binding["digest"], "mode": binding["mode"]},
                }
            ),
        )
    )
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as target:
        for name, body in files:
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(body), 0, 0o644
            target.addfile(info, io.BytesIO(body))
    return gzip.compress(raw.getvalue(), mtime=0)


def _extract_change(checkpoint: bytes, into: Path) -> Path | None:
    """`composition-change/` from a chat checkpoint, or None when it holds no change."""
    from simulate.services.hosted_harness_gateway import _archive_parts

    root = into / CHANGE
    found = False
    with tarfile.open(fileobj=io.BytesIO(checkpoint), mode="r:gz") as source:
        for member in source.getmembers():
            parts = _archive_parts(member.name)
            if not parts or parts[0] != CHANGE or not member.isfile():
                continue
            if any(part in {"", ".", ".."} for part in parts):
                raise _error(
                    "conversation_checkpoint_invalid",
                    "a change path is not relative",
                    422,
                )
            target = into.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.extractfile(member).read())
            found = True
    return root if found else None


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _error(
            "composition_change_invalid", f"{path.name} is missing or unreadable", 422
        ) from exc


def _applied(
    environment: HostedHarnessJob, composition: dict[str, Any], change: Path
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Register a sealed change and return (composition, suite manifest, personas)."""
    summary = _read(change / "change.json")
    current = composition["suite"]["digest"]
    if not isinstance(summary, dict) or summary.get("schema_version") != CHANGE_SCHEMA:
        raise _error(
            "composition_change_invalid", "change.json is not a composition change", 422
        )
    if summary.get("parent") != current:
        raise _error(
            "composition_change_invalid",
            "the change was sealed against another version of the suite",
            422,
        )
    organization = environment.organization
    environment_digest = composition["environment"][-1]["digest"]
    suite_dir = change / "suite"
    suite, personas = _checked_suite(suite_dir, environment_digest)
    if (suite.get("parent") or {}).get("digest") != current or suite[
        "digest"
    ] != summary.get("suite"):
        raise _error(
            "composition_change_invalid",
            "the sealed suite is not a child of the composition's suite",
            422,
        )
    proof_path = change / "binding-proof.json"
    proof = _read(proof_path) if proof_path.is_file() else None
    binding = composition["binding"]
    _checked_binding(
        binding,
        agent=binding["agent"],
        target_id=binding["agent"].get("provider_target_id") or "",
        environment_digest=environment_digest,
        suite_digest=suite["digest"],
        proof=proof,
    )
    release = _visible(HarnessEnvironmentRelease, organization, environment_digest)
    if release is None:
        raise _error(
            "release_not_found", "the composition's environment no longer exists", 404
        )
    with _owned_by(organization):
        row = _store_suite(organization, suite_dir, suite, personas, release)
    trajectories = _read(change / "trajectories.json")
    updated = {
        **composition,
        "suite": {"digest": row.digest, "name": row.name, "object_key": row.object_key},
        "trajectories": trajectories if isinstance(trajectories, dict) else {},
    }
    if binding["mode"] == "tool_routed":
        updated["binding_proof"] = proof
        updated["alignment"] = {
            **(composition.get("alignment") or {}),
            "unscorable": [
                {"scenario_key": key, "reason": entry.get("reason") or ""}
                for key, entry in sorted((proof.get("scenarios") or {}).items())
                if entry.get("status") == "unscorable"
            ],
        }
    return updated, suite, personas


def promote_composition_checkpoint(
    conversation: HostedHarnessConversation,
    environment: HostedHarnessJob,
    checkpoint: bytes,
    *,
    digest: str,
    sequence: int,
    expected_revision: str,
    retire_runtime: bool,
) -> HostedHarnessJob | None:
    """Publish a composition chat's checkpoint: the change it sealed, if any (contract §7.8).

    Mirrors the authored-environment promotion: only a completed composition is published to,
    an older checkpoint never rolls it back, and one built on a superseded suite is refused as
    stale.
    """
    from simulate.services.harness_scenarios import index_scenarios
    from simulate.services.hosted_harness_conversation import _bind_added_scenarios
    from simulate.services.hosted_harness_gateway import authoring_basis

    basis = authoring_basis(checkpoint) or expected_revision
    stale = None
    with tempfile.TemporaryDirectory(prefix="composition-change-") as scratch:
        change = _extract_change(checkpoint, Path(scratch))
        with transaction.atomic():
            environment = HostedHarnessJob.no_workspace_objects.select_for_update().get(
                id=environment.id
            )
            if environment.state != HostedHarnessJob.State.COMPLETED:
                return None
            payload = dict(environment.payload or {})
            metadata = dict(payload.get("metadata") or {})
            composition = dict(metadata["release_composition"])
            current = composition["suite"]["digest"]
            checkpoints = dict(metadata.get("conversation_checkpoints") or {})
            conversation_id = str(conversation.id)
            published = dict(checkpoints.get(conversation_id) or {})
            published_sequence = int(published.get("sequence") or 0)
            if published.get("digest") == digest or sequence < published_sequence:
                return environment
            if sequence == published_sequence and published:
                raise HostedHarnessError(
                    "conversation_checkpoint_conflict",
                    "the conversation sequence is already bound to another checkpoint",
                    status_code=409,
                )
            if basis != current:
                stale = current
            else:
                suite = personas = None
                if change is not None:
                    composition, suite, personas = _applied(
                        environment, composition, change
                    )
                checkpoints[conversation_id] = {
                    "digest": digest,
                    "sequence": sequence,
                    "content_revision": composition["suite"]["digest"],
                }
                metadata["release_composition"] = composition
                metadata["authoring_revision"] = composition["suite"]["digest"]
                metadata["conversation_checkpoints"] = checkpoints
                payload["metadata"] = metadata
                environment.payload = payload
                fields = ["payload", "updated_at"]
                if suite is not None:
                    environment.scenario_count = len(suite["scenarios"])
                    environment.content_updated_at = timezone.now()
                    fields += ["scenario_count", "content_updated_at"]
                environment.save(update_fields=fields)
                if suite is not None:
                    suite_dir = change / "suite"
                    index_scenarios(
                        environment,
                        scenario_documents(
                            suite, lambda path: (suite_dir / path).read_bytes()
                        ),
                        prune=True,
                        exact_keys=True,
                    )
                    _bind_added_scenarios(
                        environment,
                        personas={
                            persona["scenario_key"]: {
                                key: value
                                for key, value in persona.items()
                                if key != "scenario_key"
                            }
                            for persona in personas
                        },
                    )
    if stale is not None:
        if retire_runtime:
            HostedHarnessConversationLease.no_workspace_objects.filter(
                conversation=conversation,
                attempt__isnull=True,
                state__in=[
                    HostedHarnessConversationLease.State.STARTING,
                    HostedHarnessConversationLease.State.ACTIVE,
                ],
            ).update(
                state=HostedHarnessConversationLease.State.EXPIRED,
                updated_at=timezone.now(),
            )
        raise HostedHarnessError(
            "conversation_checkpoint_stale",
            "the composition's suite changed after this chat workspace was loaded",
            status_code=409,
        )
    return environment
