"""Keep credentials out of responses, logs, traces and error reports.

ONE KEY C2 (PLATFORM-CREDENTIAL-CUSTODY, gate MONEY_SAFETY never-list): no
Authorization header value and no provider key may appear in any response,
log line, trace export or Sentry event, and an upstream error body is
redacted before anything quotes it.

A match is replaced by a fixed marker that names only its family, such as
"[hidden: github-token]" (MARKER_FORMAT): never a prefix, suffix, length or
hash of the value (KI09 PL3). count_matches reports counts per family and
nothing else (PL4).

Import time is the standard library only, because this module loads with the
logging configuration, before Django apps. The gateway's credential-header
rule (agentcc.services.credential_headers) is imported lazily on first use,
the middleware imports asgiref lazily, and zlib and a brotli codec are
imported only to decode an encoded error body.

FAMILIES, in priority order (overlapping matches are masked as one span,
labelled by the earliest family):
- the 23 rules of KI09-PROVIDER-KEY-PATTERN-LAYER-RULES-r1-FISEC, each
  Family.pattern the rule's pattern byte for byte;
- addendum D4: an AWS secret access key, masked only within 32 characters of
  a matched key id or right after a recognised name;
- disclosed extensions, after the KI09 families so the specific ones win:
  futureagi-gateway-key, sk-generic, authorization-header, bearer-token,
  api-key-header and query-credential.

Escaped carriers (r2a, SIF-1): text that was already JSON- or repr-encoded
writes a newline or tab before a value as a backslash and a letter, or as a
backslash-u or backslash-x code. Every leading boundary (a KI09 word
boundary, a header name's lookbehind, the D4 class edge) also accepts such an
escape, so a key right after one is still found.

Addendum-1 rulings:
- D1 linear time. A substring prefilter skips every family whose literal is
  absent; each scan resumes after the masked span. The private-key-block
  rule's lazy body is quadratic when many BEGIN lines have no END, so a BEGIN
  line is found and then the next END line is searched once.
- D2 mask-through. After a match, masking runs through the end of the rule's
  character class, so no suffix of the value survives. Every rule that ends
  in a word boundary, range-capped or fixed-length (C4), is matched without
  that final boundary (Family.matcher), so a longer value is found and masked
  through its class. A private key block is masked through its END line, or
  to the end of the text when it has none.
- D3 slack-webhook and private-key-block keep no leading word boundary.
- D4 as above. A standalone 40-character secret is left to registered-value
  masking.

The KI09 exclusions stand: Mistral, Cohere, Azure OpenAI 32-hex, Twilio
SK+32-hex and FutureAGI's own 32-hex platform keys have no distinctive format
and are never pattern-matched.
"""

import functools
import re
import string
import time

__all__ = [
    "FAMILIES",
    "MARKER_FORMAT",
    "CredentialScrubErrorResponseMiddleware",
    "count_matches",
    "redact_known_values",
    "redact_upstream_body",
    "scrub_bytes",
    "scrub_credentials_event",
    "scrub_sentry_event",
    "scrub_text",
    "scrub_value",
]

MARKER_FORMAT = "[hidden: {}]"
MAX_DEPTH = 32
UPSTREAM_BODY_LIMIT = 512
# The largest decoded error body the middleware scrubs; beyond it the body is
# replaced (fail closed).
DECODED_BODY_LIMIT = 8 << 20

_SCRUB_FAILED = MARKER_FORMAT.format("scrub-failed")
_SCRUB_FAILED_BYTES = _SCRUB_FAILED.encode()
_CREDENTIAL_FIELD = MARKER_FORMAT.format("credential-field")
_CREDENTIAL_FIELD_BYTES = _CREDENTIAL_FIELD.encode()
_DEPTH = MARKER_FORMAT.format("depth")
_CYCLE = MARKER_FORMAT.format("cycle")
_TRUNCATED = " [truncated]"

_KI09 = "KI09"
_D4 = "KI09-D4"
_EXTENSION = "extension"


class Family:
    """One pattern family: its name, declared pattern, source and matcher.

    pattern is what the family declares (for a KI09 family, the rule's
    pattern byte for byte); matcher is the compiled expression that runs
    (see the escape, D2 and D1 notes in the module docstring for where the
    two differ).
    """

    __slots__ = (
        "name",
        "pattern",
        "source",
        "matcher",
        "index",
        "_literals",
        "_folded",
        "_needs",
        "_run",
        "_locate",
        "_scan",
    )

    def __init__(
        self,
        name,
        pattern,
        source,
        *,
        matcher=None,
        literals=(),
        folded=(),
        needs=(),
        run=None,
        locate=None,
        scan=None,
    ):
        self.name = name
        self.pattern = pattern
        self.source = source
        self.matcher = re.compile(pattern if matcher is None else matcher)
        self.index = -1
        self._literals = literals
        self._folded = folded
        self._needs = needs
        self._run = None if run is None else re.compile(run + "*")
        self._locate = locate
        self._scan = scan

    def __repr__(self):
        return f"Family({self.name!r}, source={self.source!r})"


# Leading boundaries (SIF-1). A JSON or repr escape before a value ends in a
# letter or a hex digit, which a plain boundary reads as part of the value;
# each lookbehind below accepts one escape instead. Each is fixed-width.
_ESCAPED = (
    r"(?<=\\[abfnrtv0])"
    r"|(?<=\\u[0-9A-Fa-f]{4})"
    r"|(?<=\\U[0-9A-Fa-f]{8})"
    r"|(?<=\\x[0-9A-Fa-f]{2})"
    r"|(?<=%[0-9A-Fa-f]{2})"
)
_LEAD = r"(?:\b|" + _ESCAPED + r")"
_NAME_LEAD = r"(?:(?<![A-Za-z0-9])|" + _ESCAPED + r")"
_AWS_LEAD = r"(?:(?<![A-Za-z0-9/+])|" + _ESCAPED + r")"
_ESCAPE_TAIL = re.compile(
    r"(?:\\[abfnrtv0]|\\u[0-9A-Fa-f]{4}|\\U[0-9A-Fa-f]{8}|\\x[0-9A-Fa-f]{2}|%[0-9A-Fa-f]{2})\Z"
)


def _escape_before(text, position):
    """Whether text[:position] ends with one escape sequence."""
    return _ESCAPE_TAIL.search(text, max(0, position - 10), position) is not None


def _word_matcher(pattern):
    """A word-boundary rule as it runs: the leading boundary also accepts an
    escape (SIF-1); a final word boundary is dropped (D2 for range caps, C4
    for fixed lengths), so a longer value is found and masked through."""
    flags = "(?i)" if pattern.startswith("(?i)") else ""
    body = pattern[len(flags) :]
    if not body.startswith(r"\b"):
        raise ValueError("not a word-boundary-led rule")
    matcher = flags + _LEAD + body[2:]
    return matcher[:-2] if matcher.endswith(r"\b") else matcher


def _after(prefix):
    """Mask from the end of prefix (matched at the match start) onwards."""
    compiled = re.compile(prefix)

    def locate(match):
        return compiled.match(match.string, match.start()).end(), match.end()

    return locate


def _named_group(match):
    return match.span(match.lastgroup)


def _url_password(match):
    """Only the password of scheme://user:password@ (the user has no colon),
    up to the last "@" before the host (C1)."""
    text = match.string
    separator = text.index(":", text.index("://", match.start()) + 3)
    return separator + 1, match.end() - 1


def _scan_regex(family, text, spans):
    search = family.matcher.search
    run = family._run
    locate = family._locate
    index = family.index
    position = 0
    while True:
        match = search(text, position)
        if match is None:
            return
        start, end = match.span() if locate is None else locate(match)
        if run is not None:
            end = run.match(text, end).end()
        if start < end:
            spans.append((start, end, index))
        # Resume after the masked span (D1): a run is never scanned twice.
        position = max(end, match.end(), match.start() + 1)


_PEM_LABEL = "(?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
_PEM_BEGIN_PATTERN = "-----BEGIN " + _PEM_LABEL
_PEM_END = re.compile("-----END " + _PEM_LABEL)


def _scan_private_keys(family, text, spans):
    """BEGIN line, then the next END line; no END masks to the end (D1, D2)."""
    begin_search = family.matcher.search
    position = 0
    while True:
        begin = begin_search(text, position)
        if begin is None:
            return
        end = _PEM_END.search(text, begin.end())
        stop = len(text) if end is None else end.end()
        spans.append((begin.start(), stop, family.index))
        if end is None:
            return
        position = stop


# r3: names select values; value contents NEVER determine whether to mask.
# A string is bounded by its unescaped closing quote or physical line end.
# An unquoted header value runs to line end (including all Digest parameters).
# Container syntax only locates string tokens; numbers/booleans/null never
# receive unquoted markers. Encoding layers are scanned inside their enclosing
# string, with offsets mapped back to that string only. No eval, scheme grammar,
# token-length threshold, call guard or identifier exemption.
_MARK = r"\[hidden: [a-z0-9-]{1,64}\]"


