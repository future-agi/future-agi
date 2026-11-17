"""The cassette scrubber's placeholders (cmd/agentcc-cassette/cassette/
placeholder.go): a recorded value that was replaced cannot be sent live.

The scrubber writes scrubbed-token-N, scrubbed-literal-N, scrubbed-id-N,
scrubbed-unmatched, user-N@example.com, 00000000-0000-4000-8000-NNNNNNNNNNNN
for a uuid, and for an id of digits a number of the same length, 9, zeros,
then N (it keeps the id's shape). That last form cannot be told from data for
certain, so a digit run of six or more in that form is taken as scrubbed: the
safe direction (the call is skipped, never sent with a wrong id).
"""

from __future__ import annotations

import re

from .toolshash import Number

MIN_ID = 6  # the scrubber's minID

PLACEHOLDER = re.compile(
    r"^(?:scrubbed-(?:token|literal|id)-[0-9]+|scrubbed-unmatched|user-[0-9]+@example\.com|"
    r"00000000-0000-4000-8000-[0-9]{12})$"
)
_IN_TEXT = re.compile(
    r"scrubbed-(?:token|literal|id)-[0-9]+|scrubbed-unmatched|user-[0-9]+(?:@|%40)example\.com|"
    r"00000000-0000-4000-8000-[0-9]{12}"
)
_DIGIT_PLACEHOLDER = re.compile(r"^90*[1-9][0-9]*$")
_DIGIT_RUN = re.compile(r"(?<![0-9A-Za-z])[0-9]{6,}(?![0-9A-Za-z])")


def _digits_placeholder(s: str) -> bool:
    return len(s) >= MIN_ID and bool(_DIGIT_PLACEHOLDER.match(s))


def holds_placeholder(value) -> bool:
    """Whether a JSON value (arguments, a body) holds a scrubbed value."""
    if isinstance(value, dict):
        return any(
            holds_placeholder(k) or holds_placeholder(v) for k, v in value.items()
        )
    if isinstance(value, list):
        return any(holds_placeholder(v) for v in value)
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int) or isinstance(value, Number):
        return _digits_placeholder(str(value))
    if isinstance(value, str):
        return bool(
            PLACEHOLDER.match(value)
            or _IN_TEXT.search(value)
            or _digits_placeholder(value)
        )
    return False


def text_holds_placeholder(text: str) -> bool:
    """Whether a URL path or query string holds a scrubbed value."""
    if _IN_TEXT.search(text):
        return True
    return any(_digits_placeholder(m.group(0)) for m in _DIGIT_RUN.finditer(text))
