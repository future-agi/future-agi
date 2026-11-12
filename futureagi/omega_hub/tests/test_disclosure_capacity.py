"""Lockstep Hub disclosure capacity; no database needed by these tests."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from omega_hub.api.serializers.cards import version_detail
from omega_hub import official_seed
from omega_hub.services import disclosure


def test_public_serializer_preserves_long_confirmed_text_and_omits_the_draft():
    value = {"summary": "A complete disclosure confirmed in review.",
             **dict.fromkeys(disclosure.PARTS, "None."), "network": "x" * 1200,
             "files": "y" * 900, "credentials": "z" * 900}
    version = SimpleNamespace(
        entry=SimpleNamespace(publisher=SimpleNamespace(handle="example"), name="entry"),
        version="1.0.0", manifest={"id": "entry", "disclosure": {"summary": "draft"}},
        readme="", files=[], digest={}, bundle_sha256="b" * 64, bundle_bytes=0,
        capabilities={}, check_summary={}, disclosure=value, rules_version=1, source_commit="a" * 40,
        status="approved", yanked_reason="", remix_of_id=None, notes=[], cast_key="",
        approved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = version_detail(version)
    assert result["disclosure"] == value
    assert "disclosure" not in result["manifest"]


def test_seed_loader_preserves_full_text_and_rejects_invalid_capacity():
    value = {"summary": "A complete disclosure confirmed in review.",
             **dict.fromkeys(disclosure.PARTS, "None."), "network": "x" * 1200}
    seed = {"writtenFrom": {"commit": "a" * 40}, "entries": {
        "test-entry": {"codeSha256": "b" * 64, "disclosure": value}}}
    with patch.object(official_seed, "load", return_value=seed):
        assert official_seed.disclosure_for("test-entry", "b" * 64) == value
        value["network"] += "x"
        try:
            official_seed.disclosure_for("test-entry", "b" * 64)
        except official_seed.SeedError as error:
            assert "network must be 4-1200 characters" in str(error)
        else:
            raise AssertionError("over-limit seed accepted")


def test_expanded_disclosure_fields_accept_the_new_capacity():
    value = {"summary": "A complete disclosure confirmed in review.",
             **dict.fromkeys(disclosure.PARTS, "None.")}
    for field in ("network", "files", "credentials"):
        for length in (4, 300, 301, 900, 1200):
            assert disclosure.shape_errors({**value, field: "x" * length}) == [], (field, length)
        for length in (3, 1201):
            assert disclosure.shape_errors({**value, field: "x" * length}), (field, length)


def test_other_disclosure_limits_and_unknown_field_policy_are_unchanged():
    value = {"summary": "A complete disclosure confirmed in review.",
             **dict.fromkeys(disclosure.PARTS, "None.")}
    for field, low, high in (("summary", 20, 200), ("shell", 4, 300), ("background", 4, 300)):
        for length in (low, high):
            assert disclosure.shape_errors({**value, field: "x" * length}) == []
        for length in (low - 1, high + 1):
            assert disclosure.shape_errors({**value, field: "x" * length})
    assert disclosure.shape_errors({**value, "details": "not part of the contract"})
