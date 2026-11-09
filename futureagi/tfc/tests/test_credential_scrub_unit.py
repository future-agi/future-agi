"""ONE KEY C2 credential scrubbers: the tfc.logging.credential_scrub contract.

T1 positive and near-miss per family, T2 false-positive corpus, D1 linear
time on adversarial 1 MiB inputs, D2 mask-through past every cap, D4 AWS
secret adjacency, PL3 fixed markers, PL4 value-free counts, the identity fast
path, non-mutation, cycles, depth, the Sentry event scrub, upstream bodies and
the error-response middleware.

r2a adds one test (or more) per C2 r1 lens finding: escaped carriers (SIF-1),
encoded error bodies, Content-Length, validators and streaming (SIF-2, SIF-7,
C2, C6, C7, C8), the value-shape header grammar (SIF-3, C3), list and pair
values under credential names (SIF-4, C10), scrub-before-truncate (SIF-5), a
password holding an unescaped "@" (C1), the header-rule import failure
(SIF-6), marker-prefixed values (SIF-8), bytearray and memoryview (SIF-9),
fixed-length mask-through (C4), key collisions (C5) and a D1 query input that
reaches its regex (C9).

Every key-shaped value is SYNTHETIC: built at run time from split prefix parts
and a deterministic tail that starts with FAKE, and tagged "R-LIT: synthetic,
runtime-built" where it is built. No literal in this file has the shape of a
credential (R9).
"""

import base64
import copy
import gzip
import hashlib
import importlib
import json
import string
import sys
import time
import uuid
import zlib
from collections import deque

import pytest

from tfc.logging import credential_scrub as cs

pytestmark = pytest.mark.unit

ALNUM = string.ascii_letters + string.digits
LETTERS = string.ascii_letters
UPPER = string.ascii_uppercase + string.digits
URLSAFE = ALNUM + "_-"
B64 = ALNUM + "+/"
TOKEN68 = ALNUM + "-._~+/"
MIB = 1 << 20
# D1: each adversarial input must finish well inside this, on the CI runner.
BUDGET_SECONDS = 2.0

HDR_AUTH = "Author" + "ization"
HDR_PROXY_AUTH = "Proxy-" + HDR_AUTH
AUTH_SCHEME = "Bear" + "er"
HDR_XAK = "x-api" + "-key"
FIELD_AK = "api" + "_key"
NAME_AWS_SECRET = "AWS_SECRET_" + "ACCESS_KEY"
PEM_BEGIN = "-----BEGIN " + "RSA PRIVATE " + "KEY-----"
PEM_END = "-----END " + "RSA PRIVATE " + "KEY-----"
CREDENTIAL_FIELD = "[hidden: credential-field]"
DEPTH = "[hidden: depth]"
SCRUB_FAILED = b"[hidden: scrub-failed]"

# One backslash. Escape sequences are assembled at run time, never typed.
BS = "\\"
ESCAPES = (
    ("newline", BS + "n"),
    ("tab", BS + "t"),
    ("crlf", BS + "r" + BS + "n"),
    ("u000a", BS + "u" + "000a"),
    ("u0022", BS + "u" + "0022"),
    ("x0a", BS + "x" + "0a"),
    ("quote", BS + '"'),
)

KI09_NAMES = (
    "aws-access-key-id",
    "github-token",
    "github-fine-grained-pat",
    "gitlab-pat",
    "slack-token",
    "slack-webhook",
    "stripe-secret",
    "openai-key",
    "openai-legacy-key",
    "anthropic-key",
    "google-api-key",
    "google-oauth-client-secret",
    "huggingface-token",
    "groq-key",
    "xai-key",
    "perplexity-key",
    "replicate-token",
    "npm-token",
    "pypi-token",
    "sendgrid-key",
    "private-key-block",
    "url-embedded-credentials",
    "authorization-bearer-header",
)
D4_NAME = "aws-secret-access-key"
EXTENSION_NAMES = (
    "futureagi-gateway-key",
    "sk-generic",
    "authorization-header",
    "bearer-token",
    "api-key-header",
    "query-credential",
)
ALL_NAMES = (*KI09_NAMES, D4_NAME, *EXTENSION_NAMES)


def j(*parts):
    return "".join(parts)


def tail(length, alphabet=ALNUM, seed=0):
    """A deterministic synthetic filler: FAKE, alphabet characters, a digit."""
    # R-LIT: synthetic, runtime-built
    body = "FAKE" + "".join(
        alphabet[(seed + 7 * index) % len(alphabet)] for index in range(length)
    )
    return body[: length - 1] + "7"


def m(name):
    return cs.MARKER_FORMAT.format(name)


def basic_credential(seed=0, extra=0):
    """A Basic credential that stays visibly synthetic once encoded."""
    # R-LIT: synthetic, runtime-built
    encoded = base64.b64encode(("svc:" + tail(20 + extra, ALNUM, seed)).encode())
    return "FAKE" + encoded.decode()


