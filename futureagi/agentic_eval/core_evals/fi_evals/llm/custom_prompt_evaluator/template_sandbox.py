"""Sandboxed rendering inputs for user-authored custom prompt templates.

Eval authors write the rule prompt and the editor messages; rows, spans and
traces supply the values, and both are rendered on the eval worker. Two rules
keep that safe:

* Templates render in :class:`PromptSandboxEnvironment`, an
  ``ImmutableSandboxedEnvironment`` that raises ``SecurityError`` on any unsafe
  attribute (``__class__``, ``__globals__``, ``list.append``, ``dict.update``)
  instead of printing a placeholder and carrying on.
* Values are bound in the render context and never become template source.
  Names Jinja cannot parse (``{{TTS Testing}}``) use display aliases; dotted
  expressions keep Jinja's lexical lookup in the caller's nested context.
  Raw blocks, comments, statement tags and string literals keep the author's
  text. Delimiters inside values always remain inert data.

Autoescape defaults off for text prompts; authored blocks still apply to ordinary
interpolations, while display aliases retain literal template-data semantics.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Any, NoReturn

from jinja2.exceptions import SecurityError
from jinja2.sandbox import ImmutableSandboxedEnvironment
from markupsafe import Markup

from .template_scan import SIMPLE_PRINT_TAG, find_data_runs

_ALIAS_PREFIX = "fi_prompt_var_"


class PromptSandboxEnvironment(ImmutableSandboxedEnvironment):
    """Immutable sandbox that fails closed on unsafe attribute access.

    The stock sandbox answers an unsafe attribute with an instance of the
    configured ``undefined`` class. With ``PreserveUndefined`` that prints
    ``{{__class__}}`` and the render continues; raising makes the refusal
    reach the caller instead.
    """

    def unsafe_undefined(self, obj: Any, attribute: str) -> NoReturn:
        raise SecurityError(
            f"access to attribute {attribute!r} of"
            f" {type(obj).__name__!r} object is unsafe."
        )


def build_prompt_environment(undefined: type) -> PromptSandboxEnvironment:
    return PromptSandboxEnvironment(
        variable_start_string="{{",
        variable_end_string="}}",
        undefined=undefined,
    )


@dataclass(frozen=True)
class PreparedPrompt:
    """Authored source plus its context and per-occurrence display aliases.

    Only literal-data runs containing spaced-name insertions become aliases.
    Their values receive the base newline/author-trim treatment without ever
    entering the parser. ``context`` is reused by later editor turns; display
    aliases are only bound for this source.
    """

    source: str
    context: dict[str, Any]
    aliases: dict[str, Any]

    def render_context(self) -> dict[str, Any]:
        return {**self.context, **self.aliases}


def prepare_prompt(
    template: str, values: dict[str, Any], *, parse_json: bool
) -> PreparedPrompt:
    """Bind legacy spaced names without splicing any value into source.

    Dotted names have already been nested by the runtime's numeric-aware
    helper. Leave their expressions alone so lexical rebinding still works.
    As at base, spaced names are consumed from context; top-level JSON strings
    are decoded only in Jinja mode, never for the spaced display values.
    """
    context = dict(values)
    aliasable: set[str] = set()
    for match in SIMPLE_PRINT_TAG.finditer(template):
        name = match.group(1).strip()
        if " " in name and name in values:
            context.pop(name, None)
            aliasable.add(name)

    if parse_json:
        _parse_json_strings(context)

    aliases: dict[str, Any] = {}
    candidates = (f"{_ALIAS_PREFIX}{index}" for index in itertools.count())
    pieces: list[str] = []
    copied = 0
    runs = find_data_runs(template, aliasable) if aliasable else ()
    for start, end, tags, strip_left, strip_right in runs:
        # A whole literal-data run lets trim cross empty/whitespace values,
        # and normalises even CR/LF pairs split across authored text and data.
        # This string is display data ONLY: it is never scanned or compiled.
        chunks = []
        pos = start
        for tag_start, tag_end, name in tags:
            chunks.extend((template[pos:tag_start], str(values[name])))
            pos = tag_end
        chunks.append(template[pos:end])
        display = "".join(chunks).replace("\r\n", "\n").replace("\r", "\n")
        if end == len(template):
            display = display.removesuffix("\n")
        if strip_left:
            display = display.lstrip()
        if strip_right:
            display = display.rstrip()
        alias = next(
            candidate
            for candidate in candidates
            if candidate not in template and candidate not in context
        )
        # This run replaces literal template data, which Jinja never escapes,
        # even in authored autoescape blocks. Marking only this inert display
        # string preserves that rule without compiling it or trusting other values.
        aliases[alias] = Markup(display)
        pieces += (template[copied:start], "{{ ", alias, " }}")
        copied = end
    pieces.append(template[copied:])
    return PreparedPrompt(source="".join(pieces), context=context, aliases=aliases)


def _parse_json_strings(context: dict[str, Any]) -> None:
    for key, value in list(context.items()):
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if (stripped.startswith("[") and stripped.endswith("]")) or (
            stripped.startswith("{") and stripped.endswith("}")
        ):
            try:
                context[key] = json.loads(value)
            except ValueError:
                pass
