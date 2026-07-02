"""Unit test for CORE-BACKEND-1190.

`_extract_recording_urls` crashed with "AttributeError: 'str' object has no
attribute 'get'" when a Vapi log's `artifact` key was present but its value was
a string (malformed provider payload). The fix type-guards `artifact` before
calling `.get`. These are pure-dict transforms — no DB needed.
"""

from tracer.utils.vapi import _extract_recording_urls


def test_string_artifact_does_not_crash():
    attrs = {}
    # Previously raised AttributeError: 'str' object has no attribute 'get'
    _extract_recording_urls({"artifact": "unexpected-string"}, attrs)
    assert attrs == {}


def test_missing_artifact_does_not_crash():
    attrs = {}
    _extract_recording_urls({}, attrs)
    assert attrs == {}


def test_none_artifact_does_not_crash():
    attrs = {}
    _extract_recording_urls({"artifact": None}, attrs)
    assert attrs == {}


def test_well_formed_recording_still_extracted():
    attrs = {}
    _extract_recording_urls(
        {"artifact": {"recording": {"mono": {"combinedUrl": "https://x/r.wav"}}}},
        attrs,
    )
    # The mono combined URL should have been written into eval_attributes.
    assert any("https://x/r.wav" == v for v in attrs.values())