def pem_block(seed=0, body_length=None):
    # R-LIT: synthetic, runtime-built (the body text starts with FAKE)
    raw = "FAKE" + base64.b64encode(b"FAKE" * 24 + bytes([seed])).decode()
    if body_length is not None:
        raw = (raw * (body_length // len(raw) + 1))[:body_length]
    body = "\n".join(raw[index : index + 64] for index in range(0, len(raw), 64))
    return PEM_BEGIN + "\n" + body + "\n" + PEM_END


def cases(seed=0, extra=0):
    """(family, text, expected scrub_text output) for every family.

    extra lengthens the value of every range-capped family (staying inside
    its cap), so two calls give values of different lengths where allowed.
    """

    def t(length, alphabet=ALNUM, offset=0):
        return tail(length, alphabet, seed + offset)

    # R-LIT: synthetic, runtime-built
    key_id = j("AK", "IA") + t(16, UPPER)
    basic = basic_credential(seed, extra)
    return [
        (
            "aws-access-key-id",
            "id " + key_id + " end",
            "id " + m("aws-access-key-id") + " end",
        ),
        ("github-token", j("gh", "p_") + t(36), m("github-token")),
        (
            "github-fine-grained-pat",
            j("github", "_pat_") + t(82, ALNUM + "_"),
            m("github-fine-grained-pat"),
        ),
        ("gitlab-pat", j("gl", "pat-") + t(20, URLSAFE), m("gitlab-pat")),
        ("slack-token", j("xo", "xb-") + t(40 + extra, ALNUM + "-"), m("slack-token")),
        (
            "slack-webhook",
            "see "
            + j("https://hooks.", "slack.com/services/T")
            + t(10, UPPER)
            + "/B"
            + t(10, UPPER, 1)
            + "/"
            + t(24 + extra)
            + " end",
            "see " + m("slack-webhook") + " end",
        ),
        ("stripe-secret", j("sk", "_live_") + t(30 + extra), m("stripe-secret")),
        ("openai-key", j("sk-", "proj-") + t(48 + extra, URLSAFE), m("openai-key")),
        (
            "openai-legacy-key",
            "sk-" + t(20) + j("T3Blb", "kFJ") + t(20, ALNUM, 1),
            m("openai-legacy-key"),
        ),
        (
            "anthropic-key",
            j("sk-ant-", "api03-") + t(95 + extra, URLSAFE),
            m("anthropic-key"),
        ),
        ("google-api-key", j("AI", "za") + t(35, URLSAFE), m("google-api-key")),
        (
            "google-oauth-client-secret",
            j("GOC", "SPX-") + t(28, URLSAFE),
            m("google-oauth-client-secret"),
        ),
        ("huggingface-token", j("hf", "_") + t(34), m("huggingface-token")),
        ("groq-key", j("gs", "k_") + t(52), m("groq-key")),
        ("xai-key", j("xa", "i-") + t(80), m("xai-key")),
        ("perplexity-key", j("pp", "lx-") + t(48), m("perplexity-key")),
        ("replicate-token", j("r8", "_") + t(37), m("replicate-token")),
        ("npm-token", j("np", "m_") + t(36), m("npm-token")),
        (
            "pypi-token",
            j("pypi-", "AgEIcHlwaS5vcmc") + t(60 + extra, URLSAFE),
            m("pypi-token"),
        ),
        (
            "sendgrid-key",
            j("SG", ".") + t(22, URLSAFE) + "." + t(43, URLSAFE, 3),
            m("sendgrid-key"),
        ),
        (
            "private-key-block",
            "pem\n" + pem_block(seed) + "\nok",
            "pem\n" + m("private-key-block") + "\nok",
        ),
        (
            "url-embedded-credentials",
            j("postgres", "://svc:") + t(20 + extra) + "@db.internal:5432/app",
            j("postgres", "://svc:")
            + m("url-embedded-credentials")
            + "@db.internal:5432/app",
        ),
        (
            "authorization-bearer-header",
            HDR_AUTH + ": " + AUTH_SCHEME + " " + t(24 + extra, TOKEN68),
            HDR_AUTH + ": " + m("authorization-bearer-header"),
        ),
        (
            D4_NAME,
            NAME_AWS_SECRET + "=" + t(40, B64),
            NAME_AWS_SECRET + "=" + m(D4_NAME),
        ),
        (
            "futureagi-gateway-key",
            j("sk-", "agentcc-") + t(32 + extra, URLSAFE),
            m("futureagi-gateway-key"),
        ),
        ("sk-generic", "sk-" + t(40 + extra, URLSAFE), m("sk-generic")),
        (
            "authorization-header",
            json.dumps({HDR_PROXY_AUTH: "Basic " + basic}),
            json.dumps({HDR_PROXY_AUTH: m("authorization-header")}),
        ),
        (
            "bearer-token",
            "sent " + AUTH_SCHEME + " " + t(24 + extra, TOKEN68) + " end",
            "sent " + AUTH_SCHEME + " " + m("bearer-token"),
        ),
        (
            "api-key-header",
            HDR_XAK + ": " + t(32 + extra),
            HDR_XAK + ": " + m("api-key-header"),
        ),
        (
            "query-credential",
            "https://api.example.test/v1/models?" + "key=" + t(24 + extra) + "&page=2",
            "https://api.example.test/v1/models?"
            + "key="
            + m("query-credential")
            + "&page=2",
        ),
    ]


def near_misses():
    """(family, text) that the named family must not match."""
    # R-LIT: synthetic, runtime-built
    return [
        ("aws-access-key-id", j("AK", "IA") + tail(15, UPPER) + " end"),
        ("aws-access-key-id", "X" + j("AK", "IA") + tail(16, UPPER)),
        ("github-token", j("gh", "p_") + tail(35)),
        ("github-token", j("gh", "x_") + tail(36)),
        ("github-fine-grained-pat", j("github", "_pat_") + tail(81, ALNUM + "_")),
        ("gitlab-pat", j("gl", "pat-") + tail(19, URLSAFE)),
        ("slack-token", j("xo", "xb-") + tail(9, ALNUM + "-")),
        ("slack-token", j("xo", "xq-") + tail(20)),
        (
            "slack-webhook",
            j("https://hooks.", "slack.com/services/T")
            + tail(7, UPPER)
            + "/B"
            + tail(10, UPPER)
            + "/"
            + tail(24),
        ),
        ("stripe-secret", j("sk", "_live_") + tail(23)),
        ("openai-key", j("sk-", "proj-") + tail(19, URLSAFE)),
        ("openai-legacy-key", "sk-" + tail(20) + j("T3Blb", "kFX") + tail(20)),
        ("anthropic-key", j("sk-ant-", "api03-") + tail(79, URLSAFE)),
        ("google-api-key", j("AI", "za") + tail(34, URLSAFE)),
        ("google-oauth-client-secret", j("GOC", "SPX-") + tail(27, URLSAFE)),
        ("huggingface-token", j("hf", "_") + tail(33)),
        ("groq-key", j("gs", "k_") + tail(51)),
        ("xai-key", j("xa", "i-") + tail(79)),
        ("perplexity-key", j("pp", "lx-") + tail(47)),
        ("replicate-token", j("r8", "_") + tail(36)),
        ("npm-token", j("np", "m_") + tail(35)),
        ("pypi-token", j("pypi-", "AgEIcHlwaS5vcmc") + tail(49, URLSAFE)),
        ("sendgrid-key", j("SG", ".") + tail(21, URLSAFE) + "." + tail(43, URLSAFE)),
        (
            "private-key-block",
            "-----BEGIN "
            + "PUBLIC "
            + "KEY-----\n"
            + tail(64)
            + "\n-----END PUBLIC KEY-----",
        ),
        ("url-embedded-credentials", "https://db.internal:5432/app?ok=1"),
        (
            "authorization-bearer-header",
            HDR_AUTH + ": " + AUTH_SCHEME + " " + tail(15, TOKEN68),
        ),
        (D4_NAME, tail(40, B64)),
        ("futureagi-gateway-key", j("sk-", "agentcc-") + tail(15, URLSAFE)),
        ("sk-generic", "sk-" + tail(31, URLSAFE)),
        ("sk-generic", "task-" + tail(40, URLSAFE)),
        ("authorization-header", json.dumps({HDR_AUTH: None})),
        ("authorization-header", HDR_AUTH + " header without separator"),
        ("bearer-token", AUTH_SCHEME),
        ("api-key-header", FIELD_AK + " without separator"),
        ("query-credential", "https://x.test/?" + "monkey=" + tail(24)),
    ]


# r3 deliberately masks code-looking named values too. Build this diagnostic
# corpus without literal header anchors (R-LIT: synthetic, runtime-built).
PYTHON_SOURCE = (
    """
def build(request, FIELD: str, AUTH: str | None = None) -> dict:
    FIELD = request.headers.get("X-Api-Key")
    headers["AUTH"] = "SCHEME {token}"
    return {"AUTH": None, "count": 3}
""".replace("FIELD", FIELD_AK)
    .replace("AUTH", HDR_AUTH)
    .replace("SCHEME", AUTH_SCHEME)
)


def false_positive_corpus():
    # R-LIT: synthetic, runtime-built (non-credential samples, labelled FAKE)
    noise = b"".join(
        hashlib.sha256(b"block %d" % index).digest() for index in range(96)
    )
    image = base64.b64encode(noise).decode()
    fernet = "gAAAAAB" + base64.urlsafe_b64encode(noise[:150]).decode()
    samples = [
        *(
            str(uuid.UUID(int=(index + 1) * 0x1234567890ABCDEF1234567))
            for index in range(8)
        ),
        *(hashlib.sha1(b"commit %d" % index).hexdigest() for index in range(8)),
        "data:image/png;base64," + image,
        "The provider rejected the request because the account is over quota. "
        "Retry later, or see https://docs.example.test/errors#quota for details. "
        "Credentials expire after an hour; the optional header is not required "
        "for public models.",
        "def build(request): return request.headers.get('Accept')",
        "enc::" + fernet,
        "fagi." + hashlib.sha256(b"workspace").hexdigest()[:32],
        "scikit-learn==1.5.2 sk-learn-compat ask-me-anything task-queue-worker-0001",
        json.dumps(
            {"event": "request_finished", "status_code": 200, "path": "/api/v1/models/"}
        ),
    ]
    return ["FAKE " + sample for sample in samples]


def excluded_formats():
    """KI09 exclusions: no distinctive format, so never pattern-matched."""
    # R-LIT: synthetic, runtime-built. A hex-only value cannot spell FAKE, so
    # each hex value carries a "FAKE:" label at the string level.
    return [
        tail(32),  # Mistral-shaped
        tail(40),  # Cohere-shaped
        "FAKE:" + hashlib.md5(b"FAKE-azure").hexdigest(),  # Azure OpenAI 32-hex
        "FAKE:" + "SK" + hashlib.md5(b"FAKE-twilio").hexdigest(),  # Twilio SK + 32-hex
        "FAKE:"
        + hashlib.md5(b"FAKE-futureagi").hexdigest(),  # FutureAGI platform 32-hex
    ]


def repeat(piece, size=MIB):
    return (piece * (size // len(piece) + 1))[:size]


def adversarial(name):
    """A 1 MiB input built to make the named family backtrack (D1)."""
    special = {
        "slack-webhook": repeat(j("https://hooks.", "slack.com/services/T")),
        "private-key-block": repeat(PEM_BEGIN + "\n"),
        "url-embedded-credentials": "@" + j("https", "://a:") + "b" * MIB,
        "authorization-bearer-header": HDR_AUTH + ":" + " " * MIB,
        D4_NAME: repeat(j("AK", "IA") + "Z" * 16 + " " + "Q" * 39 + " "),
        "authorization-header": repeat(HDR_AUTH + "="),
        "bearer-token": AUTH_SCHEME + " " * MIB,
        "api-key-header": repeat(FIELD_AK + "="),
        # C9: "=" included, so the prefilter lets the family's regex run.
        "query-credential": repeat("?" + "key="),
    }
    prefixes = {
        "aws-access-key-id": j("AK", "IA"),
        "github-token": j("gh", "p_"),
        "github-fine-grained-pat": j("github", "_pat_"),
        "gitlab-pat": j("gl", "pat-"),
        "slack-token": j("xo", "xb-"),
        "stripe-secret": j("sk", "_live_"),
        "openai-key": j("sk-", "proj-"),
        "openai-legacy-key": "sk-" + j("T3Blb", "kFJ"),
        "anthropic-key": j("sk-ant-", "api03-"),
        "google-api-key": j("AI", "za"),
        "google-oauth-client-secret": j("GOC", "SPX-"),
        "huggingface-token": j("hf", "_"),
        "groq-key": j("gs", "k_"),
        "xai-key": j("xa", "i-"),
        "perplexity-key": j("pp", "lx-"),
        "replicate-token": j("r8", "_"),
        "npm-token": j("np", "m_"),
        "pypi-token": j("pypi-", "AgEIcHlwaS5vcmc"),
        "sendgrid-key": j("SG", "."),
        "futureagi-gateway-key": j("sk-", "agentcc-"),
        "sk-generic": "sk-",
    }
    return special[name] if name in special else repeat(prefixes[name])


D1_R2A_SHAPES = (
    "escaped-prefixes",
    "escaped-names",
    "call-shaped-values",
    "dotted-values",
    "scheme-words",
    "url-many-at",
    "url-no-at",
    "quoted-params-open",
    "quoted-params-closed",
    "marker-query",
    "escaped-json-values",
    "aws-escaped",
)


def adversarial_r2a(shape):
    """A 1 MiB input aimed at a grammar r2a added (D1)."""
    inputs = {
        "escaped-prefixes": lambda: repeat(BS + "n" + j("gh", "p_")),
        "escaped-names": lambda: repeat(BS + "n" + HDR_AUTH + ": "),
        "call-shaped-values": lambda: repeat(FIELD_AK + " = " + "a" * 60 + "("),
        "dotted-values": lambda: HDR_XAK + ": " + repeat("ab."),
        "scheme-words": lambda: HDR_AUTH + ": " + repeat("a" * 31 + " "),
        "url-many-at": lambda: j("https", "://a:") + repeat("b@"),
        "url-no-at": lambda: repeat(j("https", "://a:") + "b" * 60 + " "),
        "quoted-params-open": lambda: (
            HDR_AUTH + ": Digest " + "username" + repeat('a="' + "x" * 1100)
        ),
        "quoted-params-closed": lambda: (
            HDR_AUTH + ": Digest " + "username" + repeat('a="' + "x" * 20 + '", ')
        ),
        "marker-query": lambda: repeat("&" + "key=[hidden: "),
        "escaped-json-values": lambda: repeat(
            BS + '"' + FIELD_AK + BS + '": ' + BS + '"'
        ),
        "aws-escaped": lambda: repeat(
            j("AK", "IA") + "Z" * 16 + BS + "n" + "Q" * 39 + " "
        ),
    }
    return inputs[shape]()


def anchored(seed=0):
    """(family, value) for every family, the value starting at its family's
    anchor (prefix, name or scheme), so the character before it is whatever
    precedes it in the carrier."""

    def t(length, alphabet=ALNUM, offset=0):
        return tail(length, alphabet, seed + offset)

    # R-LIT: synthetic, runtime-built
    return [
        ("aws-access-key-id", j("AK", "IA") + t(16, UPPER)),
        ("github-token", j("gh", "p_") + t(36)),
        ("github-fine-grained-pat", j("github", "_pat_") + t(82, ALNUM + "_")),
        ("gitlab-pat", j("gl", "pat-") + t(20, URLSAFE)),
        ("slack-token", j("xo", "xb-") + t(40, ALNUM + "-")),
        (
            "slack-webhook",
            j("https://hooks.", "slack.com/services/T")
            + t(10, UPPER)
            + "/B"
            + t(10, UPPER, 1)
            + "/"
            + t(24),
        ),
        ("stripe-secret", j("sk", "_live_") + t(30)),
        ("openai-key", j("sk-", "proj-") + t(48, URLSAFE)),
        ("openai-legacy-key", "sk-" + t(20) + j("T3Blb", "kFJ") + t(20, ALNUM, 1)),
        ("anthropic-key", j("sk-ant-", "api03-") + t(95, URLSAFE)),
        ("google-api-key", j("AI", "za") + t(35, URLSAFE)),
        ("google-oauth-client-secret", j("GOC", "SPX-") + t(28, URLSAFE)),
        ("huggingface-token", j("hf", "_") + t(34)),
        ("groq-key", j("gs", "k_") + t(52)),
        ("xai-key", j("xa", "i-") + t(80)),
        ("perplexity-key", j("pp", "lx-") + t(48)),
        ("replicate-token", j("r8", "_") + t(37)),
        ("npm-token", j("np", "m_") + t(36)),
        ("pypi-token", j("pypi-", "AgEIcHlwaS5vcmc") + t(60, URLSAFE)),
        ("sendgrid-key", j("SG", ".") + t(22, URLSAFE) + "." + t(43, URLSAFE, 3)),
        ("private-key-block", pem_block(seed)),
        (
            "url-embedded-credentials",
            j("postgres", "://svc:") + t(20) + "@db.internal:5432/app",
        ),
        (
            "authorization-bearer-header",
            HDR_AUTH + ": " + AUTH_SCHEME + " " + t(24, TOKEN68),
        ),
        (D4_NAME, NAME_AWS_SECRET + "=" + t(40, B64)),
        ("futureagi-gateway-key", j("sk-", "agentcc-") + t(32, URLSAFE)),
        ("sk-generic", "sk-" + t(40, URLSAFE)),
        ("authorization-header", HDR_AUTH + ": Basic " + basic_credential(seed)),
        ("bearer-token", AUTH_SCHEME + " " + t(24, TOKEN68)),
        ("api-key-header", HDR_XAK + ": " + t(32)),
        ("query-credential", "?" + "key=" + t(24)),
    ]


# --- r2b review regressions: every value built before the implementation --------

PERCENT_ESCAPES = tuple("%" + code for code in ("20", "0A", "22", "2F", "3D"))
R2B_TERMINATORS = ("<", "</td>", "|", " next", " algorithm=MD5", " (retry 2)")
R2B_SIBLINGS = (
    "",
    "\nnext",
    "\tmore",
    BS + "next",
    '"next',
    ")next",
    "]next",
    "}next",
    ">next",
    ",next",
    ";next",
)


def r2b_scheme_cases():
    # R-LIT: synthetic, runtime-built; lower-case FAKE label for these shapes.
    values = (
        tail(16, LETTERS).lower()[:-1] + "z",
        j("fake", "_secret", "_value"),
        j("fake", ".abc", ".def"),
        tail(12, LETTERS).lower()[:-1] + "z",
    )
    for scheme in ("Token", "Basic", AUTH_SCHEME, "Custom"):
        for index, value in enumerate(values):
            for suffix in ("", "\nHost: example.test", ", next"):
                yield (
                    scheme + ":" + str(index),
                    HDR_AUTH + ": " + scheme + " " + value + suffix,
                    value,
                )


def r2b_rendered_header_cases():
    """label, text, exact output; multiple values and siblings stay separate."""
    # R-LIT: synthetic, runtime-built
    for name, family, value in (
        (HDR_AUTH, "authorization-header", "Basic " + basic_credential()),
        (HDR_PROXY_AUTH, "authorization-header", "Token " + tail(20)),
        (HDR_XAK, "api-key-header", tail(32)),
        (FIELD_AK, "api-key-header", tail(32)),
    ):
        values = [value, "", value + "x"]
        marks = [m(family), "", m(family)]
        carriers = (
            (
                "go-json",
                lambda vs, name=name: json.dumps(
                    {name: vs, "accept": ["json"]}, separators=(",", ":")
                ),
            ),
            ("repr-list", lambda vs, name=name: repr({name: vs, "accept": ["json"]})),
            (
                "repr-tuple",
                lambda vs, name=name: repr({name: tuple(vs), "accept": ["json"]}),
            ),
            (
                "querydict",
                lambda vs, name=name: "<QueryDict: " + repr({name: vs}) + ">",
            ),
            (
                "json-pair",
                lambda vs, name=name: json.dumps([[name, vs[0]], ["accept", "json"]]),
            ),
            (
                "tuple-pair",
                lambda vs, name=name: repr([(name, vs[0]), ("accept", "json")]),
            ),
            (
                "list-pair-list",
                lambda vs, name=name: repr([[name, vs], ["accept", "json"]]),
            ),
            (
                "bytes-pair",
                lambda vs, name=name: repr(
                    [(name.encode(), vs[0].encode()), (b"accept", b"json")]
                ),
            ),
            (
                "dict-items",
                lambda vs, name=name: repr({name: vs[0], "accept": "json"}.items()),
            ),
            (
                "deque",
                lambda vs, name=name: repr(deque([(name, vs[0]), ("accept", "json")])),
            ),
            (
                "go-map",
                lambda vs, name=name: "map[" + name + ":[" + vs[0] + "] Accept:[json]]",
            ),
            ("logfmt", lambda vs, name=name: name + "=" + repr(vs)),
            ("json-in-json", lambda vs, name=name: json.dumps(json.dumps({name: vs}))),
        )
        for label, render in carriers:
            # Raw Go map text is not a string-token container: r3 masks to EOL.
            expected = render(marks)
            if label == "go-map":
                expected = "map[" + name + ":" + m(family)
            yield name + ":" + label, render(values), expected


def r2b_digest_value():
    # R-LIT: synthetic, runtime-built
    return (
        "Digest "
        + 'username="svc", nonce="'
        + tail(24)
        + '", response="'
        + tail(32, ALNUM, 2)
        + '"'
    )


def r2b_sibling_cases():
    # R-LIT: synthetic, runtime-built
    for prefix in (HDR_XAK + ": ", HDR_AUTH + ": Basic ", FIELD_AK + "="):
        for padding in ("=", "=="):
            value = prefix + tail(22) + padding
            for sibling in R2B_SIBLINGS:
                for separators in ((",", ":"), (", ", ": ")):
                    yield (
                        json.dumps([value, sibling, "tail"], separators=separators),
                        sibling,
                        "list",
                    )
                    yield (
                        json.dumps(
                            {"h": value, sibling: "kept"}, separators=separators
                        ),
                        sibling,
                        "dict",
                    )


@pytest.mark.parametrize("scheme", ["Token", "Basic", AUTH_SCHEME, "Custom"])
def test_r2b_c1_scheme_values_keep_only_the_call_guard(scheme):
    for label, text, value in r2b_scheme_cases():
        if label.startswith(scheme + ":"):
            safe = cs.scrub_text(text)
            assert value not in safe, label
            assert "[hidden: " in safe, label
            assert cs.scrub_text(safe) == safe, label
    call = HDR_AUTH + ": " + scheme + " base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)"
    assert cs.scrub_text(call) != call
    raw = FIELD_AK + " = " + j("fake", "_secret", "_value")
    assert cs.scrub_text(raw) == FIELD_AK + " = " + m("api-key-header")


@pytest.mark.parametrize(
    "label,text,expected",
    list(r2b_rendered_header_cases()),
    ids=[c[0] for c in r2b_rendered_header_cases()],
)
def test_r2b_sif1_rendered_lists_and_pairs(label, text, expected):
    safe = cs.scrub_text(text)
    assert safe == expected, label
    assert cs.scrub_text(safe) == safe, label
    assert cs.count_matches(safe) == {}, label
    assert bytes(cs.scrub_bytes(text.encode())).decode() == expected
    assert cs.scrub_sentry_event({"message": text})["message"] == expected
    assert cs.redact_upstream_body(text, limit=4096) == expected


@pytest.mark.parametrize(
    "escape", PERCENT_ESCAPES, ids=[s[1:] for s in PERCENT_ESCAPES]
)
def test_r2b_sif2_percent_leads_for_every_family(escape):
    for name, value in anchored():
        text = "encoded:" + escape + value
        assert "FAKE" not in cs.scrub_text(text), name
        assert name in cs.count_matches(text), name
    # D4: its explicit window-edge guard must agree with the matcher lead.
    key_id = j("AK", "IA") + tail(16, UPPER)  # R-LIT: synthetic, runtime-built
    secret = tail(40, B64, 2)  # R-LIT: synthetic, runtime-built
    assert cs.scrub_text(key_id + escape + secret) == m(
        "aws-access-key-id"
    ) + escape + m(D4_NAME)


def test_r2b_sif2_percent_query_separators():
    value = tail(32)  # R-LIT: synthetic, runtime-built
    for sep in ("%" + "3F", "%" + "3f", "%" + "26"):
        for equal in ("=", "%" + "3D", "%" + "3d"):
            text = "https://example.test/?url=nested" + sep + "key" + equal + value
            assert cs.scrub_text(text) == text[: -len(value)] + m("query-credential")


@pytest.mark.parametrize("terminator", R2B_TERMINATORS)
def test_r2b_c2_digest_terminal_parameter_is_masked(terminator):
    value = r2b_digest_value()
    for text in (
        HDR_AUTH + ": " + value + terminator,
        json.dumps({"event": HDR_AUTH + ": " + value + terminator}),
    ):
        safe = cs.scrub_text(text)
        assert "FAKE" not in safe
        assert cs.scrub_text(safe) == safe
        if text.startswith("{"):
            assert isinstance(json.loads(safe)["event"], str)


@pytest.mark.parametrize("kind", ["list", "dict"])
def test_r2b_c3_json_siblings_never_become_a_parameter(kind):
    for text, sibling, case_kind in r2b_sibling_cases():
        if case_kind != kind:
            continue
        safe = cs.scrub_text(text)
        parsed = json.loads(safe)
        assert "FAKE" not in safe
        assert cs.scrub_text(safe) == safe
        if kind == "list":
            assert len(parsed) == 3 and parsed[1:] == [sibling, "tail"]
        else:
            assert set(parsed) == {"h", sibling} and parsed[sibling] == "kept"


D1_R2B_SHAPES = (
    "percent-leads",
    "list-openers",
    "list-values",
    "pair-names",
    "padded-json",
    "digest-tails",
)


def adversarial_r2b(shape):
    # R-LIT: synthetic, runtime-built; prefix repetition, not credential literals.
    return {
        "percent-leads": lambda: repeat("%" + "20" + j("gh", "p_")),
        "list-openers": lambda: repeat(HDR_AUTH + ":["),
        "list-values": lambda: (
            '{"' + HDR_XAK + '":[' + repeat('"' + tail(20) + '",') + '""]}'
        ),
        "pair-names": lambda: repeat('("' + FIELD_AK + '",'),
        "padded-json": lambda: repeat(
            json.dumps([HDR_XAK + ": " + tail(22) + "==", "", "\n"]) + "\n"
        ),
        "digest-tails": lambda: repeat(
            HDR_AUTH + ": " + r2b_digest_value() + "</td>\n"
        ),
    }[shape]()


@pytest.mark.parametrize("shape", D1_R2B_SHAPES)
def test_r2b_d1_new_grammar_finishes_inside_budget(shape):
    text = adversarial_r2b(shape)
    started = time.perf_counter()
    assert cs.scrub_text(text) != "[hidden: scrub-failed]"
    assert time.perf_counter() - started < BUDGET_SECONDS, shape


# --- the family table ---------------------------------------------------------


def test_families_are_ordered_ki09_then_d4_then_extensions():
    assert tuple(family.name for family in cs.FAMILIES) == ALL_NAMES
    sources = [family.source for family in cs.FAMILIES]
    assert sources == ["KI09"] * 23 + ["KI09-D4"] + ["extension"] * 6
    assert all(family.pattern for family in cs.FAMILIES)
    assert cs.MARKER_FORMAT == "[hidden: {}]"


# --- T1 / PL3 / PL4 -----------------------------------------------------------


@pytest.mark.parametrize("name, text, expected", cases(), ids=[c[0] for c in cases()])
def test_t1_positive_is_replaced_by_the_fixed_marker(name, text, expected):
    result = cs.scrub_text(text)
    assert result == expected
    assert "FAKE" not in result
    assert cs.count_matches(text) == {name: 1}


@pytest.mark.parametrize("name, text", near_misses(), ids=[c[0] for c in near_misses()])
def test_t1_near_miss_is_not_matched_by_its_family(name, text):
    assert name not in cs.count_matches(text)


def test_pl3_marker_carries_no_prefix_suffix_length_or_hash():
    first = cases(seed=0, extra=0)
    second = cases(seed=5, extra=3)
    for (name, text_a, out_a), (_, text_b, out_b) in zip(first, second, strict=False):
        assert text_a != text_b, name
        assert cs.scrub_text(text_a) == cs.scrub_text(text_b) == out_a == out_b, name


def test_pl4_counts_are_family_counts_only():
    # R-LIT: synthetic, runtime-built
    key_a = j("AK", "IA") + tail(16, UPPER)
    key_b = j("AS", "IA") + tail(16, UPPER, 1)
    token = j("gh", "p_") + tail(36)
    counts = cs.count_matches("a " + key_a + " b " + key_b + " c " + token)
    assert counts == {"aws-access-key-id": 2, "github-token": 1}
    assert cs.count_matches("nothing here") == {}
    assert cs.count_matches((" " + key_a).encode()) == {"aws-access-key-id": 1}


def test_scrubbing_is_idempotent():
    for name, _text, expected in cases():
        assert cs.scrub_text(expected) == expected, name
        assert cs.count_matches(expected) == {}, name


# --- T2 and the KI09 exclusions ----------------------------------------------


def test_t2_false_positive_corpus_has_zero_hits():
    for index, sample in enumerate(false_positive_corpus()):
        assert cs.count_matches(sample) == {}, index
        assert cs.scrub_text(sample) is sample


def test_ki09_excluded_formats_stay_unmatched():
    for sample in excluded_formats():
        assert cs.scrub_text(sample) is sample
        assert cs.count_matches("value " + sample) == {}


# --- D1 linear time -----------------------------------------------------------


@pytest.mark.parametrize("name", ALL_NAMES)
def test_d1_adversarial_mebibyte_finishes_inside_the_budget(name):
    text = adversarial(name)
    assert len(text) >= MIB
    started = time.perf_counter()
    result = cs.scrub_text(text)
    elapsed = time.perf_counter() - started
    assert isinstance(result, str)
    assert elapsed < BUDGET_SECONDS, f"{name}: {elapsed:.3f}s"


@pytest.mark.parametrize("shape", D1_R2A_SHAPES)
def test_d1_r2a_shapes_finish_inside_the_budget(shape):
    text = adversarial_r2a(shape)
    assert len(text) >= MIB
    started = time.perf_counter()
    result = cs.scrub_text(text)
    elapsed = time.perf_counter() - started
    assert isinstance(result, str)
    assert elapsed < BUDGET_SECONDS, f"{shape}: {elapsed:.3f}s"


def test_c9_query_credential_adversarial_input_reaches_its_regex():
    text = adversarial("query-credential")
    assert "key=" in text
    assert cs.count_matches(text).get("query-credential", 0) >= 1


# --- D2 mask-through ----------------------------------------------------------


def test_d2_values_longer_than_the_cap_are_masked_through():
    # R-LIT: synthetic, runtime-built
    long_values = [
        ("slack-token", j("xo", "xb-") + tail(300)),
        ("stripe-secret", j("sk", "_test_") + tail(150)),
        ("openai-key", j("sk-", "proj-") + tail(260)),
        ("anthropic-key", j("sk-ant-", "api03-") + tail(200)),
        ("futureagi-gateway-key", j("sk-", "agentcc-") + tail(260)),
        ("sk-generic", "sk-" + tail(260)),
    ]
    for name, value in long_values:
        assert cs.scrub_text("a " + value + " b") == "a " + m(name) + " b", name

    url = (
        j("redis", "://svc:") + tail(300) + "@cache:6379/0"
    )  # R-LIT: synthetic, runtime-built
    assert (
        cs.scrub_text(url)
        == j("redis", "://svc:") + m("url-embedded-credentials") + "@cache:6379/0"
    )

    header = (
        HDR_AUTH + ": " + AUTH_SCHEME + " " + tail(5000, TOKEN68)
    )  # R-LIT: synthetic, runtime-built
    assert cs.scrub_text(header) == HDR_AUTH + ": " + m("authorization-bearer-header")
    bare = (
        AUTH_SCHEME + " " + tail(5000, TOKEN68) + " end"
    )  # R-LIT: synthetic, runtime-built
    assert cs.scrub_text(bare) == AUTH_SCHEME + " " + m("bearer-token")
    query = (
        "https://x.test/?" + "token=" + tail(5000) + "&a=1"
    )  # R-LIT: synthetic, runtime-built
    assert (
        cs.scrub_text(query)
        == "https://x.test/?" + "token=" + m("query-credential") + "&a=1"
    )
    raw = HDR_XAK + ": " + tail(5000)  # R-LIT: synthetic, runtime-built
    assert cs.scrub_text(raw) == HDR_XAK + ": " + m("api-key-header")

    webhook = (  # R-LIT: synthetic, runtime-built
        j("https://hooks.", "slack.com/services/T")
        + tail(10, UPPER)
        + "/B"
        + tail(10, UPPER, 1)
        + "/"
        + tail(45)
    )
    assert cs.scrub_text(webhook + " ok") == m("slack-webhook") + " ok"

    # A capped class that includes "-": no suffix survives a match.
    gitlab = (
        j("gl", "pat-") + tail(20, URLSAFE) + "-" + tail(9, ALNUM, 4)
    )  # R-LIT: synthetic, runtime-built
    assert cs.scrub_text(gitlab + " ok") == m("gitlab-pat") + " ok"


def test_d2_private_key_blocks_mask_through_the_end_line_or_the_text():
    long_block = pem_block(body_length=20000)
    assert (
        cs.scrub_text("a\n" + long_block + "\nb")
        == "a\n" + m("private-key-block") + "\nb"
    )
    unterminated = (
        "a\n" + PEM_BEGIN + "\n" + tail(64) + "\n" + tail(64, ALNUM, 1)
    )  # R-LIT: synthetic, runtime-built
    assert cs.scrub_text(unterminated) == "a\n" + m("private-key-block")


def test_c4_fixed_length_families_mask_longer_runs_through_the_class():
    # R-LIT: synthetic, runtime-built
    longer = [
        ("github-token", j("gh", "p_") + tail(40)),
        ("huggingface-token", j("hf", "_") + tail(40)),
        ("npm-token", j("np", "m_") + tail(40)),
        ("aws-access-key-id", j("AK", "IA") + tail(22, UPPER)),
        ("google-api-key", j("AI", "za") + tail(40, URLSAFE)),
        ("groq-key", j("gs", "k_") + tail(60)),
    ]
    for name, value in longer:
        assert cs.scrub_text("a " + value + " b") == "a " + m(name) + " b", name


# --- D4 AWS secret adjacency --------------------------------------------------


def test_d4_aws_secret_adjacency():
    # R-LIT: synthetic, runtime-built
    key_id = j("AK", "IA") + tail(16, UPPER)
    secret = tail(40, B64, 2)
    key_mark, secret_mark = m("aws-access-key-id"), m(D4_NAME)

    # (a) within 32 characters after or before a matched key id
    assert cs.scrub_text(key_id + " " + secret) == key_mark + " " + secret_mark
    assert (
        cs.scrub_text(key_id + " " * 32 + secret) == key_mark + " " * 32 + secret_mark
    )
    assert cs.scrub_text(secret + "\n" + key_id) == secret_mark + "\n" + key_mark
    far = key_id + " " * 33 + secret
    assert cs.scrub_text(far) == key_mark + " " * 33 + secret

    # (b) right after a recognised name
    for name in (
        NAME_AWS_SECRET,
        NAME_AWS_SECRET.lower(),
        "Secret" + "AccessKey",
        "secret" + "AccessKey",
    ):
        assert cs.scrub_text(name + "=" + secret) == name + "=" + secret_mark
        assert cs.scrub_text(name + " = " + secret) == name + " = " + secret_mark
        assert cs.scrub_text(json.dumps({name: secret})) == json.dumps(
            {name: secret_mark}
        )

    # (c) never part of a longer base64 run
    longer = tail(41, B64, 2)
    assert cs.scrub_text(key_id + " " + longer) == key_mark + " " + longer
    assert D4_NAME not in cs.count_matches(NAME_AWS_SECRET + "=" + longer)

    # (d) a standalone secret is not pattern-masked
    assert cs.scrub_text(secret) is secret
    assert cs.scrub_text("value " + secret + " end") == "value " + secret + " end"


# --- F1 / SIF-1: escaped carriers ---------------------------------------------


@pytest.mark.parametrize("escape", [e[1] for e in ESCAPES], ids=[e[0] for e in ESCAPES])
def test_f1_every_family_matches_after_an_escape(escape):
    for index, (name, value) in enumerate(anchored()):
        text = "upstream said:" + escape + value + escape + "next"
        result = cs.scrub_text(text)
        assert "FAKE" not in result, (index, name)
        assert name in cs.count_matches(text), (index, name)


def test_f1_json_and_repr_escaped_carriers():
    for index, (name, value) in enumerate(anchored(seed=3)):
        carriers = (
            json.dumps({"detail": "upstream said:\n" + value}),
            repr("upstream said:\n" + value),
            repr(("frame", "line\t" + value)),
            json.dumps(json.dumps({"detail": "upstream said:\r\n" + value})),
        )
        for carrier in carriers:
            result = cs.scrub_text(carrier)
            assert "FAKE" not in result, (index, name)
            assert name in cs.count_matches(carrier), (index, name)


def test_f1_aws_secret_after_an_escape_beside_its_key_id():
    # R-LIT: synthetic, runtime-built
    key_id = j("AK", "IA") + tail(16, UPPER)
    secret = tail(40, B64, 2)
    for label, escape in ESCAPES:
        for text in (key_id + escape + secret + escape, secret + escape + key_id):
            result = cs.scrub_text(text)
            assert "FAKE" not in result, label
            assert D4_NAME in cs.count_matches(text), label


def test_f1_query_credential_after_an_escaped_ampersand():
    sig = tail(44)  # R-LIT: synthetic, runtime-built
    for separator in (BS + "u" + "0026", "&" + "amp;"):
        text = (
            "https://acct.blob.example.test/c?sv=2024"
            + separator
            + "sig"
            + "="
            + sig
            + BS
            + '"'
        )
        result = cs.scrub_text(text)
        assert "FAKE" not in result
        assert cs.count_matches(text) == {"query-credential": 1}


def test_f1_sentry_repr_frame_vars():
    # R-LIT: synthetic, runtime-built
    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)
    token = j("gh", "p_") + tail(36)
    frame = {
        "function": "call",
        "vars": {
            "body": repr("upstream said:\n" + key),
            "lines": repr(["first", "\t" + token]),
        },
    }
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad",
                    "stacktrace": {"frames": [frame]},
                }
            ]
        }
    }
    result = cs.scrub_sentry_event(event, {})
    rendered = repr(result)
    assert "FAKE" not in rendered
    assert "[hidden: " in rendered


