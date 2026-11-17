"""Turning an approved submission into a Version (SPEC §3.2, §5.4). Called only from review.decide."""

from __future__ import annotations

from django.utils import timezone

from omega_hub.errors import HubError
from omega_hub.models import Entry, Publisher, Submission, Version
from omega_hub.services import audit, bundles, cache_keys, refs, storage

DEFAULT_CAPABILITIES = {"cli": True, "openInStudio": False, "deploy": False, "consoleTemplate": False}


def report_step(report: dict | None, step_id: str) -> dict:
    for step in (report or {}).get("steps", []):
        if step.get("id") == step_id:
            return step
    return {}


def flag_ids(report: dict | None) -> list[str]:
    return sorted({
        item["id"]
        for step in (report or {}).get("steps", [])
        for item in step.get("findings", [])
        if item.get("severity") == "flag" and item.get("id")
    })


def public_check_summary(report: dict) -> dict:
    """The public subset (SPEC §4.5): step statuses, flag counts per rule, checker identity, run time."""
    steps = {step.get("id"): step.get("status") for step in report.get("steps", []) if step.get("id")}
    per_rule: dict[str, int] = {}
    for step in report.get("steps", []):
        for item in step.get("findings", []):
            if item.get("severity") == "flag":
                per_rule[item.get("rule", "flag")] = per_rule.get(item.get("rule", "flag"), 0) + 1
    checker = report.get("checker") or {}
    return {
        "steps": steps,
        "flags": per_rule,
        "flagsReviewed": sum(per_rule.values()),
        "checkerVersion": checker.get("version"),
        "omegaCommit": (checker.get("omegaCommit") or "")[:9] or None,
        "runnerImage": checker.get("runnerImage"),
        "runMs": report_step(report, "sandbox-run").get("ms"),
        "network": report_step(report, "sandbox-run").get("network"),
    }


def search_text(manifest: dict, handle: str, readme: str) -> str:
    parts = [manifest.get("title", ""), manifest.get("tagline", ""), " ".join(manifest.get("tags") or []), handle,
             manifest.get("id", ""), readme[:4096]]
    return "\n".join(str(part) for part in parts if part)


def vendors_of(manifest: dict) -> list[str]:
    live = (manifest.get("models") or {}).get("live") or []
    return sorted({str(item.get("model", "")).split(":", 1)[0] for item in live if isinstance(item, dict) and ":" in str(item.get("model", ""))})


def refresh_entry(entry: Entry) -> None:
    """Point `latest_version` at the highest approved version and copy its listing fields."""
    latest = refs.newest_live_version(entry)
    entry.latest_version = latest
    if latest is not None:
        manifest = latest.manifest
        entry.kind = manifest.get("kind", entry.kind)
        entry.search_text = search_text(manifest, entry.publisher.handle, latest.readme)
        entry.vendors = vendors_of(manifest)
        entry.tags = [str(tag) for tag in manifest.get("tags") or []]
        entry.no_key_needed = not (manifest.get("keys") or [])
    entry.save()
    cache_keys.invalidate_listing()


def _remix_target(manifest: dict) -> Version | None:
    remix = manifest.get("remix_of")
    if not isinstance(remix, str) or "/" not in remix:
        return None
    try:
        handle, name, version = refs.parse_ref(remix)
    except HubError:
        return None
    return refs.approved_version(handle, name, version) if version else None


def publish(submission: Submission, job, *, approver, confirmed: dict, rules_version: int, via: str | None) -> Version:
    manifest = submission.manifest or {}
    if not job or not job.canonical_bundle_key:
        raise HubError("OMEGA_HUB_CHECKS_NOT_PASSED", 409)
    if not refs.is_semver(submission.version):
        # A report from a checker that predates strict SemVer can pass `1.0.03`; it is never published.
        raise HubError("OMEGA_HUB_INVALID", 400,
                       f"{submission.version!r} is not plain MAJOR.MINOR.PATCH with no leading zeros")
    data = storage.read_bytes(job.canonical_bundle_key)
    files = bundles.read_canonical(data)
    publisher = Publisher.objects.select_for_update(no_key=True).get(pk=submission.publisher_id)
    if publisher.suspended_at is not None:
        raise HubError("OMEGA_HUB_PUBLISHER_SUSPENDED", 403)
    entry, created = Entry.objects.select_for_update().get_or_create(
        publisher=publisher, name=submission.name, defaults={"kind": manifest.get("kind", "agent")}
    )
    if entry.status == Entry.Status.REMOVED:
        raise HubError("OMEGA_HUB_GONE", 410)
    if refs.version_taken({"entry": entry}, submission.version):
        raise HubError("OMEGA_HUB_VERSION_EXISTS", 409)
    now = timezone.now()
    version = Version.objects.create(
        entry=entry, version=submission.version, semver_key=refs.semver_key(submission.version),
        manifest=manifest, readme=bundles.readme_of(files), files=bundles.file_list(files),
        digest=job.report.get("digest"), bundle_key=job.canonical_bundle_key,
        bundle_sha256=job.report.get("bundleSha256"), bundle_bytes=len(data),
        capabilities={**DEFAULT_CAPABILITIES, **(report_step(job.report, "capabilities").get("result") or {})},
        check_summary=public_check_summary(job.report), disclosure=confirmed,
        disclosure_draft=manifest.get("disclosure") if isinstance(manifest.get("disclosure"), dict) else None,
        rules_version=rules_version, source_commit=submission.github_commit, notes=submission.notes,
        remix_of=_remix_target(manifest), submission=submission, approved_by=approver, approved_at=now,
    )
    if job.cast_key:
        version.cast_key = storage.cast_key(version.pk)
        storage.put_bytes(version.cast_key, storage.read_bytes(job.cast_key))
    elif files["entry"].get((manifest.get("demo") or {}).get("cast", "demo.cast")):
        version.cast_key = storage.cast_key(version.pk)
        storage.put_bytes(version.cast_key, files["entry"][(manifest.get("demo") or {}).get("cast", "demo.cast")])
    version.save(update_fields=["cast_key"])
    if entry.status == Entry.Status.UNLISTED:
        entry.status = Entry.Status.LISTED
    entry.first_published_at = entry.first_published_at or now
    if publisher.first_published_at is None:
        publisher.first_published_at = now
        publisher.save(update_fields=["first_published_at", "updated_at"])
    entry.publisher = publisher
    refresh_entry(entry)
    audit.record("version.published", target_type="version", target_id=version.pk, actor=approver,
                 publisher=publisher, data={"ref": f"{publisher.handle}/{entry.name}@{version.version}",
                                            "digest": version.digest, **({"via": via} if via else {})})
    return version
