"""Unambiguous selection for name-based guardrail writes.

The push may collapse duplicate names, but a write must never guess which
stored occurrence an admin intended. Call inside the org-config write lock,
before mutating config or policy state. Whole-array replacement remains the
explicit way to repair legacy duplicates; it does not select by check name.
"""

from agentcc.services.config_push import _RULE_TO_REGISTRY, OrgConfigRejected


class GuardrailNameAmbiguous(OrgConfigRejected):
    code = "guardrail_name_ambiguous"
    param = "name"

    def __init__(self, name, count, repair):
        super().__init__(
            f'Guardrail name "{name}" matches {count} stored entries. {repair}'
        )


def unique_check_index(checks, name, *, aliases=False, repair):
    """Return the sole matching index, None for an upsert, or reject ambiguity.

    Checks lists already resolve rule/catalog aliases; rules and policy checks
    use exact names. Preserve those existing matching semantics for unique
    entries instead of changing which check a successful write addresses.
    """

    def names(value):
        return {value, _RULE_TO_REGISTRY.get(value, value)} if aliases else {value}

    requested = names(name)
    matches = [
        i
        for i, check in enumerate(checks)
        if isinstance(check, dict) and requested & names(check.get("name"))
    ]
    if len(matches) > 1:
        raise GuardrailNameAmbiguous(name, len(matches), repair)
    return matches[0] if matches else None