# --- F3 / SIF-3, C3: the value-shape header grammar ---------------------------

F3_TERMINATORS = (
    ("end", ""),
    ("td", "</td>"),
    ("paren", ")"),
    ("bracket", "]"),
    ("pipe", "|"),
    ("bang", "!"),
    ("colon", ":"),
    ("open-paren", "("),
    ("gt", ">"),
    ("words", " and more"),
)


def f3_values(seed=0):
    """(family, value) whose value shape alone must trigger the family."""
    # R-LIT: synthetic, runtime-built
    return [
        ("authorization-header", HDR_AUTH + ": Basic " + basic_credential(seed)),
        ("authorization-header", HDR_AUTH + ": Token " + tail(20, ALNUM, seed)),
        (
            "authorization-header",
            HDR_AUTH + ": " + AUTH_SCHEME + " " + tail(12, TOKEN68, seed),
        ),
        ("authorization-header", HDR_AUTH + ": " + tail(80, LETTERS, seed)),
        ("authorization-header", HDR_PROXY_AUTH + ": " + tail(24, ALNUM, seed)),
        ("api-key-header", HDR_XAK + ": " + tail(80, LETTERS, seed)),
        ("api-key-header", "x-goog-" + "api-key" + "=" + tail(39, URLSAFE, seed)),
    ]


