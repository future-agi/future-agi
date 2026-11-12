"""Refusing HTTP proxy: records destinations only and never forwards requests."""
import json
import os
from pathlib import Path
import socketserver
from urllib.parse import urlsplit

DIRECTORY = Path(os.environ["EVAL_GUARD_DIR"])
DIRECTORY.mkdir(parents=True, exist_ok=True)


def record(kind, hostname, port):
    row = {"kind": kind, "layer": "go.proxy", "hostname": hostname, "port": port,
           "pid": os.getpid()}
    fd = os.open(DIRECTORY / f"go-proxy-{os.getpid()}.jsonl",
                 os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        data = (json.dumps(row) + "\n").encode()
        if os.write(fd, data) != len(data):
            raise OSError("Incomplete proxy record")
    finally:
        os.close(fd)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        line = self.rfile.readline(4096).decode("ascii", "replace")
        parts = line.split()
        hostname, port = "unclassifiable", 0
        if len(parts) >= 2:
            try:
                target = urlsplit("//" + parts[1] if parts[0] == "CONNECT" else parts[1])
                hostname = target.hostname or hostname
                port = target.port or (443 if parts[0] == "CONNECT" or target.scheme == "https" else 80)
            except ValueError:
                pass
        record("refused", hostname, port)
        self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")


class Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Server(("0.0.0.0", 3128), Handler) as server:
        record("installed", "0.0.0.0", 3128)
        server.serve_forever()
