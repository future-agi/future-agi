"""Validation for project tags, which PostgreSQL stores as jsonb."""

import math

# Tags are labels. On Python 3.11 the C JSON codec shares the interpreter's
# 1000-frame recursion budget, and a request parses, checks, saves and renders
# tags from deep inside that stack: through the test client the deepest value
# that round-trips is about 830 levels on the detail route and 895 on the tags
# action. Keep the limit well below that, so every value within it is stored
# and returned, and the next level still parses and gets TAGS_TOO_DEEP.
TAG_MAX_NESTING_DEPTH = 500
INVALID_TAG_STRING = (
    "tags must not contain null characters or unpaired Unicode surrogates"
)
NON_FINITE_TAG_NUMBER = "tags must only contain finite numbers"
TAGS_TOO_DEEP = f"tags support at most {TAG_MAX_NESTING_DEPTH} nested levels"
TAGS_TOO_DEEP_TO_STORE = "tags are nested too deeply to store"


def _is_jsonb_incompatible_string(value):
    """PostgreSQL jsonb rejects NUL and unpaired surrogates; JSON parsing allows both.

    Valid escaped surrogate pairs have already been combined by the parser, so
    any surrogate left is unpaired and fails strict UTF-8 encoding. Each check
    runs in C rather than looping over characters in Python.
    """
    if "\x00" in value:
        return True
    if value.isascii():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False


def tag_validation_error(tags):
    """Return why parsed ``tags`` cannot be stored as jsonb, or None.

    Any JSON value is checked, including nested keys and values. Python's JSON
    parser accepts NUL, unpaired surrogates and overflowing numbers (``1e400``
    parses as infinity), all of which PostgreSQL rejects. The outermost list or
    object is level 1. Traverse iteratively so deep nesting cannot exhaust
    Python's stack.
    """
    # A virtual level-0 parent lets a scalar ``tags`` value share the loop.
    pending = [([tags], 0)]
    while pending:
        container, depth = pending.pop()
        if depth > TAG_MAX_NESTING_DEPTH:
            return TAGS_TOO_DEEP
        if isinstance(container, dict):
            # JSON object keys are always strings.
            if any(_is_jsonb_incompatible_string(key) for key in container):
                return INVALID_TAG_STRING
            children = container.values()
        else:
            children = container
        for child in children:
            if isinstance(child, str):
                if _is_jsonb_incompatible_string(child):
                    return INVALID_TAG_STRING
            elif isinstance(child, float):
                if not math.isfinite(child):
                    return NON_FINITE_TAG_NUMBER
            elif isinstance(child, list | dict):
                pending.append((child, depth + 1))
    return None