@pytest.mark.parametrize(
    "terminator", [t[1] for t in F3_TERMINATORS], ids=[t[0] for t in F3_TERMINATORS]
)
def test_f3_value_shape_masks_whatever_follows(terminator):
    for index, (name, value) in enumerate(f3_values()):
        text = "<td>" + value + terminator
        result = cs.scrub_text(text)
        assert "FAKE" not in result, (index, name)
        assert name in cs.count_matches(text), (index, name)


def test_f3_digest_parameters_are_masked():
    # R-LIT: synthetic, runtime-built
    nonce, response = tail(24, ALNUM, 1), tail(32, ALNUM, 2)
    value = (
        "Digest "
        + "username"
        + '="svc", realm="api", nonce="'
        + nonce
        + '", uri="/v1", response="'
        + response
        + '"'
    )
    for text in (
        HDR_AUTH + ": " + value,
        json.dumps({HDR_AUTH: value}),
        repr({HDR_AUTH: value}),
    ):
        result = cs.scrub_text(text)
        assert "FAKE" not in result
        assert "authorization-header" in cs.count_matches(text)


def test_f3_code_shaped_values_are_deliberately_masked():
    lines = [
        # accounts/authentication.py:764 (docstring) and :380 (assignment),
        # the two owned-base lines r1 cited, then other code shapes.
        "        "
        + HDR_AUTH
        + ": Basic base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)",
        "        " + FIELD_AK + ' = request.headers.get("X-Api-Key")',
        "            org_" + FIELD_AK + " = authenticate_" + FIELD_AK + "(",
        FIELD_AK + " = secrets.token_urlsafe(32)",
        FIELD_AK + " = self._" + FIELD_AK,
        FIELD_AK + " = settings.OPENAI_API_KEY",
        HDR_AUTH.lower() + ": str | None = None",
        HDR_AUTH.lower() + ' = request.META.get("HTTP_' + HDR_AUTH.upper() + '", "")',
    ]
    for index, line in enumerate(lines):
        assert cs.count_matches(line), index
        assert cs.scrub_text(line) != line, index


# --- F4 / SIF-4, C10: lists and pairs under credential names -------------------


def test_f4_credential_keys_mask_lists_tuples_and_header_pairs():
    # R-LIT: synthetic, runtime-built
    basic = "Basic " + basic_credential()
    raw = tail(20)
    value = {
        HDR_AUTH: [basic],
        FIELD_AK: (raw, raw + "x"),
        "headers": [(HDR_AUTH.lower(), basic), ("accept", "application/json")],
        "scope": {
            "headers": [
                (HDR_AUTH.lower().encode(), basic.encode()),
                (b"host", b"api.example.test"),
            ]
        },
        "multi": {HDR_XAK: {raw}},
    }
    snapshot = copy.deepcopy(value)
    result = cs.scrub_value(value)
    assert value == snapshot
    assert result[HDR_AUTH] == [CREDENTIAL_FIELD]
    assert result[FIELD_AK] == (CREDENTIAL_FIELD, CREDENTIAL_FIELD)
    assert result["headers"] == [
        (HDR_AUTH.lower(), CREDENTIAL_FIELD),
        ("accept", "application/json"),
    ]
    assert result["scope"]["headers"] == [
        (HDR_AUTH.lower().encode(), CREDENTIAL_FIELD.encode()),
        (b"host", b"api.example.test"),
    ]
    assert result["multi"][HDR_XAK] == {CREDENTIAL_FIELD}
    assert "FAKE" not in repr(result)


def test_f4_pairs_are_pattern_only_without_mask_credential_keys():
    raw = tail(20)  # R-LIT: synthetic, runtime-built
    value = {"headers": [(HDR_XAK, raw)], HDR_AUTH: [raw]}
    assert cs.scrub_value(value, mask_credential_keys=False) is value


# --- F5 / SIF-5: scrub before truncation --------------------------------------


def test_f5_upstream_body_is_scrubbed_before_truncation():
    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built
    # The 512-character cut falls `inside` characters into the key; the rest
    # is long enough that the scrubbed body is still cut.
    rest = " " + "y" * 100
    for inside in (1, 4, 18, 39):
        start = 512 - inside
        for separator in (" ", BS + "n"):
            body = "x" * (start - len(separator)) + separator + key + rest
            out = cs.redact_upstream_body(body)
            assert out.endswith(" [truncated]"), inside
            assert "FAKE" not in out, inside
            assert j("gh", "p_") not in out, inside


# --- F6 / C1: a password with an unescaped "@" ---------------------------------


def test_f6_url_password_with_an_unescaped_at():
    # R-LIT: synthetic, runtime-built
    first, second = tail(12), tail(16, ALNUM, 1)
    for scheme in ("postgres", "redis", "mongodb+srv", "https"):
        text = j(scheme, "://admin:") + first + "@" + second + "@db.internal:5432/app"
        expected = (
            j(scheme, "://admin:")
            + m("url-embedded-credentials")
            + "@db.internal:5432/app"
        )
        assert cs.scrub_text(text) == expected, scheme
    compact = json.dumps(
        {
            "url": j("redis", "://u:") + first + "@cache:6379",
            "owner": "ops@example.test",
        },
        separators=(",", ":"),
    )
    expected = json.dumps(
        {
            "url": j("redis", "://u:") + m("url-embedded-credentials") + "@cache:6379",
            "owner": "ops@example.test",
        },
        separators=(",", ":"),
    )
    assert cs.scrub_text(compact) == expected


# --- identity, bytes, robustness ----------------------------------------------


def test_identity_fast_path():
    text = "an ordinary log line with nothing secret in it"
    assert cs.scrub_text(text) is text
    data = b"an ordinary payload"
    assert cs.scrub_bytes(data) is data
    value = {"event": "done", "items": [1, "two", (3, "four")], "flag": True}
    assert cs.scrub_value(value) is value
    assert cs.scrub_text(None) is None


def test_scrub_bytes_round_trips_undecodable_bytes():
    # r5: a non-BOM invalid UTF-8 prefix retains the lossless compatibility case.
    # BOM-marked inputs now have their own strict, fail-closed regressions.
    key = j("gh", "p_") + tail(36)  # synthetic test value (fi-security e126d4a6)
    raw = b"\xff\x80 prefix " + key.encode() + b" \x80 suffix"
    assert (
        cs.scrub_bytes(raw)
        == b"\xff\x80 prefix " + m("github-token").encode() + b" \x80 suffix"
    )


def test_scrub_text_handles_str_subclasses_and_surrogates():
    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built

    class Hostile(str):
        def __contains__(self, item):
            raise RuntimeError("no")

        def lower(self):
            raise RuntimeError("no")

    assert cs.scrub_text(Hostile("x " + key)) == "x " + m("github-token")
    assert cs.scrub_text("\udc80 " + key) == "\udc80 " + m("github-token")
    plain = Hostile("nothing")
    assert cs.scrub_text(plain) is plain


def test_f7_sif9_bytearray_and_memoryview_carriers():
    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built
    raw = ("body " + key).encode()
    expected = ("body " + m("github-token")).encode()
    scrubbed = cs.scrub_bytes(bytearray(raw))
    assert isinstance(scrubbed, bytearray)
    assert bytes(scrubbed) == expected
    assert bytes(cs.scrub_bytes(memoryview(raw))) == expected
    value = {"body": bytearray(raw), "view": memoryview(raw), "items": [bytearray(raw)]}
    result = cs.scrub_value(value)
    assert bytes(result["body"]) == expected
    assert bytes(result["view"]) == expected
    assert bytes(result["items"][0]) == expected
    assert cs.count_matches(bytearray(raw)) == {"github-token": 1}
    plain = bytearray(b"nothing here")
    assert cs.scrub_value(plain) is plain
    assert cs.scrub_bytes(plain) is plain


def test_f7_sif8_marker_prefixed_values_are_still_masked():
    # R-LIT: synthetic, runtime-built
    prefix = "[hidden: x]"
    basic = "Basic " + basic_credential()
    texts = [
        json.dumps({FIELD_AK: prefix + tail(32)}),
        json.dumps({HDR_AUTH: prefix + " " + basic}),
        HDR_AUTH + ": " + prefix + " " + basic,
        "https://x.test/v1?" + "key" + "=" + prefix + tail(24),
    ]
    for index, text in enumerate(texts):
        assert "FAKE" not in cs.scrub_text(text), index
        assert cs.count_matches(text), index


# --- scrub_value --------------------------------------------------------------


def test_scrub_value_deep_without_mutating_the_input():
    # R-LIT: synthetic, runtime-built
    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)
    token = tail(32, TOKEN68, 1)
    data = {
        "headers": {HDR_AUTH: AUTH_SCHEME + " " + token, "Accept": "application/json"},
        "items": [key, (key, 1), {key}, frozenset({key}), b"raw " + key.encode()],
        FIELD_AK: "short",
        key: "dict keys are text too",
        "count": 3,
        "empty": {FIELD_AK: ""},
    }
    snapshot = copy.deepcopy(data)
    result = cs.scrub_value(data)
    assert data == snapshot
    assert result is not data
    assert result["headers"][HDR_AUTH] == CREDENTIAL_FIELD
    assert result["headers"]["Accept"] == "application/json"
    assert result[FIELD_AK] == CREDENTIAL_FIELD
    assert result["empty"] == {FIELD_AK: ""}
    assert result[m("anthropic-key")] == "dict keys are text too"
    items = result["items"]
    assert items[0] == m("anthropic-key")
    assert items[1] == (m("anthropic-key"), 1)
    assert items[2] == {m("anthropic-key")}
    assert items[3] == frozenset({m("anthropic-key")})
    assert items[4] == b"raw " + m("anthropic-key").encode()
    assert result["count"] == 3
    assert "FAKE" not in repr(result)


def test_c5_scrubbed_keys_never_merge():
    # R-LIT: synthetic, runtime-built
    first = "sk-" + tail(40, URLSAFE)
    second = "sk-" + tail(40, URLSAFE, 1)
    marker = m("sk-generic")
    result = cs.scrub_value({first: 1, second: 2, marker: 3})
    assert len(result) == 3
    assert set(result) == {marker, marker + "#2", marker + "#3"}
    assert result[marker] == 3
    assert sorted(result.values()) == [1, 2, 3]
    assert "FAKE" not in repr(result)


def test_scrub_value_credential_names():
    names = (
        "client_secret",
        "refresh_token",
        "password",
        "Secret" + "AccessKey",
        "X-Portkey-Config",
        "proxy-" + HDR_AUTH.lower(),
    )
    value = dict.fromkeys(names, "plain-value")
    value["bytes_" + FIELD_AK] = b"plain"
    result = cs.scrub_value(value)
    for name in names:
        assert result[name] == CREDENTIAL_FIELD, name
    assert result["bytes_" + FIELD_AK] == CREDENTIAL_FIELD.encode()


def test_f7_sif6_header_rule_import_failure_fails_closed_and_is_not_cached(monkeypatch):
    module_name = "agentcc.services.credential_headers"
    real = importlib.import_module(module_name)
    names = (
        "x-secret-key",
        "token",
        "X-Portkey-Config",
        "Idempotency-Key",
        "token_count",
    )
    value = dict.fromkeys(names, "plain-value")
    value["event"] = "kept"

    monkeypatch.setitem(sys.modules, module_name, None)
    monkeypatch.setattr(cs, "_header_rule", None, raising=False)
    monkeypatch.setattr(cs, "_header_rule_retry_at", 0.0, raising=False)
    cs._cached_names_a_credential.cache_clear()
    try:
        failed = cs.scrub_value(value)
        for name in names:
            assert failed[name] == CREDENTIAL_FIELD, name
        assert failed["event"] == "kept"
        assert cs._cached_names_a_credential.cache_info().currsize == 0

        # Importable again: the gateway's rule is used; nothing from the
        # failed period was kept as an answer.
        monkeypatch.setitem(sys.modules, module_name, real)
        monkeypatch.setattr(cs, "_header_rule_retry_at", 0.0, raising=False)
        recovered = cs.scrub_value(value)
        assert recovered["x-secret-key"] == CREDENTIAL_FIELD
        assert recovered["Idempotency-Key"] == "plain-value"
        assert recovered["token_count"] == "plain-value"
    finally:
        cs._cached_names_a_credential.cache_clear()


def test_f7_sif6_a_raising_header_rule_reads_as_a_credential(monkeypatch):
    def broken(name):
        raise RuntimeError("rule failed")

    monkeypatch.setattr(cs, "_header_rule", broken)
    cs._cached_names_a_credential.cache_clear()
    try:
        assert (
            cs.scrub_value({"note_field": "plain-value"})["note_field"]
            == CREDENTIAL_FIELD
        )
    finally:
        cs._cached_names_a_credential.cache_clear()


def test_scrub_value_pattern_only_mode_keeps_field_values_readable():
    token = tail(32, TOKEN68, 1)  # R-LIT: synthetic, runtime-built
    value = {
        FIELD_AK: "must be at most 64 characters",
        "headers": {HDR_AUTH: AUTH_SCHEME + " " + token},
    }
    result = cs.scrub_value(value, mask_credential_keys=False)
    assert result[FIELD_AK] == "must be at most 64 characters"
    assert result["headers"][HDR_AUTH] == AUTH_SCHEME + " " + m("bearer-token")


