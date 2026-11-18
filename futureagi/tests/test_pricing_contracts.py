"""The shapes ``lookup_pricing`` and ``listed_pricing`` return are typed, and pinned.

A price keeps the model it was asked for and the unit it is quoted in, so a caller
never reads a per-image price as a per-token one. No database and no services.
"""

from __future__ import annotations

import typing
from datetime import UTC, datetime

import pytest

from agentic_eval.core_evals.run_prompt import model_pricing
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
from agentic_eval.core_evals.run_prompt.model_pricing import (
    get_model_pricing,
    listed_pricing,
    lookup_pricing,
)

J2 = datetime(2027, 1, 2, tzinfo=UTC)
CONTRACT_NAMES = (
    "TokenPricing",
    "CharacterPricing",
    "ThousandCharacterPricing",
    "MinutePricing",
    "AudioMinutePricing",
    "ImagePricing",
    "OtherPricing",
)


def _contract(name):
    # Looked up in the test, so a missing contract fails that test alone.
    return getattr(model_pricing, name)


def _contracts():
    return tuple(_contract(name) for name in CONTRACT_NAMES)


def _keys(contract):
    return set(
        typing.get_type_hints(
            _contract(contract) if isinstance(contract, str) else contract
        )
    )


def test_the_pricing_contracts_are_typed_dicts_with_these_keys():
    assert _keys("TokenPricing") == {
        "input_per_1M_tokens",
        "output_per_1M_tokens",
        "cached_input_per_1M_tokens",
        "input_per_image",
        "image_input_per_image",
    }
    assert _keys("CharacterPricing") == {"input_per_1M_characters"}
    assert _keys("ThousandCharacterPricing") == {
        "input_per_1k_characters",
        "output_per_1k_characters",
    }
    assert _keys("MinutePricing") == {"input_per_minute"}
    assert _keys("AudioMinutePricing") == {"per_minute_audio"}
    assert _keys("ImagePricing") == {
        "per_image",
        "low_quality_per_image",
        "medium_quality_per_image",
        "high_quality_per_image",
    }
    assert _keys("OtherPricing") == {"per_1M_tokens", "per_1000_pairs", "details"}
    for contract in _contracts():
        assert typing.is_typeddict(contract)
    # A speech or per-minute price is only ever that one key.
    assert _contract("CharacterPricing").__required_keys__ == {
        "input_per_1M_characters"
    }
    assert _contract("MinutePricing").__required_keys__ == {"input_per_minute"}
    assert _contract("TokenPricing").__required_keys__ == frozenset()
    assert set(typing.get_args(_contract("Pricing"))) == set(_contracts())


def test_every_catalogue_price_is_one_of_the_contracts():
    allowed = set().union(*(_keys(contract) for contract in CONTRACT_NAMES))
    priced = [row for row in AVAILABLE_MODELS if "pricing" in row]

    assert priced
    for row in priced:
        assert set(row["pricing"]) <= allowed, row["model_name"]
        assert model_pricing.pricing_unit(row["pricing"]) != "other" or set(
            row["pricing"]
        ) & {
            "per_1M_tokens",
            "per_1000_pairs",
            "details",
        }, row["model_name"]


def test_a_lookup_keeps_the_model_asked_for_and_the_unit_of_its_price():
    PricingLookup = model_pricing.PricingLookup
    assert PricingLookup._fields == ("pricing", "source", "key", "model")

    found = lookup_pricing("gemini/gemini-3.7-flash", at=J2)

    assert isinstance(found, PricingLookup)
    assert found.model == "gemini/gemini-3.7-flash"
    assert found.source == "gateway_table"
    assert found.unit == "per_1M_tokens"
    assert set(found.pricing) <= _keys("TokenPricing")
    assert found.pricing == {
        "input_per_1M_tokens": 1.5,
        "output_per_1M_tokens": 7.5,
        "cached_input_per_1M_tokens": 0.15,
    }
    assert typing.get_args(PricingLookup.__annotations__["source"]) == (
        "decision",
        "gateway_table",
        "available_models",
    )


@pytest.mark.parametrize(
    ("name", "non_token_call", "unit", "contract"),
    [
        ("gpt-4o", False, "per_1M_tokens", "TokenPricing"),
        ("256-x-256/dall-e-2", True, "per_image", "ImagePricing"),
        ("flux-general-en", True, "per_minute", "MinutePricing"),
        ("azure/whisper-1", True, "per_minute", "AudioMinutePricing"),
        ("blizzard", True, "per_1M_characters", "CharacterPricing"),
    ],
)
def test_a_lookup_names_the_unit_of_each_kind_of_price(
    name, non_token_call, unit, contract
):
    found = lookup_pricing(name, non_token_call=non_token_call)

    assert found is not None
    assert found.model == name
    assert found.unit == unit
    assert set(found.pricing) <= _keys(contract)
    assert get_model_pricing(name, non_token_call=non_token_call) == found.pricing


def test_a_decision_model_lookup_keeps_its_model_and_has_no_key(monkeypatch):
    from evaluations import decision_models

    name = "a-decision-model"
    monkeypatch.setattr(decision_models, "is_decision_model", lambda model: True)
    monkeypatch.setattr(
        "evaluations.decision_pricing.calculate_decision_cost",
        lambda model, usage: {"total_cost": 2.0},
    )

    found = lookup_pricing(name)

    assert found == model_pricing.PricingLookup(
        {"input_per_1M_tokens": 2.0, "output_per_1M_tokens": 0.0},
        "decision",
        None,
        name,
    )
    assert found.unit == "per_1M_tokens"


def test_the_unit_is_read_from_the_keys():
    pricing_unit = model_pricing.pricing_unit
    assert pricing_unit({"input_per_1M_tokens": 1.0}) == "per_1M_tokens"
    assert pricing_unit({"output_per_1M_tokens": 1.0}) == "per_1M_tokens"
    assert pricing_unit({"input_per_1k_characters": 1.0}) == "per_1k_characters"
    assert pricing_unit({"per_1000_pairs": 1.0}) == "other"
    assert pricing_unit({}) == "other"


def test_listed_pricing_returns_the_contract_of_the_row_or_none():
    chat = listed_pricing({"model_name": "gpt-4o", "mode": "chat"})
    image = listed_pricing(
        {"model_name": "256-x-256/dall-e-2", "mode": "image_generation"}
    )

    assert chat is not None
    assert model_pricing.pricing_unit(chat) == "per_1M_tokens"
    assert image == {"per_image": 0.016}
    assert model_pricing.pricing_unit(image) == "per_image"
    assert listed_pricing({"model_name": "gpt-4o", "custom": True}) is None
    assert listed_pricing({"model_name": "no-such-model-in-any-table"}) is None


def test_listed_pricing_documents_its_none_and_its_non_token_results():
    doc = listed_pricing.__doc__

    assert "None when" in doc
    for phrase in ("custom", "no table prices", "per_image", "pricing_unit"):
        assert phrase in doc
    pricing = _contract("Pricing")
    assert typing.get_type_hints(model_pricing.listed_pricing)["return"] == (
        pricing | None
    )
    assert typing.get_type_hints(model_pricing.get_model_pricing)["return"] == (
        pricing | None
    )