def _not_marker(ends):
    """Not a complete marker that ends its value (so output is stable), while
    a value that only starts with a marker is still masked (SIF-8)."""
    return r"(?!" + _MARK + r"(?:" + ends + r"|\Z))"


_QUERY_VALUE = r"""[^&#\s"'\\]"""
_TOKEN = r"[A-Za-z0-9._~+/=-]"  # KI09's declared class; not a header heuristic.
_COMPLETE_MARK = re.compile(_MARK).fullmatch
_SPACE = re.compile(r"[ \t\r\n]*").match
_LINE = re.compile(r"[^\r\n]*").match
_QUOTE_RUN = {
    '"': re.compile(r'(?:[^"\\\r\n]|\\[^\r\n])*').match,
    "'": re.compile(r"(?:[^'\\\r\n]|\\[^\r\n])*").match,
}
_CONTAINER_TOKEN = re.compile(r"[\"'\[\](){}]").search
_ESCAPE_UNIT = re.compile(
    r"\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2}|[0-7]{1,3}|[^\r\n])"
)
_SIMPLE_ESCAPES = dict(zip("abfnrtv", "\a\b\f\n\r\t\v", strict=True))
_SIMPLE_ESCAPES.update({"\\": "\\", '"': '"', "'": "'", "/": "/"})


def _encoded_word(word):
    """Names/separators only: accept literal, percent, unicode and repr bytes."""
    pieces = []
    for char in word:
        choices = [re.escape(char)]
        for code in sorted({ord(char.lower()), ord(char.upper())}):
            choices.extend((f"%{code:02x}", rf"\\u{code:04x}", rf"\\x{code:02x}"))
        pieces.append("(?:" + "|".join(choices) + ")")
    return "".join(pieces)


def _name_set(names):
    variants = set(names)
    variants.update(name.replace("-", "_") for name in names)
    return "(?:" + "|".join(_encoded_word(n) for n in sorted(variants)) + ")"


_AUTH_NAMES = _name_set({"authorization", "proxy-authorization"})
_KEY_NAMES = _name_set(
    {
        "api-key",
        "apikey",
        "x-api-key",
        "x-goog-api-key",
        "anthropic-api-key",
        "openai-api-key",
    }
)
_AUTH_NAME = re.compile("(?i)" + _AUTH_NAMES).fullmatch
_KEY_NAME = re.compile("(?i)" + _KEY_NAMES).fullmatch
_ENCODED_SPACE = r"(?:[ \t]|%20|%09|\\[tnr]|\\u00(?:20|09|0a|0d))*"
_HEADER_SEPARATOR = "(?:" + _encoded_word(":") + "|" + _encoded_word("=") + ")"
_ENCODED_QUOTE = r"(?:%22|%27|\\+[\"'])"
_NAME_SUFFIX = (
    r"(?:(?:"
    + _ENCODED_QUOTE
    + r")?"
    + _ENCODED_SPACE
    + _HEADER_SEPARATOR
    + r"|"
    + _ENCODED_QUOTE
    + _ENCODED_SPACE
    + _encoded_word(",")
    + r")"
    + _ENCODED_SPACE
)
_AUTH_ANCHOR = _NAME_LEAD + _AUTH_NAMES + _NAME_SUFFIX
_KEY_ANCHOR = _NAME_LEAD + _KEY_NAMES + _NAME_SUFFIX
_BARE_ANCHOR = _LEAD + _encoded_word("bearer") + r"(?:[ \t]|%20|%09|\\[tnr])+"
_HEADER_NEXT = re.compile(
    "(?i)(?P<auth>"
    + _AUTH_ANCHOR
    + ")|(?P<key>"
    + _KEY_ANCHOR
    + ")|(?P<bare>"
    + _BARE_ANCHOR
    + r")|(?P<quote>[\"'])"
).search
_HEADER_INDEX = {"auth": 26, "bare": 27, "key": 28}
# Same name-only rule, without encoding alternatives when the carrier contains
# no encoding introducer. Dense ordinary JSON uses this inexpensive lexer.
_AUTH_PLAIN = r"(?:proxy[-_])?authorization"
_KEY_PLAIN = r"(?:x[-_]goog[-_]|x[-_]|anthropic[-_]|openai[-_])?api[-_]?key"
_PLAIN_SEPARATOR = r"[ \t]*[:=][ \t]*"
_HEADER_PLAIN_NEXT = re.compile(
    r"(?i)(?P<auth>(?<![A-Za-z0-9])"
    + _AUTH_PLAIN
    + _PLAIN_SEPARATOR
    + r")|(?P<key>(?<![A-Za-z0-9])"
    + _KEY_PLAIN
    + _PLAIN_SEPARATOR
    + r")|(?P<bare>\bbearer[ \t]+)|(?P<quote>[\"'])"
).search


def _hide_span(text, start, end, index, spans):
    if start < end and _COMPLETE_MARK(text, start, end) is None:
        spans.append((start, end, index))


def _quoted_end(text, start):
    end = _QUOTE_RUN[text[start]](text, start + 1).end()
    return end, end + (end < len(text) and text[end] == text[start])


def _decode_token(text):
    """Decode one string layer with monotone source boundaries, never eval.

    Only shrinking escapes are decoded. Boundaries translate every inner mask
    back into its enclosing token, so a missing inner quote cannot eat JSON.
    """
    if "\\" not in text:
        return text, None
    pieces, edges, cursor = [], [0], 0
    for match in _ESCAPE_UNIT.finditer(text):
        start, end = match.span()
        pieces.append(text[cursor:start])
        edges.extend(range(cursor + 1, start + 1))
        raw = match.group()[1:]
        if raw[0] in "uUx" and len(raw) > 1:
            code = int(raw[1:], 16)
            decoded = chr(code) if code <= 0x10FFFF else match.group()
        elif raw[0] in "01234567":
            decoded = chr(int(raw, 8))
        else:
            decoded = _SIMPLE_ESCAPES.get(raw, match.group())
        pieces.append(decoded)
        if len(decoded) == 1:
            edges.append(end)
        else:
            edges.extend(range(start + 1, end + 1))
        cursor = end
    pieces.append(text[cursor:])
    edges.extend(range(cursor + 1, len(text) + 1))
    return "".join(pieces), edges


def _hide_container(text, start, index, spans):
    """Locate string tokens only; brackets/scalars are never replaced."""
    stack = [{"[": "]", "(": ")", "{": "}"}[text[start]]]
    position = start + 1
    while stack:
        token = _CONTAINER_TOKEN(text, position)
        if token is None:
            return len(text)
        position = token.start()
        char = text[position]
        if char in "\"'":
            end, after = _quoted_end(text, position)
            _hide_span(text, position + 1, end, index, spans)
            position = after
        else:
            if char in "[({":
                stack.append({"[": "]", "(": ")", "{": "}"}[char])
            elif char == stack[-1]:
                stack.pop()
            position += 1
    return position


def _header_value(text, start, index, spans, *, structural=False):
    if structural:
        start = _SPACE(text, start).end()
    if start == len(text):
        return start
    quoted = start + (text[start] in "bB" and text[start + 1 : start + 2] in ("'", '"'))
    if text[quoted] in "\"'":
        end, after = _quoted_end(text, quoted)
        _hide_span(text, quoted + 1, end, index, spans)
        return after
    # Do not scan the remaining line before visiting a quoted/container value:
    # many values on one line must not rescan that tail (D1).
    if text.startswith("[hidden: ", start):
        end = _LINE(text, start).end()
        if _COMPLETE_MARK(text, start, end) is not None:
            return end
    if text[start] in "[({":
        first = _SPACE(text, start + 1).end()
        if structural or text[first : first + 1] in ("'", '"', "[", "(", "{"):
            return _hide_container(text, start, index, spans)
    if not structural:
        end = _LINE(text, start).end()
        _hide_span(text, start, end, index, spans)
        return end
    # A non-string JSON/repr scalar. There is no textual token to replace.
    return start


def _scan_header_text(text, spans, depth=0):
    """One name-only rule, applied within bounded string-token carriers.

    Cursor advancement prevents rescanning lists or raw values. String-layer
    recursion is capped; at the cap the whole enclosing content is hidden.
    Each level is linear and the cap is constant (D1).
    """
    position = 0
    query_fields = iter(_QUERY_FIELD.finditer(text))
    query_field = next(query_fields, None)
    search = _HEADER_NEXT if "%" in text or "\\" in text else _HEADER_PLAIN_NEXT
    while True:
        match = search(text, position)
        if match is None:
            return
        if match.lastgroup != "quote":
            while query_field is not None and query_field.end() <= match.start():
                query_field = next(query_fields, None)
            if query_field is not None and query_field.start() <= match.start():
                position = match.end()
                continue
            position = max(
                match.end(),
                _header_value(text, match.end(), _HEADER_INDEX[match.lastgroup], spans),
            )
            continue
        start = match.start()
        end, after = _quoted_end(text, start)
        raw = text[start + 1 : end]
        decoded, edges = _decode_token(raw)
        index = 26 if _AUTH_NAME(decoded) else 28 if _KEY_NAME(decoded) else None
        separator = _SPACE(text, after).end()
        before = start - 1
        if before >= 0 and text[before] in "bB":
            before -= 1
        while before >= 0 and text[before] in " \t\r\n":
            before -= 1
        pair = before >= 0 and text[before] in "[("
        if (
            index is not None
            and separator < len(text)
            and (text[separator] in ":=" or (pair and text[separator] == ","))
        ):
            position = max(
                after, _header_value(text, separator + 1, index, spans, structural=True)
            )
            continue
        nested = []
        if depth >= MAX_DEPTH:
            _hide_span(raw, 0, len(raw), 26, nested)
            edges = None
        else:
            _scan_header_text(decoded, nested, depth + 1)
        for low, high, family_index in nested:
            if edges is not None:
                low, high = edges[low], edges[high]
            spans.append((start + 1 + low, start + 1 + high, family_index))
        position = after


