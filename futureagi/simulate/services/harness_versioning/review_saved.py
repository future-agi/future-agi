"""What a reviewed save leaves on its versions: the name and summary, the proofs no accepted change touched, and
where each change came from."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulate.services.harness_versioning.references import ALL
from simulate.services.harness_versioning.review_items import (
    item_index,
    whole_documents,
)

SUMMARY_ITEMS = 5


def review_summary(review: dict[str, Any]) -> str:
    """A saved version's summary, from what the person accepted: the first few changes and how many more."""
    accepted = [
        one["summary"]
        for one in (review or {}).get("changes") or []
        if one.get("decision") == "accepted"
    ]
    head = "; ".join(accepted[:SUMMARY_ITEMS])
    more = len(accepted) - SUMMARY_ITEMS
    return head + (f"; and {more} more" if more > 0 else "")


def carry_untouched_proofs(
    parent: Any, version: Any, suite_version: Any, reviewed: list[dict[str, Any]]
) -> None:
    """Scenarios no accepted change touched keep the proofs they had against the environment version this one came
    from. A change that may touch every scenario (a rule, a contract field, a world file) carries nothing.
    """
    from simulate.models import HarnessSuiteValidation

    accepted = [one for one in reviewed if one.get("decision") == "accepted"]
    if (
        parent is None
        or suite_version is None
        or any(one.get("affects") == ALL for one in accepted)
    ):
        return
    earlier = HarnessSuiteValidation.no_workspace_objects.filter(
        suite_version=suite_version, environment_version=parent
    ).first()
    if earlier is None or not isinstance(earlier.proofs, dict):
        return
    from simulate.services.hosted_harness_gateway import _scenario_token

    # A change names a scenario by its folder; a proof by the suite's key (`duplicate_refund` vs `duplicate-refund`).
    touched = {
        _scenario_token(key) for one in accepted for key in one.get("affects") or []
    }
    kept = {
        key: proof
        for key, proof in earlier.proofs.items()
        if _scenario_token(key) not in touched
    }
    if not kept:
        return
    holding = [
        key
        for key, proof in kept.items()
        if isinstance(proof, dict) and proof.get("holds") is True
    ]
    status = (
        HarnessSuiteValidation.Status.PASSED
        if len(kept) == len(earlier.proofs) and len(holding) == len(kept)
        else HarnessSuiteValidation.Status.PARTIAL
    )
    HarnessSuiteValidation.no_workspace_objects.get_or_create(
        suite_version=suite_version,
        environment_version=version,
        defaults={
            "status": status,
            "proofs": kept,
            "job": earlier.job,
            "proved_at": earlier.proved_at,
        },
    )


def name_saved(activated: dict[str, Any], *, name: str) -> None:
    """Give each version a save created the person's name and, when it was reviewed, a summary of what they
    accepted. A version the save reused keeps what it had."""
    for version, created in activated.values():
        if not created:
            continue
        fields: list[str] = []
        if name:
            version.name, fields = name[:80], [*fields, "name"]
        reviewed = (version.documents or {}).get("review_changes")
        if reviewed:
            version.summary, fields = (
                review_summary({"changes": reviewed})[:1024],
                [
                    *fields,
                    "summary",
                ],
            )
            if version.__class__.__name__ == "HarnessEnvironmentVersion":
                carry_untouched_proofs(
                    version.parent, version, version.job.suite_version, reviewed
                )
        if fields:
            version.save(update_fields=[*fields, "updated_at"])


SOURCES_PER_CHANGE = 20


def record_sources(
    provenance: dict[str, list[dict[str, Any]]] | None,
    before: Mapping[str, bytes],
    after: Mapping[str, bytes],
    *,
    origin: str,
    user: Any,
    revision: int,
    at: str,
    message_id: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Where each change came from: every item this write moved gets one source (who, through what, at which draft
    revision), appended to the sources it already had. A retried write at the same revision adds nothing. A chat
    write names the person's message it answered (`message_id`)."""
    out = {key: list(value) for key, value in (provenance or {}).items()}
    whole = whole_documents(before, after)
    old, new = item_index(before, whole), item_index(after, whole)
    moved = sorted(
        item_id
        for item_id in set(old) | set(new)
        if (old[item_id].digest if item_id in old else None)
        != (new[item_id].digest if item_id in new else None)
    )
    person = str(getattr(user, "id", "") or "") if user is not None else ""
    if message_id:
        # A chat change belongs to the person whose message it answered, made through the harness.
        agent = {"kind": "harness", "on_behalf_of": person}
        used = [{"kind": "message", "id": message_id}]
    else:
        used = []
        agent = (
            {"kind": "user", "user_id": person}
            if user is not None
            else {"kind": "harness"}
        )
    source = {
        "origin": origin,
        "agent": agent,
        "at": at,
        "draft_revision": revision,
        **({"used": list(used)} if used else {}),
    }
    for item_id in moved:
        listed = out.setdefault(item_id, [])
        if any(one.get("draft_revision") == revision for one in listed):
            continue
        listed.append(source)
        del listed[:-SOURCES_PER_CHANGE]
    return out
