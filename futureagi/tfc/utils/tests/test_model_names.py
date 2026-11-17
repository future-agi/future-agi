"""The one model id normalizer the gates, model config lookup and metering
share: a provider-prefixed model id is treated as its base model."""

from tfc.utils.model_names import base_model_name


def test_base_model_name_is_the_lowercased_last_segment():
    assert base_model_name("x/turing_small") == "turing_small"
    assert base_model_name("a/b/Turing_Flash") == "turing_flash"
    assert base_model_name("openai/gpt-4o") == "gpt-4o"


def test_base_model_name_keeps_plain_names_unchanged():
    assert base_model_name("turing_small") == "turing_small"
    assert base_model_name("gpt-4o") == "gpt-4o"


def test_base_model_name_is_empty_for_an_empty_or_missing_name():
    assert base_model_name(None) == ""
    assert base_model_name("") == ""


def test_base_model_name_keeps_case_when_asked():
    assert base_model_name("x/Turing_Small", lower=False) == "Turing_Small"
    assert base_model_name("gpt-4o", lower=False) == "gpt-4o"