_URL_PATTERN = (
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqps?|https?)"
    r"://[^\s:/@]{1,128}:[^\s@/]{1,256}@"
)
# C1: the password runs to the last "@" before the host part (a "/",
# whitespace, a quote or a bracket ends the authority); the KI09 cap is lifted
# (D2).
_URL_PASSWORD = r"""[^\s@/]+(?:@[^\s/"'`<>]*)?@"""
_URL_MATCHER = _LEAD + _URL_PATTERN[2:].replace(r"[^\s@/]{1,256}@", _URL_PASSWORD)
_AWS_SECRET_PATTERN = r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+])"
_AWS_SECRET_MATCHER = _AWS_SECRET_PATTERN.replace("(?<![A-Za-z0-9/+])", _AWS_LEAD, 1)
_ALNUM = "[A-Za-z0-9]"
_URLSAFE = "[A-Za-z0-9_-]"
_QUERY_NAMES = (
    "code",
    "authorization_code",
    "id_token",
    "access_token",
    "refresh_token",
    "token",
    "client_secret",
    "api_key",
    "key",
    "password",
    "secret",
    "apikey",
    "sig",
    "signature",
)
_QUERY = (
    r"(?i)(?:[?&#]|&amp;|\\u0026|%26|%3f|%23)"
    # Reuse both header grammars: encoded aliases and the plain fast path's
    # mixed separators. Exact name matching keeps unrelated suffixes harmless.
    + "(?:"
    + "|".join(_encoded_word(name) for name in _QUERY_NAMES)
    + "|"
    + _AUTH_NAMES
    + "|"
    + _KEY_NAMES
    + "|"
    + _KEY_PLAIN
    + ")"
    + r"(?:=|%3d)(?P<v>"
    + _not_marker(r"""[&#\s"'\\]""")
    + r"(?:"
    + _MARK
    + r")?"
    + _QUERY_VALUE
    + r"{1,4096})"
)


# A monotone iterator lets the header scanner skip whole URL parameter names,
# including unknown names whose suffix happens to be a credential header name.
_QUERY_FIELD = re.compile(
    r"(?:[?&#]|&amp;|%26|%3[fF]|%23|\\u0026)"
    r"(?:(?!%3[dDfF]|%2[36]|\\u0026)[^?&#=\s\"'])+(?:=|%3[dD])"
)

_SLACK_TOKEN = r"\bxox[abposr]-[A-Za-z0-9-]{10,250}\b"
_STRIPE = r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{24,99}\b"
_OPENAI = r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,200}\b"
_ANTHROPIC = r"\bsk-ant-(?:api03|admin01)-[A-Za-z0-9_-]{80,120}\b"
_GATEWAY = r"\bsk-agentcc-[A-Za-z0-9_-]{16,200}\b"
_SK_GENERIC = r"\bsk-[A-Za-z0-9_-]{32,200}\b"
_BEARER = "(?i)" + _BARE_ANCHOR
_AUTH_BEARER = r"(?i)\bauthorization\s*:\s*bearer\s+[A-Za-z0-9._~+/=-]{16,4096}"


def _ki09(name, pattern, **options):
    """A word-boundary KI09 rule: the pattern verbatim, the matcher derived."""
    return Family(name, pattern, _KI09, matcher=_word_matcher(pattern), **options)


FAMILIES = (
    _ki09(
        "aws-access-key-id",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        literals=("AKIA", "ASIA"),
        run="[0-9A-Z]",
    ),
    _ki09(
        "github-token",
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b",
        literals=("ghp_", "gho_", "ghu_", "ghs_", "ghr_"),
        run=_ALNUM,
    ),
    _ki09(
        "github-fine-grained-pat",
        r"\bgithub_pat_[A-Za-z0-9_]{82}\b",
        literals=("github_pat_",),
        run="[A-Za-z0-9_]",
    ),
    _ki09(
        "gitlab-pat",
        r"\bglpat-[A-Za-z0-9_-]{20}\b",
        literals=("glpat-",),
        run=_URLSAFE,
    ),
    _ki09(
        "slack-token",
        _SLACK_TOKEN,
        literals=("xox",),
        run="[A-Za-z0-9-]",
    ),
    Family(
        "slack-webhook",
        r"https://hooks\.slack\.com/services/T[A-Z0-9]{8,12}/B[A-Z0-9]{8,12}/[A-Za-z0-9]{20,30}",
        _KI09,
        literals=("hooks.slack.com/services/",),
        run=_ALNUM,
    ),
    _ki09(
        "stripe-secret",
        _STRIPE,
        literals=("_live_", "_test_"),
        run=_ALNUM,
    ),
    _ki09(
        "openai-key",
        _OPENAI,
        literals=("sk-proj-", "sk-svcacct-", "sk-admin-"),
        run=_URLSAFE,
    ),
    _ki09(
        "openai-legacy-key",
        r"\bsk-[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20}\b",
        literals=("T3BlbkFJ",),
        run=_ALNUM,
    ),
    _ki09(
        "anthropic-key",
        _ANTHROPIC,
        literals=("sk-ant-",),
        run=_URLSAFE,
    ),
    _ki09(
        "google-api-key",
        r"\bAIza[0-9A-Za-z_-]{35}\b",
        literals=("AIza",),
        run=_URLSAFE,
    ),
    _ki09(
        "google-oauth-client-secret",
        r"\bGOCSPX-[A-Za-z0-9_-]{28}\b",
        literals=("GOCSPX-",),
        run=_URLSAFE,
    ),
    _ki09(
        "huggingface-token",
        r"\bhf_[A-Za-z0-9]{34}\b",
        literals=("hf_",),
        run=_ALNUM,
    ),
    _ki09(
        "groq-key",
        r"\bgsk_[A-Za-z0-9]{52}\b",
        literals=("gsk_",),
        run=_ALNUM,
    ),
    _ki09(
        "xai-key",
        r"\bxai-[A-Za-z0-9]{80}\b",
        literals=("xai-",),
        run=_ALNUM,
    ),
    _ki09(
        "perplexity-key",
        r"\bpplx-[A-Za-z0-9]{48}\b",
        literals=("pplx-",),
        run=_ALNUM,
    ),
    _ki09(
        "replicate-token",
        r"\br8_[A-Za-z0-9]{37}\b",
        literals=("r8_",),
        run=_ALNUM,
    ),
    _ki09(
        "npm-token",
        r"\bnpm_[A-Za-z0-9]{36}\b",
        literals=("npm_",),
        run=_ALNUM,
    ),
    _ki09(
        "pypi-token",
        r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}",
        literals=("pypi-AgEIcHlwaS5vcmc",),
        run=_URLSAFE,
    ),
    _ki09(
        "sendgrid-key",
        r"\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b",
        literals=("SG.",),
        run=_URLSAFE,
    ),
    Family(
        "private-key-block",
        _PEM_BEGIN_PATTERN + r"[\s\S]{16,16384}?" + "-----END " + _PEM_LABEL,
        _KI09,
        matcher=_PEM_BEGIN_PATTERN,
        literals=("PRIVATE KEY-----",),
        scan=_scan_private_keys,
    ),
    Family(
        "url-embedded-credentials",
        _URL_PATTERN,
        _KI09,
        matcher=_URL_MATCHER,
        literals=("://",),
        needs=("@",),
        locate=_url_password,
    ),
    _ki09(
        "authorization-bearer-header",
        _AUTH_BEARER,
        folded=("authorization",),
        run=_TOKEN,
        locate=_after(r"(?i)authorization\s*:\s*bearer\s+"),
    ),
    Family(
        "aws-secret-access-key", _AWS_SECRET_PATTERN, _D4, matcher=_AWS_SECRET_MATCHER
    ),
    Family(
        "futureagi-gateway-key",
        _GATEWAY,
        _EXTENSION,
        matcher=_word_matcher(_GATEWAY),
        literals=("sk-agentcc-",),
        run=_URLSAFE,
    ),
    Family(
        "sk-generic",
        _SK_GENERIC,
        _EXTENSION,
        matcher=_word_matcher(_SK_GENERIC),
        literals=("sk-",),
        run=_URLSAFE,
    ),
    Family(
        "authorization-header",
        "(?i)" + _AUTH_ANCHOR,
        _EXTENSION,
        folded=("authorization",),
    ),
    Family(
        "bearer-token",
        _BEARER,
        _EXTENSION,
        folded=("bearer",),
    ),
    Family(
        "api-key-header",
        "(?i)" + _KEY_ANCHOR,
        _EXTENSION,
        folded=("api" + "key", "api-key", "api_key"),
    ),
    Family(
        "query-credential",
        _QUERY,
        _EXTENSION,
        literals=(
            "key=",
            "token=",
            "sig=",
            "signature=",
            "key%3D",
            "key%3d",
            "token%3D",
            "token%3d",
            "sig%3D",
            "sig%3d",
            "signature%3D",
            "signature%3d",
        ),
        run=_QUERY_VALUE,
        locate=_named_group,
    ),
)

