"""Unit tests for red-team adversarial scenario generation (TH-5642)."""

import pytest

from simulate.services.adversarial_scenarios import (
    ADVERSARIAL_CATEGORIES,
    adversarial_category_keys,
    generate_adversarial_scenarios,
)


@pytest.mark.unit
def test_catalog_covers_core_attack_classes():
    keys = set(adversarial_category_keys())
    assert {
        "jailbreak", "prompt_injection", "pii_extraction",
        "toxicity_bait", "out_of_scope", "hallucination_pressure",
    } <= keys
    # Every category has a persona prompt + a safety success criteria.
    for c in ADVERSARIAL_CATEGORIES:
        assert c.persona_prompt and c.success_criteria


@pytest.mark.unit
def test_generate_all_categories_with_domain_filled():
    out = generate_adversarial_scenarios("A bank support agent", domain="banking")
    assert len(out) == len(ADVERSARIAL_CATEGORIES)
    by_cat = {s["category"]: s for s in out}
    assert "jailbreak" in by_cat
    spec = by_cat["jailbreak"]
    assert "banking" in spec["persona_prompt"]  # {domain} filled
    assert spec["is_adversarial"] is True
    assert spec["success_criteria"]  # bound to a safety eval downstream
    assert spec["name"].startswith("Red-team:")


@pytest.mark.unit
def test_generate_subset_and_ignores_unknown():
    out = generate_adversarial_scenarios(
        "agent", categories=["pii_extraction", "does_not_exist"]
    )
    assert [s["category"] for s in out] == ["pii_extraction"]


@pytest.mark.unit
def test_domain_inferred_when_not_given():
    out = generate_adversarial_scenarios("Healthcare appointment scheduling assistant")
    # Inferred domain hint shows up in the persona prompts.
    assert any("Healthcare" in s["persona_prompt"] for s in out)


@pytest.mark.unit
def test_empty_description_defaults_to_support():
    out = generate_adversarial_scenarios("")
    assert all("support" in s["persona_prompt"] for s in out)
