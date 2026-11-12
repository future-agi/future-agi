"""An organization's privacy redactor, as the gateway applies it to span scalars.

The gateway builds one redactor per org from the active config's ``privacy``
section (internal/tenant/store.go Redactor, internal/privacy/redactor.go):
only when ``enabled`` is true; mode ``full`` redacts every scalar, ``patterns``
(also the default for an empty mode) redacts a scalar any of the org's
``redact_patterns`` matches, ``none`` redacts nothing. SIEM export and the
explorer download apply the same decision to every exported scalar
(siem_scalar.sanitize), so a value the org's policy keeps out of its spans
is kept out of them too.

Where Python cannot reproduce the gateway's decision it redacts everything
instead: an unknown mode or malformed section, a pattern Python's ``re``
cannot compile, and a POSIX class (``[[:alpha:]]``), which RE2 reads as a
class and Python as a set of characters. The ``redactPatterns`` alias the
admin API accepts counts as well. Each choice redacts more than the gateway,
never less.
"""

import re

FULL = "full"
PATTERNS = "patterns"
NONE = "none"
_STRICTNESS = {NONE: 0, PATTERNS: 1, FULL: 2}
_PATTERN_KEYS = ("redact_patterns", "redactpatterns")


class OrgRedactor:
    __slots__ = ("mode", "patterns")

    def __init__(self, mode, patterns=()):
        self.mode = mode
        self.patterns = tuple(patterns)

    def redacts(self, text):
        """Whether the org's redactor would change ``text`` (Redact(text) != text)."""
        if self.mode == FULL:
            return True
        if self.mode == PATTERNS:
            return any(pattern.search(text) for pattern in self.patterns)
        return False


OFF = OrgRedactor(NONE)
REDACT_ALL = OrgRedactor(FULL)


def _values(section, *names):
    # Go's encoding/json matches object keys to field tags case-insensitively.
    return [value for key, value in section.items() if str(key).lower() in names]


def _mode(section):
    modes = _values(section, "mode")
    if not modes:
        return PATTERNS
    resolved = []
    for mode in modes:
        if mode is None or mode == "":
            resolved.append(PATTERNS)
        elif isinstance(mode, str) and mode in _STRICTNESS:
            resolved.append(mode)
        else:
            return None
    return max(resolved, key=_STRICTNESS.__getitem__)


def _compile(entry):
    """The entry's compiled pattern; None to skip it, False to fail closed."""
    if entry is None:
        return None
    if not isinstance(entry, dict):
        return False
    texts = _values(entry, "pattern")
    if any(text is not None and not isinstance(text, str) for text in texts):
        return False
    texts = [text for text in texts if text]
    if not texts:
        return None
    if any("[:" in text for text in texts):
        return False
    try:
        return [re.compile(text) for text in texts]
    except (re.error, RecursionError, OverflowError, ValueError):
        return False


def from_privacy(privacy):
    """The redactor an org's ``privacy`` config section asks for."""
    if privacy is None or privacy == {}:
        return OFF
    if not isinstance(privacy, dict):
        return REDACT_ALL
    if not any(
        value is not False and value is not None
        for value in _values(privacy, "enabled")
    ):
        return OFF
    mode = _mode(privacy)
    if mode is None:
        return REDACT_ALL
    if mode != PATTERNS:
        return OrgRedactor(mode)
    compiled = []
    for entries in _values(privacy, *_PATTERN_KEYS):
        if entries is None:
            continue
        if not isinstance(entries, list):
            return REDACT_ALL
        for entry in entries:
            patterns = _compile(entry)
            if patterns is False:
                return REDACT_ALL
            compiled.extend(patterns or ())
    return OrgRedactor(PATTERNS, compiled)


def org_redactor(organization_id):
    """The redactor of the org's active gateway config (none without one)."""
    from agentcc.models.org_config import AgentccOrgConfig

    config = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization_id=organization_id, is_active=True
        )
        .only("privacy")
        .first()
    )
    return from_privacy(config.privacy if config is not None else None)
