"""The tools hash equals the gateway's (M17's ToolsHash), case for case:
fixtures/toolshash-vectors.json holds hashes Go computed."""

from __future__ import annotations

import base64
import json

import pytest
from conftest import FIXTURES
from connector_smoke import toolshash

VECTORS = json.loads((FIXTURES / "toolshash-vectors.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_matches_go(case):
    raw = base64.b64decode(case["input_b64"])
    if case["expected"] is None:
        with pytest.raises(toolshash.ToolsHashError):
            toolshash.tools_hash(toolshash.loads_go(raw))
        return
    tools = toolshash.loads_go(raw)
    assert toolshash.tools_hash(tools) == case["expected"]
    assert [toolshash.tool_hash(t) for t in tools] == case["per_tool"]


def test_vectors_cover_the_hard_cases():
    names = {c["name"] for c in VECTORS}
    for needed in (
        "number_literals_kept",
        "invalid_utf8_bytes_replaced_per_byte",
        "lone_surrogates_replaced",
        "duplicate_keys_last_wins",
        "line_separators_escaped",
        "non_object_tool_errors",
    ):
        assert needed in names


def test_nan_is_not_json():
    with pytest.raises(toolshash.ToolsHashError):
        toolshash.loads_go(b'[{"name":"x","inputSchema":{"maximum":NaN}}]')
