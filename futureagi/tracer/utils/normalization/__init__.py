"""Version-qualified producer semantics, shared with fi-collector.

profiles.json is authoritative; scripts/sync_trace_normalization.py generates the
Go embedded copy. Matching uses instrumentation scope, never a model or span
name. Unknown producers/versions retain existing semantics. No native/source
identity join or immutable admission is claimed by this module.
"""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

CONTRACT = json.loads(files(__package__).joinpath("profiles.json").read_text())
KIND_KEYS = (
    "fi.span.kind",
    "gen_ai.span.kind",
    "llm.request.type",
    "openinference.span.kind",
)


def requires_immutable_admission(attributes: dict[str, Any]) -> bool:
    return any(key in attributes for key in CONTRACT["reserved_admission_keys"]) or (
        "fi.usage.contribution" in attributes
        and attributes["fi.usage.contribution"] != "none"
    )


def normalize_attributes(
    attributes: dict[str, Any], scope: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Return normalized attributes without modifying the caller's source dict.

    A qualified summary is non-additive even when its children arrive later or
    not at all. Absence of request spans is incomplete capture, not permission
    to reinterpret a source summary as independent usage. Unmarked non-LLM usage from other producers remains additive; explicit
    immutable turn plans require admission and are held by the ingress. No value-based sibling deduplication.
    """
    result = dict(attributes)
    scope = scope or {}
    profile = next(
        (
            p
            for p in CONTRACT["profiles"]
            if scope.get("name") == p["scope_name"]
            and scope.get("version") in p["scope_versions"]
        ),
        None,
    )
    operation = result.get("gen_ai.operation.name")
    if not isinstance(operation, str):
        operation = ""
    if profile:
        result["fi.normalization.profile"] = profile["id"]
        if not any(result.get(key) for key in KIND_KEYS):
            kind = profile["operations"].get(operation)
            if kind:
                result["fi.span.kind"] = kind.upper()
                result["fi.source.operation"] = operation
        session = result.get(profile["session_fallback"])
        if (
            not result.get("session.id")
            and isinstance(session, str)
            and 0 < len(session.encode("utf-8")) <= 4096
        ):
            result["session.id"] = session
        if operation in profile["summary_operations"] and result.get(
            "fi.usage.contribution"
        ) in (None, "none"):
            result["fi.usage.contribution"] = "none"
            result["fi.usage.scope"] = "turn"

    if result.get("fi.usage.contribution") == "none":
        for key in list(result):
            if (
                key.startswith(tuple(CONTRACT["usage_prefixes"]))
                or key in CONTRACT["usage_keys"]
            ):
                # The incoming accounting value wins over a conflicting raw alias.
                result["fi.source.summary." + key] = result.pop(key)
        result["fi.cost.kind"] = "unknown"
    return result


def normalize_span(span: dict[str, Any]) -> dict[str, Any]:
    attributes = span.setdefault("attributes", {})
    normalized = normalize_attributes(attributes, span.get("scope"))
    attributes.clear()
    attributes.update(normalized)
    return attributes
