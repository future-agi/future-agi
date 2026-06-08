from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from accounts.services.onboarding.lifecycle_preview_snapshots import (
    DEFAULT_TARGET_ROUTE,
    MANIFEST_SCHEMA_VERSION,
    PREVIEW_SOURCE,
    preview_campaign_for_snapshot,
    preview_send_log_for_snapshot,
)
from accounts.services.onboarding.lifecycle_registry import lifecycle_campaign_by_key
from accounts.services.onboarding.lifecycle_template_context import (
    render_lifecycle_email_preview,
)
from accounts.services.onboarding.lifecycle_template_contract import (
    required_context_keys_for_template,
)

APPROVAL_METADATA_KEY = "preview_approval"
PREVIEW_APPROVAL_MISSING_REASON = "preview_approval_missing"
APPROVAL_RECORD_SCHEMA_VERSION = (
    "onboarding-lifecycle-preview-approval-record-2026-05-29.v1"
)
APPROVAL_RECORD_SOURCE = "lifecycle_preview_approval_record"


@dataclass(frozen=True)
class LifecyclePreviewApproval:
    path: str
    manifest_sha256: str
    generated_at: str
    campaign_entries: dict
    approval_record_path: str | None = None
    approval_record_sha256: str | None = None
    approved_by: str | None = None
    approved_at: str | None = None
    approved_campaign_entries: dict | None = None

    @property
    def campaign_keys(self):
        if self.approved_campaign_entries is not None:
            return tuple(self.approved_campaign_entries)
        return tuple(self.campaign_entries)

    def has_campaign(self, campaign_key):
        if campaign_key not in self.campaign_entries:
            return False
        if self.approved_campaign_entries is None:
            return True
        return campaign_key in self.approved_campaign_entries

    def metadata_for_campaign(self, campaign_key):
        entry = self.campaign_entries[campaign_key]
        metadata = {
            "manifest_path": self.path,
            "manifest_sha256": self.manifest_sha256,
            "manifest_generated_at": self.generated_at,
            "campaign_key": campaign_key,
            "html_file": entry["html_file"],
            "text_file": entry["text_file"],
            "html_sha256": entry["html_sha256"],
            "text_sha256": entry["text_sha256"],
        }
        if self.approval_record_path:
            metadata.update(
                {
                    "approval_record_path": self.approval_record_path,
                    "approval_record_sha256": self.approval_record_sha256,
                    "approved_by": self.approved_by,
                    "approved_at": self.approved_at,
                }
            )
        return metadata


@dataclass(frozen=True)
class LifecyclePreviewApprovalRecordResult:
    output_path: str
    manifest_sha256: str
    approval_record_sha256: str
    approved_by: str
    approved_at: str
    campaign_keys: tuple[str, ...]

    def to_payload(self):
        return {
            "output_path": self.output_path,
            "manifest_sha256": self.manifest_sha256,
            "approval_record_sha256": self.approval_record_sha256,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "campaign_keys": list(self.campaign_keys),
        }


def _manifest_error(message):
    return ImproperlyConfigured(
        f"Invalid lifecycle preview approval manifest: {message}"
    )


def _require_keys(mapping, expected, path):
    if set(mapping) != expected:
        missing = sorted(expected - set(mapping))
        extra = sorted(set(mapping) - expected)
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unexpected {', '.join(extra)}")
        raise _manifest_error(f"{path} has invalid fields ({'; '.join(parts)}).")


def _require_text(mapping, key, path):
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _manifest_error(f"{path}.{key} must be a non-empty string.")
    return value


def _require_sha(value, path):
    if not isinstance(value, str) or len(value) != 64:
        raise _manifest_error(f"{path} must be a SHA-256 hex digest.")
    try:
        int(value, 16)
    except ValueError as exc:
        raise _manifest_error(f"{path} must be a SHA-256 hex digest.") from exc
    return value


BASE_MANIFEST_CAMPAIGN_ENTRY_KEYS = frozenset(
    {
        "campaign_key",
        "campaign_group",
        "template_key",
        "template_version",
        "primary_path",
        "activation_stage",
        "target_action_id",
        "target_success_event",
        "route_strategy",
        "subject",
        "preheader",
        "html_file",
        "text_file",
        "html_sha256",
        "text_sha256",
        "required_context_keys",
        "digest_preview_required",
        "generated_at",
    }
)
DYNAMIC_MANIFEST_CAMPAIGN_ENTRY_KEYS = {
    "cross_path_expansion": frozenset({"expansion_target_path"}),
}


