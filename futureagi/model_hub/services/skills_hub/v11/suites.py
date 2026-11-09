"""Saved case validation and revision-CAS replacement; no provider work."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import NoReturn
from uuid import UUID

import jsonschema
from django.db import transaction
from django.utils import timezone

from accounts.models.user import User
from model_hub.models.skills_hub import HubSkill
from model_hub.models.skills_hub_v11 import HubSkillAssistProposal, HubSkillTestSuite
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11 import limits
from model_hub.services.skills_hub.v11.principals import (
    require_private_principal,
    require_scope_access,
    require_valid_scope,
)
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _invalid(message: str, *, code: str = "invalid_test_config") -> NoReturn:
    raise SkillsHubError(code, message, status=400)


def _shape(value: object, fields: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        _invalid(f"{name} must contain exactly its documented fields.")


def _text(
    value: object, name: str, *, maximum: int | None = None, allow_blank: bool = False
) -> None:
    if (
        not isinstance(value, str)
        or (not allow_blank and not value.strip())
        or (maximum is not None and len(value) > maximum)
    ):
        _invalid(f"Invalid {name}.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _invalid(f"{name} must be valid UTF-8 text.")
    if "\x00" in value:
        _invalid(f"{name} cannot contain NUL.")


def _uuid(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        _invalid("Use a valid UUID.")


def validate_file_path(path: object) -> None:
    _text(path, "file path", maximum=255)
    assert isinstance(path, str)
    segments = path.split("/")
    if (
        len(segments) > 8
        or "\\" in path
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
        or any(
            segment in {"", ".", ".."} or segment.endswith((" ", "."))
            for segment in segments
        )
    ):
        _invalid("Use a relative POSIX bundle path.")


def validate_json_schema(schema: object) -> None:
    if not isinstance(schema, dict):
        _invalid("JSON schema must be an object.")
    try:
        encoded = canonical_json(schema).encode("utf-8")
    except (ValueError, TypeError, UnicodeEncodeError, RecursionError):
        _invalid("JSON schema must contain finite JSON values.")
    if len(encoded) > limits.MAX_SCHEMA_BYTES:
        _invalid("JSON schema exceeds 16 KiB.")
    stack: list[tuple[object, int]] = [(schema, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > limits.MAX_SCHEMA_DEPTH:
            _invalid("JSON schema exceeds the maximum nesting depth.")
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(value, str) or not value.startswith("#")
                ):
                    _invalid("Only local JSON schema references are supported.")
                if key == "$id" and (
                    not isinstance(value, str) or not value.startswith("#")
                ):
                    _invalid("External JSON schema identifiers are not supported.")
                stack.append((value, depth + 1))
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError:
        _invalid("Invalid JSON schema.")


def validate_check(check: dict) -> dict:
    _shape(check, {"id", "kind", "critical", "config"}, "Check")
    result = deepcopy(check)
    result["id"] = _uuid(check["id"])
    if type(check["critical"]) is not bool:
        _invalid("Check critical must be a boolean.")
    kind, config = check["kind"], check["config"]
    fields = {
        "contains": {"text", "case_sensitive"},
        "not_contains": {"text", "case_sensitive"},
        "json_valid": set(),
        "json_equals": {"pointer", "value"},
        "json_schema": {"schema"},
        "file_delivered": {"path", "complete"},
        "semantic": {"requirement"},
    }
    if not isinstance(kind, str) or kind not in fields:
        _invalid("Unsupported check kind.", code="unsupported_check")
    _shape(config, fields[kind], "Check configuration")
    if kind in {"contains", "not_contains"}:
        _text(config["text"], "check text", maximum=limits.MAX_CHECK_TEXT)
        if type(config["case_sensitive"]) is not bool:
            _invalid("case_sensitive must be a boolean.")
    elif kind == "semantic":
        _text(
            config["requirement"], "semantic requirement", maximum=limits.MAX_CHECK_TEXT
        )
    elif kind == "file_delivered":
        validate_file_path(config["path"])
        if type(config["complete"]) is not bool:
            _invalid("complete must be a boolean.")
    elif kind == "json_equals":
        pointer = config["pointer"]
        _text(pointer, "JSON pointer", maximum=2000, allow_blank=True)
        if (pointer and not pointer.startswith("/")) or re.search(
            r"~(?![01])", pointer
        ):
            _invalid("Use an RFC6901 JSON pointer.")
        try:
            canonical_json(config["value"])
        except (ValueError, TypeError, RecursionError):
            _invalid("The expected value must be JSON.")
    elif kind == "json_schema":
        validate_json_schema(config["schema"])
    return result


def validate_cases(cases: list[dict], *, require_reviewed: bool = False) -> list[dict]:
    """Pure domain validation, shared by suite saves and run acceptance."""
    if not isinstance(cases, list) or len(cases) > limits.MAX_SUITE_CASES:
        _invalid("Save at most 20 cases.")
    normalized = []
    seen = set()
    for case in cases:
        _shape(
            case,
            {
                "id",
                "title",
                "source_group_id",
                "prompt",
                "fixtures",
                "expected_trigger",
                "checks",
                "reviewed",
            },
            "Case",
        )
        item = deepcopy(case)
        item["id"] = _uuid(case["id"])
        if item["id"] in seen:
            _invalid("Case IDs must be unique.")
        seen.add(item["id"])
        _text(case["title"], "case title", maximum=120)
        _text(case["source_group_id"], "source group")
        _text(case["prompt"], "prompt")
        if len(case["prompt"].encode("utf-8")) > limits.MAX_CASE_PROMPT_BYTES:
            _invalid("Case prompt exceeds 16 KiB.")
        if (
            case["expected_trigger"] is not None
            and type(case["expected_trigger"]) is not bool
        ):
            _invalid("expected_trigger must be a boolean or null.")
        if type(case["reviewed"]) is not bool or (
            require_reviewed and not case["reviewed"]
        ):
            _invalid("Explicitly review each selected case.")
        if not isinstance(case["fixtures"], list):
            _invalid("Fixtures must be a list.")
        fixture_ids, fixture_bytes = set(), 0
        for fixture in item["fixtures"]:
            _shape(fixture, {"id", "name", "text"}, "Fixture")
            fixture["id"] = _uuid(fixture["id"])
            if fixture["id"] in fixture_ids:
                _invalid("Fixture IDs must be unique within each case.")
            fixture_ids.add(fixture["id"])
            _text(fixture["name"], "fixture name")
            _text(fixture["text"], "fixture text", allow_blank=True)
            fixture_bytes += len(fixture["text"].encode("utf-8"))
        if fixture_bytes > limits.MAX_CASE_FIXTURE_BYTES:
            _invalid("Case fixture text exceeds 48 KiB.")
        if (
            not isinstance(case["checks"], list)
            or len(case["checks"]) > limits.MAX_CHECKS_PER_CASE
        ):
            _invalid("Each case supports up to ten checks.")
        item["checks"] = [validate_check(check) for check in case["checks"]]
        if len({check["id"] for check in item["checks"]}) != len(item["checks"]):
            _invalid("Check IDs must be unique within each case.")
        if require_reviewed and case["expected_trigger"] is None and not case["checks"]:
            _invalid("Each selected case needs a routing or check expectation.")
        normalized.append(item)
    try:
        size = len(canonical_json(normalized).encode("utf-8"))
    except (ValueError, TypeError, RecursionError, UnicodeEncodeError):
        _invalid("Cases must contain valid finite JSON values.")
    if size > limits.MAX_SUITE_BYTES:
        raise SkillsHubError(
            "payload_too_large", "The saved suite exceeds 1 MiB.", status=413
        )
    return normalized


def _skill(*, scope: SkillScope, skill_id: UUID, lock: bool = False) -> HubSkill:
    require_valid_scope(scope=scope)
    queryset = HubSkill.no_workspace_objects.filter(
        pk=skill_id,
        organization_id=scope.organization_id,
        workspace_id=scope.workspace_id,
    )
    if lock:
        queryset = queryset.select_for_update()
    skill = queryset.first()
    if skill is None:
        raise SkillsHubError("resource_not_found", "Skill not found.", status=404)
    return skill


def get_suite(*, scope: SkillScope, skill_id: UUID) -> dict:
    _skill(scope=scope, skill_id=skill_id)
    suite = (
        HubSkillTestSuite.objects.filter(
            skill_id=skill_id,
            organization_id=scope.organization_id,
            workspace_id=scope.workspace_id,
        )
        .select_related("updated_by")
        .first()
    )
    if suite is None:
        return {
            "revision": 0,
            "digest": None,
            "cases": [],
            "updated_at": None,
            "updated_by": None,
            "visibility": "workspace",
        }
    return {
        "revision": suite.revision,
        "digest": suite.digest,
        "cases": suite.cases,
        "updated_at": suite.updated_at,
        "updated_by": (
            {"id": str(suite.updated_by_id), "name": suite.updated_by.name}
            if suite.updated_by is not None
            else None
        ),
        "visibility": "workspace",
    }


def save_suite(
    *, principal: SkillPrincipal, skill_id: UUID, revision: int, cases: list[dict]
) -> HubSkillTestSuite:
    require_private_principal(principal=principal)
    user_id = principal.user_id
    assert user_id is not None
    require_scope_access(principal=principal, write=True)
    if type(revision) is not int or revision < 0:
        _invalid("Use a nonnegative suite revision.")
    normalized = validate_cases(cases)
    with transaction.atomic():
        _skill(scope=principal.scope, skill_id=skill_id, lock=True)
        suite = (
            HubSkillTestSuite.objects.select_for_update()
            .filter(
                skill_id=skill_id,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
            )
            .first()
        )
        if revision != (suite.revision if suite else 0):
            raise SkillsHubError(
                "test_suite_conflict",
                "These saved cases changed. Reload before saving.",
                status=409,
                detail={
                    "revision": suite.revision if suite else 0,
                    "updated_at": suite.updated_at.isoformat() if suite else None,
                },
            )
        old_cases = {case["id"]: case for case in suite.cases} if suite else {}
        now = timezone.now().isoformat()
        user = User.objects.only("id", "name").get(pk=user_id)
        exposed_groups = set()
        # Lineage is retained independently of expired proposal text.
        for groups in HubSkillAssistProposal.objects.filter(
            skill_id=skill_id,
            job__organization_id=principal.scope.organization_id,
            job__workspace_id=principal.scope.workspace_id,
        ).values_list("exposed_source_group_ids", flat=True):
            exposed_groups.update(groups)
        for case in normalized:
            old = old_cases.get(case["id"])
            unchanged = old is not None and all(
                old.get(key) == value
                for key, value in case.items()
                if key != "reviewed"
            )
            if old and "source" in old:
                from model_hub.services.skills_hub.v11.inventory import fingerprint

                case["source"] = {
                    **old["source"],
                    "sanitized_digest": fingerprint(case),
                }
            case["origin"] = old["origin"] if old else "manual"
            case["exposed_to_assist"] = bool(
                (old and old.get("exposed_to_assist"))
                or case["source_group_id"] in exposed_groups
            )
            if case["reviewed"]:
                case["reviewed_by"] = (
                    old["reviewed_by"]
                    if unchanged and old is not None and old.get("reviewed")
                    else {"id": str(user.pk), "name": user.name}
                )
                case["reviewed_at"] = (
                    old["reviewed_at"]
                    if unchanged and old is not None and old.get("reviewed")
                    else now
                )
            else:
                case["reviewed_by"] = None
                case["reviewed_at"] = None
        serialized = canonical_json(normalized).encode("utf-8")
        if len(serialized) > limits.MAX_SUITE_BYTES:
            raise SkillsHubError(
                "payload_too_large",
                "The saved suite exceeds 1 MiB including review metadata.",
                status=413,
            )
        if suite is None:
            suite = HubSkillTestSuite(
                skill_id=skill_id,
                organization_id=principal.scope.organization_id,
                workspace_id=principal.scope.workspace_id,
            )
        suite.revision = revision + 1
        suite.cases = normalized
        suite.digest = "sha256:" + hashlib.sha256(serialized).hexdigest()
        suite.updated_by_id = principal.user_id
        suite.updated_by_api_key_id = principal.api_key_id
        suite.save()
        return suite
