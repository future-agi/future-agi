"""Emit synthetic frontend fixtures through the frozen DRF serializers.

Run with Django initialized on the test VM; the sole argument is the output path.
This does not query storage, mint credentials, call providers or execute a job.
"""

import json
from pathlib import Path

from model_hub.serializers import skills_hub_r3 as r3
from model_hub.serializers import skills_hub_v12 as context

RESOURCE = "00000000-0000-4000-8000-000000000001"
VERSION = "00000000-0000-4000-8000-000000000002"
LEASE = "00000000-0000-4000-8000-000000000003"
DIGEST = "sha256:" + "a" * 64
TIME = "2026-11-08T12:00:00Z"
MODEL = {"model_id": "synthetic-model", "temperature": 0, "max_output_tokens": 100}


def samples() -> dict:
    native = {
        "code_commit": "b" * 40,
        "code_digest": DIGEST,
        "entrypoint": "synthetic.app:invoke",
        "image_digest": DIGEST,
        "runtime_version": "3.11.14",
        "packages": [{"name": "synthetic-app", "version": "1.0.0", "sha256": "a" * 64}],
        "package_lock_digest": DIGEST,
        "model": MODEL,
        "connection_id": RESOURCE,
        "connection_revision": "1",
        "tools_digest": DIGEST,
        "retrieval_fixture_digest": DIGEST,
        "state_schema": "synthetic-v1",
        "checkpoint_digest": DIGEST,
        "side_effect_policy": "fixture_only",
        "reset_entrypoint": "synthetic.app:reset",
        "context_mount": "/fi_context/",
        "observable_boundaries": ["catalog", "child_selection", "tool_result"],
    }
    text = {
        "model": MODEL,
        "judge": None,
        "harness_version": "contexts-text-v1.2",
        "adapter_version": "synthetic-v1",
        "evaluator_version": "synthetic-v1",
    }
    evidence = {
        "schema": 1,
        "source": "simulation_native",
        "source_id": LEASE,
        "subject_kind": "agent_context",
        "subject_id": RESOURCE,
        "candidate_digest": DIGEST,
        "execution_fingerprint": DIGEST,
        "status": "failed",
        "visibility": "workspace",
        "payload_expires_at": TIME,
        "configuration": {"schema": 1, "digest": DIGEST, "native": native},
        "annotation_revision": 2,
    }
    target = {
        "binding_id": RESOURCE,
        "environment_id": None,
        "revision": 0,
        "configuration_digest": DIGEST,
    }
    transition = {
        "label": "production",
        "expected_version": None,
        "version": 1,
        "expected_release_revision": 0,
        "expected_security_revision": 0,
        "expected_impact_fingerprint": DIGEST,
    }
    credential = {
        "purpose": "consume",
        "grants": [
            {
                "kind": "agent_context",
                "resource_id": RESOURCE,
                "labels": ["production"],
                "version_ids": [],
            }
        ],
        "binding_ids": [],
        "expires_at": TIME,
    }
    metadata = {
        **credential,
        "id": RESOURCE,
        "created_at": TIME,
        "revoked_at": None,
        "last_used_at": None,
    }
    manifest = {
        "resource_id": RESOURCE,
        "kind": "agent_context",
        "name": "synthetic-agent",
        "description": "Synthetic purpose",
        "version": 1,
        "version_id": VERSION,
        "digest": DIGEST,
        "requested": {"label": "production"},
        "entrypoint": "AGENTS.md",
        "descriptor_path": ".futureagi/context.json",
        "files": [
            {
                "path": "AGENTS.md",
                "sha256": "a" * 64,
                "size_bytes": 5,
                "origin": {
                    "kind": "owned",
                    "skill_id": None,
                    "version_id": None,
                    "source_path": None,
                },
            }
        ],
        "file_count": 26,
        "file_offset": 0,
        "next_file_offset": 25,
        "created_at": TIME,
        "resolution_id": LEASE,
        "resolution_expires_at": TIME,
    }
    trace = {
        "project_id": RESOURCE,
        "trace_id": "synthetic-trace",
        "span_id": "synthetic-span",
        "start_time": TIME,
    }
    binding = {
        "id": RESOURCE,
        "context_id": VERSION,
        "revision": 1,
        "retired_at": None,
        "name": "Synthetic repository",
        "kind": "repository",
        "selector": {"version": 1},
        "update_mode": "repository_lock",
        "profile": "codex",
        "profile_version": "1",
        "repository": {
            "provider": "github",
            "repository_id": "123",
            "default_branch": "main",
            "project_scope": "",
        },
        "expected_report_interval_seconds": 3600,
    }
    receipt = {
        "event_id": RESOURCE,
        "session_id": None,
        "instance_id": VERSION,
        "sequence": 0,
        "stage": "installed",
        "version_id": VERSION,
        "digest": DIGEST,
        "materialization_digest": DIGEST,
        "observed_at": TIME,
        "details": {
            "path": None,
            "sha256": None,
            "ranges": [],
            "runtime_version": None,
            "adapter_version": "1",
            "diagnostic_codes": [],
            "trace_ref": trace,
        },
    }
    annotation = {
        "source": "skills_text",
        "source_id": RESOURCE,
        "annotation_revision": 0,
        "check_id": None,
        "attempt_id": None,
        "verdict": "cannot_assess",
        "cited_text": ' {"user_key": "retain_me"}\n',
        "comment": " needs human review ",
    }
    head = {
        "revision": 1,
        "expected_digest": DIGEST,
        "expected_head_version": 6,
        "review_fingerprint": DIGEST,
    }
    dependency = {
        "revision": 1,
        "links": [{"skill_id": RESOURCE, "selector": {"label": "production"}}],
        "refresh_skill_ids": [RESOURCE],
    }
    return {
        "NativeConfiguration": (r3.NativeConfiguration, native),
        "TextConfiguration": (r3.TextConfiguration, text),
        "EvidenceRef": (r3.EvidenceRef, evidence),
        "TargetConfiguration": (r3.TargetConfiguration, target),
        "PointerTransition": (r3.PointerTransition, transition),
        "ReleasePacket": (
            r3.ReleasePacket,
            {
                "schema": 1,
                "subject_kind": "agent_context",
                "subject_id": RESOURCE,
                "candidate_version_id": VERSION,
                "candidate_digest": DIGEST,
                "transition": transition,
                "target": target,
                "evidence": [evidence],
                "external_pipeline": {
                    "url": "https://example.test/ci/123",
                    "configuration_identity": "ci-v1",
                    "decision": "Operator recorded hold",
                    "trust": "operator_recorded_unimported",
                    "recorded_by": RESOURCE,
                },
            },
        ),
        "HumanAnnotation": (r3.HumanAnnotation, annotation),
        "NativeSelection": (
            r3.NativeSelection,
            {
                "child_selection": [{"skill_id": RESOURCE, "expectation": "forbidden"}],
                "catalog_digest": DIGEST,
                "invocation_mode": "natural",
            },
        ),
        "RetainedPlanAcceptance": (
            r3.RetainedPlanAcceptance,
            {
                "plan_id": RESOURCE,
                "expected_fingerprint": DIGEST,
                "item_key": "source/policy",
                "mode": "patch",
                "expected_head_version": 4,
                "expected_head_digest": DIGEST,
                "rights_confirmed": True,
            },
        ),
        "CheckpointRestore": (
            r3.CheckpointRestore,
            {"checkpoint_id": RESOURCE, "revision": 0, "expected_digest": DIGEST},
        ),
        "SpendPolicyInput": (
            r3.SpendPolicyInput,
            {"revision": 0, "enabled": False, "period_limit_usd": "0.00000000"},
        ),
        "CredentialInput": (context.CredentialInput, credential),
        "CredentialMetadata": (context.CredentialMetadata, metadata),
        "CredentialIssued": (
            context.CredentialIssued,
            {
                "id": RESOURCE,
                "secret": None,
                "secret_available": False,
                "metadata": metadata,
            },
        ),
        "ConsumptionManifest": (context.ConsumptionManifest, manifest),
        "ConsumptionChunk": (
            context.ConsumptionChunk,
            {
                "resource_id": RESOURCE,
                "version_id": VERSION,
                "digest": DIGEST,
                "path": "AGENTS.md",
                "sha256": "a" * 64,
                "size_bytes": 15,
                "total_chars": 9,
                "offset": 0,
                "next_offset": 2,
                "eof": False,
                "text": '"é',
            },
        ),
        "AccessCheck": (
            context.AccessCheck,
            {
                "allowed": True,
                "version_id": VERSION,
                "digest": DIGEST,
                "checked_at": TIME,
            },
        ),
        "TraceRef": (context.TraceRef, trace),
        "BindingOut": (context.BindingOut, binding),
        "ReceiptInput": (context.ReceiptInput, receipt),
        "SessionInput": (
            context.SessionInput,
            {
                "client_session_key": "synthetic-thread",
                "incarnation_id": RESOURCE,
                "resume_session_id": None,
                "adapter_version": "1",
                "runtime_version": "1",
                "runtime_config_digest": DIGEST,
            },
        ),
        "QuarantineInput": (
            context.QuarantineInput,
            {
                "revision": 0,
                "quarantined": True,
                "reason": "Synthetic safety exercise",
                "replacement_version_id": None,
            },
        ),
        "DraftStart": (context.DraftStart, {"revision": 0, "from_version": 4}),
        "DraftSave": (
            context.DraftSave,
            {
                "revision": 1,
                "base_version": 4,
                "files": [{"path": "AGENTS.md", "content": "Preserve exact bytes.\n"}],
                "description": "Retained purpose",
                "note": " note ",
            },
        ),
        "HeadAcknowledgment": (context.HeadAcknowledgment, head),
        "ContextPublish": (
            context.ContextPublish,
            {**head, "base_version": 4, "note": "repair", "acknowledge_warnings": True},
        ),
        "DependencyPreview": (context.DependencyPreview, dependency),
        "DependencyApply": (
            context.DependencyApply,
            {**dependency, "expected_fingerprint": DIGEST},
        ),
        "ResolvedLock": (
            context.ResolvedLock,
            {
                "schema": 1,
                "links": [
                    {
                        "mount": "skills/synthetic/",
                        "skill_id": RESOURCE,
                        "requested": {"label": "production"},
                        "version": 8,
                        "version_id": VERSION,
                        "digest": DIGEST,
                    }
                ],
            },
        ),
        "ContextSpec": (
            context.ContextSpec,
            {
                "schema": 1,
                "description": "Retain purpose",
                "links": dependency["links"],
            },
        ),
    }


def export(destination: str) -> None:
    result = {}
    for name, (serializer, value) in samples().items():
        instance = serializer(data=value)
        instance.is_valid(raise_exception=True)
        result[name] = instance.data
    Path(destination).write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    )
