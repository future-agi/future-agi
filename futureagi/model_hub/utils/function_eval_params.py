from __future__ import annotations

from copy import deepcopy

FUNCTION_PARAMS_SCHEMA_KEY = "function_params_schema"


def _get_schema_from_evals_source(eval_type_id: str | None) -> dict:
    if not eval_type_id:
        return {}

    # Keep model_hub/utils/evals.py as the source of truth.
    # Local import avoids circular import at module load time.
    from model_hub.utils.evals import evals_template

    for template in evals_template:
        config = template.get("config", {}) if isinstance(template, dict) else {}
        if not isinstance(config, dict):
            continue
        if config.get("eval_type_id") != eval_type_id:
            continue

        schema = config.get(FUNCTION_PARAMS_SCHEMA_KEY, {})
        if isinstance(schema, dict):
            return deepcopy(schema)
        return {}

    return {}


def get_function_params_schema(template_config: dict | None) -> dict:
    if not isinstance(template_config, dict):
        return {}
    schema = template_config.get(FUNCTION_PARAMS_SCHEMA_KEY, {})
    if isinstance(schema, dict) and schema:
        return schema

    return _get_schema_from_evals_source(template_config.get("eval_type_id"))


def has_function_params_schema(template_config: dict | None) -> bool:
    return bool(get_function_params_schema(template_config))


def _normalize_integer(value, field: str):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            pass
    raise ValueError(f"{field} must be an integer")


def _normalize_number(value, field: str):
    # FE form fields serialize numeric inputs as strings; coerce so the
    # value validates against numeric-typed schema entries (max_latency_ms etc.).
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            pass
    raise ValueError(f"{field} must be a number")


def normalize_function_params(
    template_config: dict | None, params: dict | None
) -> dict:
    schema = get_function_params_schema(template_config)
    if not schema:
        return {}

    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError(
            "Invalid function parameter input. Please check the value and try again."
        )

    normalized: dict = {}
    unknown_keys = set(params.keys()) - set(schema.keys())
    if unknown_keys:
        unknown = ", ".join(sorted(unknown_keys))
        raise ValueError(f"Unknown function params: {unknown}")

    for name, definition in schema.items():
        if not isinstance(definition, dict):
            definition = {}

        required = bool(definition.get("required", False))
        nullable = bool(definition.get("nullable", False))
        raw_value = params.get(name, definition.get("default"))

        # FE form fields serialize blank inputs as the empty string instead
        # of omitting the key — treat that as "not provided" so the param
        # falls back to its schema default (typically None for optionals).
        if isinstance(raw_value, str) and raw_value.strip() == "":
            raw_value = definition.get("default")

        if raw_value is None:
            if required and not nullable:
                raise ValueError(f"{name} is required")
            normalized[name] = None
            continue

        field_type = definition.get("type")
        if field_type == "integer":
            value = _normalize_integer(raw_value, name)
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(f"{name} must be >= {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"{name} must be <= {maximum}")
            normalized[name] = value
        elif field_type == "boolean":
            if isinstance(raw_value, bool):
                normalized[name] = raw_value
            else:
                raise ValueError(f"{name} must be a boolean")
        elif field_type == "number":
            value = _normalize_number(raw_value, name)
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            if minimum is not None and value < minimum:
                raise ValueError(f"{name} must be >= {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"{name} must be <= {maximum}")
            normalized[name] = value
        elif field_type == "string":
            if not isinstance(raw_value, str):
                raise ValueError(f"{name} must be a string")
            normalized[name] = raw_value
        else:
            # If schema type is missing/unsupported, pass through to avoid breaking
            # future schema extension until explicit validator support is added.
            normalized[name] = raw_value

    return normalized


def normalize_eval_runtime_config(
    template_config: dict | None, runtime_config: dict | None
):
    if runtime_config is None:
        runtime_config = {}
    if not isinstance(runtime_config, dict):
        raise ValueError("Invalid configuration input. Please refresh and try again.")

    normalized_config = deepcopy(runtime_config)
    schema = get_function_params_schema(template_config)
    if not schema:
        return normalized_config

    normalized_config["params"] = normalize_function_params(
        template_config=template_config,
        params=runtime_config.get("params", {}),
    )
    return normalized_config


def params_with_defaults_for_response(
    template_config: dict | None, runtime_config: dict | None
):
    schema = deepcopy(get_function_params_schema(template_config))
    if not schema:
        return {}, {}

    normalized_config = normalize_eval_runtime_config(template_config, runtime_config)
    params = normalized_config.get("params", {})

    for key, definition in schema.items():
        if isinstance(definition, dict):
            definition["default"] = params.get(key, definition.get("default"))

    return schema, params
