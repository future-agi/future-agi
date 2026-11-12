"""Shared sandboxed prompt rendering and dataset cell context preparation."""

import datetime
import decimal
import re
import string
import uuid
from collections.abc import Iterator, Sequence

from chevron.tokenizer import tokenize as tokenize_mustache
from jinja2.exceptions import SecurityError
from jinja2.sandbox import SandboxedEnvironment

TEMPLATE_FORMAT_FSTRING = "f-string"
TEMPLATE_FORMAT_MUSTACHE = "mustache"
TEMPLATE_FORMAT_JINJA2 = "jinja2"
DEFAULT_TEMPLATE_FORMAT = TEMPLATE_FORMAT_JINJA2

_jinja2_env = SandboxedEnvironment()


class _AttributeRefused(SecurityError):
    """A Jinja template read an attribute the sandbox does not allow."""

    def __init__(self, attribute):
        super().__init__(attribute)
        self.attribute = attribute


class _RefusingSandboxedEnvironment(SandboxedEnvironment):
    """Sandbox that stops the render at an attribute it does not allow.

    The default sandbox renders such an attribute ({{ name.__class__ }}) as
    empty text and carries on, so the run silently leaves it out.
    """

    def unsafe_undefined(self, obj, attribute):
        raise _AttributeRefused(attribute)


_jinja2_refusing_env = _RefusingSandboxedEnvironment()


class TemplateExpressionRefused(ValueError):
    """A Jinja template part that is not allowed; the message says what to do."""


def _refused_message(attribute=None):
    if attribute:
        return (
            f"Not run: the prompt reads {attribute}, which a template is not "
            "allowed to read. Remove it from the prompt and run again."
        )
    return (
        "Not run: part of the prompt's template is not allowed. "
        "Remove it from the prompt and run again."
    )


_FSTRING_REFUSED = "This template can only insert a named value or one of its keys."
_FSTRING_MALFORMED = (
    "This template has a { or } that is not matched. "
    "Write {{ or }} for a literal brace."
)
_FSTRING_FIELD_HEAD = re.compile(r"[^.\[]*")
_FSTRING_FIELD_PART = re.compile(r"\.([^.\[]*)|\[([^\]]*)\]")
_FSTRING_CONVERSIONS = {"r": repr, "s": str, "a": ascii}
_FSTRING_MAX_SPEC = 1000
_FSTRING_MAX_INDEX_DIGITS = 20
# Checked by exact type, so a subclass with its own methods is refused.
_FSTRING_EXACT_TYPES = (
    datetime.date,
    datetime.time,
    datetime.datetime,
    decimal.Decimal,
    uuid.UUID,
)
_FSTRING_FORMAT_TYPES = (str, int, float, bool, type(None)) + _FSTRING_EXACT_TYPES


def _fstring_field_parts(field_name):
    """Split a field name into its dictionary keys and list indexes."""
    head = _FSTRING_FIELD_HEAD.match(field_name).group()
    parts = [head]
    position = len(head)
    while position < len(field_name):
        match = _FSTRING_FIELD_PART.match(field_name, position)
        if match is None:
            raise ValueError(_FSTRING_REFUSED)
        key = match.group(1)
        parts.append(match.group(2) if key is None else key)
        position = match.end()
    if any(not part or part.startswith("_") for part in parts):
        raise ValueError(_FSTRING_REFUSED)
    return parts


def _fstring_value(context, field_name):
    """Resolve a field by dictionary key and list index only."""
    name = _FSTRING_FIELD_HEAD.match(field_name).group()
    if not name or name.isdecimal():
        # No positional values are passed, so {} and {0} name no value.
        raise ValueError(_FSTRING_REFUSED)
    if field_name in context:
        return context[field_name]
    value = context
    for part in _fstring_field_parts(field_name):
        if isinstance(value, dict):
            if part not in value:
                raise KeyError(part)
            value = value[part]
        elif (
            isinstance(value, list)
            and part.isascii()
            and part.isdigit()
            and len(part) <= _FSTRING_MAX_INDEX_DIGITS
        ):
            value = value[int(part)]
        else:
            raise ValueError(_FSTRING_REFUSED)
    return value


_FSTRING_SPEC_NUMBER = re.compile(r"\d+")
_FSTRING_MAX_WIDTH = 1000
# For a date or time the format spec is a pattern: a number in it is literal
# text, already bounded by the spec length limit.
_FSTRING_PATTERN_TYPES = (datetime.date, datetime.time, datetime.datetime)


