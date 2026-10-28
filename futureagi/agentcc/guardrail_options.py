"""Common guardrail controls carried in the existing provider config map."""


class GuardrailOptionsError(ValueError):
    """A shared guardrail control cannot be applied safely."""


POST_RESPONSE_CHECKS = frozenset({"hallucination-detection", "data-leakage-prevention"})
REQUEST_ONLY_CHECKS = frozenset(
    {
        "prompt-injection",
        "injection-detector",
        "topic-restriction",
        "language-detection",
        "system-prompt-protection",
    }
)


def guardrail_stage(check, name=None):
    cfg = check.get("config") or {}
    if "stage" in cfg:
        return cfg["stage"]
    # Legacy rules defaulted their top-level stage to pre, but the gateway used
    # the check's native stage. Do not silently change those existing policies.
    if cfg.get("on_block") == "reflect":
        declared = check.get("stage") or check.get("phase")
        if declared:
            return declared
    return "post" if (name or check.get("name")) in POST_RESPONSE_CHECKS else "pre"


def validate_guardrail_options(check, name=None):
    check_name = name if name is not None else check.get("name", "")
    if not isinstance(check_name, str):
        raise GuardrailOptionsError("name must be a string")
    cfg = check.get("config")
    if cfg is None:
        return
    if not isinstance(cfg, dict):
        raise GuardrailOptionsError("config must be a JSON object")
    if "stage" in cfg and cfg["stage"] not in ("pre", "post"):
        raise GuardrailOptionsError("config.stage must be 'pre' or 'post'")
    if check_name in POST_RESPONSE_CHECKS and cfg.get("stage") == "pre":
        raise GuardrailOptionsError("this check requires config.stage='post'")
    request_only = check_name in REQUEST_ONLY_CHECKS or (
        (
            check_name == "tool-permissions"
            or cfg.get("provider") in ("tool_permissions", "tool_permission")
        )
        and cfg.get("apply_to") == "request"
    )
    if request_only and guardrail_stage(check, name) == "post":
        raise GuardrailOptionsError("this check supports only the request stage")
    on_block = cfg.get("on_block", "reject")
    if on_block not in ("reject", "reflect"):
        raise GuardrailOptionsError("config.on_block must be 'reject' or 'reflect'")
    if "reflect_max_attempts" in cfg:
        attempts = cfg["reflect_max_attempts"]
        if (
            isinstance(attempts, bool)
            or not isinstance(attempts, (int, float))
            or not 1 <= attempts <= 5
            or int(attempts) != attempts
        ):
            raise GuardrailOptionsError(
                "config.reflect_max_attempts must be an integer from 1 to 5"
            )
    if "reflect_debug" in cfg and not isinstance(cfg["reflect_debug"], bool):
        raise GuardrailOptionsError("config.reflect_debug must be a boolean")
    if on_block == "reflect":
        if guardrail_stage(check, name) != "post":
            raise GuardrailOptionsError(
                "retry with feedback requires a post-response check"
            )
        if check.get("action", "block") != "block":
            raise GuardrailOptionsError("retry with feedback requires action='block'")
        if check.get("mode", "sync") not in ("sync", "", None):
            raise GuardrailOptionsError(
                "retry with feedback requires synchronous execution"
            )


def validate_guardrail_collection(value):
    """Validate supported rules/list/map formats without changing their shape."""
    if not isinstance(value, dict):
        raise GuardrailOptionsError("guardrails must be a JSON object")
    for field in ("rules", "checks"):
        checks = value.get(field)
        if checks is None:
            continue
        if isinstance(checks, list):
            items = [(check, None) for check in checks]
        elif field == "checks" and isinstance(checks, dict):
            items = [(check, name) for name, check in checks.items()]
        else:
            shape = "array or object" if field == "checks" else "array"
            raise GuardrailOptionsError(f"{field} must be a JSON {shape}")
        for index, (check, name) in enumerate(items):
            if not isinstance(check, dict):
                raise GuardrailOptionsError(f"{field}[{index}] must be a JSON object")
            try:
                validate_guardrail_options(check, name)
            except GuardrailOptionsError as error:
                raise GuardrailOptionsError(f"{field}[{index}]: {error}") from error


def guardrail_wire_config(check, name=None):
    cfg = dict(check.get("config") or {})
    if cfg.get("on_block") == "reflect" and "stage" not in cfg:
        # Only new opt-in settings infer a declared stage. Existing rules keep
        # the gateway's native-stage behavior unless config.stage is explicit.
        cfg["stage"] = guardrail_stage(check, name)
    return cfg
