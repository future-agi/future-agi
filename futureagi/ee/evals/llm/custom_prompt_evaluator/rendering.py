"""Shared prompt rendering; preserves the existing custom judge semantics."""

import json

_AUTO_CONTEXT_ROOTS = ("row", "span", "trace", "session", "call")


def render_prompt(prompt, template_context, template_format, env):
    # Pre-process: handle variable names with spaces (e.g., {{TTS Testing}})
    # Jinja2 doesn't allow spaces in variable names, so we do simple string
    # replacement for these before Jinja2 parsing.
    import re

    prompt_to_render = prompt
    safe_context = dict(template_context)

    # Find all {{...}} variables and check for ones with spaces
    raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", prompt_to_render)
    for var_name in raw_vars:
        stripped = var_name.strip()
        if " " in stripped and stripped in safe_context:
            # Replace the spaced variable with its value directly
            prompt_to_render = prompt_to_render.replace(
                "{{" + var_name + "}}", str(safe_context.pop(stripped))
            )
            # Also try with extra whitespace variants
            prompt_to_render = prompt_to_render.replace(
                "{{ " + stripped + " }}", str(template_context.get(stripped, ""))
            )
        elif "." in stripped and stripped in safe_context:
            # Dotted variable names (e.g., {{json_col.field}}) are flat
            # keys in template_context but Jinja2 interprets dots as
            # nested object access. Nest them into dicts so Jinja
            # resolves naturally. Skip auto-context roots — those are
            # handled by AgentEvaluator separately.
            root = stripped.split(".")[0]
            if root not in _AUTO_CONTEXT_ROOTS:
                parts = stripped.split(".")
                value = safe_context.pop(stripped)
                target = safe_context
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value

    # In Jinja mode, parse JSON strings to native objects right
    # before rendering so {% for %} loops work correctly.
    if template_format == "jinja":
        for key in list(safe_context.keys()):
            val = safe_context[key]
            if isinstance(val, str):
                stripped = val.strip()
                if (stripped.startswith("[") and stripped.endswith("]")) or (
                    stripped.startswith("{") and stripped.endswith("}")
                ):
                    try:
                        safe_context[key] = json.loads(val)
                    except (ValueError, json.JSONDecodeError):
                        pass

    template = env.from_string(prompt_to_render)
    rendered_prompt = template.render(**safe_context)
    return rendered_prompt, safe_context
