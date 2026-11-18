from model_hub.utils.prompt_config_key_renamer import rename_legacy_camelcase_keys


def test_rename_legacy_keys_simple_dict():
    data = {"maxTokens": 123, "topP": 0.9, "responseFormat": "text"}
    out, changed = rename_legacy_camelcase_keys(data)

    assert changed is True
    assert out == {"max_tokens": 123, "top_p": 0.9, "response_format": "text"}


def test_rename_legacy_keys_nested_list_and_dict():
    data = {
        "configuration": {"presencePenalty": 1, "frequencyPenalty": 2},
        "items": [{"voiceId": "alloy"}],
    }
    out, changed = rename_legacy_camelcase_keys(data)

    assert changed is True
    assert out["configuration"] == {"presence_penalty": 1, "frequency_penalty": 2}
    assert out["items"] == [{"voice_id": "alloy"}]


def test_rename_prefers_canonical_and_drops_legacy():
    data = {"max_tokens": 111, "maxTokens": 222}
    out, changed = rename_legacy_camelcase_keys(data)

    assert changed is True
    assert out == {"max_tokens": 111}


def test_rename_is_idempotent():
    data = {"max_tokens": 123, "top_p": 0.9}
    out, changed = rename_legacy_camelcase_keys(data)

    assert changed is False
    assert out is data
