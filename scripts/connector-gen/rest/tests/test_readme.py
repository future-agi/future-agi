"""scripts/connector-gen/README.md says what is built, no more (owner rule:
no claim without proof). Each test here pins a statement the CN4 review
found too broad, so the README cannot drift back to it."""

from __future__ import annotations

import re

import support

README = support.HERE.parents[1] / "README.md"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _section(heading: str) -> str:
    """The text under a ### heading, up to the next ## or ### heading."""
    text = _text()
    start = text.index(f"### {heading}\n")
    end = re.search(r"^#{2,3} ", text[start + 4 :], re.M)
    return text[start : start + 4 + end.start()] if end else text[start:]


def test_readme_drops_the_unconditional_origin_claim():
    # True only for files the generator wrote (CN4 review P2).
    assert "The origin is always under the app's `vendor.domains`" not in _text()


def test_readme_names_where_the_origin_rule_is_enforced():
    s = _section("Where the origin rule is enforced")
    generation, ci, load = (s.index(label) for label in ("**At generation.**", "**In CI.**", "**At load (after CN10b).**"))
    assert generation < ci < load
    assert "_vendor_host" in s[generation:ci]
    assert "generate.py --check" in s[ci:load] and "ci/connector-gen.yml" in s[ci:load]
    assert "agentcc-catalog check" in s[ci:load]  # which says what it does not check
    assert "agentcc-connectors" in s[load:] and "CN10b" in s[load:]


def test_readme_says_a_tool_is_one_http_request_and_where_multi_call_flows_go():
    s = _section("One HTTP request per tool")
    assert "exactly one HTTP request" in s
    assert "CN10" in s and "code module" in s and "IR-CN4-3" in s
    assert "not built" in s  # no such module exists on gw/CN4 or gw/CN10 yet


def test_readme_request_contract_refuses_dot_segments():
    s = _section("Request contract")
    assert "`.` or `..`" in s


def test_readme_lists_the_known_gaps_it_qualifies():
    s = _section("Known gaps")
    for gap in ("form field", "`scopes: []`", "user info", "Postman", "spaceDelimited"):
        assert gap in s, gap


def test_readme_says_github_runs_the_job_once_it_is_wired():
    # IR-CN4b-1 wired ci/connector-gen.yml into .github/workflows (test_ci_job.py holds the two copies
    # byte-identical), so "no CI runs either check" is no longer true.
    text = _text()
    assert "until then no CI runs either check" not in text
    assert "it runs once an integrator copies it" not in text
    assert "test_ci_job.py" in text  # where the byte-identity of the two copies is held


def test_readme_request_contract_states_what_the_runtime_refuses_at_load():
    # IR-CN10b-1: the rules agentcc-connectors (CN10b, merged on this line) applies when it loads a
    # bundle belong in the contract the generator writes against.
    s = _section("Request contract")
    assert "not served at all" in s and "one log record per refused tool" in s
    assert 'starts with "/"' in s
    assert "credential part or constant" in s and "without case" in s
    assert "annotation keywords only" in s and "`const`, `enum`, `default` and `examples`" in s
    assert "readOnlyHint" in s and "destructiveHint" in s  # IR-CN4b-2: the method rule, at load too


def test_readme_says_the_load_check_is_on_this_line():
    s = _section("Where the origin rule is enforced")
    assert "Neither is merged into this branch" not in s
    assert "Until the second and third are live" not in s
