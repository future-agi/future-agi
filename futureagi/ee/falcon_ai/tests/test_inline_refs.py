"""Unit tests for ``_inline_refs`` in ``ee.falcon_ai.llm_client``.

The helper flattens JSON Schema ``$defs`` / ``$ref`` so that schemas survive
upstream providers that reject those features in tool function declarations.
Every assertion below pins one observable contract of the helper.

These tests are intentionally pure-Python — no Django, no DB, no network —
so they run fast in CI and prevent regressions on the contract itself.
"""

from __future__ import annotations

import copy
import json
import sys
from typing import Any

import pytest
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Import the helper through a path that does not require the Falcon Django app
# to be installed. _inline_refs is a pure-Python function; the surrounding
# module imports httpx + structlog only, which are dev deps.
# ---------------------------------------------------------------------------
from ee.falcon_ai.llm_client import _inline_refs


# ---------------------------------------------------------------------------
# Helpers shared by the assertion suite
# ---------------------------------------------------------------------------


def _walk_count(node: Any, key: str) -> int:
    """Recursively count occurrences of ``key`` in dict/list structure."""
    if isinstance(node, dict):
        n = 1 if key in node else 0
        return n + sum(_walk_count(v, key) for v in node.values())
    if isinstance(node, list):
        return sum(_walk_count(x, key) for x in node)
    return 0


def _property_names(node: Any, out: set[str] | None = None) -> set[str]:
    """Collect every property name declared under any ``properties:`` block."""
    if out is None:
        out = set()
    if isinstance(node, dict):
        if isinstance(node.get("properties"), dict):
            out.update(node["properties"].keys())
        for v in node.values():
            _property_names(v, out)
    elif isinstance(node, list):
        for x in node:
            _property_names(x, out)
    return out


def _assert_no_refs(node: Any) -> None:
    """Assert no $defs / $ref keys anywhere in the structure."""
    assert _walk_count(node, "$defs") == 0, "leaked $defs"
    assert _walk_count(node, "$ref") == 0, "leaked $ref"


# ---------------------------------------------------------------------------
# Basic flatten — the bug the helper was written to fix
# ---------------------------------------------------------------------------


class TestBasicFlatten:
    def test_single_ref_inlines_target(self):
        schema = {
            "type": "object",
            "properties": {"cfg": {"$ref": "#/$defs/Cfg"}},
            "$defs": {"Cfg": {"type": "object", "properties": {"name": {"type": "string"}}}},
        }
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        assert flat["properties"]["cfg"]["type"] == "object"
        assert flat["properties"]["cfg"]["properties"]["name"] == {"type": "string"}

    def test_ref_inside_array_items(self):
        schema = {
            "type": "object",
            "properties": {"items": {"type": "array", "items": {"$ref": "#/$defs/Item"}}},
            "$defs": {"Item": {"type": "integer"}},
        }
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        assert flat["properties"]["items"]["items"] == {"type": "integer"}

    def test_same_def_referenced_multiple_times(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"$ref": "#/$defs/Shared"},
                "b": {"$ref": "#/$defs/Shared"},
                "c": {"$ref": "#/$defs/Shared"},
            },
            "$defs": {"Shared": {"type": "object", "properties": {"x": {"type": "string"}}}},
        }
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        for slot in ("a", "b", "c"):
            assert flat["properties"][slot]["type"] == "object"
            assert flat["properties"][slot]["properties"]["x"] == {"type": "string"}

    def test_nested_refs_resolve_transitively(self):
        schema = {
            "type": "object",
            "properties": {"top": {"$ref": "#/$defs/A"}},
            "$defs": {
                "A": {"type": "object", "properties": {"b": {"$ref": "#/$defs/B"}}},
                "B": {"type": "object", "properties": {"c": {"$ref": "#/$defs/C"}}},
                "C": {"type": "integer"},
            },
        }
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        assert (
            flat["properties"]["top"]["properties"]["b"]["properties"]["c"]
            == {"type": "integer"}
        )


# ---------------------------------------------------------------------------
# Sibling-key preservation — the reviewer's catch
# ---------------------------------------------------------------------------