def test_scrub_value_is_cycle_safe():
    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built
    loop = [key]
    loop.append(loop)
    result = cs.scrub_value(loop)
    assert result[0] == m("github-token")
    assert "FAKE" not in repr(result)

    mapping = {"key": key}
    mapping["self"] = mapping
    assert "FAKE" not in repr(cs.scrub_value(mapping))

    # 2**64 paths through 64 shared levels: only a per-object walk finishes.
    shared = [key]
    for _ in range(64):
        shared = [shared, shared]
    started = time.perf_counter()
    result = cs.scrub_value(shared)
    assert time.perf_counter() - started < BUDGET_SECONDS
    node = result
    while isinstance(node, list):
        node = node[0]
    assert node == DEPTH


def test_scrub_value_is_depth_bounded():
    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built
    deep = key
    for _ in range(40):
        deep = [deep]
    result = repr(cs.scrub_value(deep))
    assert DEPTH in result
    assert "FAKE" not in result

    shallow = key
    for _ in range(10):
        shallow = [shallow]
    result = cs.scrub_value(shallow)
    for _ in range(10):
        result = result[0]
    assert result == m("github-token")


def test_scrub_value_passes_other_scalars_unchanged():
    marker_object = object()
    buffer = bytearray(b"x")
    for value in (marker_object, buffer, 5, 2.5, None, True):
        assert cs.scrub_value(value) is value


# --- the structlog processor --------------------------------------------------


def test_structlog_processor_scrubs_fields_and_keeps_internal_identity():
    key = j("sk-", "proj-") + tail(48, URLSAFE)  # R-LIT: synthetic, runtime-built
    record = object()
    event = {
        "event": "upstream said " + key,
        FIELD_AK: "plain",
        "exception": "Traceback (most recent call last):\nValueError: " + key,
        "count": 2,
        "_record": record,
        "_from_structlog": True,
    }
    result = cs.scrub_credentials_event(None, "error", event)
    assert result["_record"] is record
    assert result["_from_structlog"] is True
    assert result["event"] == "upstream said " + m("openai-key")
    assert result[FIELD_AK] == CREDENTIAL_FIELD
    assert result["exception"].endswith("ValueError: " + m("openai-key"))
    assert result["count"] == 2

    ordinary = {"event": "fine", "_record": record, "count": 1}
    assert cs.scrub_credentials_event(None, "info", ordinary) is ordinary


# --- Sentry -------------------------------------------------------------------


def test_sentry_event_scrub():
    # R-LIT: synthetic, runtime-built
    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)
    token = tail(32, TOKEN68, 1)
    session = tail(32, ALNUM, 2)
    url = j("https", "://svc:") + tail(20, ALNUM, 3) + "@api.example.test/v1"
    event = {
        "event_id": "0123456789abcdef0123456789abcdef",
        "level": "error",
        "message": "failed with " + key,
        "logentry": {"message": "failed %s", "params": [key]},
        "request": {
            "url": "https://api.example.test/v1/models",
            "headers": {
                HDR_AUTH: AUTH_SCHEME + " " + token,
                "Content-Type": "application/json",
            },
            "data": {"prompt": "hello", FIELD_AK: token},
            "cookies": {"sessionid": session},
            "query_string": "key=" + token + "&page=2",
        },
        "exception": {
            "values": [
                {
                    "type": "GatewayClientError",
                    "value": "Gateway returned 400: " + key,
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "call",
                                "vars": {
                                    FIELD_AK: "'" + token + "'",
                                    "headers": {HDR_XAK: token},
                                },
                            }
                        ]
                    },
                }
            ]
        },
        "breadcrumbs": {"values": [{"message": "POST " + url, "data": {"url": url}}]},
        "extra": {"note": "provider said " + key},
        "contexts": {
            "upstream": {"body": "echo " + HDR_AUTH + ": " + AUTH_SCHEME + " " + token}
        },
        "tags": {"provider": key},
    }
    snapshot = copy.deepcopy(event)
    result = cs.scrub_sentry_event(event, {})
    assert result is not None
    rendered = repr(result)
    for secret in (key, token, session, tail(20, ALNUM, 3)):
        assert secret not in rendered
    assert "FAKE" not in rendered
    assert "[hidden: " in rendered
    assert result["event_id"] == snapshot["event_id"]
    assert result["level"] == "error"
    assert result["request"]["headers"]["Content-Type"] == "application/json"
    assert result["request"]["query_string"].endswith("&page=2")
    assert event == snapshot

    ordinary = {"message": "nothing to hide", "level": "warning"}
    assert cs.scrub_sentry_event(ordinary, {}) == ordinary


# --- upstream bodies ----------------------------------------------------------


def test_redact_upstream_body():
    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)  # R-LIT: synthetic, runtime-built
    assert cs.redact_upstream_body(None) == ""
    assert cs.redact_upstream_body(42) == ""
    assert cs.redact_upstream_body(b"bytes") == ""
    assert cs.redact_upstream_body("plain") == "plain"
    assert cs.redact_upstream_body("upstream " + key) == "upstream " + m(
        "anthropic-key"
    )
    assert cs.redact_upstream_body("x" * 600) == "x" * 512 + " [truncated]"
    assert cs.redact_upstream_body("y" * 20, limit=10) == "y" * 10 + " [truncated]"
    # Scrubbed before truncation: not even the key's prefix survives the cut.
    out = cs.redact_upstream_body("a" * 500 + " " + key)
    assert out.endswith(" [truncated]")
    assert key[:12] not in out
    assert "FAKE" not in out


# --- the error-response middleware --------------------------------------------


def _middleware_result(response):
    from django.test import RequestFactory

    middleware = cs.CredentialScrubErrorResponseMiddleware(lambda request: response)
    return middleware(RequestFactory().get("/"))


def _decoded_body(response):
    coding = (response.get("Content-Encoding") or "").strip().lower()
    if coding == "gzip":
        return gzip.decompress(response.content)
    if coding == "deflate":
        return zlib.decompress(response.content)
    assert coding in ("", "identity"), coding
    return response.content


def _brotli_codec():
    for name in ("brotli", "brotlicffi"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/problem+json",
        "text/plain; charset=utf-8",
        "text/html",
        "application/xml",
    ],
)
def test_middleware_scrubs_error_bodies_and_updates_content_length(content_type):
    from django.http import HttpResponse

    # R-LIT: synthetic, runtime-built
    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)
    token = tail(32, TOKEN68, 1)
    body = json.dumps({"error": {"message": "upstream rejected " + key}}).encode()
    response = HttpResponse(body, status=400, content_type=content_type)
    response["Content-Length"] = str(len(body))
    response["X-Upstream-Echo"] = AUTH_SCHEME + " " + token
    result = _middleware_result(response)
    assert result.status_code == 400
    assert key.encode() not in result.content
    assert b"FAKE" not in result.content
    assert m("anthropic-key").encode() in result.content
    assert int(result["Content-Length"]) == len(result.content)
    assert token not in result["X-Upstream-Echo"]
    if content_type.endswith("json"):
        json.loads(result.content)


def test_middleware_leaves_other_content_types_bodies_alone():
    from django.http import HttpResponse

    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)  # R-LIT: synthetic, runtime-built
    body = b"\x00binary " + key.encode()
    response = HttpResponse(body, status=500, content_type="application/octet-stream")
    assert _middleware_result(response).content == body


@pytest.mark.parametrize("status", [200, 201, 302])
def test_middleware_never_changes_2xx_or_3xx(status):
    from django.http import HttpResponse

    # A one-time reveal endpoint legitimately returns a new gateway key.
    key = j("sk-", "agentcc-") + tail(40, URLSAFE)  # R-LIT: synthetic, runtime-built
    body = json.dumps({"key": key}).encode()
    response = HttpResponse(body, status=status, content_type="application/json")
    response["X-Echo"] = AUTH_SCHEME + " " + tail(32, TOKEN68, 1)
    echo = response["X-Echo"]
    result = _middleware_result(response)
    assert result.content == body
    assert result["X-Echo"] == echo


def test_middleware_never_consumes_streaming_bodies():
    from django.http import StreamingHttpResponse

    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)  # R-LIT: synthetic, runtime-built
    chunks = [b"chunk ", key.encode()]
    response = StreamingHttpResponse(
        iter(chunks), status=500, content_type="text/plain"
    )
    result = _middleware_result(response)
    assert result.streaming
    assert b"".join(result.streaming_content) == b"".join(chunks)


def test_c7_streaming_error_responses_are_never_rewritten():
    from django.http import StreamingHttpResponse

    token = tail(32, TOKEN68, 1)  # R-LIT: synthetic, runtime-built
    response = StreamingHttpResponse(
        iter([b"chunk"]), status=500, content_type="text/plain"
    )
    response["X-Echo"] = AUTH_SCHEME + " " + token
    echo = response["X-Echo"]
    result = _middleware_result(response)
    assert result["X-Echo"] == echo
    assert b"".join(result.streaming_content) == b"chunk"


async def test_middleware_async_path():
    from django.http import HttpResponse
    from django.test import RequestFactory

    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built
    response = HttpResponse(
        ("bad " + key).encode(), status=502, content_type="text/plain"
    )

    async def get_response(request):
        return response

    middleware = cs.CredentialScrubErrorResponseMiddleware(get_response)
    result = await middleware(RequestFactory().get("/"))
    assert result.content == ("bad " + m("github-token")).encode()


def test_f1_middleware_scrubs_json_bodies_with_escaped_newlines():
    from django.http import HttpResponse

    # R-LIT: synthetic, runtime-built
    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)
    echo = "Host: gw\r\n" + HDR_AUTH + ": Basic " + basic_credential()
    body = json.dumps({"detail": "upstream said:\n" + key, "echo": echo}).encode()
    response = HttpResponse(body, status=502, content_type="application/json")
    response["Content-Length"] = str(len(body))
    result = _middleware_result(response)
    assert b"FAKE" not in result.content
    assert m("anthropic-key").encode() in result.content
    assert m("authorization-header").encode() in result.content
    assert int(result["Content-Length"]) == len(result.content)
    json.loads(result.content)


@pytest.mark.parametrize("coding", ["gzip", "deflate"])
def test_f2_encoded_error_bodies_are_decoded_scrubbed_and_reframed(coding):
    from django.http import HttpResponse

    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)  # R-LIT: synthetic, runtime-built
    body = json.dumps({"error": {"message": "upstream rejected " + key}}).encode()
    encoded = gzip.compress(body) if coding == "gzip" else zlib.compress(body)
    response = HttpResponse(encoded, status=502, content_type="application/json")
    response["Content-Encoding"] = coding
    response["Content-Length"] = str(len(encoded))
    response["ETag"] = '"' + hashlib.sha1(body).hexdigest() + '"'
    result = _middleware_result(response)
    plain = _decoded_body(result)
    assert key.encode() not in plain
    assert b"FAKE" not in plain
    assert m("anthropic-key").encode() in plain
    json.loads(plain)
    assert int(result["Content-Length"]) == len(result.content)
    assert not result.has_header("ETag")


def test_f2_encoded_body_without_credentials_is_left_encoded():
    from django.http import HttpResponse

    body = json.dumps({"error": {"message": "quota exceeded, retry later"}}).encode()
    encoded = gzip.compress(body)
    response = HttpResponse(encoded, status=429, content_type="application/json")
    response["Content-Encoding"] = "gzip"
    response["ETag"] = '"v1"'
    result = _middleware_result(response)
    assert result.content == encoded
    assert result["Content-Encoding"] == "gzip"
    assert result["ETag"] == '"v1"'


@pytest.mark.parametrize("coding", ["compress", "gzip", "x-unknown", "gzip, compress"])
def test_f2_undecodable_encodings_fail_closed(coding):
    from django.http import HttpResponse

    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)  # R-LIT: synthetic, runtime-built
    # Not a valid stream for any coding listed: the plain text is labelled.
    body = ("upstream rejected " + key).encode()
    response = HttpResponse(body, status=500, content_type="application/json")
    response["Content-Encoding"] = coding
    response["Content-Length"] = str(len(body))
    result = _middleware_result(response)
    assert result.status_code == 500
    assert result.content == SCRUB_FAILED
    assert int(result["Content-Length"]) == len(SCRUB_FAILED)
    assert not result.has_header("Content-Encoding")
    assert result["Content-Type"].startswith("text/plain")


def test_f2_brotli_decoded_when_available_else_failed_closed():
    from django.http import HttpResponse

    key = j("sk-ant-", "api03-") + tail(95, URLSAFE)  # R-LIT: synthetic, runtime-built
    body = json.dumps({"error": {"message": "upstream rejected " + key}}).encode()
    codec = _brotli_codec()
    payload = codec.compress(body) if codec is not None else body
    response = HttpResponse(payload, status=502, content_type="application/json")
    response["Content-Encoding"] = "br"
    response["Content-Length"] = str(len(payload))
    result = _middleware_result(response)
    assert int(result["Content-Length"]) == len(result.content)
    if codec is None:
        assert result.content == SCRUB_FAILED
    else:
        plain = (
            result.content
            if not result.has_header("Content-Encoding")
            else codec.decompress(result.content)
        )
        assert key.encode() not in plain
        assert m("anthropic-key").encode() in plain


def test_f2_failure_path_replaces_body_and_fixes_content_length():
    from django.http import HttpResponse

    class Unreadable(HttpResponse):
        failures = 1

        @property
        def content(self):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("unreadable body")
            return HttpResponse.content.fget(self)

        @content.setter
        def content(self, value):
            HttpResponse.content.fset(self, value)

    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built
    body = ("bad " + key).encode()
    response = Unreadable(body, status=500, content_type="text/plain")
    response["Content-Length"] = str(len(body))
    result = _middleware_result(response)
    assert result.content == SCRUB_FAILED
    assert int(result["Content-Length"]) == len(SCRUB_FAILED)


def test_c8_body_validators_are_dropped_after_a_rewrite():
    from django.http import HttpResponse

    key = j("gh", "p_") + tail(36)  # R-LIT: synthetic, runtime-built
    response = HttpResponse(
        ("bad " + key).encode(), status=400, content_type="text/plain"
    )
    response["ETag"] = '"v1"'
    response["Content-MD5"] = "placeholder-digest"
    response["Digest"] = "sha-256=placeholder"
    result = _middleware_result(response)
    assert result.content == ("bad " + m("github-token")).encode()
    for name in ("ETag", "Content-MD5", "Digest"):
        assert not result.has_header(name), name


# --- r3: name-only masking; no scheme, token length or code exemptions ---------