def _expected_preview_values(campaign, generated_at):
    now = parse_datetime(generated_at)
    if now is None:
        raise _manifest_error("manifest.generated_at must be an ISO datetime.")
    preview_campaign = preview_campaign_for_snapshot(campaign)
    send_log = preview_send_log_for_snapshot(preview_campaign, now=now)
    preview = render_lifecycle_email_preview(
        send_log=send_log,
        campaign=preview_campaign,
        target_route=DEFAULT_TARGET_ROUTE,
        now=now,
    )
    return preview_campaign, preview


def _validate_campaign_entry(entry, index, manifest_generated_at):
    path = f"campaigns.{index}"
    campaign_key = _require_text(entry, "campaign_key", path)
    expected_keys = BASE_MANIFEST_CAMPAIGN_ENTRY_KEYS | (
        DYNAMIC_MANIFEST_CAMPAIGN_ENTRY_KEYS.get(campaign_key) or frozenset()
    )
    _require_keys(entry, expected_keys, path)
    campaign = lifecycle_campaign_by_key(campaign_key)
    if not campaign:
        raise _manifest_error(f"{path}.campaign_key is not configured.")
    preview_campaign, preview = _expected_preview_values(
        campaign, manifest_generated_at
    )

    expected_values = {
        "campaign_group": preview_campaign["campaign_group"],
        "template_key": preview_campaign["template_key"],
        "template_version": preview_campaign["template_version"],
        "primary_path": preview_campaign["primary_path"],
        "activation_stage": preview_campaign["entry_stages"][0],
        "target_action_id": preview_campaign["target_action_id"],
        "target_success_event": preview_campaign["target_success_event"],
        "route_strategy": preview_campaign["route_strategy"],
        "subject": preview["subject"],
        "preheader": preview["preheader"],
        "generated_at": manifest_generated_at,
    }
    if campaign_key in DYNAMIC_MANIFEST_CAMPAIGN_ENTRY_KEYS:
        expected_values["expansion_target_path"] = preview_campaign[
            "expansion_target_path"
        ]
    for key, expected in expected_values.items():
        if entry[key] != expected:
            raise _manifest_error(f"{path}.{key} does not match current preview.")

    expected_context_keys = sorted(
        required_context_keys_for_template(campaign["template_key"])
    )
    if entry["required_context_keys"] != expected_context_keys:
        raise _manifest_error(
            f"{path}.required_context_keys does not match template contract."
        )
    expected_digest_requirement = campaign.get("requires_digest_preview") is True
    if entry["digest_preview_required"] is not expected_digest_requirement:
        raise _manifest_error(
            f"{path}.digest_preview_required does not match current registry."
        )
    _require_text(entry, "html_file", path)
    _require_text(entry, "text_file", path)
    _require_sha(entry["html_sha256"], f"{path}.html_sha256")
    _require_sha(entry["text_sha256"], f"{path}.text_sha256")
    return campaign_key


def _approval_record_error(message):
    return ImproperlyConfigured(f"Invalid lifecycle preview approval record: {message}")


def _record_error(message):
    return _approval_record_error(message)


def _require_record_keys(mapping, expected, path):
    if set(mapping) != expected:
        missing = sorted(expected - set(mapping))
        extra = sorted(set(mapping) - expected)
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra:
            parts.append(f"unexpected {', '.join(extra)}")
        raise _record_error(f"{path} has invalid fields ({'; '.join(parts)}).")


def _require_record_text(mapping, key, path, *, allow_empty=False):
    value = mapping.get(key)
    if not isinstance(value, str):
        raise _record_error(f"{path}.{key} must be a string.")
    if not allow_empty and not value.strip():
        raise _record_error(f"{path}.{key} must be a non-empty string.")
    return value


def _require_record_datetime(mapping, key, path):
    value = _require_record_text(mapping, key, path)
    if parse_datetime(value) is None:
        raise _record_error(f"{path}.{key} must be an ISO datetime.")
    return value


