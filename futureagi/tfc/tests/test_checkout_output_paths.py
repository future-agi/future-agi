"""Which path the final-output scrub takes, and identifiers that follow a run.

Inside a checkout request, the middleware puts the session's spellings in the
request scope. When every scoped value is a Checkout Session identifier, the
identifier shape already replaces each occurrence in unescaped text, so the
line is redacted with string operations only and reads the same as it does
outside the request; any other scoped value keeps the general correlated
path. Separately, an identifier that directly follows a longer
identifier-shaped run is replaced whole, in every in-scope spelling. All
values are synthetic.
"""

import json
import logging

import pytest

from tfc.logging import checkout_output
from tfc.logging.checkout import _values, _variants
from tfc.logging.checkout_encoding import decoded_levels
from tfc.logging.checkout_format import redact_session_ids
from tfc.logging.checkout_output import scrub_rendered
from tfc.tests.test_checkout_output_edges import configured_handlers

pytestmark = pytest.mark.integration
PREFIX = "/agentcc/managed-credits/checkout/"
TAIL = "SyntheticPathSession0428AbCdEfGh"
SESSION = "cs_test_" + TAIL
LIVE = "cs_live_" + TAIL
OTHER = "synthetic-scope-value-0428"
# Messages the general path is not needed for: one literal checkout path, a
# bare identifier, or neither.
FAST = {
    "ordinary": "GET /ordinary/r4vcountvalue0004/ status=200",
    "checkout": "GET " + PREFIX + SESSION + "/ status=200",
    "bare": "checkout " + SESSION + " settled",
    "other-path-and-bare": "GET " + PREFIX + "r4vcountvalue0004/ for " + SESSION,
    "live": "GET " + PREFIX + LIVE + "/ status=200",
}
# Every other placement: repeated, beside a marker, rendered route arguments,
# escapes and percent-encoding in the same line.
BROAD = {
    **FAST,
    "path-and-repeat": "GET " + PREFIX + SESSION + "/ then " + SESSION,
    "two-paths": PREFIX + SESSION + "/ and " + PREFIX + SESSION + "/",
    "beside-marker": "[redacted]" + SESSION + "[redacted]",
    "route-arguments": (
        "ResolverMatch(kwargs={'session_id': '" + SESSION + "'}, "
        "url_name='agentcc-managed-credit-checkout-status', "
        "route='agentcc/managed-credits/checkout/<str:session_id>/')"
    ),
    "escaped": "GET " + PREFIX + SESSION + "/ 100% \\n done",
    "percent": "GET " + PREFIX.replace("/", "%2F") + SESSION + "%2F",
}


def rendered(tmp_path, monkeypatch, environment, message):
    """The configured console handler's own text for one record."""
    (handler, *others) = configured_handlers(tmp_path, monkeypatch, environment)
    try:
        record = logging.LogRecord(
            "checkout.paths", 20, __file__, 1, "%s", (message,), None
        )
        return handler.format(record)
    finally:
        for item in (handler, *others):
            item.close()


def assert_value_free(text, *values):
    for level in decoded_levels(text):
        for value in values:
            assert value not in level, (value, text)


@pytest.fixture
def general_calls(monkeypatch):
    calls = []
    general = checkout_output._sanitized

    def recording(text, values):
        calls.append(values)
        return general(text, values)

    monkeypatch.setattr(checkout_output, "_sanitized", recording)
    return calls


def scoped(text, values):
    token = _values.set(values)
    try:
        return scrub_rendered(text)
    finally:
        _values.reset(token)


@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("kind", sorted(FAST))
def test_identifier_scope_takes_the_string_path(
    tmp_path, monkeypatch, general_calls, environment, kind
):
    text = rendered(tmp_path, monkeypatch, environment, FAST[kind])
    within = scoped(text, _variants(SESSION))
    assert general_calls == []
    outside = scoped(text, ())
    assert general_calls == []
    assert within == outside
    assert_value_free(within, SESSION, LIVE, TAIL)
    if kind == "ordinary":
        assert within is text
    else:
        assert "[redacted]" in within
    if environment == "production":
        assert json.loads(within)["logger"] == "checkout.paths"


@pytest.mark.parametrize("environment", ["production", "local"])
@pytest.mark.parametrize("kind", sorted(BROAD))
@pytest.mark.parametrize("scope", ["identifier", "other", "both", "none"])
def test_every_placement_is_value_free_in_and_out_of_scope(
    tmp_path, monkeypatch, environment, kind, scope
):
    values = {
        "identifier": _variants(SESSION),
        "other": _variants(OTHER),
        "both": (*_variants(SESSION), *_variants(OTHER)),
        "none": (),
    }[scope]
    text = rendered(tmp_path, monkeypatch, environment, BROAD[kind] + " " + OTHER)
    result = scoped(text, values)
    assert_value_free(result, SESSION, LIVE, TAIL)
    if scope in ("other", "both"):
        assert_value_free(result, OTHER)
    if environment == "production":
        assert json.loads(result)["logger"] == "checkout.paths"


@pytest.mark.parametrize("scope", ["other", "both"])
def test_other_scoped_values_keep_the_correlated_path(general_calls, scope):
    values = (
        _variants(OTHER)
        if scope == "other"
        else (*_variants(SESSION), *_variants(OTHER))
    )
    result = scoped(json.dumps({"event": "settled " + OTHER, "count": 1}), values)
    assert len(general_calls) == 1
    assert json.loads(result) == {"event": "settled [redacted]", "count": 1}


def percent(text):
    return text.replace("_", "%5F")


def escaped(text):
    return text.replace("_", "\\u005f")


RUNS = {
    "test-run": "cs_test_" + "A" * 22,
    "live-run": "cs_live_" + "Z9" * 15,
    "long-run": "cs_test_" + "B" * 40,
}


@pytest.mark.parametrize("run", sorted(RUNS))
@pytest.mark.parametrize("identifier", [SESSION, LIVE])
@pytest.mark.parametrize("spelling", ["canonical", "percent", "json"])
@pytest.mark.parametrize("scope", ["identifier", "none"])
def test_identifier_after_an_identifier_shaped_run(run, identifier, spelling, scope):
    spell = {"canonical": str, "percent": percent, "json": escaped}[spelling]
    # The run ends in "cs", the first two characters of the identifier.
    value = spell(RUNS[run] + identifier)
    line = json.dumps({"event": "GET /ordinary/" + value + "/ settled"})
    result = scoped(line, _variants(identifier) if scope == "identifier" else ())
    assert_value_free(result, identifier, identifier[len("cs_test_") :])
    assert json.loads(result)["event"].startswith("GET /ordinary/")
    assert_value_free(redact_session_ids(spell(RUNS[run] + identifier)), identifier[8:])