R3_NAMES = (
    HDR_AUTH,
    HDR_PROXY_AUTH,
    HDR_XAK,
    "api" + "-key",
    "x-goog-" + "api-key",
    "anthropic-" + "api-key",
    "openai-" + "api-key",
)


def r3_sinks(text):
    """All six text-bearing public sinks, including the real middleware code."""

    class Response:
        status_code = 502
        streaming = False

        def __init__(self):
            self.content = text.encode()
            self.headers = {
                "Content-Type": "application/json",
                "Content-Length": str(len(self.content)),
            }

    response = cs.CredentialScrubErrorResponseMiddleware(lambda request: Response())(
        None
    )
    assert int(response.headers["Content-Length"]) == len(response.content)
    return (
        cs.scrub_text(text),
        bytes(cs.scrub_bytes(text.encode())).decode(),
        cs.scrub_sentry_event({"message": text})["message"],
        cs.scrub_credentials_event(None, "error", {"event": text})["event"],
        cs.redact_upstream_body(text, limit=len(text) + 4096),
        response.content.decode(),
    )


@pytest.mark.parametrize("name", R3_NAMES)
def test_r3_any_value_shape_and_case(name):
    # R-LIT: synthetic, runtime-built. No provider-shaped literal is persisted.
    values = [
        tail(5),
        j("fake", "_secret"),
        j("fake", ".value"),
        j("fake", "(call)"),
        tail(24) + BS + "/" + tail(6),
    ]
    for spelling in (name, name.lower(), name.upper(), name.swapcase()):
        for scheme in ("", AUTH_SCHEME + " ", "Basic ", "Token ", "Digest ", "Other "):
            for value in values:
                credential = scheme + value
                for text in (
                    spelling + ": " + credential,
                    spelling + "=" + credential,
                    json.dumps({spelling: credential}),
                    repr({spelling: credential}),
                    json.dumps(
                        {"event": spelling + ": " + credential, "level": "info"}
                    ),
                ):
                    safe = cs.scrub_text(text)
                    assert (
                        value not in safe and "FAKE" not in safe and "fake" not in safe
                    )
                    assert cs.scrub_text(safe) == safe and not cs.count_matches(safe)
                    if text.startswith('{"'):
                        assert isinstance(json.loads(safe), dict)


@pytest.mark.parametrize("leading", [",", ":", "}", "]", " :", " ,", " ]", " }"])
def test_r3_digest_punctuation_never_terminates_mask(leading):
    # R-LIT: synthetic, runtime-built; the hex response is explicitly FAKE-labelled.
    nonce = tail(24)
    response = hashlib.md5(("FAKE" + leading).encode()).hexdigest()
    value = (
        'Digest username="'
        + leading
        + 'svc", realm="'
        + leading
        + 'realm", nonce="'
        + nonce
        + '", response="'
        + response
        + '"'
    )
    for text in (
        HDR_AUTH + ": " + value,
        json.dumps({"event": HDR_AUTH + ": " + value, "level": "info"}),
        json.dumps({HDR_AUTH: value}),
        repr({HDR_AUTH: value}),
    ):
        for safe in r3_sinks(text):
            assert nonce not in safe and response not in safe
            assert cs.scrub_text(safe) == safe
            if text.startswith('{"'):
                json.loads(safe)


@pytest.mark.parametrize("depth", [1, 2, 3, 5])
def test_r3_list_trailing_backslash_and_pretty_layers(depth):
    # R-LIT: synthetic, runtime-built
    for name in R3_NAMES:
        for indent in (None, 0, 2, 4):
            for count in (1, 2, 3, 4, 8):
                value = {
                    name: [tail(16) + BS * count, j("fake", "_short"), "", [tail(5)]],
                    "accept": ["json"],
                }
                text = json.dumps(value, indent=indent)
                for _ in range(depth - 1):
                    text = json.dumps(text)
                for safe in r3_sinks(text):
                    assert "FAKE" not in safe and "fake_short" not in safe
                    decoded = safe
                    for _ in range(depth):
                        decoded = json.loads(decoded)
                    assert decoded["accept"] == ["json"]
                    assert decoded[name][2] == ""
                    assert cs.scrub_text(safe) == safe and not cs.count_matches(safe)


@pytest.mark.parametrize(
    "number", [12345678, -12345678, 1234.5678, 1.2345678e30, None, True, False]
)
def test_r3_json_scalars_never_receive_unquoted_markers(number):
    # R-LIT: synthetic, runtime-built
    for name in R3_NAMES:
        for value in (number, [number], [number, tail(5), [number, tail(6)]]):
            text = json.dumps({name: value, "count": 1}, indent=2)
            for safe in r3_sinks(text):
                parsed = json.loads(safe)
                assert parsed["count"] == 1 and "FAKE" not in safe
                if not isinstance(value, list) or len(value) == 1:
                    assert parsed[name] == value
                else:
                    assert parsed[name][0] == number and parsed[name][2][0] == number


def test_r3_incomplete_inner_fragment_keeps_outer_json():
    # R-LIT: synthetic, runtime-built
    for name in R3_NAMES:
        value = tail(20)
        for fragment in (
            name + ': ["Basic ' + value,
            name + ': "' + value,
            name + ": " + value + BS,
        ):
            text = json.dumps({"detail": fragment, "level": "info"})
            for safe in r3_sinks(text):
                assert value not in safe
                assert json.loads(safe)["level"] == "info"
                assert cs.scrub_text(safe) == safe


@pytest.mark.parametrize("indent", [None, 0, 2, 4])
def test_r3_pairs_accept_all_formatting_whitespace(indent):
    # R-LIT: synthetic, runtime-built
    for name in R3_NAMES:
        value = tail(5)
        source = [[name, [value, value + BS]], ["accept", "json"]]
        text = json.dumps(source, indent=indent)
        for depth in (1, 2, 3):
            if depth > 1:
                text = json.dumps(text)
            safe = cs.scrub_text(text)
            decoded = safe
            for _ in range(depth):
                decoded = json.loads(decoded)
            assert value not in safe and decoded[1] == ["accept", "json"]


def test_r3_encoded_names_and_separators_are_not_an_escape_hatch():
    from urllib.parse import quote

    # R-LIT: synthetic, runtime-built
    value = tail(5)
    for name in R3_NAMES:
        carriers = (
            name + ": " + value,
            json.dumps({name: value}),
            repr([(name, value)]),
        )
        for text in carriers:
            fully = "".join("%" + format(ord(char), "02x") for char in text)
            encoded_name = "".join("%" + format(ord(char), "02X") for char in name)
            forms = (
                quote(text, safe=""),
                fully,
                text.replace(name, encoded_name),
                text.replace(
                    name, "".join(BS + "u" + format(ord(char), "04x") for char in name)
                ),
            )
            for encoded in forms:
                safe = cs.scrub_text(encoded)
                assert safe != encoded and "[hidden: " in safe
                assert value not in safe
                if encoded.startswith('{"'):
                    json.loads(safe)


def test_r3_line_end_and_outer_string_boundaries():
    # R-LIT: synthetic, runtime-built
    value = tail(5)
    for name in R3_NAMES:
        for end in ("\n", "\r", "\r\n"):
            text = name + ": " + value + ", other=lost" + end + "next line kept"
            safe = cs.scrub_text(text)
            assert value not in safe and safe.endswith(end + "next line kept")
        text = json.dumps([name + ": " + value + '="inner"', "", "next"])
        safe = cs.scrub_text(text)
        assert value not in safe and json.loads(safe)[1:] == ["", "next"]


def test_r3_code_exemptions_removed_and_marker_prefix_is_not_exempt():
    # R-LIT: synthetic, runtime-built
    for prefix in ("await ", "yield ", "not ", "Basic base64(", "[hidden: x]"):
        text = FIELD_AK + " = " + prefix + j("fake", "_value")
        assert "fake" not in cs.scrub_text(text)
    assert cs.scrub_text(PYTHON_SOURCE) != PYTHON_SOURCE


D1_R3_SHAPES = (
    "encoded-names",
    "escaped-lists",
    "pretty-pairs",
    "short-values",
    "dangling-quotes",
)


def adversarial_r3(shape):
    # R-LIT: synthetic, runtime-built
    return {
        "encoded-names": lambda: repeat("%" + "41uthorization%3A" + tail(5) + "\n"),
        "escaped-lists": lambda: repeat(
            json.dumps(json.dumps({HDR_XAK: [tail(5) + BS, tail(5)]})) + "\n"
        ),
        "pretty-pairs": lambda: repeat(
            json.dumps([[HDR_AUTH, tail(5)]], indent=2) + "\n"
        ),
        "short-values": lambda: repeat(HDR_XAK + ": " + tail(5) + "\n"),
        "dangling-quotes": lambda: '"' + repeat(BS + '"'),
    }[shape]()


@pytest.mark.parametrize("shape", D1_R3_SHAPES)
def test_r3_d1_name_only_scanner_budget(shape):
    text = adversarial_r3(shape)
    started = time.perf_counter()
    assert cs.scrub_text(text) != "[hidden: scrub-failed]"
    assert time.perf_counter() - started < BUDGET_SECONDS


# --- r4: redact_known_values (ONEKEY-C2-SCRUB-R4-KNOWNVALUES) -----------------
# Contract: each known value of 8 or more characters is replaced wherever it
# occurs in data, in any per-character mix of the raw character, percent
# escapes (either hex case, "+" for a space), JSON backslash-u escapes (either
# hex case) and the short quote, backslash and slash escapes, Python/JS
# backslash-x escapes and HTML decimal or hex entities. str stays str, bytes
# stays bytes; it never raises. The function is imported lazily inside each
# test, the way callers import it. Escapes are assembled from BS and chr() at
# run time, never typed.

KNOWN = "[hidden: known-value]"
FAILED_TEXT = "[hidden: scrub-failed]"


def redact_known(*args, **kwargs):
    from tfc.logging.credential_scrub import redact_known_values

    return redact_known_values(*args, **kwargs)


def known_value(seed=0):
    """FAKE with a quote, a backslash, a space, a slash and non-ASCII text."""
    # synthetic test value (fi-security e126d4a6)
    return j(
        "FAKE", '"', BS, " /", chr(0xE9), chr(0x20AC), chr(0x1F511), "kv", str(seed)
    )


def hex_case(number, width, upper):
    return format(number, "0" + str(width) + ("X" if upper else "x"))


def pct(char, upper=True):
    return "".join("%" + hex_case(byte, 2, upper) for byte in char.encode("utf-8"))


def json_u(char, upper=False):
    """JSON backslash-u, as a surrogate pair above the BMP."""
    code = ord(char)
    units = (code,)
    if code >= 0x10000:
        units = (0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF))
    return "".join(BS + "u" + hex_case(unit, 4, upper) for unit in units)


def x_point(char, upper=False):
    """Python/JS backslash-x: the code point when it fits, else UTF-8 bytes."""
    code = ord(char)
    data = bytes((code,)) if code < 0x100 else char.encode("utf-8")
    return "".join(BS + "x" + hex_case(byte, 2, upper) for byte in data)


def x_utf8(char):
    """A bytes repr: backslash-x per UTF-8 byte."""
    return "".join(BS + "x" + hex_case(byte, 2, False) for byte in char.encode("utf-8"))


def html_dec(char):
    return "&#" + str(ord(char)) + ";"


def html_hex(char, upper=False):
    return "&#" + ("X" if upper else "x") + hex_case(ord(char), 1, upper) + ";"


def json_short(char):
    return BS + char if char in '"/' + BS else char


def plus_pct(char):
    return "+" if char == " " else pct(char)


def alternate_case(text):
    """Hex letters alternate upper and lower case (MIXED case in one escape).
    Only applied to escape text, whose letters a-f are all hex digits."""
    out, upper = [], True
    for char in text:
        if char in "abcdefABCDEF":
            char = char.upper() if upper else char.lower()
            upper = not upper
        out.append(char)
    return "".join(out)


KNOWN_FORMS = {
    "raw": lambda char: char,
    "pct-upper": lambda char: pct(char, True),
    "pct-lower": lambda char: pct(char, False),
    "pct-mixed": lambda char: alternate_case(pct(char, False)),
    "pct-plus-space": plus_pct,
    "json-u-lower": json_u,
    "json-u-upper": lambda char: json_u(char, True),
    "json-u-mixed": lambda char: alternate_case(json_u(char)),
    "json-short": json_short,
    "x-lower": x_point,
    "x-upper": lambda char: x_point(char, True),
    "x-mixed": lambda char: alternate_case(x_point(char)),
    "x-utf8": x_utf8,
    "html-dec": html_dec,
    "html-hex-lower": html_hex,
    "html-hex-upper": lambda char: html_hex(char, True),
    "html-hex-mixed": lambda char: alternate_case(html_hex(char)),
}
KNOWN_ROTATION = tuple(sorted(KNOWN_FORMS))


@pytest.mark.parametrize("form", KNOWN_ROTATION)
def test_r4_known_value_every_form_of_every_character(form):
    value = known_value()
    encoded = "".join(KNOWN_FORMS[form](char) for char in value)
    assert form == "raw" or encoded != value
    text = "lead <" + encoded + "> trail"
    assert redact_known(text, [value]) == "lead <" + KNOWN + "> trail"


def test_r4_escapes_of_alphanumerics_and_mixed_case_digits_are_covered():
    value = known_value()
    for form in (
        "json-u-lower",
        "json-u-upper",
        "json-u-mixed",
        "x-mixed",
        "pct-mixed",
    ):
        letters = "".join(KNOWN_FORMS[form](char) for char in "FAKEkv0")
        assert "F" not in letters and "k" not in letters
    mixed = alternate_case(json_u(chr(0x20AC)) + pct(chr(0xE9), False))
    assert mixed == BS + "u" + "20Ac" + "%C3%a9"
    assert (
        redact_known(alternate_case(json_u("F")) + "AKE" + value[4:], [value]) == KNOWN
    )


@pytest.mark.parametrize("offset", range(len(KNOWN_ROTATION)))
def test_r4_known_value_mixes_forms_within_one_value(offset):
    value = known_value(offset % 10)
    encoded = "".join(
        KNOWN_FORMS[KNOWN_ROTATION[(offset + index) % len(KNOWN_ROTATION)]](char)
        for index, char in enumerate(value)
    )
    text = '{"detail": "' + encoded + '", "next": 1}'
    assert redact_known(text, [value]) == '{"detail": "' + KNOWN + '", "next": 1}'