def _require_record_sha(value, path):
    if not isinstance(value, str) or len(value) != 64:
        raise _record_error(f"{path} must be a SHA-256 hex digest.")
    try:
        int(value, 16)
    except ValueError as exc:
        raise _record_error(f"{path} must be a SHA-256 hex digest.") from exc
    return value


def _approval_record_campaign_entry(entry):
    return {
        "campaign_key": entry["campaign_key"],
        "html_sha256": entry["html_sha256"],
        "text_sha256": entry["text_sha256"],
    }


def _selected_campaign_entries(approval, campaign_keys):
    if not campaign_keys:
        return tuple(approval.campaign_entries[key] for key in approval.campaign_keys)
    seen = set()
    entries = []
    for campaign_key in campaign_keys:
        if campaign_key in seen:
            raise _record_error(f"campaign {campaign_key} is duplicated.")
        seen.add(campaign_key)
        if campaign_key not in approval.campaign_entries:
            raise _record_error(f"campaign {campaign_key} is not in the manifest.")
        entries.append(approval.campaign_entries[campaign_key])
    return tuple(entries)


def _approval_record_payload(
    approval,
    *,
    approved_by,
    approved_at,
    campaign_keys=None,
    note="",
):
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise _record_error("approved_by must be a non-empty string.")
    if not isinstance(note, str):
        raise _record_error("note must be a string.")
    approved_at_text = approved_at.isoformat()
    selected_entries = _selected_campaign_entries(approval, campaign_keys or ())
    return {
        "schema_version": APPROVAL_RECORD_SCHEMA_VERSION,
        "source": APPROVAL_RECORD_SOURCE,
        "decision": "approved",
        "approved_by": approved_by.strip(),
        "approved_at": approved_at_text,
        "manifest_sha256": approval.manifest_sha256,
        "manifest_generated_at": approval.generated_at,
        "campaign_count": len(selected_entries),
        "campaigns": [
            _approval_record_campaign_entry(entry) for entry in selected_entries
        ],
        "note": note,
    }


def _validate_approval_record_campaign(entry, index, approval):
    path = f"campaigns.{index}"
    _require_record_keys(
        entry,
        {"campaign_key", "html_sha256", "text_sha256"},
        path,
    )
    campaign_key = _require_record_text(entry, "campaign_key", path)
    manifest_entry = approval.campaign_entries.get(campaign_key)
    if not manifest_entry:
        raise _record_error(f"{path}.campaign_key is not in the manifest.")
    expected_values = {
        "html_sha256": manifest_entry["html_sha256"],
        "text_sha256": manifest_entry["text_sha256"],
    }
    for key, expected in expected_values.items():
        if entry[key] != expected:
            raise _record_error(f"{path}.{key} does not match the manifest.")
        _require_record_sha(entry[key], f"{path}.{key}")
    return campaign_key


def _load_approval_record(path, approval):
    record_path = Path(path)
    try:
        raw = record_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _record_error(f"{record_path} could not be read.") from exc
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _record_error(f"{record_path} is not valid JSON.") from exc
    if not isinstance(record, dict):
        raise _record_error("record root must be a mapping.")

    _require_record_keys(
        record,
        {
            "schema_version",
            "source",
            "decision",
            "approved_by",
            "approved_at",
            "manifest_sha256",
            "manifest_generated_at",
            "campaign_count",
            "campaigns",
            "note",
        },
        "record",
    )
    if record["schema_version"] != APPROVAL_RECORD_SCHEMA_VERSION:
        raise _record_error("schema_version is not supported.")
    if record["source"] != APPROVAL_RECORD_SOURCE:
        raise _record_error("source is not lifecycle preview approval record.")
    if record["decision"] != "approved":
        raise _record_error("decision must be approved.")
    approved_by = _require_record_text(record, "approved_by", "record")
    approved_at = _require_record_datetime(record, "approved_at", "record")
    if record["manifest_sha256"] != approval.manifest_sha256:
        raise _record_error("manifest_sha256 does not match the manifest.")
    _require_record_sha(record["manifest_sha256"], "record.manifest_sha256")
    if record["manifest_generated_at"] != approval.generated_at:
        raise _record_error("manifest_generated_at does not match the manifest.")
    if not isinstance(record["note"], str):
        raise _record_error("record.note must be a string.")
    campaigns = record["campaigns"]
    if not isinstance(campaigns, list) or not campaigns:
        raise _record_error("campaigns must be a non-empty list.")
    if record["campaign_count"] != len(campaigns):
        raise _record_error("campaign_count does not match campaigns.")

    approved_entries = {}
    for index, entry in enumerate(campaigns):
        if not isinstance(entry, dict):
            raise _record_error(f"campaigns.{index} must be a mapping.")
        campaign_key = _validate_approval_record_campaign(entry, index, approval)
        if campaign_key in approved_entries:
            raise _record_error(f"campaigns.{index}.campaign_key is duplicated.")
        approved_entries[campaign_key] = entry

    return {
        "path": str(record_path),
        "sha256": sha256(raw.encode("utf-8")).hexdigest(),
        "approved_by": approved_by,
        "approved_at": approved_at,
        "campaign_entries": approved_entries,
    }