class TestSiblingPreservation:
    def test_sibling_description_preserved(self):
        schema = {
            "type": "object",
            "properties": {
                "cfg": {
                    "$ref": "#/$defs/Cfg",
                    "description": "Per-slot description",
                }
            },
            "$defs": {"Cfg": {"type": "object", "properties": {"x": {"type": "string"}}}},
        }
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        assert flat["properties"]["cfg"]["description"] == "Per-slot description"

    def test_sibling_default_preserved(self):
        schema = {
            "type": "object",
            "properties": {
                "cfg": {
                    "$ref": "#/$defs/Cfg",
                    "default": {"name": "fallback"},
                }
            },
            "$defs": {"Cfg": {"type": "object", "properties": {"name": {"type": "string"}}}},
        }
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        assert flat["properties"]["cfg"]["default"] == {"name": "fallback"}

    def test_sibling_title_and_examples_preserved(self):
        schema = {
            "type": "object",
            "properties": {
                "cfg": {
                    "$ref": "#/$defs/Cfg",
                    "title": "Pinned title",
                    "examples": [{"name": "a"}, {"name": "b"}],
                }
            },
            "$defs": {"Cfg": {"type": "object", "properties": {"name": {"type": "string"}}}},
        }
        flat = _inline_refs(schema)
        assert flat["properties"]["cfg"]["title"] == "Pinned title"
        assert flat["properties"]["cfg"]["examples"] == [{"name": "a"}, {"name": "b"}]

    def test_sibling_wins_on_overlap_per_2020_12(self):
        """JSON Schema 2020-12: siblings constrain the referenced target; on key
        conflict the sibling wins."""
        schema = {
            "type": "object",
            "properties": {
                "cfg": {
                    "$ref": "#/$defs/Cfg",
                    "description": "OVERRIDE",
                }
            },
            "$defs": {
                "Cfg": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "description": "TARGET",
                }
            },
        }
        flat = _inline_refs(schema)
        assert flat["properties"]["cfg"]["description"] == "OVERRIDE"

    def test_sibling_value_can_contain_nested_ref(self):
        """A sibling key whose value contains its own $ref must also resolve."""
        schema = {
            "type": "object",
            "properties": {
                "x": {
                    "$ref": "#/$defs/Base",
                    "description": "with nested",
                    "examples": [{"$ref": "#/$defs/Example"}],
                }
            },
            "$defs": {
                "Base": {"type": "object", "properties": {"v": {"type": "integer"}}},
                "Example": {"v": 42},
            },
        }
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        assert flat["properties"]["x"]["examples"] == [{"v": 42}]
        assert flat["properties"]["x"]["description"] == "with nested"


# ---------------------------------------------------------------------------
# Pass-through / edge cases
# ---------------------------------------------------------------------------


class TestPassThrough:
    def test_primitives_returned_unchanged(self):
        assert _inline_refs(None) is None
        assert _inline_refs(42) == 42
        assert _inline_refs("hello") == "hello"
        assert _inline_refs(True) is True

    def test_empty_dict(self):
        assert _inline_refs({}) == {}

    def test_empty_list(self):
        assert _inline_refs([]) == []

    def test_schema_without_refs_is_unchanged_structurally(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string"},
                "b": {"type": "array", "items": {"type": "integer"}},
            },
        }
        before = copy.deepcopy(schema)
        flat = _inline_refs(schema)
        assert flat == schema
        # Input not mutated
        assert schema == before

    def test_ref_to_missing_def_is_passed_through(self):
        """If $ref points into #/$defs/ but the def doesn't exist, leave the
        node alone — Vertex will reject it loudly. Don't silently mangle."""
        schema = {"$ref": "#/$defs/Missing", "$defs": {"Other": {"type": "string"}}}
        flat = _inline_refs(schema)
        # $defs is dropped, $ref survives because no resolution happened
        assert "$ref" in flat
        assert "$defs" not in flat

    def test_external_ref_is_passed_through(self):
        """Refs not pointing into #/$defs/ are out of scope — left alone."""
        schema = {
            "type": "object",
            "properties": {"x": {"$ref": "https://example.com/schema.json"}},
        }
        flat = _inline_refs(schema)
        assert flat["properties"]["x"]["$ref"] == "https://example.com/schema.json"