def test_r4_known_value_in_real_encoder_output():
    from html import escape
    from urllib.parse import quote, quote_plus

    value = known_value()
    for ensure_ascii in (True, False):
        carrier = json.dumps({"detail": value, "n": 1}, ensure_ascii=ensure_ascii)
        safe = redact_known(carrier, [value])
        assert json.loads(safe) == {"detail": KNOWN, "n": 1}
    assert redact_known(ascii(value), [value]) == "'" + KNOWN + "'"
    assert redact_known(repr(value.encode("utf-8")), [value]) == "b'" + KNOWN + "'"
    for carrier in (
        quote(value, safe=""),
        quote_plus(value, safe=""),
        escape(value),
        value.encode("ascii", "xmlcharrefreplace").decode("ascii"),
    ):
        assert carrier != value
        assert redact_known("v=" + carrier + ";", [value]) == "v=" + KNOWN + ";"


def test_r4_bytes_input_keeps_its_type():
    value = known_value()
    raw = value.encode("utf-8")
    data = (
        b"pre " + raw + b" mid " + value.encode("ascii", "xmlcharrefreplace") + b" end"
    )
    expected = b"pre " + KNOWN.encode() + b" mid " + KNOWN.encode() + b" end"
    for values in ([value], [raw], (raw,), {value}, [bytearray(raw)]):
        safe = redact_known(data, values)
        assert type(safe) is bytes and safe == expected
    safe = redact_known(bytearray(data), [value])
    assert type(safe) is bytearray and bytes(safe) == expected
    safe = redact_known(memoryview(data), [value])
    assert isinstance(safe, memoryview) and bytes(safe) == expected
    plain = b"pre nothing known end"
    assert redact_known(plain, [value]) is plain
    undecodable = bytes((0xFF,)) + raw + bytes((0xFE,))
    safe = redact_known(undecodable, [value])
    assert safe == bytes((0xFF,)) + KNOWN.encode() + bytes((0xFE,))


def test_r4_values_shorter_than_eight_are_ignored():
    # synthetic test value (fi-security e126d4a6)
    seven, eight = "FAKE" + "ab7", "FAKE" + "abc8"
    text = "a " + seven + " b " + eight + " c"
    assert redact_known(text, [seven, eight]) == "a " + seven + " b " + KNOWN + " c"
    assert redact_known(text, [seven]) is text
    assert redact_known(text, ["", None]) is text
    # synthetic test value (fi-security e126d4a6)
    wide = "FAKE" + chr(0xE9) * 3
    assert len(wide) == 7 and len(wide.encode("utf-8")) == 10
    carrier = "x " + wide
    assert redact_known(carrier, [wide]) is carrier


def test_r4_identity_and_non_text_inputs():
    value = known_value()
    text = "nothing known here"
    assert redact_known(text, [value]) is text
    assert redact_known(text, []) is text
    assert redact_known(text, None) is text
    # dict/list recursion is NOT part of this contract: non-text passes as is.
    for other in (None, 5, 2.5, [value], {"k": value}, (value,)):
        assert redact_known(other, [value]) is other

    class Text(str):
        pass

    safe = redact_known(Text("a " + value + " b"), [value])
    assert type(safe) is str and safe == "a " + KNOWN + " b"


def test_r4_a_lone_text_value_is_one_value_not_characters():
    value = known_value()
    text = "a " + value + " b"
    assert redact_known(text, value) == "a " + KNOWN + " b"
    assert redact_known(text, value.encode("utf-8")) == "a " + KNOWN + " b"


def test_r4_marker_duplicates_and_scrub_text_stability():
    first, second = known_value(1), known_value(2)
    text = "x " + first + " y " + "".join(pct(char) for char in second) + " z"
    safe = redact_known(text, [first, second, first])
    assert safe == "x " + KNOWN + " y " + KNOWN + " z"
    assert cs.scrub_text(safe) == safe
    assert redact_known(safe, [first, second]) is safe
    custom = "[hidden: tenant-key]"
    expected = "x " + custom + " y " + custom + " z"
    assert redact_known(text, [first, second], marker=custom) == expected
    data = text.encode("utf-8")
    assert redact_known(data, [first, second], marker=custom) == expected.encode()
    assert (
        redact_known(data, [first, second], marker=custom.encode()) == expected.encode()
    )


def test_r4_overlapping_occurrences_are_masked_as_one_span():
    # synthetic test value (fi-security e126d4a6)
    head, rear = "FAKE" + "overlap1", "lap1" + "FAKEtail"
    text = "<" + "FAKEoverlap1FAKEtail" + ">"
    assert redact_known(text, [head, rear]) == "<" + KNOWN + ">"
    # synthetic test value (fi-security e126d4a6)
    cyclic = "FAKE" * 3
    assert redact_known("<" + "FAKE" * 4 + ">", [cyclic]) == "<" + KNOWN + ">"
    spaced = "<" + cyclic + "|" + cyclic + ">"
    assert redact_known(spaced, [cyclic]) == "<" + KNOWN + "|" + KNOWN + ">"


def test_r4_regex_metacharacters_are_literal_and_near_misses_stay():
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE.*(+)?[x]{2}|$^"
    other = "FAKEabcdefgh"
    assert redact_known(other, [value]) is other
    assert redact_known("<" + value + ">", [value]) == "<" + KNOWN + ">"
    near = known_value()
    for miss in (near[:-1], near[1:], near.upper(), near.replace(" ", "_")):
        text = "<" + miss + ">"
        assert redact_known(text, [near]) is text


class _ExplodingValues:
    def __iter__(self):
        raise RuntimeError("synthetic failure")


def test_r4_never_raises_and_fails_closed():
    value = known_value()
    text = "a " + value + " b"
    assert redact_known(text, _ExplodingValues()) == FAILED_TEXT
    assert (
        redact_known(text.encode("utf-8"), _ExplodingValues()) == FAILED_TEXT.encode()
    )
    safe = redact_known(bytearray(text.encode("utf-8")), _ExplodingValues())
    assert type(safe) is bytearray and bytes(safe) == FAILED_TEXT.encode()
    assert redact_known(text, 12345678) == FAILED_TEXT
    assert redact_known(text, [value, 12345678]) == FAILED_TEXT
    assert redact_known(text, [value], marker=12345678) == FAILED_TEXT
    assert redact_known(text, [None, value]) == "a " + KNOWN + " b"


def test_r4_redact_known_values_is_exported():
    assert "redact_known_values" in cs.__all__


D1_R4_SHAPES = (
    "value-prefix",
    "encoded-prefix",
    "introducers",
    "cyclic",
    "backslash-run",
)


def adversarial_r4(shape, value):
    # Data shapes only: prefix repetition and escape introducers, no new value.
    return {
        "value-prefix": lambda: repeat(value[:-1] + "#"),
        "encoded-prefix": lambda: repeat(
            "".join(pct(c, False) for c in value[:-1]) + "#"
        ),
        "introducers": lambda: repeat("%" + BS + "u" + "&#" + BS + "x" + BS + "U"),
        "cyclic": lambda: repeat("FAKE"),
        "backslash-run": lambda: repeat(BS),
    }[shape]()


@pytest.mark.parametrize("shape", D1_R4_SHAPES)
def test_r4_d1_known_values_finish_inside_budget(shape):
    value = known_value()
    # synthetic test value (fi-security e126d4a6)
    cyclic = "FAKE" * 3
    text = adversarial_r4(shape, value)
    started = time.perf_counter()
    safe = redact_known(text, [value, cyclic])
    assert safe != FAILED_TEXT
    assert time.perf_counter() - started < BUDGET_SECONDS


# --- r5: encoded runs, declared charsets, structured JSON, bounded matching ---

R5_FORMS = (
    "pct-upper",
    "pct-lower",
    "pct-mixed",
    "json-u-lower",
    "json-u-upper",
    "x-lower",
    "x-upper",
    "html-dec",
    "html-hex-lower",
    "html-hex-upper",
)
R5_QUERY_NAMES = (
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
)


def r5_provider():
    # synthetic test value (fi-security e126d4a6)
    return "ghp_" + "FAKE" + "abcdefghijklmnopqrstuvwxyz012345"


@pytest.mark.parametrize("form", R5_FORMS)
def test_r5_encoded_provider_runs_all_public_sinks(form):
    value = r5_provider()
    # synthetic test value (fi-security e126d4a6)
    encoded = "".join(KNOWN_FORMS[form](char) for char in value)
    carrier = "before " + encoded + " after"
    for safe in r3_sinks(carrier):
        assert safe == "before " + m("github-token") + " after"
    assert (
        cs.scrub_value({"detail": carrier})["detail"]
        == "before " + m("github-token") + " after"
    )


def test_r5_mixed_named_entities_nested_encoding_and_whole_run():
    value = r5_provider()
    # synthetic test value (fi-security e126d4a6)
    mixed = "".join(
        KNOWN_FORMS[R5_FORMS[i % len(R5_FORMS)]](c) for i, c in enumerate(value)
    )
    for encoded in (
        mixed,
        mixed.replace("_", "&lowbar;"),
        "".join(pct(c) for c in mixed),
        value.replace("_", "&lowbar;"),
    ):
        assert (
            cs.scrub_text("before " + encoded + "%21 after")
            == "before " + m("github-token") + " after"
        )
    for family, carrier, _expected in cases():
        # synthetic test value (fi-security e126d4a6)
        encoded = "".join(pct(c) for c in carrier)
        safe = cs.scrub_text(encoded)
        assert safe != encoded and "[hidden: " in safe, family


def test_r5_encoded_provider_inside_json_keeps_json_shape():
    # synthetic test value (fi-security e126d4a6)
    encoded = "".join(json_u(c) for c in r5_provider())
    text = '{"detail":"' + encoded + '","count":1}'
    for safe in r3_sinks(text):
        assert json.loads(safe) == {"detail": m("github-token"), "count": 1}


@pytest.mark.parametrize("upper", [False, True])
def test_r5_encoded_a_prefilter_without_literal_a_all_sinks(upper):
    # synthetic test value (fi-security e126d4a6)
    value = "0123456789" * 4
    name = HDR_AUTH.upper() if upper else HDR_AUTH.lower()
    name = name.replace("A" if upper else "a", json_u("A" if upper else "a"))
    for text in (name + ": " + value, '{"' + name + '":"' + value + '"}'):
        assert "a" not in text.lower()
        for safe in r3_sinks(text):
            assert value not in safe and "[hidden: " in safe
            if text.startswith("{"):
                assert isinstance(json.loads(safe), dict)


@pytest.mark.parametrize("name", R5_QUERY_NAMES)
@pytest.mark.parametrize("separator", ["?", "#", "#?", "?ok=1&", "#ok=1&"])
def test_r5_query_and_fragment_name_decoding_and_boundaries(name, separator):
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-query-value"
    for spelling in (
        name,
        name.upper(),
        "".join(pct(c, i % 2 == 0) for i, c in enumerate(name)),
    ):
        text = (
            "https://example.invalid/cb"
            + separator
            + spelling
            + "="
            + value
            + "&next=kept#tail"
        )
        for safe in (
            cs.scrub_text(text),
            cs.scrub_bytes(text.encode()).decode(),
            cs.scrub_value(text),
        ):
            assert value not in safe and "[hidden: " in safe
            assert safe.endswith("&next=kept#tail")
    normal = "https://example.invalid/?not_" + name + "=" + value
    assert cs.scrub_text(normal) == normal


def test_r5_query_delimiters_encoded_values_and_idempotence():
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE" + "&fragment#tail"
    text = (
        "https://example.invalid/#code=" + "".join(pct(c) for c in value) + "&next=ok"
    )
    safe = cs.scrub_text(text)
    assert safe == "https://example.invalid/#code=" + m("query-credential") + "&next=ok"
    assert cs.scrub_text(safe) == safe
    # synthetic test value (fi-security e126d4a6)
    texts = [
        lead + "code" + "=" + "FAKE" + "&" + name + "=" + "FAKE"
        for lead, name in (("?", "password"), ("#", "secret"))
    ]
    for text in texts:
        safe = cs.scrub_text(text)
        assert "FAKE" not in safe and cs.scrub_text(safe) == safe


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"])
def test_r5_bom_and_explicit_charset_bytes(codec):
    import codecs

    bom = {
        "utf-16-le": codecs.BOM_UTF16_LE,
        "utf-16-be": codecs.BOM_UTF16_BE,
        "utf-32-le": codecs.BOM_UTF32_LE,
        "utf-32-be": codecs.BOM_UTF32_BE,
    }[codec]
    value = r5_provider()
    text = json.dumps({"detail": value, "ok": 1})
    for prefix in (b"", bom):
        raw = prefix + text.encode(codec)
        for kind in (bytes, bytearray, memoryview):
            data = kind(raw)
            safe = cs.scrub_bytes(data, charset=codec)
            assert type(safe) is kind
            assert json.loads(bytes(safe).decode(codec).lstrip(chr(0xFEFF))) == {
                "detail": m("github-token"),
                "ok": 1,
            }
            safe_known = redact_known(data, [value], charset=codec)
            assert type(safe_known) is kind
            assert json.loads(bytes(safe_known).decode(codec).lstrip(chr(0xFEFF))) == {
                "detail": KNOWN,
                "ok": 1,
            }
        if prefix:
            safe = cs.scrub_bytes(raw)
            assert json.loads(safe) == {"detail": m("github-token"), "ok": 1}
            assert json.loads(redact_known(raw, [value])) == {"detail": KNOWN, "ok": 1}
            assert json.loads(cs.scrub_value(raw)) == {
                "detail": m("github-token"),
                "ok": 1,
            }
        assert cs.scrub_bytes(raw, charset="utf-8") == SCRUB_FAILED
    plain = bom + "ordinary".encode(codec)
    assert cs.scrub_bytes(plain) is plain


@pytest.mark.parametrize("codec", ["utf-16", "utf-32"])
def test_r5_middleware_charset_bom_consistency_and_validators(codec):
    # synthetic test value (fi-security e126d4a6)
    value = "0123456789" * 4
    text = json.dumps({HDR_AUTH: value, "ok": 1})

    class Response:
        status_code = 502
        streaming = False

        def __init__(self, declared):
            self.content = text.encode(codec)
            self.headers = {
                "Content-Type": "application/json" + declared,
                "Content-Length": str(len(self.content)),
                "ETag": "synthetic-validator",
            }

    for declared in (
        "",
        "; charset=" + codec,
        '; charset="' + codec + '"',
        "; charset=utf-8",
    ):
        response = Response(declared)
        result = cs.CredentialScrubErrorResponseMiddleware(lambda request: response)(  # noqa: B023
            None
        )
        assert result is response and result.status_code == 502
        assert int(result.headers["Content-Length"]) == len(result.content)
        assert "ETag" not in result.headers
        if declared.endswith("utf-8"):
            assert result.content == SCRUB_FAILED
            assert result.headers["Content-Type"] == "text/plain; charset=utf-8"
        else:
            parsed = json.loads(result.content)
            assert value not in parsed[HDR_AUTH] and parsed["ok"] == 1


