"""Tests for taxonomy-driven persona generation (no live LLM, no GPU)."""

import pytest

from simulate.constants.edge_case_taxonomy import EDGE_CASE_TAXONOMY, EDGE_CASE_KEYS
from simulate.services.persona_generator import (
    generate_personas,
    taxonomy_coverage,
)


class TestEdgeCaseTaxonomy:
    def test_keys_unique(self):
        assert len(EDGE_CASE_KEYS) == len(set(EDGE_CASE_KEYS))
        assert len(EDGE_CASE_TAXONOMY) >= 8

    def test_weights_positive(self):
        for item in EDGE_CASE_TAXONOMY:
            assert item["weight"] >= 1
            assert item["prompt"]


class TestGeneratePersonas:
    def test_generates_requested_count(self):
        personas = generate_personas("refund flow", n=50, seed=42)
        assert len(personas) == 50

    def test_covers_full_taxonomy(self):
        personas = generate_personas("billing support", n=50, seed=42)
        assert taxonomy_coverage(personas) == 1.0

    def test_deterministic(self):
        first = generate_personas("support", n=20, seed=7)
        second = generate_personas("support", n=20, seed=7)
        assert first == second

    def test_unique(self):
        personas = generate_personas("support", n=50, seed=42)
        names = [(p["name"], p["edge_case"]) for p in personas]
        assert len(set(names)) == len(names)

    def test_payload_shape(self):
        personas = generate_personas("test", n=5, seed=1)
        for persona in personas:
            assert persona["persona_type"] == "workspace"
            assert persona["gender"]
            assert persona["edge_case"] in EDGE_CASE_KEYS
            assert persona["additional_instruction"]

    def test_empty_source(self):
        personas = generate_personas("", n=5, seed=1)
        assert len(personas) == 5