def _fstring_spec_too_wide(value, conversion, format_spec):
    """Whether a width or precision in a format spec is above the limit."""
    if conversion is None and type(value) in _FSTRING_PATTERN_TYPES:
        return False
    for number in _FSTRING_SPEC_NUMBER.findall(format_spec):
        number = number.lstrip("0")
        if len(number) > 4 or int(number or "0") > _FSTRING_MAX_WIDTH:
            return True
    return False


def _fstring_formatted(value, conversion, format_spec):
    """Apply a conversion and format spec to a value the renderer formats."""
    if (
        type(value) not in _FSTRING_FORMAT_TYPES
        or (conversion is not None and conversion not in _FSTRING_CONVERSIONS)
        or "{" in format_spec
        or "}" in format_spec
        or len(format_spec) > _FSTRING_MAX_SPEC
        or _fstring_spec_too_wide(value, conversion, format_spec)
    ):
        raise ValueError(_FSTRING_REFUSED)
    if conversion is not None:
        value = _FSTRING_CONVERSIONS[conversion](value)
    try:
        return format(value, format_spec)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(_FSTRING_REFUSED) from None


def render_fstring_template(template_str: str, context: dict) -> str:
    """Render an f-string format template ({name}) with a strict renderer.

    A field inserts a value found by dictionary key or list index: a plain
    name (names may contain spaces and dots), {name[key]}, {name.key} and
    {name[0]}. None, dates, times, decimals and ids insert their text as
    before. A conversion or format spec applies to text, numbers, booleans,
    dates, times, decimals and ids only, with a format spec of at most 1000
    characters. Anything else in a field is refused with a plain ValueError;
    a missing name raises KeyError.
    """
    try:
        pieces = list(string.Formatter().parse(template_str))
    except ValueError:
        raise ValueError(_FSTRING_MALFORMED) from None

    rendered = []
    for literal_text, field_name, format_spec, conversion in pieces:
        rendered.append(literal_text)
        if field_name is None:
            continue
        value = _fstring_value(context, field_name)
        if conversion or format_spec:
            rendered.append(_fstring_formatted(value, conversion, format_spec))
        elif value is None or type(value) in _FSTRING_EXACT_TYPES:
            rendered.append(str(value))
        elif isinstance(value, (str, int, float, bool, dict, list)):
            rendered.append(str(value))
        else:
            raise ValueError(_FSTRING_REFUSED)
    return "".join(rendered)


# The mustache renderer below follows the renderer of chevron 0.14.0
# (https://github.com/noahmorrison/chevron) step by step and uses its
# tokenizer, with three changes: a name resolves by dictionary key and list
# index only, a value is never called, and a partial renders as empty text
# without reading a file.
#
# chevron is distributed under the MIT License:
#
# Copyright (c) 2014 Noah Morrison
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# & is escaped first.
_MUSTACHE_HTML_CODES = (("&", "&amp;"), ('"', "&quot;"), ("<", "&lt;"), (">", "&gt;"))


def _mustache_html_escape(text):
    """HTML-escape & " < > in a variable's text."""
    for char, code in _MUSTACHE_HTML_CODES:
        text = text.replace(char, code)
    return text


def _mustache_value(key, scopes):
    """Resolve a name by dictionary key and list index only.

    A name that does not resolve in a scope is looked up in the next one; a
    name found in no scope resolves to "".
    """
    if key == ".":
        return scopes[0]

    for scope in scopes:
        try:
            for child in key.split("."):
                if isinstance(scope, dict):
                    scope = scope[child]
                elif isinstance(scope, (list, str)):
                    scope = scope[int(child)]
                else:
                    raise KeyError(child)

            # 0 and False are returned as they are; any other falsy value as "".
            if scope in (0, False):
                return scope
            return scope or ""
        except (AttributeError, KeyError, IndexError, ValueError):
            pass

    return ""


