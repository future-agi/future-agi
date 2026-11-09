"""Real native insert/block serialization and decoding; no network or database.

Admission is per <=64-row block, not per socket write. A block already admitted
may finish its bytes; a revoked/expired guard must prevent the following block.
"""

import io
import runpy
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from clickhouse_driver import Client, defines
from clickhouse_driver.block import ColumnOrientedBlock, RowOrientedBlock
from clickhouse_driver.bufferedreader import BufferedSocketReader
from clickhouse_driver.bufferedwriter import BufferedSocketWriter
from clickhouse_driver.protocol import ClientPacketTypes, ServerPacketTypes
from clickhouse_driver.reader import read_binary_str
from clickhouse_driver.streams.native import BlockInputStream
from clickhouse_driver.varint import read_varint

support = runpy.run_path(
    str(Path(__file__).with_name("test_falcon_transport_offline.py"))
)


class NativeChunkTests(unittest.TestCase):
    setUp = support["TransportTests"].setUp
    install = support["TransportTests"].install
    scope = support["TransportTests"].scope

    def native(self, effect=None, *, modern=False):
        # Aborts are sticky; each subcase is a distinct analysis invocation.
        self.guard = self.Guard("project", "user", "org", "workspace", 60)
        self.context.background_analysis = self.guard
        self.guard.deadline = time.monotonic() + 60
        client = Client(
            host="offline.invalid",
            compression=False,
            settings={"insert_block_size": 512},
            send_receive_timeout=0.5,
        )
        connection = client.connection
        connection.connected = True
        revision = (
            defines.DBMS_MIN_PROTOCOL_VERSION_WITH_PROFILE_EVENTS_IN_INSERT
            if modern
            else defines.DBMS_MIN_REVISION_WITH_TEMPORARY_TABLES
        )
        connection.server_info = SimpleNamespace(used_revision=revision)
        connection.context.server_info = connection.server_info
        connection.context.settings = {}
        connection.context.client_settings = client.client_settings
        case = self

        class Socket:
            timeout = 0.25

            def __init__(self):
                self.writes = []
                self.timeouts = []

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value

            def sendall(self, data):
                self.writes.append(bytes(data))
                self.timeouts.append(self.timeout)
                # Large driver buffers may flush within an admitted block.
                # Changing the guard here must stop the NEXT block, not this one.
                if effect == "revoked":
                    case.active = False
                elif effect == "deadline":
                    case.guard.deadline = 0

            def close(self):
                pass

        wire = Socket()
        connection.socket = wire
        connection.fout = BufferedSocketWriter(wire, defines.BUFFER_SIZE)
        connection.block_out = connection.get_block_out_stream()
        schema = [("id", "UInt32"), ("vector", "Array(Float32)")]
        # Exactly representable floats; verify every value after wire decoding.
        rows = [(i, [float(i)] * 1536) for i in range(512)]
        return client, wire, schema, rows

    def decode(self, client, wire):
        payload = b"".join(wire.writes)
        incoming = io.BytesIO(payload)

        class Socket:
            def recv_into(self, buffer, *args):
                return incoming.readinto(buffer)

        reader = BufferedSocketReader(Socket(), len(payload) + 1)
        stream = BlockInputStream(reader, client.connection.context)
        blocks, tables = [], []
        # One read fills this oversized buffer; position then measures the whole
        # payload, even when a block originally spanned multiple socket writes.
        while not blocks or reader.position < len(payload):
            self.assertEqual(read_varint(reader), ClientPacketTypes.DATA)
            tables.append(read_binary_str(reader))
            blocks.append(stream.read())
        return blocks, tables

    def assert_rows(self, client, wire, expected, *, bounded=True):
        blocks, tables = self.decode(client, wire)
        actual = [(row[0], list(row[1])) for b in blocks for row in b.get_rows()]
        self.assertEqual(len(actual), len(expected))
        for actual_row, expected_row in zip(actual, expected, strict=True):
            self.assertEqual(actual_row, expected_row)
        if bounded:
            self.assertTrue(all(b.num_rows <= 64 for b in blocks))
        self.assertTrue(all(0 < value <= 0.25 for value in wire.timeouts))
        return blocks, tables

    def test_real_insert_revocation_stops_before_next_chunk(self):
        client, wire, schema, rows = self.native("revoked")
        with self.scope():
            self.transport.guard_native_client(client)
            with self.assertRaises(self.Aborted):
                client.send_data(RowOrientedBlock(schema), rows)
        blocks, _ = self.assert_rows(client, wire, rows[:64])
        self.assertEqual([b.num_rows for b in blocks], [64])

    def test_real_insert_deadline_stops_before_next_chunk(self):
        client, wire, schema, rows = self.native("deadline")
        with self.scope():
            self.transport.guard_native_client(client)
            with self.assertRaises(self.Aborted):
                client.send_data(RowOrientedBlock(schema), rows)
        self.assert_rows(client, wire, rows[:64])

    def test_authorized_row_and_column_inserts_preserve_all_rows(self):
        for columnar in (False, True):
            with self.subTest(columnar=columnar):
                client, wire, schema, rows = self.native()
                data = list(zip(*rows, strict=True)) if columnar else rows
                with self.scope():
                    self.transport.guard_native_client(client)
                    count = client.send_data(
                        RowOrientedBlock(schema),
                        data,
                        columnar=columnar,
                    )
                self.assertEqual(count, len(rows))
                blocks, _ = self.assert_rows(client, wire, rows)
                self.assertEqual([b.num_rows for b in blocks], [64] * 8 + [0])

    def test_direct_native_blocks_cannot_bypass_chunking(self):
        for block_cls in (RowOrientedBlock, ColumnOrientedBlock):
            with self.subTest(block_cls=block_cls):
                self.active = True
                client, wire, schema, rows = self.native("revoked")
                data = (
                    list(zip(*rows, strict=True))
                    if block_cls is ColumnOrientedBlock
                    else rows
                )
                with self.scope():
                    self.transport.guard_native_client(client)
                    with self.assertRaises(self.Aborted):
                        client.connection.send_data(block_cls(schema, data))
                self.assert_rows(client, wire, rows[:64])

    def test_external_tables_use_bounded_named_blocks(self):
        client, wire, schema, rows = self.native()
        with self.scope():
            self.transport.guard_native_client(client)
            client.connection.send_external_tables(
                [{"name": "vectors", "structure": schema, "data": rows}],
            )
        blocks, tables = self.assert_rows(client, wire, rows)
        self.assertEqual([b.num_rows for b in blocks], [64] * 8 + [0])
        self.assertEqual(tables, ["vectors"] * 8 + [""])

    def test_external_table_revocation_stops_before_next_chunk(self):
        client, wire, schema, rows = self.native("revoked")
        with self.scope():
            self.transport.guard_native_client(client)
            with self.assertRaises(self.Aborted):
                client.connection.send_external_tables(
                    [{"name": "vectors", "structure": schema, "data": rows}],
                )
        self.assert_rows(client, wire, rows[:64])

    def test_modern_insert_drains_one_profile_response_per_chunk(self):
        client, wire, schema, rows = self.native(modern=True)
        client.last_query = SimpleNamespace(store_profile=Mock())
        client.connection.receive_packet = Mock(
            return_value=SimpleNamespace(
                type=ServerPacketTypes.PROFILE_EVENTS,
                profile_info=object(),
            ),
        )
        with self.scope():
            self.transport.guard_native_client(client)
            self.assertEqual(client.send_data(RowOrientedBlock(schema), rows), 512)
        self.assert_rows(client, wire, rows)
        self.assertEqual(client.connection.receive_packet.call_count, 9)

    def test_tighter_block_size_and_settings_are_preserved(self):
        client, wire, schema, rows = self.native()
        settings = {**client.client_settings, "insert_block_size": 16}
        client.connection.context.client_settings = settings
        with self.scope():
            self.transport.guard_native_client(client)
            self.assertEqual(client.send_data(RowOrientedBlock(schema), rows), 512)
        self.assertEqual(client.connection.context.client_settings, settings)
        blocks, _ = self.assert_rows(client, wire, rows)
        self.assertEqual([b.num_rows for b in blocks], [16] * 32 + [0])

    def test_settings_restored_after_uncatchable_abort(self):
        client, wire, schema, rows = self.native("deadline")
        settings = client.connection.context.client_settings
        self.assertFalse(issubclass(self.Aborted, Exception))
        with self.scope():
            self.transport.guard_native_client(client)
            with self.assertRaises(self.Aborted):
                client.send_data(RowOrientedBlock(schema), rows)
        self.assertEqual(client.connection.context.client_settings, settings)
        self.assertEqual(settings["insert_block_size"], 512)
        self.assert_rows(client, wire, rows[:64])

    def test_interactive_insert_keeps_original_block_size(self):
        client, wire, schema, rows = self.native()
        with self.scope(False):
            self.assertIs(self.transport.guard_native_client(client), client)
            self.assertEqual(client.send_data(RowOrientedBlock(schema), rows), 512)
        blocks, _ = self.assert_rows(client, wire, rows, bounded=False)
        self.assertEqual([b.num_rows for b in blocks], [512, 0])


if __name__ == "__main__":
    unittest.main()
