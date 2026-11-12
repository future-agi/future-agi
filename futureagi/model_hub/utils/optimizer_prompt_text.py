"""Shared text assembly and UUID references for optimizer admission and copies."""

import re
from copy import deepcopy
from uuid import UUID

COLUMN_REFERENCE_PATTERN = re.compile(
    r"\{\{\s*([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?=\s*(?:\}\}|\.|\[))"
)


class OptimizerCopyRefused(ValueError):
    """A fixed, value-free refusal shared by the experiment copy writers."""


def _text_slots(messages):
    for message in messages or []:
        content = message.get("content", [])
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    yield part, "text"
        elif isinstance(content, str):
            yield message, "content"


def optimizer_prompt_text(messages, *, strip=True):
    """Join text without separators; templates opt out of outer whitespace trimming."""
    text = "".join(part.get(key, "") for part, key in _text_slots(messages))
    return text.strip() if strip else text


def canonicalize_optimizer_prompt_text(text):
    """Use execution's Unicode rules without requiring EE in model_hub imports."""
    try:
        from ee.agent_opt.utils.template_variables import normalize_prompt_text
    except ImportError:
        # OSS does not perform the EE execution-time normalization either.
        return text
    return normalize_prompt_text(text)


def optimizer_column_references(messages):
    """Inspect canonical fields and canonical text assembled across boundaries."""

    def references(value):
        if isinstance(value, str):
            canonical = canonicalize_optimizer_prompt_text(value)
            for match in COLUMN_REFERENCE_PATTERN.finditer(canonical):
                yield UUID(match.group(1))
        elif isinstance(value, list):
            for item in value:
                yield from references(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from references(item)

    return set(references(messages)) | set(references(optimizer_prompt_text(messages)))


def replace_optimizer_column_ids(text, mapping):
    """Replace only the UUID, preserving whitespace and nested path suffixes."""

    def replace(match):
        replacement = mapping.get(str(UUID(match.group(1))))
        if replacement is None:
            return match.group(0)
        prefix_length = match.start(1) - match.start()
        return match.group(0)[:prefix_length] + replacement

    return COLUMN_REFERENCE_PATTERN.sub(replace, text)


def _remap_optimizer_text_fragments(fragments, mapping):
    """Project canonical UUID edits onto their original characters and fragments.

    NFKC can expand one UUID glyph into several characters. Replacing that glyph
    in its own fragment, rather than reslicing normalized text, keeps neighboring
    text and media boundaries intact. Invisible characters and non-UUID text are
    retained verbatim; only a glyph intersecting a mapped UUID is rewritten.
    """
    text = "".join(fragments)
    canonical = canonicalize_optimizer_prompt_text(text)
    if not any(
        str(UUID(match.group(1))) in mapping
        for match in COLUMN_REFERENCE_PATTERN.finditer(canonical)
    ):
        return fragments

    forms = {char: canonicalize_optimizer_prompt_text(char) for char in set(text)}
    expanded = "".join(forms[char] for char in text)
    origins = [
        (index, offset)
        for index, char in enumerate(text)
        for offset in range(len(forms[char]))
    ]
    edits = {}
    raw_start = canonical_start = 0
    # ASCII reference syntax cannot be created by cross-character composition.
    # Character-wise normalization locates candidates, but the whole-string
    # normalizer remains authoritative (including composition elsewhere).
    for candidate in COLUMN_REFERENCE_PATTERN.finditer(expanded):
        start, within = origins[candidate.start()]
        # Opening braces are normalization boundaries. Normalize disjoint prefix
        # chunks, not every entire prefix, to keep offset calculation linear.
        canonical_start += len(
            canonicalize_optimizer_prompt_text(text[raw_start:start])
        )
        raw_start = start
        match = COLUMN_REFERENCE_PATTERN.match(canonical, canonical_start + within)
        if match is None or match.group(1) != candidate.group(1):
            continue
        replacement = mapping.get(str(UUID(match.group(1))))
        if replacement is None:
            continue
        for position, char in zip(
            range(candidate.start(1), candidate.end(1)), replacement, strict=True
        ):
            index, offset = origins[position]
            edits.setdefault(index, list(forms[text[index]]))[offset] = char

    remapped = []
    offset = 0
    for fragment in fragments:
        remapped.append(
            "".join(
                "".join(edits[offset + index]) if offset + index in edits else char
                for index, char in enumerate(fragment)
            )
        )
        offset += len(fragment)
    return remapped


def remap_optimizer_messages(messages, column_mapping):
    """Copy canonical whole/split references without moving message boundaries.

    Unknown references deliberately remain for the destination validator to refuse.
    Both recursive fields and assembled text use the same canonical remapping.
    """
    mapping = {
        str(UUID(str(old))): str(UUID(str(new))) for old, new in column_mapping.items()
    }

    def remap(value):
        if isinstance(value, str):
            return _remap_optimizer_text_fragments([value], mapping)[0]
        if isinstance(value, list):
            return [remap(item) for item in value]
        if isinstance(value, dict):
            return {key: remap(item) for key, item in value.items()}
        return value

    copied = remap(deepcopy(messages))
    slots = list(_text_slots(copied))
    remapped = _remap_optimizer_text_fragments(
        [part.get(key, "") for part, key in slots], mapping
    )
    for (part, key), text in zip(slots, remapped, strict=True):
        part[key] = text
    return copied
