"""Unit tests for the voice display-attrs backfill's pure helpers."""

from tracer.scripts.backfill_voice_display_attrs import (
    _ENRICHABLE_PREDICATE,
    _MARKER_KEY,
    _PROVIDER_FILTER,
    _build_batch_insert_sql,
    _ch_str_lit,
    _extract_raw_log,
    _id_keyed_map_literal,
    _inner_map_literal,
    _parse_raw_log,
    _split_delta,
)
from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS


def test_parse_raw_log_from_json_string():
    # raw_log is stored as a JSON string under attrs_string['raw_log'].
    assert _parse_raw_log('{"id": "c1", "status": "ended"}') == {
        "id": "c1",
        "status": "ended",
    }


def test_parse_raw_log_dict_passthrough():
    assert _parse_raw_log({"id": "c2"}) == {"id": "c2"}


def test_parse_raw_log_bad_inputs():
    assert _parse_raw_log(None) == {}
    assert _parse_raw_log("") == {}
    assert _parse_raw_log("not json") == {}
    assert _parse_raw_log("[1, 2]") == {}  # JSON but not an object
    assert _parse_raw_log(123) == {}


def test_extract_raw_log_prefers_attributes_extra():
    # PR #1693: raw_log in attributes_extra (as a JSON string leaf).
    extra = '{"raw_log": "{\\"id\\": \\"from_extra\\"}"}'
    assert _extract_raw_log(extra, '{"id": "from_attrs_string"}') == {
        "id": "from_extra"
    }


def test_extract_raw_log_falls_back_to_attrs_string():
    # Pre-fix rows: attributes_extra empty/absent → use attrs_string['raw_log'].
    assert _extract_raw_log("{}", '{"id": "from_attrs_string"}') == {
        "id": "from_attrs_string"
    }
    assert _extract_raw_log(None, '{"id": "c1"}') == {"id": "c1"}
    assert _extract_raw_log("", '{"id": "c1"}') == {"id": "c1"}


def test_extract_raw_log_unparseable_extra_falls_back():
    # attributes_extra present but its raw_log leaf is junk → fall back.
    assert _extract_raw_log('{"raw_log": "not json"}', '{"id": "fb"}') == {"id": "fb"}


def test_extract_raw_log_neither_location():
    assert _extract_raw_log("{}", "") == {}
    assert _extract_raw_log('{"other": 1}', None) == {}


def test_ch_str_lit_escapes_quotes_and_backslashes():
    # The exact bug: apostrophes in summaries must escape, not switch quote style.
    assert _ch_str_lit("husband's policy") == "'husband\\'s policy'"
    assert _ch_str_lit("a\\b") == "'a\\\\b'"
    assert _ch_str_lit("+15551230000") == "'+15551230000'"


def test_ch_str_lit_escapes_backslash_before_quote():
    # Order matters: escape backslash first, else \' double-escapes wrong.
    assert _ch_str_lit("a\\'b") == "'a\\\\\\'b'"


def test_ch_str_lit_passes_control_and_unicode_verbatim():
    # newline/tab/unicode are NOT CH metacharacters inside '...'; only ' and \
    # are escaped, everything else passes through literally.
    assert _ch_str_lit("line1\nline2\ttab") == "'line1\nline2\ttab'"
    assert _ch_str_lit("café ☎ 日本") == "'café ☎ 日本'"
    assert _ch_str_lit("brace{}paren()") == "'brace{}paren()'"


def test_split_delta_forces_marker_on_string_only_delta():
    # C2: a delta with NO numeric key must still land call.message_count in
    # attrs_number, else the span never gets marked and re-scans forever.
    ns, nn, nb = _split_delta({"call.summary": "hi", "call.recording_available": True})
    assert ns == {"call.summary": "hi"}
    assert nb == {"call.recording_available": 1}
    assert nn == {_MARKER_KEY: 0.0}  # forced marker


def test_split_delta_keeps_real_message_count():
    ns, nn, nb = _split_delta({"call.message_count": 7, "call.summary": "x"})
    assert nn[_MARKER_KEY] == 7.0  # setdefault does not clobber the real count


