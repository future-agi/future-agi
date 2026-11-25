"""A real PgBouncer between the eval-task worker and the test PostgreSQL.

Production workers reach PostgreSQL through a transaction-mode PgBouncer. Here
PgBouncer runs as a child process, and its link to PostgreSQL passes a gate the
test can shut: a shut gate refuses new connections and drops the open ones, as
a stopped or firewalled PostgreSQL does, while PgBouncer itself stays up.
"""

import os
import shutil
import socket
import subprocess
import threading
from contextlib import suppress
from pathlib import Path

import psycopg

PGBOUNCER_BIN = (
    os.environ.get("PGBOUNCER_BIN")
    or shutil.which("pgbouncer")
    # Debian and Ubuntu install it in /usr/sbin, which a PATH may omit.
    or shutil.which("pgbouncer", path="/usr/sbin:/sbin")
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Gate:
    """TCP forwarder from PgBouncer to PostgreSQL that can be shut and reopened."""

    def __init__(self, target: tuple[str, int]):
        self._target = target
        self._lock = threading.Lock()
        self._links: set[socket.socket] = set()
        self._listener: socket.socket | None = None
        self.port = _free_port()
        self.open()

    def open(self) -> None:
        if self._listener is not None:
            return
        self._listener = socket.create_server(("127.0.0.1", self.port))
        threading.Thread(
            target=self._accept, args=(self._listener,), daemon=True
        ).start()

    def shut(self) -> None:
        """Refuse new connections and drop the open ones."""
        listener, self._listener = self._listener, None
        if listener is not None:
            # shutdown() wakes the blocked accept() on Linux; close() alone does not.
            with suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            listener.close()
        with self._lock:
            links, self._links = self._links, set()
        self._drop(*links)

    def _accept(self, listener: socket.socket) -> None:
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            try:
                server = socket.create_connection(self._target)
            except OSError:
                client.close()
                continue
            with self._lock:
                if self._listener is not listener:
                    self._drop(client, server)
                    return
                self._links.update((client, server))
            for src, dst in ((client, server), (server, client)):
                threading.Thread(
                    target=self._pump, args=(src, dst), daemon=True
                ).start()

    def _pump(self, src: socket.socket, dst: socket.socket) -> None:
        with suppress(OSError):
            while data := src.recv(65536):
                dst.sendall(data)
        with self._lock:
            self._links.difference_update((src, dst))
        self._drop(src, dst)

    @staticmethod
    def _drop(*sockets: socket.socket) -> None:
        for sock in sockets:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()


class PgBouncer:
    """``pgbouncer`` in the foreground, in front of ``backend_port``."""

    def __init__(self, directory: Path, *, backend_port: int, user: str, password):
        self.user = user
        self.port = _free_port()
        self.log: list[str] = []
        userlist = directory / "userlist.txt"
        userlist.write_text(f'"{user}" "{password}"\n')
        config = directory / "pgbouncer.ini"
        # The settings the Helm chart asks of a production pooler, plus short
        # retry and wait bounds so an outage shows within seconds.
        config.write_text(
            "[databases]\n"
            f"* = host=127.0.0.1 port={backend_port} user={user} password={password}\n"
            "[pgbouncer]\n"
            "listen_addr = 127.0.0.1\n"
            f"listen_port = {self.port}\n"
            "unix_socket_dir =\n"
            "auth_type = trust\n"
            f"auth_file = {userlist}\n"
            f"admin_users = {user}\n"
            "pool_mode = transaction\n"
            "server_reset_query_always = 1\n"
            "ignore_startup_parameters = extra_float_digits,search_path\n"
            "server_login_retry = 1\n"
            "query_wait_timeout = 2\n"
        )
        self._up = threading.Event()
        self._proc = subprocess.Popen(
            [PGBOUNCER_BIN, str(config)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        # Always drain the log: a full pipe would block PgBouncer.
        threading.Thread(target=self._read_log, daemon=True).start()
        if not self._up.wait(timeout=30) or self._proc.poll() is not None:
            self.stop()
            raise RuntimeError("PgBouncer did not start:\n" + "".join(self.log))

    def _read_log(self) -> None:
        for line in self._proc.stdout:
            self.log.append(line)
            if "process up" in line:
                self._up.set()
        self._up.set()  # EOF: let the constructor see that it exited

    def admin(self, command: str) -> None:
        """Run one admin-console command (simple query protocol only)."""
        with psycopg.connect(
            host="127.0.0.1",
            port=self.port,
            dbname="pgbouncer",
            user=self.user,
            autocommit=True,
        ) as conn:
            result = conn.pgconn.exec_(command.encode())
            assert result.status == psycopg.pq.ExecStatus.COMMAND_OK, (
                result.error_message
            )

    def stop(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()


class Outage:
    """PostgreSQL unavailable behind a running PgBouncer, as in production.

    ``postgres-stopped``: PostgreSQL refuses PgBouncer (the gate is shut).
    ``paused``: PAUSE, as for maintenance. ``disabled``: DISABLE then KILL.
    """

    KINDS = ("postgres-stopped", "paused", "disabled")

    def __init__(self, kind: str, gate: Gate, bouncer: PgBouncer, dbname: str):
        assert kind in self.KINDS, kind
        self.kind = kind
        self._gate = gate
        self._bouncer = bouncer
        self._dbname = dbname
        self._lock = threading.Lock()
        self._active = False

    def start(self) -> None:
        with self._lock:
            if self.kind == "postgres-stopped":
                self._gate.shut()
            elif self.kind == "paused":
                self._bouncer.admin(f"PAUSE {self._dbname}")
            else:
                self._bouncer.admin(f"DISABLE {self._dbname}")
                self._bouncer.admin(f"KILL {self._dbname}")
            self._active = True

    def end(self) -> None:
        with self._lock:
            if not self._active:
                return
            if self.kind == "postgres-stopped":
                self._gate.open()
            elif self.kind == "paused":
                self._bouncer.admin(f"RESUME {self._dbname}")
            else:
                self._bouncer.admin(f"ENABLE {self._dbname}")
                self._bouncer.admin(f"RESUME {self._dbname}")
            self._active = False
