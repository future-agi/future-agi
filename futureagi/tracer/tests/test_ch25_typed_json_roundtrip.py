"""Bullet-proof regression test for CH 25.3 attributes_extra type preservation.

Background — the bug this test guards against:
    The original schema declared `attributes_extra` as `JSON(max_dynamic_paths=0)`
    on the rationale that overflow attributes should land in the shared sub-
    column store (preventing sub-column explosion on customer payloads).
    Empirically — see DECISIONS #028 and schema 013 — CH 25.3's typed JSON
    storage stringifies numeric leaves nested INSIDE ARRAYS regardless of
    max_dynamic_paths. Surfaced in production via Vapi voice traces where
    `raw_log.artifact.messages[i].duration` came back as `"932"` instead of
    `932`, and `_process_vapi_logs` crashed on `str / int`.

    Schema 013 changed `attributes_extra` to plain `String CODEC(ZSTD(3))`,
    which preserves the original JSON text verbatim. This test asserts that
    contract end-to-end against a live CH 25.3 instance.

When to run:
    Integration test — requires the local CH 25.3 container at port 19001
    (the migration test rig). Marked `integration` so unit-test runs skip
    it. CI runs it as part of the migration validation suite.

What it does:
    1. Creates a temp `_test_roundtrip_<uuid>` table with one row whose
       `attributes_extra` is a JSON blob containing every leaf-type-in-array
       case we know breaks: int, float, bool, null, nested arrays.
    2. Reads the value back and parses with `json.loads`.
    3. Asserts each leaf round-trips byte-for-byte with the original Python
       types preserved.

If this test ever fails:
    Either schema 013 was reverted, or someone changed the column type back
    to typed JSON. The fix is to keep `attributes_extra` as String. The
    typed-JSON sub-column path indexes are unused on this column anyway —
    we have typed Maps (`attrs_string`/`attrs_number`/`attrs_bool`) for
    queryable attribute access.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

try:
    import clickhouse_connect
except ImportError:  # pragma: no cover
    clickhouse_connect = None


CH_HOST = os.environ.get("CH25_HOST", "127.0.0.1")
CH_PORT = int(os.environ.get("CH25_HTTP_PORT", "19001"))


# NOTE (codex P3 finding 2026-05-26): the previous implementation called
# `_ch_available()` during collection via `pytest.mark.skipif`. That opened
# a CH client BEFORE any test ran, so `pytest --collect-only` and
# unrelated discovery (e.g. CI test sharding, IDE test discovery) had to
# wait on a network round-trip to CH 25.3 and could fail flakily if the
# sidecar was warming up. We now defer the availability check to fixture
# time — the marker stays `@pytest.mark.integration` (selectable via
# `-m integration`/`-m 'not integration'`) and the actual reachability
# probe runs only when a test in this file is actually executed.

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True, scope="module")
def _require_ch25():
    """Fixture-time reachability gate (replaces collection-time skipif)."""
    if clickhouse_connect is None:
        pytest.skip("clickhouse-connect not installed")
    try:
        c = clickhouse_connect.get_client(
            host=CH_HOST, port=CH_PORT, send_receive_timeout=5
        )
        c.command("SELECT 1")
    except Exception as exc:
        pytest.skip(
            f"CH 25.3 not reachable on {CH_HOST}:{CH_PORT} ({exc!r}); "
            f"integration test"
        )


# Fixture payloads. Each is a representative shape for a (observation_type,
# provider) combo flagged as affected in the D-028 audit. NOT a 1:1 mirror —
# `_FIXTURES` is a hand-maintained subset that exercises the failure modes
# the audit found, not an exhaustive enumeration of every customer payload
# shape per combo. If the audit grows new affected combos (e.g. anthropic,
# langchain, langgraph), add a fixture here AND a `# Provider:` comment that
# matches the audit's combo label. Drift between this list and shape_audit
# is acceptable for unaffected shapes (overflow column is String anyway) but
# unacceptable for new affected shapes — those need explicit fixtures.

_FIXTURES: dict[str, dict] = {
    # conversation/vapi — the original incident. raw_log.artifact.messages[i].duration
    # is int(ms), `_process_vapi_logs` divides by 1000.
    "conversation_vapi": {
        "raw_log": {
            "artifact": {
                "messages": [
                    {"role": "user", "duration": 932, "secondsFromStart": 0.501},
                    {"role": "bot", "duration": 871, "secondsFromStart": 1.443},
                ],
            },
            "costBreakdown": {"stt": 0.04, "llm": 0.15, "tts": 0.08, "transport": 0.0},
            "metadata": {"is_simulator": False, "user_count": 1, "agent_count": 0},
        }
    },
    # conversation/retell — transcript[i].words[j].{start,end} are floats.
    # Used by _process_retell_logs for per-word latency aggregation.
    "conversation_retell": {
        "transcript_object": [
            {
                "role": "agent",
                "words": [
                    {"word": "Hello", "start": 0.123, "end": 0.456},
                    {"word": "world", "start": 0.500, "end": 0.890},
                ],
            },
            {
                "role": "user",
                "words": [
                    {"word": "Hi", "start": 1.234, "end": 1.456},
                ],
            },
        ],
    },
    # llm/openai — output.value.logprobs[i].logprob, OTel streaming chunks.
    "llm_openai": {
        "output": {
            "value": {
                "logprobs": [
                    {"token": "Hi", "logprob": -0.0008923, "top_5": [-0.01, -0.5, -1.2]},
                    {"token": ".", "logprob": -2.7e-05, "top_5": [-0.001]},
                ],
                "usage": {"prompt_tokens": 47, "completion_tokens": 138, "total_tokens": 185},
            },
        },
    },
    # llm/google + llm/gcp.vertex.agent — GenAI OTel candidates_tokens_details
    "llm_google": {
        "usage_metadata": {
            "prompt_token_count": 47,
            "candidates_token_count": 138,
            "candidates_tokens_details": [
                {"modality": "TEXT", "token_count": 136},
                {"modality": "IMAGE", "token_count": 2},
            ],
        },
    },
    # tool spans — schema.parameters.additionalProperties is bool; was → "false"
    "tool_with_bool_schema": {
        "span_attributes": {
            "tool.parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        },
    },
    # Worst-case nested: deeply-nested arrays with mixed-type leaves at every depth
    "deeply_nested_mixed": {
        "deeply": {
            "nested": [
                {
                    "inside": [
                        {
                            "an": {
                                "array": [
                                    {"with": 42, "and": 3.14, "or": True, "maybe": None}
                                ],
                            },
                        },
                    ],
                },
            ],
        },
    },
    # Large 64-bit integers that JS-serialization could lossy-convert.
    "large_ints": {
        "ts_ms": [1773816575961, 1773816575962, 1773816575963],
        "ids":   [9007199254740992, -9007199254740992],
    },
}

# Compatibility alias for the original test that takes the full superset.
_NESTED_FIXTURE = {
    "raw_log": {
        **_FIXTURES["conversation_vapi"]["raw_log"],
        "logprobs": _FIXTURES["llm_openai"]["output"]["value"]["logprobs"],
        "deeply": _FIXTURES["deeply_nested_mixed"]["deeply"],
    }
}


@pytest.fixture(scope="module")
def ch_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, send_receive_timeout=30
    )


@pytest.fixture()
def temp_table(ch_client):
    """Make + drop a temp table with `attributes_extra String`. We don't reuse
    the real `spans` table because (a) we don't want test rows in it and
    (b) the temp table mirrors the column type contract independent of
    other schema columns.
    """
    name = f"_test_roundtrip_{uuid.uuid4().hex[:8]}"
    ch_client.command(
        f"CREATE TABLE {name} (id String, attributes_extra String) "
        f"ENGINE=MergeTree ORDER BY id"
    )
    yield name
    ch_client.command(f"DROP TABLE {name}")


def _walk_and_assert(expected, actual, path=""):
    """Recursive type-and-value parity check. Raises AssertionError with a
    JSONPath-like trail on the first divergence, so failures are debuggable
    without printing the whole 2KB payload.
    """
    assert type(expected) is type(actual), (
        f"type mismatch at {path}: expected {type(expected).__name__}, "
        f"got {type(actual).__name__}; expected_value={expected!r} actual_value={actual!r}"
    )
    if isinstance(expected, dict):
        assert set(expected.keys()) == set(actual.keys()), (
            f"key set mismatch at {path}: "
            f"missing={set(expected) - set(actual)} extra={set(actual) - set(expected)}"
        )
        for k in expected:
            _walk_and_assert(expected[k], actual[k], f"{path}.{k}")
    elif isinstance(expected, list):
        assert len(expected) == len(actual), (
            f"list length mismatch at {path}: expected {len(expected)}, got {len(actual)}"
        )
        for i, (e, a) in enumerate(zip(expected, actual)):
            _walk_and_assert(e, a, f"{path}[{i}]")
    else:
        assert expected == actual, (
            f"value mismatch at {path}: expected {expected!r}, got {actual!r}"
        )


def test_string_column_preserves_numeric_leaves_in_arrays(ch_client, temp_table):
    """End-to-end round-trip: write nested numerics in arrays, read back, parse,
    verify Python type identity. The actual bug fix.
    """
    json_text = json.dumps(_NESTED_FIXTURE)
    ch_client.insert(
        temp_table,
        [["a", json_text]],
        column_names=["id", "attributes_extra"],
    )

    rows = ch_client.query(
        f"SELECT attributes_extra FROM {temp_table} WHERE id = 'a'"
    ).result_rows
    assert len(rows) == 1

    raw = rows[0][0]
    assert isinstance(raw, str), (
        f"attributes_extra returned as {type(raw).__name__}; "
        f"expected String — schema 013 may have been reverted"
    )
    actual = json.loads(raw)
    _walk_and_assert(_NESTED_FIXTURE, actual)


def test_spans_table_attributes_extra_is_string(ch_client):
    """Guard: the real `spans` table's `attributes_extra` MUST be String type.
    If anyone re-applies a schema that changes this to JSON, this fails
    immediately — protects us against silently reverting the fix.
    """
    rows = ch_client.query(
        "SELECT type FROM system.columns "
        "WHERE database = 'default' AND table = 'spans' "
        "  AND name = 'attributes_extra'"
    ).result_rows
    assert rows, "spans.attributes_extra column missing"
    actual_type = rows[0][0]
    assert actual_type == "String", (
        f"spans.attributes_extra is {actual_type!r}; expected 'String'. "
        f"If this changed back to a JSON variant, voice/eval providers will "
        f"silently break on numeric-leaf type stringification. See schema 013 + DECISIONS #028."
    )


# Full schema contract guard. attributes_extra MUST be String (schema 013,
# exact match — this is the load-bearing fix). The other two typed-JSON
# columns store metadata without nested-array numerics — they keep typed-
# JSON for path-access via system.json indexes. Per codex P3 finding
# 2026-05-26, those two are matched as "any JSON(...)" rather than the
# exact rendered type string, so the test survives CH 25.x patch versions
# that re-canonicalize the JSON column DDL.
_EXPECTED_SPANS_TYPES = {
    # column: (kind, matcher) where matcher is either:
    #   "exact" — type string must match `expected` exactly
    #   "prefix" — type string must start with `expected`
    "attributes_extra": ("exact",  "String"),
    "resource_attrs":   ("prefix", "JSON("),
    "metadata":         ("prefix", "JSON("),
}


def test_spans_table_typed_json_contract(ch_client):
    """Single source of truth for which spans columns are String vs typed JSON.
    Fails fast on any divergence so the contract can't drift silently.

    Also asserts the FULL contract documented in D-025: `attributes_extra`
    must be `String` AND use `CODEC(ZSTD(3))` AND default to `'{}'`. The
    codec choice is correctness-adjacent — losing ZSTD would silently
    inflate prod storage; losing the default would break inserts that
    omit the column.
    """
    rows = ch_client.query(
        "SELECT name, type FROM system.columns "
        "WHERE database = 'default' AND table = 'spans' "
        "  AND name IN ('attributes_extra', 'resource_attrs', 'metadata')"
    ).result_rows
    actual = {name: type_ for name, type_ in rows}
    missing = set(_EXPECTED_SPANS_TYPES) - set(actual)
    assert not missing, f"spans table missing columns: {sorted(missing)}"
    for col, (kind, expected) in _EXPECTED_SPANS_TYPES.items():
        got = actual[col]
        if kind == "exact":
            ok = got == expected
        elif kind == "prefix":
            ok = got.startswith(expected)
        else:  # pragma: no cover — programmer error
            raise ValueError(f"unknown kind {kind!r} in _EXPECTED_SPANS_TYPES")
        assert ok, (
            f"spans.{col}: expected {kind}-match {expected!r}, got {got!r}. "
            f"If you changed this on purpose, update _EXPECTED_SPANS_TYPES and "
            f"verify _process_vapi_logs / _process_retell_logs / eval reader "
            f"still type-preserve nested-array leaves."
        )

    # Codec + default contract for `attributes_extra` specifically.
    # `system.columns` exposes these as separate columns; check via SHOW CREATE
    # which embeds the column-level codec and default in the table DDL.
    ddl_rows = ch_client.query(
        "SELECT create_table_query FROM system.tables "
        "WHERE database = 'default' AND name = 'spans'"
    ).result_rows
    assert ddl_rows, "spans table missing from system.tables"
    ddl = ddl_rows[0][0]
    # Be tolerant of CH's DDL canonicalization (whitespace, quoting) but pin
    # both substrings — if either is missing, the column was re-created
    # without the documented codec/default.
    assert "attributes_extra" in ddl, "attributes_extra missing from spans DDL"
    assert "ZSTD(3)" in ddl, (
        "attributes_extra codec is not ZSTD(3) — schema 013 contract violated. "
        "Re-apply schema/013_attributes_extra_as_string.sql with the canonical "
        "ALTER documented in DECISIONS #025."
    )
    # The default literal CH stores as `'{}'` (with quotes); accept either
    # shape since CH may canonicalize.
    assert "'{}'" in ddl or "DEFAULT '{}'" in ddl, (
        "attributes_extra default '{}' not present in DDL — inserts that "
        "omit the column will fail with NOT NULL violation. Re-apply schema 013."
    )


@pytest.mark.parametrize("shape_name", sorted(_FIXTURES.keys()))
def test_real_provider_shapes_roundtrip(ch_client, temp_table, shape_name):
    """For each (observation_type, provider) shape catalogued in shape_audit,
    write the payload as attributes_extra → read back → assert Python type
    identity at every leaf. This is the bulletproof check that schema 013
    covers all real-world data shapes, not just the synthetic Vapi case.

    A failure here points to a NEW shape that the typed-JSON regression
    affects — investigate the column type and either expand schema 013 or
    document why the new shape is exempt.
    """
    payload = _FIXTURES[shape_name]
    json_text = json.dumps(payload)
    ch_client.insert(
        temp_table,
        [[shape_name, json_text]],
        column_names=["id", "attributes_extra"],
    )
    rows = ch_client.query(
        f"SELECT attributes_extra FROM {temp_table} WHERE id = %(id)s",
        parameters={"id": shape_name},
    ).result_rows
    assert len(rows) == 1
    raw = rows[0][0]
    assert isinstance(raw, str), (
        f"attributes_extra returned as {type(raw).__name__}; "
        f"expected String — schema 013 may have been reverted"
    )
    _walk_and_assert(payload, json.loads(raw), path=shape_name)
