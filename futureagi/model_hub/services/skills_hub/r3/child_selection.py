"""Natural child selection is independent of answer quality and root delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.inventory import fingerprint

SelectionVerdict = Literal["pass", "fail", "unverifiable", "not_applicable"]


@dataclass(frozen=True)
class ChildObservation:
    skill_id: str
    requested: bool | None
    root_delivered: bool | None


@dataclass(frozen=True)
class SelectionObservation:
    catalog_digest: str
    invocation_mode: Literal["natural", "forced"]
    complete: bool
    instrumented: bool
    children: tuple[ChildObservation, ...]


def catalog_identity(links: list[dict]) -> str:
    """Bind the entire catalog, including children with no asserted expectation."""
    rows = []
    seen = set()
    for link in links:
        try:
            skill_id, version_id = (
                str(UUID(str(link["skill_id"]))),
                str(UUID(str(link["version_id"]))),
            )
            digest, mount = link["digest"], link["mount"]
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            raise SkillsHubError(
                "invalid_test_config", "Invalid pinned child catalog.", status=400
            ) from exc
        if (
            skill_id in seen
            or not isinstance(digest, str)
            or not isinstance(mount, str)
        ):
            raise SkillsHubError(
                "invalid_test_config",
                "Use distinct pinned child identities.",
                status=400,
            )
        seen.add(skill_id)
        rows.append(
            {
                "skill_id": skill_id,
                "version_id": version_id,
                "digest": digest,
                "mount": mount,
            }
        )
    if len(rows) > 20:
        raise SkillsHubError(
            "invalid_test_config", "At most 20 linked skills are supported.", status=400
        )
    return fingerprint(sorted(rows, key=lambda row: row["skill_id"]))


def validate_selection(selection: dict, *, links: list[dict]) -> dict:
    if not isinstance(selection, dict) or set(selection) != {
        "child_selection",
        "catalog_digest",
        "invocation_mode",
    }:
        raise SkillsHubError(
            "invalid_test_config", "Use the native selection contract.", status=400
        )
    expected_digest = catalog_identity(links)
    if selection["catalog_digest"] != expected_digest:
        raise SkillsHubError(
            "test_plan_changed", "The selected child catalog changed.", status=409
        )
    if selection["invocation_mode"] not in {"natural", "forced"}:
        raise SkillsHubError(
            "invalid_test_config", "Choose natural or forced invocation.", status=400
        )
    rows = selection["child_selection"]
    if not isinstance(rows, list) or len(rows) > 20:
        raise SkillsHubError(
            "invalid_test_config", "Use at most 20 child expectations.", status=400
        )
    available = {str(link["skill_id"]) for link in links}
    seen, normalized = set(), []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"skill_id", "expectation"}:
            raise SkillsHubError(
                "invalid_test_config", "Invalid child expectation.", status=400
            )
        try:
            identity = str(UUID(str(row["skill_id"])))
        except (ValueError, TypeError, AttributeError) as exc:
            raise SkillsHubError(
                "invalid_test_config", "Invalid child identity.", status=400
            ) from exc
        if (
            identity not in available
            or identity in seen
            or row["expectation"] not in {"required", "forbidden", "optional"}
        ):
            raise SkillsHubError(
                "invalid_test_config",
                "Expectations must name distinct catalog children.",
                status=400,
            )
        seen.add(identity)
        normalized.append({"skill_id": identity, "expectation": row["expectation"]})
    return {
        **selection,
        "child_selection": sorted(normalized, key=lambda row: row["skill_id"]),
    }


def evaluate_child_selection(
    *, selection: dict, observation: SelectionObservation
) -> list[dict]:
    """A missing receipt is not proof of nonselection. Forced runs prove no routing."""
    if (
        type(observation.complete) is not bool
        or type(observation.instrumented) is not bool
    ):
        raise SkillsHubError(
            "invalid_receipt",
            "Selection completeness and instrumentation must be booleans.",
            status=400,
        )
    observations = {row.skill_id: row for row in observation.children}
    if len(observations) != len(observation.children):
        raise SkillsHubError(
            "invalid_receipt", "Duplicate child observations.", status=400
        )
    if any(
        type(value) is not bool and value is not None
        for row in observation.children
        for value in (row.requested, row.root_delivered)
    ):
        raise SkillsHubError(
            "invalid_receipt",
            "Selection observations must be booleans or unknown.",
            status=400,
        )
    trusted = (
        observation.catalog_digest == selection["catalog_digest"]
        and observation.invocation_mode == selection["invocation_mode"] == "natural"
    )
    results = []
    for expected in selection["child_selection"]:
        row = observations.get(expected["skill_id"])
        expectation = expected["expectation"]
        verdict: SelectionVerdict = "unverifiable"
        reason = "selection_not_observed"
        if expectation == "optional":
            verdict, reason = "not_applicable", "optional_child"
        elif not trusted:
            reason = "forced_or_catalog_mismatch"
        elif row is not None:
            if expectation == "forbidden" and (
                row.requested is True or row.root_delivered is True
            ):
                verdict, reason = "fail", "forbidden_child_requested_or_delivered"
            elif (
                expectation == "required"
                and observation.instrumented
                and row.requested is True
                and row.root_delivered is True
            ):
                verdict, reason = "pass", "required_child_delivered"
            elif observation.complete and observation.instrumented:
                if (
                    expectation == "forbidden"
                    and row.requested is False
                    and row.root_delivered is False
                ):
                    verdict, reason = "pass", "forbidden_child_not_selected"
                elif expectation == "required" and (
                    row.requested is False or row.root_delivered is False
                ):
                    verdict, reason = "fail", "required_child_not_delivered"
        results.append({**expected, "verdict": verdict, "reason": reason})
    return results
