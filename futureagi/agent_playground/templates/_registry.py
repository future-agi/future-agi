from typing import Any, TypedDict


class TemplateDefinition(TypedDict):
    name: str
    display_name: str
    description: str
    icon: str | None
    categories: list[str]
    input_definition: list[dict[str, Any]]
    output_definition: list[dict[str, Any]]
    input_mode: str
    output_mode: str
    config_schema: dict[str, Any]


_TEMPLATE_REGISTRY: dict[str, TemplateDefinition] = {}


def register_template(definition: TemplateDefinition) -> None:
    """Register a template definition. Silently skips if already registered."""
    name = definition["name"]
    if name in _TEMPLATE_REGISTRY:
        return  # already registered, skip
    _TEMPLATE_REGISTRY[name] = definition


def get_all_templates() -> dict[str, TemplateDefinition]:
    """Import all template modules and return a copy of the registry."""
    # Import template modules to trigger registration
    import agent_playground.templates.evaluation  # noqa: F401
    import agent_playground.templates.llm_prompt  # noqa: F401

    return dict(_TEMPLATE_REGISTRY)
