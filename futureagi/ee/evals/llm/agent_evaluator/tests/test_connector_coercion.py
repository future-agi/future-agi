"""Tests for ``_coerce_connector_ids`` — the defensive tools-config reader
that lets the agent evaluator tolerate both the canonical
``{"internet", "connectors"}`` shape and the legacy flat-map
``{uuid: true}`` shape written by an older FE. See TH-5276 / TH-5279.
"""

from ee.evals.llm.agent_evaluator.evaluator import _coerce_connector_ids


def test_canonical_shape_returns_connectors_list():
    cfg = {
        "internet": True,
        "connectors": ["uuid-a", "uuid-b", "uuid-c"],
    }
    assert _coerce_connector_ids(cfg) == ["uuid-a", "uuid-b", "uuid-c"]


def test_canonical_shape_with_empty_connectors_returns_empty():
    assert _coerce_connector_ids({"internet": True, "connectors": []}) == []
    assert _coerce_connector_ids({"internet": False, "connectors": []}) == []


def test_canonical_shape_with_null_connectors_returns_empty():
    assert _coerce_connector_ids({"internet": True, "connectors": None}) == []


def test_canonical_shape_filters_falsy_entries():
    cfg = {"internet": False, "connectors": ["uuid-a", "", None, "uuid-b"]}
    assert _coerce_connector_ids(cfg) == ["uuid-a", "uuid-b"]


def test_canonical_shape_non_list_connectors_returns_empty():
    """Defensive: malformed ``connectors`` (e.g. someone wrote a string by
    mistake) must not crash."""
    cfg = {"internet": False, "connectors": "uuid-a"}
    assert _coerce_connector_ids(cfg) == []


def test_legacy_flat_map_returns_keys():
    cfg = {"uuid-a": True, "uuid-b": True}
    assert sorted(_coerce_connector_ids(cfg)) == ["uuid-a", "uuid-b"]


def test_legacy_flat_map_excludes_internet_key():
    """The legacy shape never bundled ``internet`` in the tools dict, but
    defend in depth: if it ever appeared, treat it as a flag, not a
    connector UUID."""
    cfg = {"internet": True, "uuid-a": True}
    assert _coerce_connector_ids(cfg) == ["uuid-a"]


def test_legacy_flat_map_skips_falsy_values():
    cfg = {"uuid-a": True, "uuid-b": False, "uuid-c": True}
    assert sorted(_coerce_connector_ids(cfg)) == ["uuid-a", "uuid-c"]


def test_legacy_flat_map_all_falsy_returns_empty():
    assert _coerce_connector_ids({"uuid-a": False, "uuid-b": None}) == []


def test_empty_dict_returns_empty():
    assert _coerce_connector_ids({}) == []


def test_none_input_returns_empty():
    assert _coerce_connector_ids(None) == []


def test_non_dict_input_returns_empty():
    assert _coerce_connector_ids("not a dict") == []
    assert _coerce_connector_ids(["uuid-a"]) == []
    assert _coerce_connector_ids(42) == []


def test_canonical_precedence_when_both_shapes_present():
    """If a row somehow has both ``connectors`` and stray UUID keys, the
    canonical ``connectors`` array wins. Pins behaviour against ambiguous
    mid-migration data."""
    cfg = {
        "internet": True,
        "connectors": ["uuid-canonical"],
        "uuid-stray": True,  # would only match the legacy branch
    }
    assert _coerce_connector_ids(cfg) == ["uuid-canonical"]