for _index, _family in enumerate(FAMILIES):
    _family.index = _index
del _index, _family

_AWS_KEY_INDEX = 0
_D4_INDEX = 23
_MARKERS = tuple(MARKER_FORMAT.format(family.name) for family in FAMILIES)
_LITERALS = tuple(
    (literal, family.index) for family in FAMILIES for literal in family._literals
)
_FOLDED = tuple(
    (literal, family.index) for family in FAMILIES for literal in family._folded
)
# Characters that (?i) matching can fold onto an ASCII letter while
# str.lower() does not produce that letter: with any of them present, every
# case-insensitive family runs (the prefilter must never hide a match).
_FOLDS_TO_ASCII = ("\u212a", "\u0130", "\u0131", "\u017f")

# D4: the secret is never part of a longer run of its class (c), sits within
# 32 characters of a matched key id (a) or right after a recognised name (a,
# b). Names: AWS_SECRET_ACCESS_KEY, aws_secret_access_key, SecretAccessKey,
# secretAccessKey and their case/separator variants. An escape sequence
# before the secret or the name counts as a class edge (SIF-1).
_AWS_SECRET = FAMILIES[_D4_INDEX].matcher
_AWS_SECRET_NAMED = re.compile(
    _NAME_LEAD
    + r"(?i:(?:aws[-_]?)?secret[-_]?access[-_]?key)"
    + r"""\\{0,3}["']?[ \t]{0,16}[:=][ \t]{0,16}\\{0,3}["']?"""
    + r"(?P<v>[A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])"
)
_AWS_CHARS = frozenset(string.ascii_letters + string.digits + "/+")
_AWS_WINDOW = 32
_AWS_SECRET_LENGTH = 40


def _scan_aws_secrets(text, key_ids, named, spans):
    length = len(text)
    found = set()
    reach = _AWS_WINDOW + _AWS_SECRET_LENGTH
    for key_start, key_end, _ in key_ids:
        low = max(0, key_start - reach)
        high = min(length, key_end + reach)
        for match in _AWS_SECRET.finditer(text, low, high):
            start, end = match.span()
            if start in found:
                continue
            # The window edges look like text edges to the expression.
            if (
                start > 0
                and text[start - 1] in _AWS_CHARS
                and not _escape_before(text, start)
            ):
                continue
            if end < length and text[end] in _AWS_CHARS:
                continue
            after = 0 <= start - key_end <= _AWS_WINDOW
            before = 0 <= key_start - end <= _AWS_WINDOW
            if after or before:
                found.add(start)
                spans.append((start, end, _D4_INDEX))
    if named:
        for match in _AWS_SECRET_NAMED.finditer(text):
            start, end = match.span("v")
            if start not in found:
                found.add(start)
                spans.append((start, end, _D4_INDEX))


def _spans(text):
    """Masked spans of text as merged [start, end, family index] lists."""
    active = set()
    for literal, index in _LITERALS:
        if index not in active and literal in text:
            active.add(index)
    if text.isascii() or not any(char in text for char in _FOLDS_TO_ASCII):
        folded = text.lower()
    else:
        folded = None
    for literal, index in _FOLDED:
        if index not in active and (folded is None or literal in folded):
            active.add(index)
    if any(char in text for char in "?&#%") or r"\u" "0026" in text:
        active.add(29)  # URL names are decoded by the query scanner, not prefiltered.
    named_aws = folded is None or ("secret" in folded and "access" in folded)
    # Every supported name contains the ASCII letter a. Without a literal or
    # encoded a, escaped provider-prefix noise cannot be a header. This coarse
    # prefilter is value-independent and avoids the large name matcher on it.
    encoded_a = folded is None or (
        folded is not None
        and (
            "a" in folded
            or any(
                code in folded
                for code in (
                    "%41",
                    "%61",
                    r"\u" "0041",
                    r"\u" "0061",
                    r"\x41",
                    r"\x61",
                    r"\u00000041",
                    r"\u00000061",
                    r"\101",
                    r"\141",
                )
            )
        )
    )
    headers = bool(active.intersection(_HEADER_INDEX.values())) or (
        encoded_a and ("%" in text or "\\" in text)
    )
    if not active and not named_aws and not headers:
        return ()
    spans = []
    if headers:
        _scan_header_text(text, spans)
    key_ids = ()
    for index in sorted(active):
        if index in _HEADER_INDEX.values():
            continue  # the shared string-token scanner already handled these
        family = FAMILIES[index]
        if family._needs and not all(literal in text for literal in family._needs):
            continue
        before = len(spans)
        (family._scan or _scan_regex)(family, text, spans)
        if index == _AWS_KEY_INDEX:
            key_ids = spans[before:]
    if key_ids or named_aws:
        _scan_aws_secrets(text, key_ids, named_aws, spans)
    if not spans:
        return ()
    spans.sort()
    merged = []
    for start, end, index in spans:
        if merged and start < merged[-1][1]:
            last = merged[-1]
            if end > last[1]:
                last[1] = end
            if index < last[2]:
                last[2] = index
        else:
            merged.append([start, end, index])
    return merged


def _render(text, merged):
    pieces = []
    cursor = 0
    for start, end, index in merged:
        pieces.append(text[cursor:start])
        pieces.append(_MARKERS[index])
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


# A lexer, not a value-shaped expression: each alternative has one introducer
# and consumes a single escape. Escaped bytes/code points are interpreted lazily.
_ENCODING = re.compile(
    r"%[0-9a-fA-F]{2}|\\(?:u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|x[0-9a-fA-F]{2})"
    r"|&#(?:[xX][0-9a-fA-F]+|[0-9]+);|&[A-Za-z][A-Za-z0-9]{0,31};"
)
_RUN_BREAK = frozenset("\"'<>()[{},]=:?&#" + "\\")
_ENCODING_WORK_LIMIT = 32 << 20


def _escape_options(text, start, *, short=False):
    """Decoded single-layer alternatives, including both meanings of x bytes.

    Raw characters are handled by the caller. Keeping the raw and decoded
    edges separate avoids the ambiguous concatenated-regex backtracking trap.
    """
    match = _ENCODING.match(text, start)
    if match is None:
        if short and text[start : start + 1] == "\\" and start + 1 < len(text):
            char = _SIMPLE_ESCAPES.get(text[start + 1])
            if char is not None:
                return [(char, start + 2)]
        if short and text[start : start + 1] == "+":
            return [(" ", start + 1)]
        return []
    raw, end = match.group(), match.end()
    if len(raw) > 4096:
        raise ValueError("escape budget")
    if raw.startswith("&"):
        import html

        decoded = html.unescape(raw)
        return [(decoded, end)] if decoded != raw else []
    if raw[0] == "%" or raw.startswith(r"\x"):
        width = 3 if raw[0] == "%" else 4
        prefix = "%" if width == 3 else r"\x"
        number = int(raw[-2:], 16)
        options = [
            (
                chr(number)
                if width == 4
                else bytes((number,)).decode("utf-8", "surrogateescape"),
                end,
            )
        ]
        size = (
            2
            if 0xC2 <= number <= 0xDF
            else 3
            if 0xE0 <= number <= 0xEF
            else 4
            if 0xF0 <= number <= 0xF4
            else 1
        )
        if size > 1:
            data = [number]
            stop = end
            for _ in range(size - 1):
                unit = text[stop : stop + width]
                if not unit.startswith(prefix) or len(unit) != width:
                    break
                try:
                    byte = int(unit[-2:], 16)
                except ValueError:
                    break
                if not 0x80 <= byte <= 0xBF:
                    break
                data.append(byte)
                stop += width
            if len(data) == size:
                try:
                    options.append((bytes(data).decode("utf-8"), stop))
                except UnicodeError:
                    pass
        return options
    code = int(raw[2:], 16)
    decoded = chr(code)
    options = [(decoded, end)]
    if raw[1] == "u" and 0xD800 <= code <= 0xDBFF:
        following = text[end : end + 6]
        if re.fullmatch(r"\\u[dD][c-fC-F][0-9a-fA-F]{2}", following):
            low = int(following[2:], 16)
            options.append(
                (chr(0x10000 + ((code - 0xD800) << 10) + low - 0xDC00), end + 6)
            )
    return options


