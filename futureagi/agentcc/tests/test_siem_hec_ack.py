"""Untrusted HEC acknowledgements: bounded, typed, never echoed."""

from types import SimpleNamespace

import pytest

from agentcc.services.siem_hec import MAX_ACK_BYTES, acknowledgement


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b'{"code":0}', ""),
        (b'{"code":4,"text":"untrusted"}', "hec_token_rejected"),
        (b'{"code":false}', "hec_invalid_ack"),
        (b'{"code":"0"}', "hec_invalid_ack"),
        (b'{"code":8}', "hec_rejected"),
        (b"[]", "hec_invalid_ack"),
        (b"", "hec_invalid_ack"),
        (b"not json", "hec_invalid_ack"),
    ],
)
def test_acknowledgement(raw, expected):
    response = SimpleNamespace(iter_content=lambda chunk_size: iter([raw]))
    assert acknowledgement(response) == expected


def test_oversized_response_is_not_consumed():
    def chunks(chunk_size):
        assert chunk_size == MAX_ACK_BYTES + 1
        yield b"x" * chunk_size
        pytest.fail("read beyond the acknowledgement limit")

    assert acknowledgement(SimpleNamespace(iter_content=chunks)) == "hec_invalid_ack"
