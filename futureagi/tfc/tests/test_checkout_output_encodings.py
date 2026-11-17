"""Encoding scope and line integrity at the configured sinks (681 addendum 5).

In scope: canonical, single and double percent-encoded, and JSON \\u-escaped
spellings of the checkout prefix and value. Non-BMP and structural characters
never break a log line: every emitted production line still parses. Nested
encodings beyond two levels and line-split carriers are the accepted residual
(see the lane README); they are recorded here as expected failures.
"""

import io
import json
import logging
import re
from collections import Counter
from enum import IntEnum
from urllib.parse import quote, unquote

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from tfc.logging.checkout import CheckoutLoggingMiddleware
from tfc.tests.checkout_sinks import configured_sinks, native_emit

pytestmark = pytest.mark.integration
PREFIX = "/agentcc/managed-credits/checkout/"
VALUE = "synthetic-encoding-session-0428"
TEXT_SINKS = ("record", "console-production", "file-production", "console-local")
_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")


def escaped(text):
    return "".join(f"\\u{ord(character):04x}" for character in text)


def letters(prefix, value):
    # Percent-encoded letters in the prefix and the session (non-canonical).
    prefix = prefix.replace("checkout", "checkou%74").replace("ordinary", "ordinar%79")
    return prefix + "%73" + value[1:] + "/"


def doubled(prefix, value):
    return quote(quote(prefix + value + "/", safe=""), safe="")


FORMS = {
    "canonical": lambda prefix, value: prefix + value + "/",
    "percent": lambda prefix, value: quote(prefix + value + "/", safe=""),
    "percent-letters": letters,
    "percent-double": doubled,
    "json-prefix": lambda prefix, value: prefix.replace("/", "\\u002f") + value + "/",
    "json-value": lambda prefix, value: prefix + escaped(value) + "/",
    "json-both": lambda prefix, value: escaped(prefix) + escaped(value) + "/",
    "renderer-escaped-value": lambda prefix, value: prefix + value + "é/",
}


def decoded(text):
    """Every in-scope spelling, decoded for the assertion (not for output)."""
    for _ in range(4):
        # A renderer's escaped backslash first, then one escape level.
        text = text.replace("\\\\", "\\")
        text = unquote(_UNICODE.sub(lambda m: chr(int(m[1], 16)), text))
    return text


def emitted(message, *, scope=None):
    with configured_sinks("checkout.output.encodings") as (logger, sinks):
        if scope is None:
            logger.error("GET %s", message)
        else:

            def respond(request):
                logger.error("GET %s", message)
                return HttpResponse("ok")

            CheckoutLoggingMiddleware(respond)(RequestFactory().get(scope))
    return sinks


@pytest.mark.parametrize("form", sorted(FORMS))
@pytest.mark.parametrize("checkout", [True, False])
def test_in_scope_spellings_at_every_sink(form, checkout):
    prefix = PREFIX if checkout else "/ordinary/"
    sinks = emitted(FORMS[form](prefix, VALUE))
    outputs = sinks.outputs()
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.structured(), "a production line is not one JSON document"
    for name in TEXT_SINKS:
        observed = VALUE in decoded(outputs[name])
        assert observed is not checkout, (name, form, outputs[name])


@pytest.mark.parametrize("environment_line", ["non-bmp", "lone-surrogate"])
@pytest.mark.parametrize("checkout", [True, False])
def test_non_bmp_and_lone_surrogates_never_break_a_line(environment_line, checkout):
    tail = "\U0001f600" if environment_line == "non-bmp" else "\udcff"
    prefix = PREFIX if checkout else "/ordinary/"
    sinks = emitted(prefix + VALUE + tail + "/")
    outputs = sinks.outputs()
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.structured(), "a production line is not one JSON document"
    for name in TEXT_SINKS:
        assert (VALUE in decoded(outputs[name])) is not checkout, name


STRUCTURAL = ['"', "\\", "{", "}", ",", ":", "[", "]", "'", "<", "\t", "%"]


