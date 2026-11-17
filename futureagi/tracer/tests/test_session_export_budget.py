"""Session dataset preflight returns bounded identities and sizes, not payloads."""
from unittest.mock import MagicMock, patch

import pytest

from tracer.services.clickhouse.v2.span_reader import CHSpanReader


def test_session_export_manifest_projects_sizes_and_limits_before_hydration():
    reader = object.__new__(CHSpanReader)
    reader._client = MagicMock()
    reader._client.query.return_value.result_rows = []
    with patch.object(reader, "resolve_ids", return_value=({"session": ["session"]}, {"session": "session"})):
        roots = reader.session_export_roots(
            ["session"], project_id="project", max_turns=10,
            max_payload_bytes=1024, include_turns=True,
        )
    assert roots == []
    sql = reader._client.query.call_args.args[0]
    projection = sql.split(" FROM (")[0]
    assert "length(input) + length(output)" in projection
    assert "attributes_extra" not in projection
    assert "LIMIT 11" in sql
    assert reader._client.query.call_args.kwargs["parameters"]["pid"] == "project"


@pytest.mark.parametrize("count, payload_bytes, message", [
    (3, 1, "2 turns"), (1, 201, "200 payload bytes"),
])
def test_session_export_manifest_refuses_over_budget(count, payload_bytes, message):
    reader = object.__new__(CHSpanReader)
    reader._client = MagicMock()
    reader._client.query.return_value.result_rows = [
        (f"root-{i}", "project", f"trace-{i}", "session", None, None, payload_bytes)
        for i in range(count)
    ]
    with patch.object(reader, "resolve_ids", return_value=({"session": ["session"]}, {"session": "session"})):
        with pytest.raises(ValueError, match=message):
            reader.session_export_roots(
                ["session"], project_id="project", max_turns=2,
                max_payload_bytes=200, include_turns=True,
            )


def test_session_export_manifest_does_not_measure_unmapped_turns():
    reader = object.__new__(CHSpanReader)
    reader._client = MagicMock()
    reader._client.query.return_value.result_rows = []
    with patch.object(reader, "resolve_ids", return_value=({"session": ["session"]}, {"session": "session"})):
        reader.session_export_roots(
            ["session"], project_id="project", max_turns=10,
            max_payload_bytes=1024, include_turns=False,
        )
    projection = reader._client.query.call_args.args[0].split(" FROM (")[0]
    assert "0 AS payload_bytes" in projection
    assert "length(input)" not in projection


def test_session_hydration_rechecks_bytes_on_server_after_preflight():
    reader = object.__new__(CHSpanReader)
    reader._client = MagicMock()
    reader._client.query.return_value.result_rows = [("root", None, None)]
    with pytest.raises(ValueError, match="200 payload bytes"):
        reader.export_fields_by_ids(
            ["root"], {"input", "output"}, project_id="project", org_id="org",
            max_payload_bytes=200,
        )
    call = reader._client.query.call_args
    assert "sum(length(input) + length(output)) OVER ()" in call.args[0]
    assert "if(export_bytes <= %(export_max_bytes)s, input, NULL)" in call.args[0]
    assert "if(export_bytes <= %(export_max_bytes)s, output, NULL)" in call.args[0]
    assert call.kwargs["parameters"]["export_max_bytes"] == 200
    assert call.kwargs["parameters"]["pid"] == "project"
    assert call.kwargs["parameters"]["oid"] == "org"


def test_session_hydration_under_budget_keeps_input_and_output():
    reader = object.__new__(CHSpanReader)
    reader._client = MagicMock()
    reader._client.query.return_value.result_rows = [("root", '"question"', '"answer"')]
    assert reader.export_fields_by_ids(
        ["root"], {"input", "output"}, project_id="project", max_payload_bytes=200,
    ) == {"root": {"id": "root", "input": '"question"', "output": '"answer"'}}


@pytest.mark.integration
def test_session_export_budget_queries_against_clickhouse():
    import uuid

    from tracer.services.clickhouse.v2 import get_reader
    from tracer.tests.test_span_reader_remap_roots import _root
    from tracer.tests.test_session_trace_ids_prefilter import _client

    client = _client()
    project, session, trace = [str(uuid.uuid4()) for _ in range(3)]
    try:
        root_id = _root(
            client, project, trace, session=session,
            input='"question"', output='"answer"',
        )
        with get_reader() as reader:
            roots = reader.session_export_roots(
                [session], project_id=project, max_turns=1,
                max_payload_bytes=18, include_turns=True,
            )
            assert len(roots) == 1
            assert roots[0].id == root_id
            assert roots[0].payload_bytes == 18
            assert not hasattr(roots[0], "input")
            assert reader.export_fields_by_ids(
                [root_id], {"input", "output"}, project_id=project, max_payload_bytes=18,
            )[root_id]["input"] == '"question"'
            with pytest.raises(ValueError, match="17 payload bytes"):
                reader.session_export_roots(
                    [session], project_id=project, max_turns=1,
                    max_payload_bytes=17, include_turns=True,
                )
            with pytest.raises(ValueError, match="17 payload bytes"):
                reader.export_fields_by_ids(
                    [root_id], {"input", "output"}, project_id=project, max_payload_bytes=17,
                )
            roots = reader.session_export_roots(
                [session], project_id=project, max_turns=1,
                max_payload_bytes=0, include_turns=False,
            )
            assert len(roots) == 1
            assert roots[0].payload_bytes == 0
    finally:
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id = %(pid)s SETTINGS mutations_sync=2",
            parameters={"pid": project},
        )
        client.close()
