"""Find the print tags Jinja itself would interpolate in a prompt template.

:func:`~.template_sandbox.prepare_prompt` binds ``{{ spaced name }}`` data
through generated identifiers. Only tags that Jinja reads as interpolations
may be rewritten. Text inside ``{% raw %}`` blocks,
``{# #}`` comments, statement tags and string literals is the author's literal
text and stays exactly as written.

The scanner follows the root, raw, comment and tag states of the Jinja 3.1
lexer for the environment :func:`~.template_sandbox.build_prompt_environment`
builds (``{{ }}``, ``{% %}`` and ``{# #}`` delimiters, no line statements, no
trim or lstrip). Inside a tag it tracks only string literals and bracket
depth, because nothing else decides where Jinja ends a tag. When the source
holds a construct Jinja rejects (an unterminated tag, comment, raw block or
string, or an unexpected closing bracket), scanning stops there. Such a
template fails to compile whether or not the rest of it is rewritten.
"""

from __future__ import annotations

import re
from collections.abc import Container, Iterator

# A print tag with no braces inside; group 1 is its expression. This is the
# spelling a spaced or dotted mapped name is written in, before and after the
# sandbox change.
SIMPLE_PRINT_TAG = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")

# Where template data ends. The alternatives are tried in the lexer's order at
# each position, so ``{% raw %}`` wins over a plain statement tag.
_TAG_START = re.compile(
    r"(?P<raw>\{%[-+]?\s*raw\s*(?:-%\}|%\}))"
    r"|(?P<print>\{\{)"
    r"|(?P<statement>\{%)"
    r"|(?P<comment>\{#)"
)
_RAW_END = re.compile(r"\{%[-+]?\s*endraw\s*[-+]?%\}")
_COMMENT_END = re.compile(r"#\}")
_PRINT_END = re.compile(r"-?\}\}")
_STATEMENT_END = re.compile(r"[-+]?%\}")
_STRING = re.compile(r"'[^'\\]*(?:\\.[^'\\]*)*'|\"[^\"\\]*(?:\\.[^\"\\]*)*\"", re.S)
_OPENERS = "([{"
_CLOSERS = ")]}"


def find_print_tags(
    source: str, names: Container[str]
) -> Iterator[tuple[int, int, str]]:
    """Yield ``(start, end, name)`` for each print tag naming one of ``names``.

    A tag qualifies when it starts in template data and its whole expression,
    trimmed, is in ``names``. Raw blocks, comments, statement tags and every
    other print tag are skipped as the lexer would skip them, so a ``{{ }}``
    spelling inside them is never yielded.
    """
    for start, end, kind, name in _scan_tags(source, names):
        if kind == "value":
            yield start, end, name


def find_data_runs(source: str, names: Container[str]):
    """Find literal-data runs containing mapped prints, with authored trim flags.

    Treat ONLY the selected print tags as inert insertions. Every other Jinja
    tag separates data runs; raw blocks and comments are opaque. Malformed
    source remains in source so the compiler's syntax policy still applies.
    """
    data_start = 0
    strip_left = False
    tags = []
    for start, end, kind, name in _scan_tags(source, names):
        if kind == "value":
            tags.append((start, end, name))
            continue
        if tags:
            yield (
                data_start,
                start,
                tags,
                strip_left,
                source[start + 2 : start + 3] == "-",
            )
        data_start = end
        strip_left = kind != "invalid" and source[end - 3] == "-"
        tags = []
    if tags:
        yield data_start, len(source), tags, strip_left, False


def _scan_tags(source: str, names: Container[str]):
    """Scan authored source only; values never enter this lexer."""
    pos = 0
    while True:
        start = _TAG_START.search(source, pos)
        if start is None:
            return
        kind = start.lastgroup
        if kind == "print":
            tag = SIMPLE_PRINT_TAG.match(source, start.start())
            if tag is not None and tag.group(1).strip() in names:
                yield tag.start(), tag.end(), "value", tag.group(1).strip()
                pos = tag.end()
                continue
            end = _tag_end(source, start.end(), _PRINT_END)
        elif kind == "statement":
            end = _tag_end(source, start.end(), _STATEMENT_END)
        else:
            closing = _RAW_END if kind == "raw" else _COMMENT_END
            found = closing.search(source, start.end())
            end = None if found is None else found.end()
        if end is None:
            yield start.start(), len(source), "invalid", None
            return
        yield start.start(), end, kind, None
        pos = end


def _tag_end(source: str, pos: int, closing: re.Pattern[str]) -> int | None:
    """Index just past the delimiter that ends the tag whose body starts at pos.

    Like the lexer, a closing delimiter only counts outside string literals
    and with every bracket opened in the tag closed again.
    """
    depth = 0
    while pos < len(source):
        if depth == 0:
            found = closing.match(source, pos)
            if found is not None:
                return found.end()
        char = source[pos]
        if char in "'\"":
            literal = _STRING.match(source, pos)
            if literal is None:
                return None
            pos = literal.end()
            continue
        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            if depth == 0:
                return None
            depth -= 1
        pos += 1
    return None