def test_supported_provider_filter_uses_stored_lowercase_values():
    # Must match what CH stores; a mismatch silently zeroes the backfill.
    assert _PROVIDER_FILTER == (
        "provider IN ('vapi', 'retell', 'eleven_labs', 'bland', 'twilio')"
    )
    assert "livekit" not in _PROVIDER_FILTER and "others" not in _PROVIDER_FILTER


def test_enrichable_predicate_shape():
    assert "NOT mapContains(attrs_number, 'call.message_count')" in _ENRICHABLE_PREDICATE
    assert "rl != ''" in _ENRICHABLE_PREDICATE


def test_inner_map_literal_by_kind():
    assert _inner_map_literal({"call.summary": "hi"}, "str") == "map('call.summary', 'hi')"
    assert _inner_map_literal({"call.cost_cents": 2.29}, "num") == "map('call.cost_cents', 2.29)"
    assert _inner_map_literal({"call.recording_available": 1}, "bool") == "map('call.recording_available', 1)"


def test_inner_map_literal_escapes_string_values():
    assert _inner_map_literal({"call.summary": "it's here"}, "str") == "map('call.summary', 'it\\'s here')"


def test_id_keyed_map_literal_empty_is_none():
    # No row in the batch has this kind → leave the column untouched.
    assert _id_keyed_map_literal([], "num") is None


def test_id_keyed_map_literal_str_is_bare_nested_map():
    lit = _id_keyed_map_literal([("span1", {"call.summary": "hi"})], "str")
    assert lit == "map('span1', map('call.summary', 'hi'))"


def test_id_keyed_map_literal_num_and_bool_are_cast():
    n = _id_keyed_map_literal([("s1", {"call.message_count": 3})], "num")
    assert n == "CAST(map('s1', map('call.message_count', 3.0)) AS Map(String, Map(String, Float64)))"
    b = _id_keyed_map_literal([("s1", {"call.recording_available": 1})], "bool")
    assert b == "CAST(map('s1', map('call.recording_available', 1)) AS Map(String, Map(String, UInt8)))"


def test_id_keyed_map_literal_multiple_rows():
    lit = _id_keyed_map_literal(
        [("s1", {"call.summary": "a"}), ("s2", {"call.summary": "b"})], "str"
    )
    assert lit == "map('s1', map('call.summary', 'a'), 's2', map('call.summary', 'b'))"


def test_build_batch_insert_sql_shape_and_omits_version():
    batch = [
        ("s1", {"call.summary": "hi"}, {"call.message_count": 3}, {"call.recording_available": 1}),
    ]
    sql = _build_batch_insert_sql(batch)
    assert sql.startswith("INSERT INTO spans (")
    # Each map merged via an id-keyed lookup.
    assert "mapUpdate(attrs_string, map('s1', map('call.summary', 'hi'))[id])" in sql
    assert "mapUpdate(attrs_number, CAST(map('s1', map('call.message_count', 3.0))" in sql
    assert "mapUpdate(attrs_bool, CAST(map('s1', map('call.recording_available', 1))" in sql
    assert "now64(6)" in sql  # updated_at bumped
    # No FINAL; dedup via version ordering; ids bound for idx_id pruning.
    assert "FINAL" not in sql
    assert "ORDER BY _version DESC" in sql
    assert "LIMIT 1 BY id" in sql
    assert "id IN %(ids)s" in sql
    # _version is NOT an insert column, so its DEFAULT (fresh ns ts) wins dedup.
    assert "_version" not in CH_INSERT_COLUMNS


def test_build_batch_insert_sql_omits_absent_kinds():
    # A batch with only string deltas leaves attrs_number / attrs_bool untouched.
    batch = [("s1", {"call.summary": "hi"}, {}, {})]
    sql = _build_batch_insert_sql(batch)
    assert "mapUpdate(attrs_string" in sql
    assert "mapUpdate(attrs_number" not in sql
    assert "mapUpdate(attrs_bool" not in sql


def test_build_batch_insert_sql_all_empty_only_bumps_updated_at():
    # Degenerate batch (no deltas at all): no mapUpdate, just the updated_at bump.
    sql = _build_batch_insert_sql([("s1", {}, {}, {})])
    assert "mapUpdate" not in sql
    assert "now64(6)" in sql


