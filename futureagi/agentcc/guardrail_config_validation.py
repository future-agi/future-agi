"""Selected-only builtin config checks, based on the Go leaf constructors.

These are not the broader, uppercase reference PII IDs. Unknown/custom check
names retain their own factory contract. Regex checking is deliberately bounded:
Go regexp compilation at the gateway, not a foreign regex engine, is authoritative.
"""

# Existing org-rule aliases also resolve to these builtins in config_push.
BUILTIN_CONFIG_ALIASES = {
    "pii-detector": "pii-detection",
    "secrets-detector": "secret-detection",
}

# Pin these against the leaf catalogs in tests; do not copy the reference API IDs.
SELECTORS = {
    "pii-detection": (
        "entities",
        frozenset(
            {
                "email",
                "phone",
                "ssn",
                "credit_card",
                "ipv4",
                "ipv6",
                "dob",
                "passport",
                "drivers_license",
                "iban",
                "zip_code",
                "aws_key",
                "api_key",
                "url_credentials",
                "mac_address",
                "ein",
                "mrn",
                "bitcoin",
            }
        ),
    ),
    "content-moderation": (
        "categories",
        frozenset({"toxicity", "hate_speech", "violence", "sexual", "self_harm"}),
    ),
    "secret-detection": (
        "types",
        frozenset(
            {
                "aws_access_key",
                "aws_secret_key",
                "github_token",
                "gitlab_token",
                "slack_token",
                "stripe_key",
                "openai_key",
                "private_key",
                "connection_string",
                "jwt",
                "password_assign",
            }
        ),
    ),
}
BUILTIN_CONFIG_NAMES = (
    frozenset(SELECTORS)
    | {
        "language-detection",
        "topic-restriction",
        "input-validation",
        "keyword-blocklist",
    }
    | BUILTIN_CONFIG_ALIASES.keys()
)


def _text(value):
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _string_array(value):
    return isinstance(value, list) and all(_text(item) for item in value)


def bounded_go_pattern_error(pattern):
    r"""Reject a bounded set of definitely invalid Go regex constructs.

    Checks trailing escapes, unclosed classes/groups, unmatched close groups,
    lookaround and numeric/named backreferences. Tracks escaped literals, Go
    \Q...\E quoting, literal leading ']' in classes and POSIX classes. Go octal
    escapes (including \12), Unicode classes, flags and named groups survive.

    This is NOT an RE2 parser: repetition bounds, class ranges, Unicode class
    names and other syntax remain the Go runtime's responsibility. We deliberately
    do not reject unfamiliar syntax using Python re or an allowlist of patterns.
    No raw pattern is included in the returned error.
    """
    depth = 0
    in_class = False
    class_first = False
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "\\":
            if i + 1 == len(pattern):
                return "pattern has a trailing escape"
            escaped = pattern[i + 1]
            if escaped == "Q" and not in_class:
                end = pattern.find(r"\E", i + 2)
                i = len(pattern) if end < 0 else end + 2
                continue
            if escaped in "123456789":
                # Go accepts two/three-digit octal, not single-digit backrefs.
                if (
                    escaped in "89"
                    or i + 2 == len(pattern)
                    or pattern[i + 2] not in "01234567"
                ):
                    return "pattern uses a backreference unsupported by Go"
            if escaped in "kg" and i + 2 < len(pattern) and pattern[i + 2] in "<{'":
                return "pattern uses a backreference unsupported by Go"
            i += 2
            if in_class:
                class_first = False
            continue
        if in_class:
            if char == "[" and pattern.startswith("[:", i):
                # A complete POSIX class is one class element, not its closing ].
                end = pattern.find(":]", i + 2)
                if end >= 0:
                    i = end + 2
                    class_first = False
                    continue
            if char == "]" and not class_first:
                in_class = False
            class_first = False
        elif char == "[":
            in_class = True
            class_first = True
            if i + 1 < len(pattern) and pattern[i + 1] == "^":
                i += 1
        elif char == "(":
            if pattern.startswith(("(?=", "(?!", "(?<=", "(?<!", "(?P="), i):
                return "pattern uses lookaround or a backreference unsupported by Go"
            depth += 1
        elif char == ")":
            if depth == 0:
                return "pattern has an unmatched closing group"
            depth -= 1
        i += 1
    if in_class:
        return "pattern has an unterminated character class"
    if depth:
        return "pattern has an unterminated group"
    return None


def _length_error(config):
    fields = ("min_message_length", "max_message_length", "max_total_length")
    for field in fields:
        if field not in config:
            continue
        value = config[field]
        # JSON numbers reach Go as float64 before conversion to a 64-bit int.
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value < 2**63
            or value != int(value)
            or float(value) >= 2**63
        ):
            return f"{field} must be a nonnegative integer below 2^63"
    minimum = config.get("min_message_length", 0)
    for field in fields[1:]:
        maximum = config.get(field, 0)
        if maximum > 0 and minimum > maximum:
            return f"min_message_length must not exceed enabled {field}"
    return None


def builtin_config_error(name, config):
    """Return a value-free reason for the six builtin families, or None.

    Omitted config is supplied as {} by the caller; explicit nonobjects are not
    defaults. Empty selectors preserve constructor defaults. Empty language/topic
    settings impose no restriction (they do not enable protection).
    """
    name = BUILTIN_CONFIG_ALIASES.get(name, name)
    if name not in BUILTIN_CONFIG_NAMES or name == "keyword-blocklist":
        return None
    if not isinstance(config, dict):
        return "config must be a JSON object"
    if name in SELECTORS:
        field, catalog = SELECTORS[name]
        if field in config and (
            not _string_array(config[field])
            or any(item not in catalog for item in config[field])
        ):
            return f"{field} must be an array of supported detector names"
    elif name == "language-detection":
        if "allowed_scripts" in config and not _string_array(config["allowed_scripts"]):
            return "allowed_scripts must be an array of strings"
    elif name == "topic-restriction":
        for field in ("allowed_topics", "forbidden_topics"):
            if field not in config:
                continue
            topics = config[field]
            if not isinstance(topics, list):
                return f"{field} must be an array of topic objects"
            for topic in topics:
                if not isinstance(topic, dict):
                    return f"{field} must contain only topic objects"
                if "name" in topic and not _text(topic["name"]):
                    return f"{field} topic name must be a string"
                keywords = topic.get("keywords")
                if not _string_array(keywords) or not keywords:
                    return f"{field} keywords must be a non-empty array of strings"
    elif name == "input-validation":
        for field in ("deny_patterns", "require_patterns"):
            if field not in config:
                continue
            patterns = config[field]
            if not _string_array(patterns):
                return f"{field} must be an array of strings"
            for pattern in patterns:
                if reason := bounded_go_pattern_error(pattern):
                    return f"{field}: {reason}; Go runtime validates remaining syntax"
        return _length_error(config)
    return None