def test_r5_unsafe_decoding_and_invalid_keywords_never_raise():
    import codecs

    for raw, charset in (
        (codecs.BOM_UTF16_LE + b"x", None),
        (codecs.BOM_UTF32_BE + b"x", None),
        (b"abc", "utf-16-le"),
        (b"abc", "x-not-a-charset"),
        (b"abc", object()),
        (b"\xff", "utf-8"),
        (b"a\x00b\x00", None),
    ):
        assert cs.scrub_bytes(raw, charset=charset) == SCRUB_FAILED
        assert redact_known(raw, ["FAKEabcd"], charset=charset) == SCRUB_FAILED
    assert cs.scrub_bytes(None, charset="unknown") is None
    assert (
        cs.scrub_value({"nested": b"abc"}, charset="unknown")["nested"] == SCRUB_FAILED
    )
    assert cs.scrub_bytes(b"caf\xe9", charset="latin-1") == b"caf\xe9"


def test_r5_json_strings_are_scrubbed_structurally_and_stay_strings():
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-opaque-credential"
    source = {"password": value, "data": [{"client_secret": value}], "ok": 1}
    text = json.dumps(source)
    for carrier in (text, json.dumps([source]), json.dumps({"wrapped": text})):
        safe = cs.scrub_value(carrier)
        assert type(safe) is str and value not in safe
        assert isinstance(json.loads(safe), (dict, list))
        assert cs.scrub_value(safe) == safe
    parsed = json.loads(cs.scrub_value(text))
    assert parsed["ok"] == 1 and parsed["password"] == CREDENTIAL_FIELD
    assert cs.scrub_value(text, mask_credential_keys=False) is text
    assert cs.scrub_value("not JSON") == "not JSON"
    nested = '{"password":"FAKE"}'
    for _ in range(cs.MAX_DEPTH + 2):
        nested = json.dumps({"nested": nested})
        if len(nested) > 200000:
            break
    assert "FAKE" not in cs.scrub_value(nested)


@pytest.mark.parametrize(
    "tail_char,form", [(BS, json_short), ("%", pct), ("&", html_dec)]
)
def test_r5_known_value_terminal_encoded_span_is_wholly_masked(tail_char, form):
    # synthetic test value (fi-security e126d4a6)
    value = "FAKEabcd" + tail_char
    # synthetic test value (fi-security e126d4a6)
    encoded = value[:-1] + form(tail_char)
    for kind in (str, bytes):
        carrier = '"' + encoded + '"'
        data = carrier if kind is str else carrier.encode()
        safe = redact_known(data, [value])
        assert safe == (
            '"' + KNOWN + '"' if kind is str else ('"' + KNOWN + '"').encode()
        )
        assert json.loads(safe) == KNOWN
    if tail_char == BS:
        assert json.loads(
            redact_known(json.dumps({"detail": value, "ok": 1}), [value])
        ) == {"detail": KNOWN, "ok": 1}


def test_r5_known_values_min_length_is_explicit_and_validated():
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE"
    assert redact_known(value, [value]) is value
    for minimum in (1, 4):
        assert redact_known(value, [value], min_length=minimum) == KNOWN
        assert (
            redact_known(value.encode(), [value], min_length=minimum) == KNOWN.encode()
        )
        assert (
            redact_known("".join(pct(c) for c in value), [value], min_length=minimum)
            == KNOWN
        )
    for invalid in (0, -1, "1", None, True):
        assert redact_known(value, [value], min_length=invalid) == FAILED_TEXT


def test_r5_d1_prefix_matching_ambiguous_backslash_subprocess_deadline():
    import subprocess

    # The subprocess imports only the owned module; no pytest/Django or fixture values printed.
    program = """import importlib.util, sys
spec = importlib.util.spec_from_file_location("scrub_budget", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# synthetic test value (fi-security e126d4a6)
value = "FAKE" + chr(92) * 28 + "Z"
# synthetic test value (fi-security e126d4a6)
text = "FAKE" + chr(92) * 56 + "YZ"
safe = m.redact_known_values(text, [value])
assert safe == text or safe == "[hidden: scrub-failed]"
# synthetic test value (fi-security e126d4a6)
positive = "FAKE" + chr(92) * 56 + "Z"
assert m.redact_known_values(positive, [value]) == "[hidden: known-value]"
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, cs.__file__],
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0


def test_r5_known_value_budget_exhaustion_is_categorical(monkeypatch):
    monkeypatch.setattr(cs, "_KNOWN_WORK_LIMIT", 1)
    value = known_value()
    assert redact_known(value, [value]) == FAILED_TEXT
    assert redact_known(value.encode(), [value]) == SCRUB_FAILED


def test_r5_text_and_known_value_size_limits_fail_closed():
    oversized = "x" * (cs.DECODED_BODY_LIMIT + 1)
    assert cs.scrub_text(oversized) == FAILED_TEXT
    assert cs.scrub_bytes(oversized.encode()) == SCRUB_FAILED
    assert redact_known(oversized, ["FAKEabcd"]) == FAILED_TEXT


# Additional r5 controls appended after the initial sealed RED, before the
# repeated-run expansion repair. All initial RED test bytes remain a prefix.


def test_r5_d1_many_encoded_hits_one_run_subprocess_deadline():
    import subprocess

    program = """import importlib.util, sys
spec = importlib.util.spec_from_file_location("scrub_runs", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
# synthetic test value (fi-security e126d4a6)
value = "ghp_" + "FAKE" + "abcdefghijklmnopqrstuvwxyz012345"
# synthetic test value (fi-security e126d4a6)
encoded = "".join("%" + format(ord(c), "02x") for c in value)
# synthetic test value (fi-security e126d4a6)
text = (encoded + "%21") * 2000
assert m.scrub_text(text) == "[hidden: github-token]"
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, cs.__file__],
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0


def test_r5_d1_encoded_query_delimiter_noise_subprocess_deadline():
    import subprocess

    program = """import importlib.util, sys
spec = importlib.util.spec_from_file_location("scrub_delimiters", sys.argv[1])
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
text = ("%26" + "aaaa") * 10000
assert m.scrub_text(text) == text
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, cs.__file__],
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0


def test_r5_known_named_entities_include_multicodepoint_aliases():
    for before, after in (("fj", "FAKE01"), ("FAKE01", "fj")):
        # synthetic test value (fi-security e126d4a6)
        value = before + after
        # synthetic test value (fi-security e126d4a6)
        encoded = value.replace("fj", "&fjlig;")
        assert redact_known(encoded, [value]) == KNOWN


def test_r5_utf8_bom_and_compressed_utf16_response():
    import codecs

    value = r5_provider()
    raw = codecs.BOM_UTF8 + value.encode()
    assert cs.scrub_bytes(raw) == codecs.BOM_UTF8 + m("github-token").encode()
    assert cs.scrub_bytes(raw, charset="utf-16") == SCRUB_FAILED

    class Response:
        status_code = 502
        streaming = False

        def __init__(self, charset):
            self.content = gzip.compress(
                json.dumps({HDR_AUTH: value, "ok": 1}).encode("utf-16")
            )
            self.headers = {
                "Content-Type": "application/json; charset=" + charset,
                "Content-Encoding": "gzip",
                "Digest": "synthetic-validator",
                "Content-Length": str(len(self.content)),
            }

    for charset in ("utf-16", "utf-8"):
        response = Response(charset)
        result = cs.CredentialScrubErrorResponseMiddleware(lambda request: response)(  # noqa: B023
            None
        )
        assert result is response and result.status_code == 502
        assert int(result.headers["Content-Length"]) == len(result.content)
        assert (
            "Content-Encoding" not in result.headers and "Digest" not in result.headers
        )
        if charset == "utf-16":
            assert (
                json.loads(result.content)["ok"] == 1
                and value.encode("utf-16-le") not in result.content
            )
        else:
            assert result.content == SCRUB_FAILED
            assert result.headers["Content-Type"] == "text/plain; charset=utf-8"


# --- r6: exact URL header aliases and duplicate-member JSON strings -----------

R6_QUERY_HEADER_NAMES = tuple(
    sorted(
        {name.lower() for name in R3_NAMES}
        | {name.lower().replace("-", "_") for name in R3_NAMES}
        | {
            "apikey",
            "x-api_key",
            "x_api-key",
            "x-goog_api-key",
            "x_goog-api_key",
            "anthropic_api-key",
            "openai-api_key",
            "x-apikey",
            "x_apikey",
            "x-goog-apikey",
            "x_goog_apikey",
            "anthropic-apikey",
            "openai_apikey",
        }
    )
)
R6_LAST_MEMBERS = ("null", '""', "0", "[]", "{}", "false", '["ordinary"]', '{"ok":1}')


@pytest.mark.parametrize("name", R6_QUERY_HEADER_NAMES)
@pytest.mark.parametrize(
    "separator", ["?", "#", "#?", "?before=kept&", "#before=kept&"]
)
def test_r6_header_aliases_use_url_boundaries_across_sinks(name, separator):
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-r6-url-value"
    spellings = (
        name,
        name.upper(),
        "".join(pct(c, False) for c in name),
        "".join(pct(c, True) for c in name.upper()),
        "".join(pct(c, i % 2 == 0) if i % 3 else c for i, c in enumerate(name)),
    )
    for spelling in spellings:
        url = (
            "https://example.invalid/cb"
            + separator
            + spelling
            + "="
            + value
            + "&next=kept#tail"
        )
        expected = url.replace(value, m("query-credential"))
        for carrier in (url, json.dumps({"detail": url, "ok": 1})):
            for safe in (*r3_sinks(carrier), cs.scrub_value(carrier)):
                assert value not in safe and cs.scrub_text(safe) == safe
                if carrier.startswith("{"):
                    assert json.loads(safe) == {"detail": expected, "ok": 1}
                else:
                    assert safe == expected


@pytest.mark.parametrize("name", R6_QUERY_HEADER_NAMES)
def test_r6_unrelated_url_parameter_names_remain_unchanged(name):
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-r6-negative"
    for unrelated in ("not_" + name, name + "_extra"):
        for spelling in (unrelated, "".join(pct(c) for c in unrelated)):
            for separator in ("?", "#"):
                text = (
                    "https://example.invalid/"
                    + separator
                    + spelling
                    + "="
                    + value
                    + "&next=kept"
                )
                for safe in (*r3_sinks(text), cs.scrub_value(text)):
                    assert safe == text


@pytest.mark.parametrize("separator", ["?", "#"])
def test_r6_multiple_header_aliases_long_encoded_values_and_empty_fields(separator):
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE" * 1100
    # synthetic test value (fi-security e126d4a6)
    encoded = "".join(pct(c) for c in "FAKE&fragment#suffix")
    url = (
        "https://example.invalid/"
        + separator
        + HDR_AUTH.lower()
        + "="
        + value
        + "&next=kept&"
        + HDR_XAK
        + "="
        + encoded
        + "#tail"
    )
    expected = url.replace(value, m("query-credential")).replace(
        encoded, m("query-credential")
    )
    for safe in (*r3_sinks(url), cs.scrub_value(url)):
        assert safe == expected and cs.scrub_text(safe) == safe
    empty = "https://example.invalid/" + separator + HDR_XAK + "=&next=kept"
    assert cs.scrub_text(empty) is empty


def r6_structural_sinks(text):
    return (
        cs.scrub_value(text),
        cs.scrub_value({"payload": text})["payload"],
        cs.scrub_sentry_event({"message": text})["message"],
        cs.scrub_credentials_event(None, "error", {"event": text})["event"],
    )


@pytest.mark.parametrize("name", ["password", "client_secret", FIELD_AK, HDR_AUTH])
@pytest.mark.parametrize("last", R6_LAST_MEMBERS)
def test_r6_duplicate_credential_members_fail_closed_across_structural_sinks(
    name, last
):
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-r6-discarded-member"
    pair = json.dumps(name) + ":"
    text = "{" + pair + json.dumps(value) + "," + pair + last + "}"
    for carrier in (text, "[" + text + "]", '{"nested":' + text + "}"):
        for safe in r6_structural_sinks(carrier):
            assert type(safe) is str and safe == FAILED_TEXT
            assert value not in safe and cs.scrub_value(safe) == safe
    wrapped = json.dumps({"wrapped": text, "ok": 1})
    for safe in r6_structural_sinks(wrapped):
        assert type(safe) is str and value not in safe
        assert json.loads(safe) == {"wrapped": FAILED_TEXT, "ok": 1}


@pytest.mark.parametrize("last", R6_LAST_MEMBERS)
def test_r6_duplicate_parent_containers_fail_closed_recursively(last):
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-r6-discarded-parent"
    first = json.dumps({"data": {"client_secret": value}})
    duplicate = first[:-1] + ',"data":' + last + "}"
    for carrier in (
        duplicate,
        "[" + duplicate + "]",
        '{"nested":[0,' + duplicate + "]}",
    ):
        for safe in r6_structural_sinks(carrier):
            assert safe == FAILED_TEXT and type(safe) is str
    # Same name in different objects is not a duplicate-member object.
    ordinary = '{ "data": {"ok": 1}, "nested": [{"data": 0}] }'
    assert cs.scrub_value(ordinary) is ordinary


def test_r6_duplicate_decoded_names_and_noncredential_members_fail_closed():
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-r6-escaped-member"
    escaped_name = "passw" + json_u("o") + "rd"
    text = '{"password":' + json.dumps(value) + ',"' + escaped_name + '":null}'
    for safe in r6_structural_sinks(text):
        assert safe == FAILED_TEXT and value not in safe
    benign_duplicate = '{"ordinary":1,"ordinary":0}'
    assert cs.scrub_value(benign_duplicate) == FAILED_TEXT
    assert cs.scrub_value(text, mask_credential_keys=False) == FAILED_TEXT


def test_r6_unique_json_formatting_identity_and_structural_scrubbing_are_preserved():
    ordinary = '{ "data": [{"same": 0}, {"same": 1}], "ok": true }'
    assert cs.scrub_value(ordinary) is ordinary
    # synthetic test value (fi-security e126d4a6)
    value = "FAKE-r6-unique-member"
    unique = json.dumps({"password": value, "ok": 1})
    for safe in r6_structural_sinks(unique):
        assert json.loads(safe) == {"password": CREDENTIAL_FIELD, "ok": 1}
        assert type(safe) is str and cs.scrub_value(safe) == safe
    assert cs.scrub_value(unique, mask_credential_keys=False) is unique
    malformed = "{ordinary not JSON"
    assert cs.scrub_value(malformed) is malformed
