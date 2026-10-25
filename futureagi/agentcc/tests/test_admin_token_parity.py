"""Django and the gateway must hold the admin token to the same policy.

The gateway refuses to start on a token its policy refuses
(agentcc-gateway/internal/config/admin_token.go); Django's admin endpoints,
which accept the same credential, deny every request on one
(agentcc/admin_token.py). Both sides refuse and accept the same tokens.
"""

import json
import re
from pathlib import Path

import pytest

from agentcc import admin_token

_GATEWAY_CONFIG = (
    Path(__file__).resolve().parents[3] / "agentcc-gateway/internal/config"
)
_POLICY = _GATEWAY_CONFIG / "admin_token.go"
_CASES = _GATEWAY_CONFIG / "testdata/admin_token_cases.json"


def _go_string_list(name):
    source = _POLICY.read_text()
    block = re.search(
        rf"^var {name} = \[\]string\{{\n(.*?)^\}}",
        source,
        re.MULTILINE | re.DOTALL,
    )
    assert block, f"{name} not found in {_POLICY}"
    body = re.sub(r"//[^\n]*", "", block.group(1))
    values = re.findall(r'"([^"]*)"', body)
    assert values, f"parsed nothing from {name}"
    return values


def _go_int_const(name):
    match = re.search(rf"^const {name} = (\d+)$", _POLICY.read_text(), re.MULTILINE)
    assert match, f"{name} not found in {_POLICY}"
    return int(match.group(1))


def test_the_published_defaults_match():
    assert _go_string_list("publishedAdminTokens") == list(
        admin_token.PUBLISHED_ADMIN_TOKENS
    )


def test_the_common_defaults_match():
    assert set(_go_string_list("commonAdminTokens")) == set(
        admin_token.COMMON_ADMIN_TOKENS
    )


def test_the_placeholder_markers_match():
    assert _go_string_list("placeholderAdminTokenMarkers") == list(
        admin_token.PLACEHOLDER_ADMIN_TOKEN_MARKERS
    )


def test_the_limits_match():
    assert _go_int_const("MinAdminTokenLength") == admin_token.MIN_ADMIN_TOKEN_LENGTH
    assert (
        _go_int_const("minAdminTokenDistinctChars")
        == admin_token.MIN_ADMIN_TOKEN_DISTINCT_CHARS
    )
    assert admin_token.ENV_ALLOW_INSECURE_ADMIN_TOKEN in _POLICY.read_text()


def _shared_cases():
    cases = json.loads(_CASES.read_text())
    assert cases["refused"] and cases["accepted"]
    return cases


@pytest.mark.parametrize("token", _shared_cases()["refused"])
def test_django_refuses_what_the_gateway_refuses(token):
    assert admin_token.admin_token_problem(token)


@pytest.mark.parametrize("token", _shared_cases()["accepted"])
def test_django_accepts_what_the_gateway_accepts(token):
    assert admin_token.admin_token_problem(token) == ""


@pytest.mark.parametrize(
    "value, allowed",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        (" t ", True),
        ("", False),
        ("0", False),
        ("false", False),
        ("yes", False),
    ],
)
def test_the_dev_opt_in_reads_like_the_gateways(value, allowed):
    # The gateway parses it with strconv.ParseBool, which takes no "yes".
    assert admin_token.insecure_admin_token_allowed(value) is allowed