# ---------------------------------------------------------------------------
# Safety guards — depth limit, mutation, serialisability
# ---------------------------------------------------------------------------


class TestSafety:
    def test_input_is_not_mutated(self):
        schema = {
            "type": "object",
            "properties": {"cfg": {"$ref": "#/$defs/Cfg"}},
            "$defs": {"Cfg": {"type": "object", "properties": {"x": {"type": "string"}}}},
        }
        snapshot = copy.deepcopy(schema)
        _inline_refs(schema)
        assert schema == snapshot, "input dict was mutated"

    def test_output_is_json_serialisable(self):
        schema = {
            "type": "object",
            "properties": {"cfg": {"$ref": "#/$defs/Cfg", "description": "x"}},
            "$defs": {"Cfg": {"type": "object", "properties": {"v": {"type": "integer"}}}},
        }
        json.dumps(_inline_refs(schema))  # must not raise

    def test_depth_limit_stops_runaway(self):
        """A self-referential $defs would infinite-loop without the depth guard."""
        schema = {"$ref": "#/$defs/Loop", "$defs": {"Loop": {"$ref": "#/$defs/Loop"}}}
        # Should return in bounded time (depth limit kicks in)
        result = _inline_refs(schema)
        # We don't care about the exact shape — just that it terminated and
        # is serialisable / valid Python.
        json.dumps(result)


# ---------------------------------------------------------------------------
# Real Pydantic schemas — the actual shape Falcon's tool registry produces
# ---------------------------------------------------------------------------


class TestRealPydanticSchemas:
    def test_pydantic_model_with_nested_field_flattens_cleanly(self):
        class Inner(BaseModel):
            name: str = Field(..., description="Inner name")

        class Outer(BaseModel):
            primary: Inner = Field(..., description="The primary inner")
            fallback: Inner = Field(default=None, description="The fallback inner")  # type: ignore[assignment]

        schema = Outer.model_json_schema()
        # sanity — Pydantic should produce $defs/$ref for the shared Inner type
        assert _walk_count(schema, "$defs") >= 1
        assert _walk_count(schema, "$ref") >= 1

        flat = _inline_refs(schema)
        _assert_no_refs(flat)

        # Per-slot descriptions must survive
        assert flat["properties"]["primary"]["description"] == "The primary inner"
        assert flat["properties"]["fallback"]["description"] == "The fallback inner"

        # Inner field description must also survive into the inlined target
        primary = flat["properties"]["primary"]
        assert primary["properties"]["name"]["description"] == "Inner name"

    def test_pydantic_deeply_nested_models_flatten_cleanly(self):
        class Leaf(BaseModel):
            v: int

        class Mid(BaseModel):
            leaf: Leaf

        class Top(BaseModel):
            mid: Mid

        schema = Top.model_json_schema()
        flat = _inline_refs(schema)
        _assert_no_refs(flat)
        # Walk all the way down to the leaf
        assert flat["properties"]["mid"]["properties"]["leaf"]["properties"]["v"]["type"] == "integer"

    def test_property_count_is_preserved(self):
        """Inlining must not silently drop any property name."""

        class Sub(BaseModel):
            a: str
            b: int
            c: bool

        class Top(BaseModel):
            x: Sub
            y: Sub
            z: str

        schema = Top.model_json_schema()
        flat = _inline_refs(schema)
        names_in = _property_names(schema)
        names_out = _property_names(flat)
        assert names_in.issubset(names_out), f"lost properties: {names_in - names_out}"


# ---------------------------------------------------------------------------
# MCP tools — external schemas we don't control. These often arrive with
# $defs / $ref because well-developed MCP servers reuse type definitions.
# The wrapper's ``input_schema`` returns the raw MCP inputSchema verbatim,
# so the helper is the only line of defence before the schema goes on the
# wire.
# ---------------------------------------------------------------------------