def test_build_batch_insert_sql_multi_row_maps():
    batch = [
        ("s1", {"call.summary": "a"}, {"call.message_count": 1}, {}),
        ("s2", {"call.summary": "b"}, {"call.message_count": 2}, {}),
    ]
    sql = _build_batch_insert_sql(batch)
    assert "mapUpdate(attrs_string, map('s1', map('call.summary', 'a'), 's2', map('call.summary', 'b'))[id])" in sql
    assert "map('s1', map('call.message_count', 1.0), 's2', map('call.message_count', 2.0))" in sql


def test_build_batch_insert_sql_scopes_project_when_passed():
    batch = [("s1", {"call.summary": "hi"}, {}, {})]
    sql = _build_batch_insert_sql(batch, ["11111111-1111-1111-1111-111111111111"])
    assert "project_id IN (toUUID('11111111-1111-1111-1111-111111111111'))" in sql
    # …and no project scoping when omitted
    assert "project_id IN" not in _build_batch_insert_sql(batch)


def test_marker_key_is_message_count():
    assert _MARKER_KEY == "call.message_count"


def test_extract_display_attrs_vapi():
    from tracer.scripts.backfill_voice_display_attrs import (
        CALL_DISPLAY_ATTR_KEYS,
        extract_display_attrs,
    )
    from tracer.utils.otel import CallAttributes

    raw = {
        "type": "inboundPhoneCall",
        "status": "ended",
        "startedAt": "2026-06-19T12:00:00.000Z",
        "endedAt": "2026-06-19T12:01:00.000Z",
        "createdAt": "2026-06-19T11:59:00.000Z",
        "customer": {"number": "+15551230000"},
        "variableValues": {"phoneNumber": "+18005551234"},
        "summary": "Booked a demo",
        "cost": 0.25,
        "assistantId": "asst_9",
        "messages": [
            {"role": "assistant", "message": "Hi", "duration": 2000,
             "secondsFromStart": 0, "time": 1700000000000},
            {"role": "user", "message": "Hello", "duration": 1000,
             "secondsFromStart": 2, "time": 1700000002000},
        ],
    }
    out = extract_display_attrs(raw, "vapi")
    assert out and set(out).issubset(CALL_DISPLAY_ATTR_KEYS)
    assert out[CallAttributes.STATUS_DISPLAY] == "completed"
    assert out[CallAttributes.CALL_TYPE] == "inbound"
    assert out[CallAttributes.CUSTOMER_NAME] == "+15551230000"
    assert out[CallAttributes.ASSISTANT_PHONE_NUMBER] == "+18005551234"


def test_extract_display_attrs_retell():
    from tracer.scripts.backfill_voice_display_attrs import (
        CALL_DISPLAY_ATTR_KEYS,
        extract_display_attrs,
    )
    from tracer.utils.otel import CallAttributes

    raw = {
        "call_id": "c1",
        "agent_id": "agent_9",
        "agent_name": "Healthcare Check-In",
        "call_status": "ended",
        "direction": "inbound",
        "to_number": "+12345162722",
        "from_number": "+18568806998",
        "start_timestamp": 1763541469420,
        "end_timestamp": 1763541606176,
        "call_cost": {"combined_cost": 29.68, "product_costs": []},
        "call_analysis": {"call_summary": "Confirmed."},
        "transcript_with_tool_calls": [
            {"role": "agent", "content": "Hi", "words": [{"start": 2.0, "end": 5.0}]},
        ],
    }
    out = extract_display_attrs(raw, "retell")
    assert out and set(out).issubset(CALL_DISPLAY_ATTR_KEYS)
    assert out[CallAttributes.STATUS_DISPLAY] == "completed"
    assert out[CallAttributes.CALL_TYPE] == "inbound"


def test_extract_display_attrs_empty_and_unknown():
    from tracer.scripts.backfill_voice_display_attrs import (
        extract_display_attrs,
    )

    assert extract_display_attrs({}, "vapi") == {}
    assert extract_display_attrs(None, "vapi") == {}
    assert extract_display_attrs({"id": "x"}, "no_such_provider") == {}
