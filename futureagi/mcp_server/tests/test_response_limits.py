import json

import pytest

from mcp_server.response_limits import (
    MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    bounded_response,
)


def test_blob_previews_preserve_ids_rows_and_pagination():
    original = {
        "count": 2,
        "next": "https://example.test/spans/?page=2",
        "results": [
            {
                "id": "one",
                "input": "x" * 1_000_000,
                "metadata": {"raw": "y" * 1_000_000},
            },
            {"id": "two", "output": "z" * 1_000_000},
        ],
    }
    result = bounded_response(original)
    assert result["count"] == 2
    assert result["next"] == original["next"]
    assert [row["id"] for row in result["results"]] == ["one", "two"]
    assert result["_mcp"]["fields"] == [
        "/results/0/input",
        "/results/0/metadata/raw",
        "/results/1/output",
    ]
    assert len(json.dumps(result).encode()) < MAX_RESPONSE_BYTES
    assert len(original["results"][0]["input"]) == 1_000_000


def test_oversized_page_is_rejected_instead_of_skipping_rows():
    with pytest.raises(ResponseTooLargeError, match="smaller page"):
        bounded_response(
            {"next": "page=2", "results": [{"id": "x" * 100} for _ in range(1000)]}
        )


def test_small_response_is_unchanged():
    result = {"items": [{"id": "one", "metadata": {"tokens": 10}}], "next": None}
    assert bounded_response(result) == result


def test_preserves_long_pagination_cursor_inside_metadata():
    cursor = "x" * 3000
    data = {"metadata": {"next_cursor": cursor}, "table": []}
    assert bounded_response(data) == data


def test_rejects_excessive_nesting_without_a_recursion_error():
    data = "value"
    for _ in range(110):
        data = {"content": data}
    with pytest.raises(ResponseTooLargeError, match="complexity"):
        bounded_response(data)


def test_protocol_errors_bound_large_api_details():
    from mcp_server.mcp_app import _error_result

    result = _error_result("Rejected", code="HTTP_400", data={"details": "x" * 100000})
    assert result.isError
    assert len(result.model_dump_json().encode()) < 1024
    assert result.structuredContent["error"]["code"] == "HTTP_400"