def _normalized_layer(text):
    """Decode one shrinking layer, with source boundaries for every character."""
    pieces, edges, cursor = [], [0], 0
    for match in _ENCODING.finditer(text):
        start = match.start()
        if start < cursor:
            continue
        options = _escape_options(text, start)
        if not options:
            continue
        decoded, end = options[-1]
        pieces.append(text[cursor:start])
        edges.extend(range(cursor + 1, start + 1))
        pieces.append(decoded)
        edges.extend([end] * len(decoded))
        cursor = end
    if not cursor:
        return text, None
    pieces.append(text[cursor:])
    edges.extend(range(cursor + 1, len(text) + 1))
    return "".join(pieces), edges


def _merge_spans(spans):
    spans.sort(key=lambda span: (span[0], span[1], span[2]))
    merged = []
    for start, end, index in spans:
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
            merged[-1][2] = min(index, merged[-1][2])
        else:
            merged.append([start, end, index])
    return merged


def _all_spans(text):
    """Raw and decoded detections, projected back into the original carrier.

    Encoded provider runs are hidden as a unit. Header/query rules keep their
    value-only boundaries, including enclosing JSON syntax and URL delimiters.
    Decoding is bounded and never substitutes decoded content into the output.
    """
    if len(text) > DECODED_BODY_LIMIT:
        raise ValueError("text budget")
    spans = list(_spans(text))
    current, edges, work = text, None, 0
    for _ in range(MAX_DEPTH):
        if _ENCODING.search(current) is None:
            break
        work += len(current)
        if work > _ENCODING_WORK_LIMIT:
            raise ValueError("encoding budget")
        decoded, mapping = _normalized_layer(current)
        if mapping is None:
            break
        edges = mapping if edges is None else [edges[i] for i in mapping]
        run_start = run_end = -1
        for start, end, index in _spans(decoded):
            low, high = edges[start], edges[end]
            if (
                index not in (22, 26, 27, 28, 29)
                and text[low:high] == decoded[start:end]
            ):
                continue
            if index not in (21, 22, 26, 27, 28, 29):
                # Spans are ordered. Cache the expanded run so many matches in
                # one delimiter-free run cannot repeatedly walk the same tail.
                if run_start <= start and end <= run_end:
                    start, end = run_start, run_end
                else:
                    while (
                        start > 0
                        and not decoded[start - 1].isspace()
                        and decoded[start - 1] not in _RUN_BREAK
                    ):
                        start -= 1
                    while (
                        end < len(decoded)
                        and not decoded[end].isspace()
                        and decoded[end] not in _RUN_BREAK
                    ):
                        end += 1
                    run_start, run_end = start, end
                low, high = edges[start], edges[end]
            spans.append((low, high, index))
        current = decoded
    else:
        if _ENCODING.search(current):
            raise ValueError("encoding depth")
    return _merge_spans(spans)


def scrub_text(s):
    """s with every credential replaced by its family marker.

    Returns s itself when nothing matches (and any non-str unchanged). Never
    raises: on an internal error the result is "[hidden: scrub-failed]".
    """
    if not isinstance(s, str):
        return s
    try:
        text = s if type(s) is str else str.__str__(s)
        merged = _all_spans(text)
        if not merged:
            return s
        return _render(text, merged)
    except Exception:
        return _SCRUB_FAILED


_BYTES_LIKE = (bytes, bytearray, memoryview)


def _same_kind(original, data):
    """data as the kind of bytes-like original (SIF-9)."""
    if isinstance(original, bytearray):
        return bytearray(data)
    if isinstance(original, memoryview):
        return memoryview(data)
    return data


def _decode_bytes(raw, charset=None):
    """Validate an explicit charset/BOM and retain its exact wire encoding.

    Legacy non-BOM bytes without a declaration keep UTF-8 surrogateescape.
    BOM/declaration errors, unknown/stateful codecs and ambiguous NUL-bearing
    byte bodies fail closed. A UTF-32 BOM is checked before the UTF-16 prefix.
    """
    import codecs

    if len(raw) > DECODED_BODY_LIMIT:
        raise ValueError("byte budget")
    declared = None if charset is None else codecs.lookup(charset).name
    allowed = {
        "utf-8",
        "utf-8-sig",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
        "utf-32",
        "utf-32-le",
        "utf-32-be",
        "ascii",
        "iso8859-1",
        "cp1252",
    }
    if declared is not None and declared not in allowed:
        raise ValueError("unsupported charset")
    codec, prefix = declared or "utf-8", b""
    for bom, encoding, generic in (
        (codecs.BOM_UTF32_LE, "utf-32-le", "utf-32"),
        (codecs.BOM_UTF32_BE, "utf-32-be", "utf-32"),
        (codecs.BOM_UTF16_LE, "utf-16-le", "utf-16"),
        (codecs.BOM_UTF16_BE, "utf-16-be", "utf-16"),
        (codecs.BOM_UTF8, "utf-8", "utf-8-sig"),
    ):
        if raw.startswith(bom):
            if declared not in (None, encoding, generic):
                raise ValueError("charset contradicts BOM")
            codec, prefix = encoding, bom
            break
    if not prefix and codec in ("utf-16", "utf-32"):
        raise ValueError("byte order requires BOM")
    if codec == "utf-8-sig":
        codec = "utf-8"
    errors = "surrogateescape" if declared is None and not prefix else "strict"
    if b"\x00" in raw and not codec.startswith(("utf-16", "utf-32")):
        raise ValueError("ambiguous byte encoding")
    text = raw[len(prefix) :].decode(codec, errors)
    if prefix + text.encode(codec, errors) != raw:
        raise ValueError("non-roundtripping encoding")
    return text, codec, prefix, errors


def _scrub_bytes_checked(b, charset=None):
    raw = b if type(b) is bytes else bytes(b)
    text, codec, prefix, errors = _decode_bytes(raw, charset)
    merged = _all_spans(text)
    if not merged:
        return b
    return _same_kind(b, prefix + _render(text, merged).encode(codec, errors))


def scrub_bytes(b, *, charset=None):
    """Scrub bytes-like data, preserving its kind and validated wire encoding.

    UTF-16/32 BOMs take priority over guesses, never over a contradictory
    explicit charset. Unsafe decoding returns a categorical UTF-8 marker.
    Non-text inputs pass unchanged; non-BOM undeclared bytes retain the
    lossless surrogateescape compatibility path. No exception escapes.
    """
    if not isinstance(b, _BYTES_LIKE):
        return b
    try:
        return _scrub_bytes_checked(b, charset)
    except Exception:
        return _same_kind(b, _SCRUB_FAILED_BYTES)


def count_matches(text):
    """{family name: count} for text (str or bytes-like); counts only (PL4)."""
    try:
        if isinstance(text, _BYTES_LIKE):
            text = _decode_bytes(bytes(text))[0]
        if not isinstance(text, str):
            return {}
        if type(text) is not str:
            text = str.__str__(text)
        counts = {}
        for _start, _end, index in _all_spans(text):
            name = FAMILIES[index].name
            counts[name] = counts.get(name, 0) + 1
        return counts
    except Exception:
        return {"scrub-failed": 1}


# --- structured values --------------------------------------------------------

# Recognised secret names, compared lower-cased with "_" read as "-".
_RECOGNISED_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "api-key",
        "apikey",
        "x-api-key",
        "x-goog-api-key",
        "secret-key",
        "client-secret",
        "access-token",
        "refresh-token",
        "password",
        "aws-secret-access-key",
        "secretaccesskey",
    }
)
# SIF-6: while the gateway's rule cannot be imported, a name containing any of
# these, or ending in "key", reads as a credential (the fail-closed side).
_FALLBACK_WORDS = (
    "auth",
    "bearer",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "credential",
    "cookie",
    "signature",
    "token",
    "api-key",
    "apikey",
    "private-key",
    "access-key",
    "portkey",
)
_RULE_RETRY_SECONDS = 30.0
_header_rule = None
_header_rule_retry_at = 0.0


def _credential_header_rule():
    """agentcc.services.credential_headers.is_credential_header, imported
    lazily. None while it cannot be imported: the failure is retried after
    _RULE_RETRY_SECONDS and never stored as the rule (SIF-6)."""
    global _header_rule, _header_rule_retry_at
    rule = _header_rule
    if rule is not None:
        return rule
    now = time.monotonic()
    if now < _header_rule_retry_at:
        return None
    try:
        from agentcc.services.credential_headers import is_credential_header
    except Exception:
        _header_rule_retry_at = now + _RULE_RETRY_SECONDS
        return None
    _header_rule = is_credential_header
    return is_credential_header


def _normalized_name(name):
    return name.lower().replace("_", "-")


def _fallback_names_a_credential(name):
    normalized = _normalized_name(name)
    return (
        normalized in _RECOGNISED_NAMES
        or any(word in normalized for word in _FALLBACK_WORDS)
        or normalized.endswith("key")
    )


def _names_a_credential(name):
    if _normalized_name(name) in _RECOGNISED_NAMES:
        return True
    rule = _credential_header_rule()
    if rule is None:
        return _fallback_names_a_credential(name)
    try:
        return bool(rule(name))
    except Exception:
        return True  # a failing rule reads as a credential (fail closed)


