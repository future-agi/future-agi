"""A PostgreSQL stand-in that refuses every login with one FATAL error.

PostgreSQL answers a login with an ErrorResponse while it starts up, shuts
down or recovers. This listener sends that packet, so libpq and psycopg build
the client's error from the same bytes a real server in that state sends.
"""

import socket
import struct
import threading
from contextlib import suppress

_SSL_REQUEST = 80877103
_GSSENC_REQUEST = 80877104


def _read(conn: socket.socket, size: int) -> bytes | None:
    data = b""
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


class RefusingPostgres:
    """Answer each login with ``FATAL <sqlstate> <message>``, then close it."""

    def __init__(self, message: str, *, sqlstate: str = "57P03"):
        fields = b"".join(
            key + value.encode() + b"\0"
            for key, value in (
                (b"S", "FATAL"),
                (b"V", "FATAL"),
                (b"C", sqlstate),
                (b"M", message),
            )
        )
        self._packet = b"E" + struct.pack("!i", len(fields) + 5) + fields + b"\0"
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        # shutdown() wakes the blocked accept() on Linux; close() alone does not.
        with suppress(OSError):
            self._listener.shutdown(socket.SHUT_RDWR)
        self._listener.close()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            threading.Thread(target=self._refuse, args=(conn,), daemon=True).start()

    def _refuse(self, conn: socket.socket) -> None:
        with conn, suppress(OSError):
            while True:
                header = _read(conn, 8)
                if header is None:
                    return
                length, code = struct.unpack("!ii", header)
                if _read(conn, length - 8) is None:
                    return
                if code not in (_SSL_REQUEST, _GSSENC_REQUEST):
                    break
                conn.sendall(b"N")  # no encryption; the client goes on in clear
            conn.sendall(self._packet)
