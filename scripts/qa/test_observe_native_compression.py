"""Native transport contracts only: no ClickHouse service or query execution."""

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import clickhouse_driver
import pytest
from clickhouse_driver import defines
from clickhouse_driver.block import RowOrientedBlock
from clickhouse_driver.compression.lz4 import Compressor
from clickhouse_driver.connection import ServerInfo
from clickhouse_driver.errors import ChecksumDoesntMatchError
from clickhouse_driver.streams.compressed import (
    CompressedBlockInputStream,
    CompressedBlockOutputStream,
)

from replay_observe_queries_readonly import ReadOnlyExecutor
from tracer.services.clickhouse.client import ClickHouseClient


def _args():
    return SimpleNamespace(
        host="offline.invalid", port=9000, database="fixture", safety_seconds=60
    )


@pytest.mark.parametrize("mode", ["candidate", "reference_diagnostic"])
def test_replay_native_compression_preserves_constructor(monkeypatch, mode):
    monkeypatch.setenv("OBSERVE_CH_USER", "fixture-reader")
    monkeypatch.setenv("OBSERVE_CH_PASSWORD", "")
    driver = Mock()
    monkeypatch.setattr(clickhouse_driver, "Client", driver)
    executor = ReadOnlyExecutor(_args(), ("project",), 123.0, mode=mode)
    driver.assert_called_once_with(
        "offline.invalid",
        port=9000,
        database="fixture",
        user="fixture-reader",
        password="",
        connect_timeout=3,
        send_receive_timeout=63,
        compression="lz4",
    )
    assert executor.mode == mode
    assert executor.projects == ("project",)
    assert executor.deadline == 123.0
    assert executor.calls == []
    assert executor.supports_bounded_speculative_reads == (
        mode == "reference_diagnostic"
    )


def test_replay_missing_codec_does_not_retry(monkeypatch):
    driver = Mock(side_effect=RuntimeError("missing codec"))
    monkeypatch.setattr(clickhouse_driver, "Client", driver)
    with pytest.raises(RuntimeError, match="missing codec"):
        ReadOnlyExecutor(_args(), ("project",), 123.0)
    driver.assert_called_once()


@pytest.mark.parametrize("owner", ["app", "replay"])
def test_native_compression_full_typed_block_and_checksum(owner):
    # Actual driver/codec, in-memory Native blocks, not a server or SQL proof.
    native = (
        ClickHouseClient(
            host="offline.invalid",
            port=9000,
            user="fixture-reader",
            password="",
            database="fixture",
            server_enforced_readonly=True,
        )._create_client()
        if owner == "app"
        else ReadOnlyExecutor(_args(), ("project",), 123.0).client
    )
    connection = native.connection
    assert not connection.connected
    assert connection.compressor_cls is Compressor
    context = connection.context
    context.server_info = ServerInfo(
        "offline",
        25,
        3,
        14,
        defines.CLIENT_REVISION,
        "UTC",
        "offline",
        defines.CLIENT_REVISION,
    )
    columns = [
        ("text", "String"),
        ("nullable", "Nullable(String)"),
        ("flag", "Bool"),
        ("signed", "Int64"),
        ("unsigned", "UInt64"),
        ("float", "Float64"),
        ("map", "Map(String, String)"),
        ("array", "Array(Nullable(Int64))"),
    ]
    rows = [
        (
            "雪'\\\x00" * 8192,
            None,
            False,
            -(2**63),
            2**64 - 1,
            -0.0,
            {"unicode": "雪", "empty": "", "quote": "'\\\x00"},
            [None, -1, 0, 2**63 - 1],
        ),
        ("", "retained", True, 2**63 - 1, 0, 1.25, {}, []),
    ]
    raw = BytesIO()
    writer = CompressedBlockOutputStream(
        connection.compressor_cls, defines.DEFAULT_COMPRESS_BLOCK_SIZE, raw, context
    )
    writer.write(
        RowOrientedBlock(columns_with_types=columns, data=rows, types_check=True)
    )
    encoded = raw.getvalue()
    decoded = CompressedBlockInputStream(BytesIO(encoded), context).read()
    assert decoded.columns_with_types == columns
    assert decoded.get_rows() == rows
    assert [tuple(type(v) for v in row) for row in decoded.get_rows()] == [
        tuple(type(v) for v in row) for row in rows
    ]
    assert decoded.get_rows()[0][5].hex() == (-0.0).hex()
    corrupt = bytes([encoded[0] ^ 1]) + encoded[1:]
    with pytest.raises(ChecksumDoesntMatchError):
        CompressedBlockInputStream(BytesIO(corrupt), context).read()
    assert not connection.connected