# Holds answers given by the gateway's rule only: _is_credential_name never
# consults it while the rule is unavailable.
_cached_names_a_credential = functools.lru_cache(maxsize=4096)(_names_a_credential)


def _is_credential_name(key):
    try:
        if isinstance(key, _BYTES_LIKE):
            key = bytes(key).decode("utf-8", "replace")
        elif isinstance(key, str):
            key = key if type(key) is str else str.__str__(key)
        else:
            return False
    except Exception:
        return True
    if _credential_header_rule() is None:
        return _fallback_names_a_credential(key)
    if len(key) > 128:
        return _names_a_credential(key)
    return _cached_names_a_credential(key)


def _non_empty_text(value):
    if isinstance(value, str):
        return str.__len__(value) > 0
    if isinstance(value, bytes):
        return bytes.__len__(value) > 0
    return False


def _hidden_text(value):
    """The credential-field marker for a non-empty str (kept if it is one)."""
    if str.__len__(value) == 0 or _COMPLETE_MARK(value) is not None:
        return value
    return _CREDENTIAL_FIELD


def _hidden_bytes(value):
    data = value if type(value) is bytes else bytes(value)
    if not data or data == _CREDENTIAL_FIELD_BYTES:
        return value
    return _same_kind(value, _CREDENTIAL_FIELD_BYTES)


def _suffixed(key, count):
    if isinstance(key, bytes):
        return key + b"#%d" % count
    return f"{key}#{count}"


def _rebuilt(entries):
    """A dict from (key, safe key, safe item) entries. A scrubbed key never
    replaces another entry: it takes a "#n" suffix instead (C5)."""
    used = {safe_key for key, safe_key, _ in entries if safe_key is key}
    result = {}
    for key, safe_key, safe_item in entries:
        if safe_key is not key:
            name, count = safe_key, 2
            while name in used:
                name = _suffixed(safe_key, count)
                count += 1
            used.add(name)
            safe_key = name
        result[safe_key] = safe_item
    return result


_SEQUENCES = (list, tuple, set, frozenset)
_CONTAINERS = (dict, *_SEQUENCES)


class _ValueScrubber:
    """One deep scrub: cycle-safe (on-path ids), shared objects once (memo).

    hide marks a value under a credential name: every non-empty str or
    bytes-like leaf of it, through lists, tuples and sets, becomes the
    credential-field marker (SIF-4, C10). A mapping under a credential name is
    walked normally: its own keys decide.
    """

    __slots__ = ("mask", "path", "memo", "charset")

    def __init__(self, mask, charset=None):
        self.mask = mask
        self.charset = charset
        self.path = set()
        self.memo = {}

    def scrub(self, value, depth, hide=False):
        if isinstance(value, str):
            if hide:
                return _hidden_text(value)
            text = value if type(value) is str else str.__str__(value)
            if len(text) > DECODED_BODY_LIMIT:
                return _SCRUB_FAILED
            rendered = scrub_text(value)
            if text.lstrip().startswith(("{", "[")):
                if depth > MAX_DEPTH:
                    return _DEPTH
                import json

                duplicate = False

                def object_pairs(pairs):
                    nonlocal duplicate
                    result = dict(pairs)
                    # The hook visits every object, including nested objects.
                    # Discarded members must never survive via source identity.
                    duplicate = duplicate or len(result) != len(pairs)
                    return result

                try:
                    parsed = json.loads(rendered, object_pairs_hook=object_pairs)
                except ValueError:
                    parsed = None
                if duplicate:
                    return _SCRUB_FAILED
                if isinstance(parsed, (dict, list)):
                    safe = self.scrub(parsed, depth + 1)
                    if safe is not parsed:
                        return json.dumps(safe, ensure_ascii=True)
            return rendered
        if isinstance(value, _BYTES_LIKE):
            return (
                _hidden_bytes(value)
                if hide
                else scrub_bytes(value, charset=self.charset)
            )
        if not isinstance(value, _CONTAINERS):
            return value
        if hide and isinstance(value, dict):
            hide = False
        key = id(value)
        if key in self.path:
            return _CYCLE
        if depth > MAX_DEPTH:
            return _DEPTH
        cached = self.memo.get((key, hide))
        if cached is not None:
            return cached[1]
        self.path.add(key)
        try:
            if isinstance(value, dict):
                result = self.mapping(value, depth)
            else:
                result = self.items(value, depth, hide)
        finally:
            self.path.discard(key)
        self.memo[(key, hide)] = (value, result)
        return result

    def mapping(self, value, depth):
        changed = False
        entries = []
        for key, item in dict.items(value):
            if isinstance(key, str):
                safe_key = scrub_text(key)
            elif isinstance(key, bytes):
                safe_key = scrub_bytes(key)
            else:
                safe_key = key
            hide = self.mask and _is_credential_name(key)
            safe_item = self.scrub(item, depth + 1, hide)
            if safe_key is not key or safe_item is not item:
                changed = True
            entries.append((key, safe_key, safe_item))
        return _rebuilt(entries) if changed else value

    def items(self, value, depth, hide):
        if isinstance(value, list):
            kind = list
        elif isinstance(value, tuple):
            kind = tuple
        elif isinstance(value, frozenset):
            kind = frozenset
        else:
            kind = set
        originals = list(value)
        if hide:
            results = [self.scrub(item, depth + 1, True) for item in originals]
        elif (
            self.mask
            and kind in (list, tuple)
            and len(originals) == 2
            and _is_credential_name(originals[0])
        ):
            # A (name, value) header pair: httpx and requests header lists,
            # ASGI scope headers.
            results = [
                self.scrub(originals[0], depth + 1),
                self.scrub(originals[1], depth + 1, True),
            ]
        else:
            results = [self.scrub(item, depth + 1) for item in originals]
        if all(new is old for new, old in zip(results, originals, strict=False)):
            return value
        return kind(results)


def scrub_value(value, *, mask_credential_keys=True, charset=None):
    """A deep-scrubbed copy of value; value itself when nothing changes.

    str and bytes-like values go through scrub_text/scrub_bytes (charset is
    forwarded to byte leaves). JSON-object/array strings are also parsed and
    scrubbed structurally, then re-serialized as strings when changed. Duplicate
    members at any object depth fail closed to the categorical failure marker.
    Existing text markers and unchanged unique-member JSON formatting are retained. dict (keys
    too, as text), list, tuple, set and frozenset are rebuilt only where
    something changed, and scrubbed keys never merge (a "#n" suffix keeps
    them apart); any other value passes unchanged. With mask_credential_keys,
    every non-empty str/bytes leaf under a credential-named key (the gateway's
    credential-header rule or a recognised secret name), through lists,
    tuples and sets, and the value of a (name, value) pair whose name is a
    credential name, becomes "[hidden: credential-field]". Cycle-safe;
    containers deeper than MAX_DEPTH become "[hidden: depth]". The input is
    never mutated.
    """
    try:
        return _ValueScrubber(mask_credential_keys, charset).scrub(value, 0)
    except Exception:
        return _SCRUB_FAILED


_INTERNAL_FIELDS = ("_record", "_from_structlog")
_SAFE_EVENT_FIELDS = ("level", "logger", "timestamp")


def scrub_credentials_event(logger, method_name, event_dict):
    """structlog processor: every field but the internal ones, deep-scrubbed
    with credential-named keys masked. Returns event_dict itself when nothing
    changes; _record and _from_structlog always keep their identity."""
    try:
        fields = {
            key: value
            for key, value in event_dict.items()
            if key not in _INTERNAL_FIELDS
        }
        scrubbed = _ValueScrubber(True).mapping(fields, 0)
        if scrubbed is fields:
            return event_dict
        for key in _INTERNAL_FIELDS:
            if key in event_dict:
                scrubbed[key] = event_dict[key]
        return scrubbed
    except Exception:
        failed = {"event": _SCRUB_FAILED}
        for key in _SAFE_EVENT_FIELDS:
            value = event_dict.get(key) if isinstance(event_dict, dict) else None
            if isinstance(value, str):
                failed[key] = scrub_text(value)
        for key in _INTERNAL_FIELDS:
            if isinstance(event_dict, dict) and key in event_dict:
                failed[key] = event_dict[key]
        return failed


_SENTRY_SCHEMA_FIELDS = (
    "event_id",
    "timestamp",
    "level",
    "platform",
    "logger",
    "release",
    "environment",
)


def _scrub_sentry_request(request):
    changes = {}
    cookies = request.get("cookies")
    if isinstance(cookies, dict):
        masked = {
            name: (_CREDENTIAL_FIELD if _non_empty_text(value) else value)
            for name, value in cookies.items()
        }
        if any(masked[name] is not cookies[name] for name in cookies):
            changes["cookies"] = masked
    query = request.get("query_string")
    if isinstance(query, str) and query and query[0] not in "?&":
        # Sentry keeps the query without its "?": scrub it as a query.
        safe = scrub_text("?" + query)
        if safe != "?" + query:
            changes["query_string"] = safe[1:] if safe.startswith("?") else safe
    if not changes:
        return request
    return {**request, **changes}


