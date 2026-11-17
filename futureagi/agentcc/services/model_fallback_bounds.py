"""Bound new chain edits while preserving unchanged pre-limit configurations."""

import copy

from pydantic import TypeAdapter, ValidationError

from agentcc.contracts.gateway_admin import (
    OrgConfig,
    RolloutRoutingConfig,
    RoutingConfig,
)

_CHAIN = TypeAdapter(list[str])
_MAX_FALLBACKS = next(
    branch["additionalProperties"]["maxItems"]
    for branch in RoutingConfig.model_json_schema()["properties"]["model_fallbacks"][
        "anyOf"
    ]
    if branch.get("type") == "object"
)


def _sections(routing):
    if not isinstance(routing, dict):
        return
    yield ("routing",), routing, RoutingConfig
    rollout = routing.get("rollout")
    if isinstance(rollout, dict) and isinstance(rollout.get("routing"), dict):
        yield (
            ("routing", "rollout", "routing"),
            rollout["routing"],
            RolloutRoutingConfig,
        )


def _chains(section, spelling):
    value = section.get(spelling)
    return value if isinstance(value, dict) else {}


def validate_model_fallback_bounds(routing, stored=None):
    """Reject only new/changed over-limit chains, including shadowed aliases.

    The generated contract supplies the limit. Compare under the writer's org
    lock; activation compares to the stored target, not the currently active row.
    """
    previous = {path: section for path, section, _ in _sections(stored)}
    oversized = []
    for path, section, model in _sections(routing):
        before = previous.get(path, {})
        for spelling in ("model_fallbacks", "modelFallbacks"):
            if spelling not in section:
                continue
            try:
                model.model_validate({spelling: section[spelling]})
            except ValidationError as exc:
                for error in exc.errors():
                    location = ".".join([*path, *(str(part) for part in error["loc"])])
                    if error["type"] != "too_long":
                        raise ValueError(f"{location}: {error['msg']}") from exc
                    name = error["loc"][-1]
                    old = _chains(before, spelling)
                    # A canonical patch may migrate the effective camel spelling.
                    if spelling == "model_fallbacks" and spelling not in before:
                        old = _chains(before, "modelFallbacks")
                    if name in old and section[spelling][name] == old[name]:
                        continue
                    oversized.append((location, error["ctx"]))
    if oversized:
        location, context = oversized[0]
        # Bound even an arbitrarily long model ID; the Console accepts <240 chars.
        location = " ".join(location.split())
        if len(location) > 120:
            location = location[:117] + "..."
        message = (
            f"{location} has {context['actual_length']} fallback models; "
            f"limit is {context['max_length']}"
        )
        if len(oversized) > 1:
            message += f"; and {len(oversized) - 1} more chains over the limit"
        raise ValueError(message + ".")


def collapse_stored_fallback_aliases(routing, patch):
    """Make a canonical merge patch repair the effective stored camel value.

    Only the stored alias is collapsed. Both spellings explicitly supplied by
    a caller are still checked, so a new shadowed over-limit chain is refused.
    Unmentioned model chains survive the merge, including legacy long chains.
    """
    routing = copy.deepcopy(routing)
    patches = {path: section for path, section, _ in _sections(patch)}
    for path, section, _ in _sections(routing):
        change = patches.get(path, {})
        if "model_fallbacks" in change and "modelFallbacks" not in change:
            if "modelFallbacks" in section:
                section.setdefault("model_fallbacks", section["modelFallbacks"])
                del section["modelFallbacks"]
    return routing


def dump_stored_org_config(payload):
    """Serialize legacy chains without weakening the public generated contract.

    New edits are bounded by the writer. For push/pull of saved legacy rows,
    validate every element (including the tail), temporarily replace only long
    arrays for the bounded contract check, then restore them in the canonical
    dump. The gateway still truncates with its warning; storage is never trimmed.
    All other contract validation and alias normalization remain in force.
    """
    checked = copy.deepcopy(payload)
    legacy = []
    for path, section, _ in _sections(checked.get("routing")):
        spelling = (
            "model_fallbacks" if "model_fallbacks" in section else "modelFallbacks"
        )
        for name, chain in _chains(section, spelling).items():
            if isinstance(chain, list) and len(chain) > _MAX_FALLBACKS:
                legacy.append((path, name, _CHAIN.validate_python(chain)))
                section[spelling][name] = []
    dumped = OrgConfig.model_validate(checked).model_dump(
        by_alias=True, exclude_none=True
    )
    for path, name, chain in legacy:
        target = dumped
        for part in path:
            target = target[part]
        target["model_fallbacks"][name] = chain
    return dumped