def load_lifecycle_preview_approval_manifest(path):
    return load_lifecycle_preview_approval(path)


def load_lifecycle_preview_approval(path, approval_record_path=None):
    manifest_path = Path(path)
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _manifest_error(f"{manifest_path} could not be read.") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _manifest_error(f"{manifest_path} is not valid JSON.") from exc
    if not isinstance(manifest, dict):
        raise _manifest_error("manifest root must be a mapping.")

    _require_keys(
        manifest,
        {"schema_version", "generated_at", "source", "count", "campaigns"},
        "manifest",
    )
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise _manifest_error("schema_version is not supported.")
    if manifest["source"] != PREVIEW_SOURCE:
        raise _manifest_error("source is not lifecycle preview snapshot.")
    generated_at = _require_text(manifest, "generated_at", "manifest")
    campaigns = manifest["campaigns"]
    if not isinstance(campaigns, list) or not campaigns:
        raise _manifest_error("campaigns must be a non-empty list.")
    if manifest["count"] != len(campaigns):
        raise _manifest_error("count does not match campaigns.")

    entries = {}
    for index, entry in enumerate(campaigns):
        if not isinstance(entry, dict):
            raise _manifest_error(f"campaigns.{index} must be a mapping.")
        campaign_key = _validate_campaign_entry(entry, index, generated_at)
        if campaign_key in entries:
            raise _manifest_error(f"campaigns.{index}.campaign_key is duplicated.")
        entries[campaign_key] = entry

    approval = LifecyclePreviewApproval(
        path=str(manifest_path),
        manifest_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        generated_at=generated_at,
        campaign_entries=entries,
    )
    if not approval_record_path:
        return approval

    record = _load_approval_record(approval_record_path, approval)
    return LifecyclePreviewApproval(
        path=approval.path,
        manifest_sha256=approval.manifest_sha256,
        generated_at=approval.generated_at,
        campaign_entries=approval.campaign_entries,
        approval_record_path=record["path"],
        approval_record_sha256=record["sha256"],
        approved_by=record["approved_by"],
        approved_at=record["approved_at"],
        approved_campaign_entries=record["campaign_entries"],
    )


def write_lifecycle_preview_approval_record(
    *,
    manifest_path,
    output_path,
    approved_by,
    campaign_keys=None,
    approved_at=None,
    note="",
    force=False,
):
    approval = load_lifecycle_preview_approval_manifest(manifest_path)
    output = Path(output_path)
    if output.exists() and not force:
        raise _record_error(f"{output} already exists. Use --force to overwrite.")
    approved_at = approved_at or timezone.now()
    record = _approval_record_payload(
        approval,
        approved_by=approved_by,
        approved_at=approved_at,
        campaign_keys=campaign_keys,
        note=note,
    )
    raw = json.dumps(record, indent=2, sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(raw, encoding="utf-8")
    return LifecyclePreviewApprovalRecordResult(
        output_path=str(output),
        manifest_sha256=approval.manifest_sha256,
        approval_record_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        approved_by=record["approved_by"],
        approved_at=record["approved_at"],
        campaign_keys=tuple(entry["campaign_key"] for entry in record["campaigns"]),
    )
