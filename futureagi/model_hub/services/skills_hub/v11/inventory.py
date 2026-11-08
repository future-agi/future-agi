"""Local, deterministic inventory and review; never hides model-visible text."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from model_hub.services.skills_hub.checks import run_checks
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256
from model_hub.services.skills_hub.v11.limits import MAX_OUTBOUND_FINDINGS

_ARTIFACT = re.compile(
    r"(?:^|[/_.-])(evals?|tests?|benchmarks?|expected_outputs?|grading)(?:$|[/_.-])",
    re.I,
)


def canonical_json(value: object) -> str:
    def encode(item: object) -> str:
        if isinstance(item, (UUID, Decimal)):
            return str(item)
        if isinstance(item, datetime):
            return item.isoformat()
        raise TypeError("Not a JSON value")

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=encode,
    )


def fingerprint(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _texts(cases: list[dict]) -> Iterable[tuple[str, int]]:
    for case in cases:
        yield case["prompt"], 40
        for check in case["checks"]:
            if check["kind"] in {"contains", "not_contains"}:
                yield check["config"]["text"], 32
            elif check["kind"] == "json_equals":
                yield canonical_json(check["config"]["value"]), 32


def secret_findings(text: str, *, location: dict) -> list[dict]:
    """Reuse v1 detection, returning no matched bytes or arbitrary check messages."""
    checks = run_checks([{"path": "SKILL.md", "content": text}])
    findings = []
    for index, check in enumerate(checks["items"]):
        if check["code"] != "secret_suspected":
            continue
        safe_location = dict.fromkeys(
            ("skill_id", "digest", "path", "case_id", "fixture_id", "check_id", "line")
        )
        safe_location.update(location, line=check.get("line"))
        findings.append(
            {
                "finding_id": fingerprint(
                    {
                        "location": safe_location,
                        "text_hash": file_sha256(text),
                        "index": index,
                    }
                ),
                "code": "secret_suspected",
                "location": safe_location,
                "explanation": "Possible credential in outbound input. Review locally; matched text is redacted.",
            }
        )
    return findings


def build_inventory(*, bundles: list[dict], cases: list[dict]) -> dict:
    inventory, outbound = [], []
    overlaps = [(_normalized(text), minimum) for text, minimum in _texts(cases)]
    for bundle in bundles:
        files = []
        for item in sorted(bundle["files"], key=lambda item: item["path"]):
            content, path = item["content"], item["path"]
            flags = []
            if _ARTIFACT.search(path):
                flags.append("evaluation_artifact")
            normalized = _normalized(content)
            if any(
                len(text) >= minimum and text in normalized
                for text, minimum in overlaps
            ):
                flags.append("case_overlap")
            findings = secret_findings(
                content,
                location={
                    "kind": "bundle_file",
                    "skill_id": bundle["skill_id"],
                    "digest": bundle["digest"],
                    "path": path,
                },
            )
            if findings:
                flags.append("secret_suspected")
            checks = run_checks([item])["items"]
            if any(check["code"] == "executable_content" for check in checks):
                flags.append("executable_content")
            outbound.extend(findings)
            files.append(
                {
                    "path": path,
                    "sha256": file_sha256(content),
                    "size_bytes": len(content.encode()),
                    "flags": flags,
                }
            )
        inventory.append(
            {"skill_id": bundle["skill_id"], "digest": bundle["digest"], "files": files}
        )
    for case in cases:
        outbound.extend(
            secret_findings(
                case["prompt"], location={"kind": "case_prompt", "case_id": case["id"]}
            )
        )
        for fixture in case["fixtures"]:
            outbound.extend(
                secret_findings(
                    fixture["name"] + "\n" + fixture["text"],
                    location={
                        "kind": "fixture",
                        "case_id": case["id"],
                        "fixture_id": fixture["id"],
                    },
                )
            )
        for check in case["checks"]:
            if check["kind"] == "semantic":
                outbound.extend(
                    secret_findings(
                        check["config"]["requirement"],
                        location={
                            "kind": "check",
                            "case_id": case["id"],
                            "check_id": check["id"],
                        },
                    )
                )
    # Reject rather than omit findings that a reviewer would otherwise never see.
    if len(outbound) > MAX_OUTBOUND_FINDINGS:
        raise SkillsHubError(
            "test_input_unsafe",
            "Too many outbound findings; remove suspected credentials first.",
            status=422,
        )
    return {"inventory": inventory, "outbound_findings": outbound}


def bind_findings(findings: list[dict], plan_fingerprint: str) -> list[dict]:
    return [
        {**item, "finding_id": fingerprint([plan_fingerprint, item["finding_id"]])}
        for item in findings
    ]


def validate_secret_review(
    *, findings: list[dict], decisions: list[dict]
) -> list[dict]:
    known = {item["finding_id"] for item in findings}
    accepted = {}
    for item in decisions:
        finding_id, reason = item.get("finding_id"), item.get("reason", "")
        if (
            not isinstance(finding_id, str)
            or set(item) != {"finding_id", "reason"}
            or finding_id not in known
            or finding_id in accepted
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 2000
        ):
            raise SkillsHubError(
                "invalid_inventory_review",
                "Review must name each exact finding once with a reason.",
                status=400,
            )
        accepted[finding_id] = reason.strip()
    if set(accepted) != known:
        raise SkillsHubError(
            "test_input_unsafe",
            "Resolve every outbound secret finding before sending input.",
            status=422,
        )
    return [{"finding_id": key, "reason": accepted[key]} for key in sorted(accepted)]


def validate_inventory_review(
    *,
    plan_fingerprint: str,
    inventory: list[dict],
    findings: list[dict],
    review: dict,
    purpose: str,
    exposed: bool = False,
) -> tuple[dict, list[str]]:
    if (
        set(review)
        != {
            "fingerprint",
            "reviewed_all_files",
            "no_known_answer_keys",
            "classifications",
            "false_positive_secret_findings",
        }
        or review.get("fingerprint") != plan_fingerprint
        or review.get("reviewed_all_files") is not True
    ):
        raise SkillsHubError(
            "invalid_inventory_review",
            "Review the complete current inventory.",
            status=400,
        )
    known = {}
    for bundle in inventory:
        for item in bundle["files"]:
            key = (bundle["skill_id"], item["path"], item["sha256"])
            known[key] = item
    classifications = {}
    reasons = set()
    for item in review.get("classifications", []):
        key = (item.get("skill_id"), item.get("path"), item.get("sha256"))
        classification, reason = item.get("classification"), item.get("reason", "")
        if (
            set(item) != {"skill_id", "path", "sha256", "classification", "reason"}
            or key not in known
            or key in classifications
            or classification not in {"reference_material", "evaluation_material"}
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 2000
        ):
            raise SkillsHubError(
                "invalid_inventory_review",
                "Classifications must identify exact files and explain the decision.",
                status=400,
            )
        classifications[key] = {**item, "reason": reason.strip()}
        if classification == "evaluation_material":
            reasons.add("evaluation_material")
    for key, item in known.items():
        if "evaluation_artifact" in item["flags"] and key not in classifications:
            raise SkillsHubError(
                "invalid_inventory_review",
                "Classify every flagged evaluation artifact.",
                status=400,
            )
        if "case_overlap" in item["flags"]:
            reasons.add("case_overlap")
    if review.get("no_known_answer_keys") is not True:
        reasons.add("known_answer_keys")
    if exposed:
        reasons.add("assistance_exposure")
    if purpose == "diagnostic":
        reasons.add("diagnostic_requested")
    if reasons and purpose != "diagnostic":
        raise SkillsHubError(
            "evaluation_material_present",
            "Edit the real draft or run a diagnostic check.",
            status=422,
        )
    accepted = {
        "fingerprint": plan_fingerprint,
        "reviewed_all_files": True,
        "no_known_answer_keys": review.get("no_known_answer_keys") is True,
        "classifications": [classifications[key] for key in sorted(classifications)],
        "false_positive_secret_findings": validate_secret_review(
            findings=findings,
            decisions=review.get("false_positive_secret_findings", []),
        ),
    }
    return accepted, sorted(reasons)
