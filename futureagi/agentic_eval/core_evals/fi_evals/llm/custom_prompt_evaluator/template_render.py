"""Runtime compatibility around the reviewed, value-free template preparation.

The runtime evaluator (unlike the EE copy) nests numeric path components as
lists, supports flat trace/row keys, and preserves invalid template syntax as
literal text. None of those behaviours requires compiling data as template
source. Keep these differences here, not in the shared scanner/sandbox port.
"""

import copy

from jinja2.exceptions import SecurityError, TemplateSyntaxError

from agentic_eval.core.utils.jinja_utils import nest_dotted_value

from .template_sandbox import prepare_prompt
from .template_scan import SIMPLE_PRINT_TAG

MAX_RENDERED_PROMPT_CHARS = 1_000_000


def _bounded_text(chunks):
    """Bound accumulated template output, including expanding author loops.

    This is not a CPU or allocation sandbox: one expression can allocate a
    large intermediate, and loops emitting no text still need worker limits.
    """
    parts = []
    total = 0
    for chunk in chunks:
        total += len(chunk)
        if total > MAX_RENDERED_PROMPT_CHARS:
            raise ValueError(
                f"rendered prompt exceeds {MAX_RENDERED_PROMPT_CHARS} characters"
            )
        parts.append(chunk)
    return "".join(parts)


def _literal_chunks(template, values):
    """Single-pass syntax-error fallback; its result is NEVER compiled.

    Replacing in the original text only prevents an earlier value containing
    another column's print tag from being substituted a second time.
    """
    copied = 0
    for match in SIMPLE_PRINT_TAG.finditer(template):
        yield template[copied : match.start()]
        name = match.group(1).strip()
        if name in values:
            yield str(values[name])
        elif " " in name:
            # The base prepass normalised unknown spaced-name placeholders.
            yield "{{" + name + "}}"
        else:
            yield match.group(0)
        copied = match.end()
    yield template[copied:]


def render_prompt(
    env, template, safe_context, fallback_kwargs=None, *, parse_json=False
):
    """Render with immutable access, context-only values and a clear size limit.

    Preserve the caller's top-level context shaping for later editor turns,
    but copy dotted roots before nesting so row/trace containers are untouched.
    Only spaced-name fallback values are admitted, as in the base evaluator.
    """
    if template is None or (not template and not parse_json):
        return template
    fallback_kwargs = fallback_kwargs or {}
    values = dict(safe_context)
    names = [match.group(1).strip() for match in SIMPLE_PRINT_TAG.finditer(template)]
    for name in names:
        if " " in name and name not in values and name in fallback_kwargs:
            values[name] = fallback_kwargs[name]

    shaped = dict(values)
    copied_roots = set()
    for name in names:
        # Consumed flat keys must not be nested again: a later child path may
        # have replaced the parent's value since its first occurrence.
        if " " in name or "." not in name or name not in shaped:
            continue
        parts = name.split(".")
        root = parts[0]
        if root not in copied_roots:
            if root in shaped:
                shaped[root] = copy.deepcopy(shaped[root])
            copied_roots.add(root)
        # Runtime's existing TH-5443 list-index semantics, including the
        # trace/row roots that the EE helper otherwise leaves to its caller.
        nest_dotted_value(shaped, parts, values[name])
        # Leave the authored expression intact: loop/set/macro bindings must
        # shadow this outer context, rather than read a captured value alias.
        shaped.pop(name, None)

    prepared = prepare_prompt(template, shaped, parse_json=parse_json)
    safe_context.clear()
    safe_context.update(prepared.context)

    try:
        compiled = env.from_string(prepared.source)
        return _bounded_text(compiled.generate(**prepared.render_context()))
    except TemplateSyntaxError:
        return _bounded_text(_literal_chunks(template, {**values, **prepared.context}))
    except SecurityError as exc:
        # Never fall back after sandbox refusal, including System/editor turns.
        raise ValueError(f"Unsafe prompt template: {exc}") from exc