@pytest.mark.parametrize("segment", STRUCTURAL)
@pytest.mark.parametrize("scoped", [True, False])
def test_structural_segments_keep_every_line_parseable(segment, scoped):
    path = PREFIX + segment + "/"
    scope = PREFIX + quote(segment, safe="") + "/" if scoped else None
    sinks = emitted(path, scope=scope)
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.structured(), "a production line is not one JSON document"
    if scoped:
        # Inside the request, the session is redacted in its path context.
        for line in sinks.outputs()["console-production"].splitlines():
            assert json.loads(line)["event"] == "GET " + PREFIX + "[redacted]/"


@pytest.mark.parametrize("segment", ['"', "\\", "{", ":", "event", "level"])
def test_scoped_structural_session_keeps_unrelated_lines_parseable(segment):
    # Every line of a checkout request whose session is structural or a schema
    # word still parses, including lines that never name the path.
    with configured_sinks("checkout.output.encodings") as (logger, sinks):

        def respond(request):
            logger.error("unrelated line with %s inside", segment)
            logger.error("ordinary line")
            return HttpResponse("ok")

        CheckoutLoggingMiddleware(respond)(
            RequestFactory().get(PREFIX + quote(segment, safe="") + "/")
        )
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.structured(), "a production line is not one JSON document"
    lines = sinks.outputs()["console-production"].splitlines()
    assert json.loads(lines[-1])["event"] == "ordinary line"


@pytest.mark.parametrize("kind", [int, float], ids=["int-subclass", "float-subclass"])
@pytest.mark.parametrize("form", ["msg", "arg-s", "arg-r", "list-arg"])
def test_numeric_subclass_renders_once_per_native_use(kind, form):
    calls = Counter()
    source = io.StringIO("ordinary-constant-text" * 100)

    class Number(kind):
        def __str__(self):
            calls["str"] += 1
            return source.read(22)

        def __repr__(self):
            calls["repr"] += 1
            return source.read(22)

    def run():
        source.seek(0)
        item = Number(40)
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(logging.Formatter())
        log = logging.Logger("checkout.numeric.evaluation", 1)
        log.handlers = [handler]
        if form == "msg":
            log.error(item)
        elif form == "arg-s":
            log.error("item %s", item)
        elif form == "arg-r":
            log.error("item %r", item)
        else:
            log.error("items %r", [item])
        observed = (Counter(calls), source.tell(), output.getvalue())
        calls.clear()
        return observed

    with_boundary = run()
    with native_emit():
        natively = run()
    assert with_boundary == natively
    assert sum(with_boundary[0].values()) == 1, with_boundary
    assert with_boundary[1] == 22


class Severity(IntEnum):
    ERROR = 40


class NumericSeverity(int):
    pass


@pytest.mark.parametrize(
    "level",
    [40, Severity.ERROR, NumericSeverity(40)],
    ids=["int", "int-enum", "int-subclass"],
)
def test_numeric_level_reaches_sinks_unchanged(level):
    with configured_sinks("checkout.output.encodings") as (logger, sinks):
        logger.log(level, PREFIX + VALUE + "/")
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.records[-1].levelno is level, "numeric severity was replaced"
    assert sinks.leaks(VALUE) == []
    assert sinks.structured(), "a production line is not one JSON document"


@pytest.mark.xfail(
    reason="accepted residual (681 addendum 5 item 4): nesting beyond the"
    " decoder's six levels; no product code path emits it",
    strict=False,
)
def test_residual_seven_level_percent_nesting():
    path = PREFIX + VALUE + "/"
    for _ in range(7):
        path = path.replace("%", "%25").replace("/", "%2f")
    sinks = emitted(path)
    assert sinks.leaks(VALUE) == []
    assert all(VALUE not in decoded(text) for text in sinks.outputs().values())


@pytest.mark.xfail(
    reason="accepted residual (681 addendum 5 item 4): a line break between"
    " the prefix and the session; no product code path emits it",
    strict=False,
)
def test_residual_line_split_carrier():
    sinks = emitted(PREFIX + "\n" + VALUE + "/")
    assert sinks.leaks(VALUE) == []
