"""
Golden tests for the guardrail evals that could previously fail open.

Each test loads the corresponding YAML, materializes the embedded code body in
an isolated namespace, then exercises the evaluator directly -- the same
approach as test_validators.py, pinning eval-body correctness rather than the
sandbox dispatch path.

The shared defect: each evaluator guarded only the *wholly* empty case, so a
partially-degenerate input silently narrowed or neutralised the check instead
of rejecting it, and the eval reported a pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

YAML_DIR = Path(__file__).resolve().parent.parent / "function"

SSN_TEXT = "my ssn is 123-45-6789"


def _load_eval(name: str):
    """Load YAML, materialize the code body, return the evaluate callable."""
    path = YAML_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    ns: dict = {}
    runner = __builtins__["exec"] if isinstance(__builtins__, dict) else getattr(__builtins__, "exec")
    runner(compile(code, str(path), "exec"), ns)
    return ns["evaluate"]


# ---------------------------------------------------------------------------
# regex_pii_detection: an unrecognized type must not shrink the scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("detect_types", ["SSN,email", "SSN", "Ssn,EMAIL", " ssn , EMAIL "])
def test_detect_types_are_case_insensitive(detect_types):
    # Pattern keys are lowercase, so "SSN" previously matched nothing and was
    # dropped. With "SSN,email" the scan narrowed to email alone and returned
    # score 1.0 "No PII (checked: email)" over text holding a real SSN.
    ev = _load_eval("regex_pii_detection")
    r = ev(None, None, None, None, text=SSN_TEXT, detect_types=detect_types)
    assert r["score"] == 0.0, f"SSN must be detected for detect_types={detect_types!r}, got {r}"
    assert "SSN" in r["reason"]


def test_unsupported_detect_types_fail_closed():
    ev = _load_eval("regex_pii_detection")
    r = ev(None, None, None, None, text=SSN_TEXT, detect_types="bogus")
    assert r["score"] == 0.0
    assert "bogus" in r["reason"]


def test_partially_unsupported_detect_types_fail_closed():
    # The important case: one good type is not licence to skip the rest.
    ev = _load_eval("regex_pii_detection")
    r = ev(None, None, None, None, text=SSN_TEXT, detect_types="ssn,bogus")
    assert r["score"] == 0.0
    assert "bogus" in r["reason"]


def test_json_scalar_detect_types_is_not_iterated_per_character():
    ev = _load_eval("regex_pii_detection")
    r = ev(None, None, None, None, text=SSN_TEXT, detect_types='"ssn"')
    assert r["score"] == 0.0
    assert "SSN" in r["reason"]


def test_clean_text_still_passes():
    ev = _load_eval("regex_pii_detection")
    r = ev(None, None, None, None, text="hello world", detect_types="ssn,email")
    assert r["score"] == 1.0


def test_default_detect_types_still_scans_everything():
    ev = _load_eval("regex_pii_detection")
    for dt in (None, "", "   "):
        r = ev(None, None, None, None, text=SSN_TEXT, detect_types=dt)
        assert r["score"] == 0.0, f"detect_types={dt!r} must fall back to all types, got {r}"


# ---------------------------------------------------------------------------
# contains_any / contains_none: a blank keyword matches every string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("keywords", ["foo,", ",foo", "foo, ,bar", ""])
def test_contains_any_ignores_blank_keywords(keywords):
    # "" is a substring of every string, so one blank entry made the verdict
    # independent of the text and contains_any always passed. The emptiness
    # guard missed it because [""] is truthy.
    ev = _load_eval("contains_any")
    r = ev(None, None, None, None, text="hello world", keywords=keywords)
    assert r["score"] == 0.0, f"keywords={keywords!r} must not match, got {r}"


@pytest.mark.parametrize("keywords", ["foo,", ",foo", "foo, ,bar", ""])
def test_contains_none_ignores_blank_keywords(keywords):
    # Mirror image: contains_none always failed.
    ev = _load_eval("contains_none")
    r = ev(None, None, None, None, text="hello world", keywords=keywords)
    assert r["score"] == 1.0, f"keywords={keywords!r} must not match, got {r}"


def test_contains_any_still_matches_real_keywords():
    ev = _load_eval("contains_any")
    assert ev(None, None, None, None, text="hello world", keywords="hello")["score"] == 1.0
    assert ev(None, None, None, None, text="hello world", keywords="hello,")["score"] == 1.0
    assert ev(None, None, None, None, text="hello world", keywords="nope")["score"] == 0.0


def test_contains_none_still_flags_real_keywords():
    ev = _load_eval("contains_none")
    assert ev(None, None, None, None, text="hello world", keywords="hello")["score"] == 0.0
    assert ev(None, None, None, None, text="hello world", keywords="hello,")["score"] == 0.0
    assert ev(None, None, None, None, text="hello world", keywords="nope")["score"] == 1.0


def test_contains_case_insensitive_still_honoured():
    ev = _load_eval("contains_any")
    r = ev(None, None, None, None, text="Hello World", keywords="hello", case_sensitive=False)
    assert r["score"] == 1.0