def scrub_sentry_event(event, hint=None):
    """Deep-scrub a Sentry event (request headers, data, cookies and query
    string, exception values, frame vars, breadcrumbs, extra, contexts, tags,
    message, logentry). Returns the event, never None; the input is not
    mutated."""
    if not isinstance(event, dict):
        return event
    try:
        result = scrub_value(event, mask_credential_keys=True)
        if not isinstance(result, dict):
            raise TypeError("event scrub failed")
        request = result.get("request")
        if isinstance(request, dict):
            safe_request = _scrub_sentry_request(request)
            if safe_request is not request:
                result = dict(result)
                result["request"] = safe_request
        return result
    except Exception:
        failed = {"message": _SCRUB_FAILED}
        for key in _SENTRY_SCHEMA_FIELDS:
            value = event.get(key)
            if isinstance(value, str):
                failed[key] = scrub_text(value)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                failed[key] = value
        return failed


def redact_upstream_body(text, limit=UPSTREAM_BODY_LIMIT):
    """An upstream body safe to quote: scrub_text, then at most limit
    characters plus " [truncated]". None and non-str give "".

    The scrub runs on the whole body before the cut (SIF-5), so a key that
    straddles the limit is masked whole; the cut can only shorten a marker.
    """
    if not isinstance(text, str):
        return ""
    text = text if type(text) is str else str.__str__(text)
    safe = scrub_text(text)
    limit = max(0, int(limit))
    if len(safe) > limit:
        return safe[:limit] + _TRUNCATED
    return safe


# --- known values ----------------------------------------------------------------

# r4 (ONEKEY-C2-SCRUB-R4-KNOWNVALUES): values the caller already holds, such as
# the platform's own keys, which have no distinctive format (the KI09
# exclusions) and so are never pattern-matched by the families above.
_KNOWN_VALUE = MARKER_FORMAT.format("known-value")
_KNOWN_VALUE_MIN_LENGTH = 8
_RE_BACKSLASH = re.escape("\\")
# Characters with a short backslash escape: JSON's quote, backslash and
# slash, and the Python/JS apostrophe.
_SHORT_ESCAPED = frozenset("\"\\/'")


def _hex_digits(number, width=1):
    """number in hex, at least width digits, each letter digit in either case."""
    return "".join(
        "[" + digit + digit.upper() + "]" if digit in "abcdef" else digit
        for digit in format(number, "x").rjust(width, "0")
    )


def _utf8(char):
    try:
        return char.encode("utf-8", "surrogateescape")
    except UnicodeError:
        return char.encode("utf-8", "surrogatepass")


@functools.lru_cache(maxsize=1024)
def _char_forms(char):
    """A one-character candidate prefilter, NEVER concatenated with another.

    Multi-codepoint named entities containing the character are candidates
    too; the non-backtracking lexer/NFA validates their complete decoded text.
    """
    code = ord(char)
    data = _utf8(char)
    forms = [re.escape(char), "".join("%" + _hex_digits(byte, 2) for byte in data)]
    if char == " ":
        forms.append(re.escape("+"))
    if code < 0x10000:
        forms.append(_RE_BACKSLASH + "u" + _hex_digits(code, 4))
    else:
        high = 0xD800 + ((code - 0x10000) >> 10)
        low = 0xDC00 + ((code - 0x10000) & 0x3FF)
        forms.append(
            _RE_BACKSLASH
            + "u"
            + _hex_digits(high, 4)
            + _RE_BACKSLASH
            + "u"
            + _hex_digits(low, 4)
        )
        forms.append(_RE_BACKSLASH + "U" + _hex_digits(code, 8))
    if char in _SHORT_ESCAPED:
        forms.append(_RE_BACKSLASH + re.escape(char))
    if code < 0x100:
        forms.append(_RE_BACKSLASH + "x" + _hex_digits(code, 2))
    if code >= 0x80:
        forms.append("".join(_RE_BACKSLASH + "x" + _hex_digits(b, 2) for b in data))
    forms.append("&#0*" + str(code) + ";")
    forms.append("&#[xX]0*" + _hex_digits(code) + ";")
    from html.entities import html5

    forms.extend(
        "&" + name
        for name, decoded in html5.items()
        if name.endswith(";") and char in decoded
    )
    return "(?:" + "|".join(forms) + ")"


def _known_text(value):
    """A known value or marker as text (bytes-like: UTF-8, surrogateescape)."""
    if isinstance(value, str):
        return value if type(value) is str else str.__str__(value)
    if isinstance(value, _BYTES_LIKE):
        return bytes(value).decode("utf-8", "surrogateescape")
    raise TypeError("a known value is str or bytes-like")


_KNOWN_WORK_LIMIT = 8_000_000
_KNOWN_VALUE_LIMIT = 64
_KNOWN_CHARACTER_LIMIT = 65536


def _known_spans(text, values, min_length=_KNOWN_VALUE_MIN_LENGTH):
    """Bounded, non-backtracking NFA; union ALL accepted source spans.

    At a source offset, a prefix length needs only its earliest start: later
    starts with the same prefix/offset have identical futures and are covered
    by the earlier span. Raw and encoded edges coexist (including ambiguous
    backslashes), and terminal edges all contribute their complete spans.
    No value is compiled into a concatenation of ambiguous alternatives.
    The work, value-count, value-size and escape-width caps are fixed; on
    exhaustion the caller emits a categorical marker, never a partial result.
    """
    from heapq import heappop, heappush

    unique, characters = {}, 0
    for count, item in enumerate(values):
        if count >= 4096:
            raise ValueError("known iterable budget")
        if item is None:
            continue
        value = _known_text(item)
        if len(value) < min_length or value in unique:
            continue
        characters += len(value)
        if len(unique) >= _KNOWN_VALUE_LIMIT or characters > _KNOWN_CHARACTER_LIMIT:
            raise ValueError("known value budget")
        unique[value] = None
    work = characters
    if work > _KNOWN_WORK_LIMIT:
        raise ValueError("known scan budget")
    ends = {}
    encoded = any(char in text for char in "%\\&+")
    for value in unique:
        # A necessary one-character check, never a value-shaped concatenation;
        # _char_forms includes every semicolon-terminated named HTML alias.
        if encoded and re.search(_char_forms(value[-1]), text) is None:
            continue
        if not encoded:
            # Literal-only search has no regex ambiguity and includes overlaps.
            position = text.find(value)
            while position >= 0:
                end = position + len(value)
                ends[position] = max(ends.get(position, 0), end)
                work += 1
                if work > _KNOWN_WORK_LIMIT:
                    raise ValueError("known scan budget")
                position = text.find(value, position + 1)
            continue
        # A SINGLE-character alternation only locates candidate starts. The
        # lexer/NFA, not its first accepted alternative, determines match ends.
        search = re.compile(_char_forms(value[0])).search
        root = search(text)
        pending, positions = {}, []
        while root is not None or positions:
            position = min(
                root.start() if root is not None else len(text),
                positions[0] if positions else len(text),
            )
            states = pending.pop(position, {})
            if positions and positions[0] == position:
                heappop(positions)
            if root is not None and root.start() == position:
                states[0] = position
                root = search(text, position + 1)
            options = [(text[position], position + 1)]
            if text[position] in "%\\&+":
                options.extend(_escape_options(text, position, short=True))
            for prefix, start in states.items():
                for decoded, end in options:
                    work += 1
                    if work > _KNOWN_WORK_LIMIT:
                        raise ValueError("known scan budget")
                    if not value.startswith(decoded, prefix):
                        continue
                    following = prefix + len(decoded)
                    if following == len(value):
                        ends[start] = max(ends.get(start, 0), end)
                    elif end < len(text):
                        future = pending.get(end)
                        if future is None:
                            future = pending[end] = {}
                            heappush(positions, end)
                        future[following] = min(future.get(following, start), start)
    # An offset sweep, not a sort of potentially quadratic overlapping matches.
    merged = []
    if ends:
        for start in range(len(text)):
            end = ends.get(start)
            if end is None:
                continue
            if merged and start < merged[-1][1]:
                merged[-1][1] = max(end, merged[-1][1])
            else:
                merged.append([start, end])
    return merged