class TestMCPToolSchemas:
    def _build_mcp_wrapper(self, tool_schema: dict):
        """Construct an ``MCPToolWrapper`` against a fake connector + supplied
        tool schema, so we can exercise the same path the agent does at
        runtime."""

        # Stand-in connector object; only ``name`` is used by the wrapper
        class _Connector:
            name = "test_connector"

        # Import here so the rest of the test module stays importable even if
        # the wrapper has a heavy import side-effect later.
        from ee.falcon_ai.mcp_tools import MCPToolWrapper

        return MCPToolWrapper(_Connector(), tool_schema)

    def test_mcp_schema_with_defs_ref_flattens_cleanly(self):
        """Representative MCP server response: input contains a reused
        sub-type via $defs / $ref."""
        tool_schema = {
            "name": "fetch_records",
            "description": "Fetch records from the connector",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "primary_filter":  {"$ref": "#/$defs/Filter"},
                    "fallback_filter": {"$ref": "#/$defs/Filter"},
                },
                "required": ["primary_filter"],
                "$defs": {
                    "Filter": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string"},
                            "op": {"type": "string", "enum": ["eq", "neq", "gt", "lt"]},
                            "value": {"type": "string"},
                        },
                        "required": ["field", "op"],
                    }
                },
            },
        }
        wrapper = self._build_mcp_wrapper(tool_schema)

        # The path the agent takes: raw input_schema → flattener
        raw_schema = wrapper.input_schema
        flat = _inline_refs(raw_schema)
        _assert_no_refs(flat)
        # Both slots resolved to the full Filter shape
        for slot in ("primary_filter", "fallback_filter"):
            assert flat["properties"][slot]["type"] == "object"
            assert "field" in flat["properties"][slot]["properties"]
            assert flat["properties"][slot]["properties"]["op"]["enum"] == [
                "eq", "neq", "gt", "lt"
            ]

    def test_mcp_schema_with_per_slot_description_preserved(self):
        """MCP servers commonly attach a per-slot description on top of a
        shared $ref. That description must reach the model."""
        tool_schema = {
            "name": "search",
            "description": "Run a search",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include": {
                        "$ref": "#/$defs/Filter",
                        "description": "Records matching this filter are returned",
                    },
                    "exclude": {
                        "$ref": "#/$defs/Filter",
                        "description": "Records matching this filter are dropped",
                    },
                },
                "$defs": {
                    "Filter": {"type": "object", "properties": {"field": {"type": "string"}}}
                },
            },
        }
        wrapper = self._build_mcp_wrapper(tool_schema)
        flat = _inline_refs(wrapper.input_schema)
        _assert_no_refs(flat)
        assert (
            flat["properties"]["include"]["description"]
            == "Records matching this filter are returned"
        )
        assert (
            flat["properties"]["exclude"]["description"]
            == "Records matching this filter are dropped"
        )

    def test_mcp_schema_without_properties_returns_safe_default(self):
        """If an MCP server returns a tool with no inputSchema.properties,
        the wrapper substitutes a safe default. Flattener must accept that."""
        tool_schema = {"name": "ping", "description": "ping", "inputSchema": {}}
        wrapper = self._build_mcp_wrapper(tool_schema)
        flat = _inline_refs(wrapper.input_schema)
        assert flat == {"type": "object", "properties": {}}

    def test_mcp_schema_deeply_nested_refs(self):
        """Some MCP servers expose deeply nested record types. Flattener must
        resolve refs at every depth."""
        tool_schema = {
            "name": "upsert",
            "description": "Upsert nested record",
            "inputSchema": {
                "type": "object",
                "properties": {"record": {"$ref": "#/$defs/Record"}},
                "$defs": {
                    "Record": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "metadata": {"$ref": "#/$defs/Metadata"},
                        },
                    },
                    "Metadata": {
                        "type": "object",
                        "properties": {
                            "owner": {"$ref": "#/$defs/User"},
                        },
                    },
                    "User": {
                        "type": "object",
                        "properties": {"email": {"type": "string"}},
                    },
                },
            },
        }
        wrapper = self._build_mcp_wrapper(tool_schema)
        flat = _inline_refs(wrapper.input_schema)
        _assert_no_refs(flat)
        leaf = flat["properties"]["record"]["properties"]["metadata"]["properties"]["owner"]
        assert leaf == {"type": "object", "properties": {"email": {"type": "string"}}}
