"""Packet-free pre-native refusal controls and a separate real loopback control."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys

positive = len(sys.argv) > 1 and sys.argv[1] == "positive"
reached = []
if not positive:
    def forbidden(*args, **kwargs):
        reached.append("native")
        raise AssertionError("Original network primitive reached")

    class SentinelSocket(socket.socket):
        connect = forbidden
        connect_ex = forbidden
        sendto = forbidden
        sendmsg = forbidden

    socket.socket = SentinelSocket
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr",
                 "getnameinfo", "create_connection"):
        setattr(socket, name, forbidden)

source = Path(__file__).with_name("python_guard.py")
spec = importlib.util.spec_from_file_location("editor_guard_control", source)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
if positive:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(("localhost", server.getsockname()[1]), timeout=3) as client:
            peer, _ = server.accept()
            with peer:
                client.sendall(b"positive-loopback")
                assert peer.recv(64) == b"positive-loopback"
    assert not guard.ATTEMPTS
    print(json.dumps({"status": "PASS", "runtime": "Python", "loopbackRoundTrip": True}))
else:
    os.environ["EVAL_GUARD_CONTROL"] = "1"
    count = 0
    for hostname in ("editor-control.invalid", "203.0.113.17"):
        address = (hostname, 443)
        with socket.socket() as tcp, socket.socket(type=socket.SOCK_DGRAM) as udp:
            controls = [
                lambda: tcp.connect(address), lambda: tcp.connect_ex(address),
                lambda: socket.create_connection(address),
                lambda: udp.sendto(b"control", address),
                lambda: udp.sendto(b"control", 0, address),
                lambda: udp.sendmsg([b"control"], [], 0, address),
                lambda: socket.getaddrinfo(hostname, 443),
                lambda: socket.gethostbyname(hostname),
                lambda: socket.gethostbyname_ex(hostname),
                lambda: socket.gethostbyaddr(hostname),
                lambda: socket.getnameinfo(address, 0),
            ]
            for call in controls:
                before = len(guard.ATTEMPTS)
                try:
                    call()
                except guard.network_isolation.ExternalNetworkRefused:
                    pass
                else:
                    raise AssertionError("Missing refusal")
                assert len(guard.ATTEMPTS) == before + 1
                assert not reached, "Refusal must precede the original primitive"
                count += 1
    for hostname in ("127.0.0.1%other.invalid", "0.0.0.0%other.invalid"):
        try:
            socket.getaddrinfo(hostname, 443)
        except guard.network_isolation.ExternalNetworkRefused:
            count += 1
        else:
            raise AssertionError("Malformed literal escaped")
    assert not reached
    records = [json.loads(line) for path in guard.DIRECTORY.glob("python-*.jsonl")
               for line in path.read_text().splitlines()]
    assert sum(row["kind"] == "refusal-control" for row in records) == count
    print(json.dumps({"status": "PASS", "runtime": "Python", "refusalControls": count,
                      "originalPrimitiveCalls": len(reached), "persisted": True}))