def _render_mustache_tokens(tokens, scopes):
    """Render mustache tokens against a stack of scopes, innermost first."""
    output = []
    for tag, key in tokens:
        current_scope = scopes[0]

        if tag == "end":
            del scopes[0]

        # Inside a falsy section, only nested sections are tracked.
        elif not current_scope and len(scopes) != 1:
            if tag in ("section", "inverted section"):
                scopes.insert(0, False)

        elif tag == "literal":
            output.append(key)

        elif tag == "variable":
            thing = _mustache_value(key, scopes)
            if thing is True and key == ".":
                # An inverted section pushed True; use the scope below it.
                thing = scopes[1]
            if not isinstance(thing, str):
                thing = str(thing)
            output.append(_mustache_html_escape(thing))

        elif tag == "no escape":
            thing = _mustache_value(key, scopes)
            if not isinstance(thing, str):
                thing = str(thing)
            output.append(thing)

        elif tag == "section":
            scope = _mustache_value(key, scopes)
            if isinstance(scope, (Sequence, Iterator)) and not isinstance(scope, str):
                # Gather the tokens up to this section's end tag and render
                # them once for each item.
                section_tokens = []
                tags_with_same_key = 0
                for token in tokens:
                    if token == ("section", key):
                        tags_with_same_key += 1
                    if token == ("end", key):
                        tags_with_same_key -= 1
                        if tags_with_same_key < 0:
                            break
                    section_tokens.append(token)

                for thing in scope:
                    output.append(
                        _render_mustache_tokens(iter(section_tokens), [thing] + scopes)
                    )
            else:
                # Any other value, a callable included, becomes the scope of
                # the section and is never called.
                scopes.insert(0, scope)

        elif tag == "inverted section":
            scope = _mustache_value(key, scopes)
            scopes.insert(0, not scope)

        # A partial tag adds no text and never reads a file. Set-delimiter
        # tags are applied by the tokenizer and add no text either.

    return "".join(output)


def render_mustache_template(template_str: str, context: dict) -> str:
    """Render a mustache format template ({{name}}) with a strict renderer.

    Variables (HTML-escaped unless written {{{name}}} or {{& name}}),
    sections, inverted sections, comments, {{.}}, dotted names and
    set-delimiter tags render as before. A name resolves by dictionary key
    and list index only (a numeric part indexes a list or text), a value is
    never called, and a partial tag renders as empty text without reading a
    file.
    """
    return _render_mustache_tokens(tokenize_mustache(template_str), [context])


def render_template(
    template_str: str,
    context: dict,
    template_format: str = None,
    *,
    refuse_unsafe: bool = False,
) -> str:
    """Render using the same formats and sandbox as dataset Run Prompt.

    With refuse_unsafe, a Jinja template that reads an attribute the sandbox
    does not allow raises TemplateExpressionRefused with a plain message,
    instead of rendering that part as empty text.
    """
    if not template_str:
        return template_str or ""

    if template_format is None:
        template_format = DEFAULT_TEMPLATE_FORMAT

    if template_format == TEMPLATE_FORMAT_FSTRING:
        return render_fstring_template(template_str, context)
    elif template_format == TEMPLATE_FORMAT_MUSTACHE:
        return render_mustache_template(template_str, context)
    elif template_format == TEMPLATE_FORMAT_JINJA2:
        # Preserve support for column names containing spaces.
        processed = template_str
        safe_ctx = dict(context)
        raw_vars = re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", processed)
        for var_name in raw_vars:
            stripped = var_name.strip()
            if " " in stripped and stripped in safe_ctx:
                processed = processed.replace(
                    "{{" + var_name + "}}", str(safe_ctx.pop(stripped))
                )
                processed = processed.replace(
                    "{{ " + stripped + " }}", str(context.get(stripped, ""))
                )
        if not refuse_unsafe:
            return _jinja2_env.from_string(processed).render(**safe_ctx)
        try:
            return _jinja2_refusing_env.from_string(processed).render(**safe_ctx)
        except SecurityError as refused:
            raise TemplateExpressionRefused(
                _refused_message(getattr(refused, "attribute", None))
            ) from None
    else:
        raise ValueError(
            f"Unknown template_format: {template_format}. "
            f"Supported: {TEMPLATE_FORMAT_FSTRING}, {TEMPLATE_FORMAT_MUSTACHE}, {TEMPLATE_FORMAT_JINJA2}"
        )


class JsonStr(dict):
    """Allow property access while preserving the original JSON string output."""

    def __init__(self, data, raw):
        super().__init__(data)
        self._raw = raw

    def __str__(self):
        return self._raw


def add_template_context_value(context, name, value, template_format=None):
    """Add one dataset cell, preserving JSON objects, Jinja lists and dotted names.

    Return the prepared value so Run Prompt can also register its column-ID alias.
    """
    cell_value = value if value is not None else ""
    if cell_value and isinstance(cell_value, str):
        from model_hub.utils.json_path_resolver import parse_json_safely

        parsed_json, is_valid = parse_json_safely(cell_value)
        if is_valid and isinstance(parsed_json, dict):
            cell_value = JsonStr(parsed_json, cell_value)
        elif (
            is_valid
            and isinstance(parsed_json, list)
            and template_format in ("jinja", "jinja2")
        ):
            cell_value = parsed_json

    parts = name.split(".")
    current = context
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            current[part] = cell_value
        else:
            if part not in current:
                current[part] = {}
            current = current[part]
    return cell_value
