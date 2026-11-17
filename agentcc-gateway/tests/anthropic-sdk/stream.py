"""Official SDK accumulation contract; only the loopback Go fixture is reachable."""
import json
import os
import sys
from urllib.parse import urlparse

import anthropic

base_url, model, scenario = sys.argv[1:]
assert urlparse(base_url).hostname == "127.0.0.1"
assert anthropic.__version__ == "2.3.0", anthropic.__version__
client = anthropic.Anthropic(
    base_url=base_url, api_key=os.environ["SDK_FIXTURE_KEY"], max_retries=0, timeout=15
)
params = dict(model=model, max_tokens=64, messages=[{"role": "user", "content": "hi"}])
if scenario == "tool":
    params["tools"] = [{"name": "weather", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]

text = ""
try:
    with client.messages.stream(**params) as stream:
        for delta in stream.text_stream:
            text += delta
        final = stream.get_final_message()
except anthropic.APIError as exc:
    # A native error event must raise a typed APIError. Never swallow the
    # AttributeError/TypeError that broken SDK accumulation used to trigger.
    assert scenario == "error", repr(exc)
    assert "synthetic stream failure" in str(exc), repr(exc)
    assert text == "Hello world", text
    print(json.dumps({"sdk": "python", "scenario": scenario, "expected_api_error": type(exc).__name__, "partial_text": text}))
else:
    assert scenario != "error", "upstream failure was silently accepted"
    assert final.stop_sequence is None
    assert final.usage.input_tokens > 0
    assert final.usage.output_tokens > 0
    if scenario != "estimated":
        assert final.usage.input_tokens == 9
        assert final.usage.output_tokens == 4
    if scenario == "tool":
        assert text == ""
        assert final.stop_reason == "tool_use"
        assert len(final.content) == 1
        block = final.content[0]
        assert block.type == "tool_use" and block.id == "toolu_test"
        assert block.name == "weather" and block.input == {"city": "Paris"}
    else:
        assert text == "Hello world", text
        assert final.stop_reason == "end_turn"
        assert final.content[-1].type == "text" and final.content[-1].text == text
        if scenario == "thinking" and os.environ["SDK_FIXTURE_NATIVE"] == "true":
            assert len(final.content) == 2
            assert final.content[0].type == "thinking"
            assert final.content[0].thinking == "Consider"
            assert final.content[0].signature == "synthetic-signature"
        else:
            # Canonical reasoning_content is unsupported, not a thinking block.
            assert len(final.content) == 1
            assert final.content[0].type == "text" and final.content[0].text == text
    print(json.dumps({"sdk": "python", "scenario": scenario, "text": text, "usage": final.usage.model_dump(), "stop_reason": final.stop_reason}))
finally:
    client.close()