def redact_known_values(data, values, *, marker=None, min_length=8, charset=None):
    """data with every occurrence of each known value replaced by marker.

    data is str or bytes-like. BOMs and optional charset declarations use the
    same validated wire-encoding rules as scrub_bytes; absent both, legacy
    UTF-8 surrogateescape is retained. The result has data's type (str for a str
    subclass, bytes for a bytes subclass), and data itself comes back when
    nothing matches. Any other data, dict and list included, is returned
    unchanged: structured values are not walked here.

    values is an iterable of str or bytes-like values; a single str or
    bytes-like value counts as one value (never as its characters), and None
    entries are skipped. min_length is a positive integer, default 8; callers
    may lower it for values they know are secrets. Shorter values are ignored.
    An occurrence is found in any per-character mix of: the raw character;
    percent escapes of its UTF-8 bytes in either hex case ("+" for a space);
    JSON backslash-u escapes in either hex case (a surrogate pair above the
    BMP) and the backslash escapes of a quote, apostrophe, backslash or
    slash; Python/JS backslash-x escapes of the code point or of its UTF-8
    bytes, and backslash-U above the BMP; HTML decimal and hex entities
    (either case, leading zeros allowed) and HTML named entities. Overlapping
    occurrences and alternative terminal spellings are masked as one full span.

    marker (str or bytes-like) defaults to "[hidden: known-value]". The input
    cap is DECODED_BODY_LIMIT, shared with scrub_text. A per-call deduplicated
    value set and bounded non-backtracking NFA replace the r4 concatenated
    regex. Fixed work/escape/value budgets fail closed instead of returning
    partial output. Never raises: on any error or budget exhaustion the result
    is "[hidden: scrub-failed]" in data's type.
    """
    if isinstance(data, str):
        failed = _SCRUB_FAILED
    elif isinstance(data, _BYTES_LIKE):
        failed = _same_kind(data, _SCRUB_FAILED_BYTES)
    else:
        return data
    try:
        if type(min_length) is not int or min_length < 1:
            raise ValueError("invalid minimum length")
        if isinstance(data, str):
            text = data if type(data) is str else str.__str__(data)
        else:
            raw = data if type(data) is bytes else bytes(data)
            text, codec, prefix, errors = _decode_bytes(raw, charset)
        if len(text) > DECODED_BODY_LIMIT:
            raise ValueError("text budget")
        if values is None:
            return data
        if isinstance(values, (str, *_BYTES_LIKE)):
            values = (values,)
        safe_marker = _KNOWN_VALUE if marker is None else _known_text(marker)
        merged = _known_spans(text, values, min_length)
        if not merged:
            return data
        pieces = []
        cursor = 0
        for start, end in merged:
            pieces.append(text[cursor:start])
            pieces.append(safe_marker)
            cursor = end
        pieces.append(text[cursor:])
        safe = "".join(pieces)
        if isinstance(data, str):
            return safe
        return _same_kind(data, prefix + safe.encode(codec, errors))
    except Exception:
        return failed


# --- error responses -----------------------------------------------------------


def _coroutine_helpers():
    try:
        from asgiref.sync import iscoroutinefunction, markcoroutinefunction
    except ImportError:
        import inspect

        return inspect.iscoroutinefunction, getattr(
            inspect, "markcoroutinefunction", None
        )
    return iscoroutinefunction, markcoroutinefunction


def _scrubbed_media_type(content_type):
    media = str(content_type or "").split(";", 1)[0].strip().lower()
    return media.startswith("text/") or media.endswith(
        ("/json", "+json", "/xml", "+xml")
    )


# Validators computed over the body: stale (and derived from the unscrubbed
# body) once it is rewritten (C8).
_BODY_VALIDATORS = ("ETag", "Content-MD5", "Digest", "Content-Digest", "Repr-Digest")
_FAILED_CONTENT_TYPE = "text/plain; charset=utf-8"
_GZIP_WBITS = 31


class _Undecodable(Exception):
    """An encoded body that cannot be decoded within DECODED_BODY_LIMIT."""


def _drop_header(headers, name):
    try:
        if name in headers:
            del headers[name]
    except Exception:
        pass


def _set_header(headers, name, value):
    try:
        headers[name] = value
    except Exception:
        pass


def _content_codings(value):
    """Content-Encoding codings in the order applied, identity left out."""
    if not value:
        return []
    codings = [part.strip().lower() for part in str(value).split(",")]
    return [coding for coding in codings if coding and coding != "identity"]


def _inflate(data, wbits):
    import zlib

    pieces = []
    total = 0
    while True:
        inflater = zlib.decompressobj(wbits)
        piece = inflater.decompress(data, DECODED_BODY_LIMIT + 1 - total)
        total += len(piece)
        pieces.append(piece)
        if total > DECODED_BODY_LIMIT or inflater.unconsumed_tail or not inflater.eof:
            raise _Undecodable("over the limit or truncated")
        data = inflater.unused_data
        if not data or wbits != _GZIP_WBITS:
            break
    if data:
        raise _Undecodable("trailing data")
    return b"".join(pieces)


def _brotli_codec():
    import importlib

    for name in ("brotli", "brotlicffi"):
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    return None


def _decoded(data, coding):
    if coding in ("gzip", "x-gzip"):
        return _inflate(data, _GZIP_WBITS)
    if coding == "deflate":
        import zlib

        try:
            return _inflate(data, zlib.MAX_WBITS)
        except zlib.error:
            return _inflate(data, -zlib.MAX_WBITS)  # raw deflate
    if coding == "br":
        codec = _brotli_codec()
        if codec is None:
            raise _Undecodable("no brotli codec")
        decoded = codec.decompress(data)
        if len(decoded) > DECODED_BODY_LIMIT:
            raise _Undecodable("over the limit")
        return decoded
    raise _Undecodable("unsupported content coding")


def _fail_closed(response):
    """Replace the body with the fixed value-free marker text, plain and with
    a matching Content-Length (SIF-2, SIF-7)."""
    try:
        response.content = _SCRUB_FAILED_BYTES
    except Exception:
        return
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    _drop_header(headers, "Content-Encoding")
    _set_header(headers, "Content-Type", _FAILED_CONTENT_TYPE)
    try:
        if "Content-Length" in headers:
            headers["Content-Length"] = str(len(_SCRUB_FAILED_BYTES))
    except Exception:
        pass
    for name in _BODY_VALIDATORS:
        _drop_header(headers, name)


def _rewrite(response, body, *, drop_encoding=False):
    response.content = body
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    if drop_encoding:
        _drop_header(headers, "Content-Encoding")
    if "Content-Length" in headers:
        headers["Content-Length"] = str(len(body))
    for name in _BODY_VALIDATORS:
        _drop_header(headers, name)


def _response_charset(headers):
    if headers is None:
        return None
    declared = None
    for part in str(headers.get("Content-Type", "")).split(";")[1:]:
        name, equal, value = part.partition("=")
        if name.strip().lower() != "charset":
            continue
        if declared is not None or not equal:
            raise ValueError("ambiguous charset declaration")
        value = value.strip()
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError("invalid charset declaration")
            value = value[1:-1]
        declared = value
    return declared


def _scrub_content(response):
    """Scrub a rendered error body. An encoded body is decoded first and, when
    it changes, sent plain; one that cannot be decoded, or any failure here,
    is replaced by the fixed marker text (fail closed)."""
    try:
        headers = getattr(response, "headers", None)
        content = response.content
        codings = _content_codings(
            headers.get("Content-Encoding") if headers is not None else None
        )
        charset = _response_charset(headers)
        if not codings:
            safe = _scrub_bytes_checked(content, charset)
            if safe is not content:
                _rewrite(response, safe)
            return None
        plain = content
        for coding in reversed(codings):
            plain = _decoded(plain, coding)
        safe = _scrub_bytes_checked(plain, charset)
        if safe is plain:
            return None  # nothing to hide: the encoded body is left as it is
        _rewrite(response, safe, drop_encoding=True)
    except Exception:
        _fail_closed(response)
    return None


def _scrub_headers(headers):
    try:
        items = list(headers.items())
    except Exception:
        return
    for name, value in items:
        try:
            safe = scrub_text(value) if isinstance(value, str) else scrub_bytes(value)
            if safe is not value:
                headers[name] = safe
        except Exception:
            _set_header(headers, name, _SCRUB_FAILED)


def _scrub_error_response(response):
    """A 4xx/5xx response with credential-free headers and, for a non-streaming
    JSON, problem+json, text/* or XML body, a credential-free body. A
    streaming response is never rewritten (C7)."""
    status = getattr(response, "status_code", None)
    if not isinstance(status, int) or isinstance(status, bool) or status < 400:
        return response
    if getattr(response, "streaming", False) or hasattr(response, "streaming_content"):
        return response
    headers = getattr(response, "headers", None)
    if headers is not None:
        _scrub_headers(headers)
    try:
        content_type = headers.get("Content-Type", "") if headers is not None else ""
        if not _scrubbed_media_type(content_type):
            return response
        if getattr(response, "is_rendered", True) is False:
            # A deferred-rendering response: scrub once it is rendered.
            response.add_post_render_callback(_scrub_content)
            return response
    except Exception:
        _fail_closed(response)
        return response
    _scrub_content(response)
    return response


class CredentialScrubErrorResponseMiddleware:
    """Error responses (status >= 400) leave without a credential.

    2xx and 3xx responses are never changed: a one-time reveal endpoint
    legitimately returns a new gateway key. Streaming responses are never
    rewritten. Install it first (outermost) in MIDDLEWARE so it sees every
    error response, including those produced by other middleware; a body
    compressed by an inner middleware (GZipMiddleware) is decoded (gzip,
    deflate, br with a codec) and scrubbed, or replaced when it cannot be.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        iscoroutinefunction, markcoroutinefunction = _coroutine_helpers()
        self.is_async = iscoroutinefunction(get_response)
        if self.is_async and markcoroutinefunction is not None:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.is_async:
            return self.__acall__(request)
        return _scrub_error_response(self.get_response(request))

    async def __acall__(self, request):
        return _scrub_error_response(await self.get_response(request))
