"""Loopback-only wire fixture; production code has no test routing switches."""

import gzip
import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

PUBLIC_IP = "93.184.216.34"


@pytest.fixture
def outbound_wire(monkeypatch, tmp_path):
    try:
        from tfc.utils import outbound_destination as destination
    except ImportError:
        # The identical public-response contract also runs against the base.
        destination = None

    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def respond(self):
            size = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(size).decode()
            received.append((self.command, self.path, dict(self.headers), body))
            if self.path == "/drop-response":
                self.close_connection = True
                return
            if self.path == "/slow-headers":
                time.sleep(2)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header(
                    "Location", f"http://127.0.0.1:{self.server.server_port}/followed"
                )
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if self.path == "/compressed":
                self.send_header("Content-Encoding", "gzip")
                output = gzip.compress(b"x" * (6 * 1024 * 1024))
            elif self.path == "/large":
                output = b"x" * (5 * 1024 * 1024 + 1)
            elif self.path == "/drip":
                output = b"x" * 20
            else:
                output = json.dumps(
                    {
                        "method": self.command,
                        "path": self.path,
                        "host": self.headers["Host"],
                        "body": body,
                    }
                ).encode()
            # No Content-Length: the byte budget must also cover streaming bodies.
            self.end_headers()
            try:
                if self.path == "/drip":
                    for value in output:
                        self.wfile.write(bytes([value]))
                        self.wfile.flush()
                        time.sleep(0.1)
                else:
                    self.wfile.write(output)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    state = SimpleNamespace(
        mode="public",
        received=received,
        events=tmp_path / "events.jsonl",
        port=server.server_port,
        ca=None,
    )
    state.url = f"http://public.fixture.test:{state.port}"
    real_popen = subprocess.Popen

    def fixture_popen(command, **kwargs):
        bootstrap = """
import ipaddress, json, runpy, socket, ssl, sys, time
module, port, mode, events, ca = sys.argv[1:]
port = int(port)
namespace = runpy.run_path(module)
real_connect = socket.socket.connect
calls = 0
def event(kind, value):
    with open(events, "a") as stream:
        stream.write(json.dumps([kind, value]) + "\\n")
def resolve(host, service, **kwargs):
    global calls
    calls += 1
    event("dns", host)
    if host != "public.fixture.test" or calls != 1:
        raise OSError("unexpected test resolution")
    if mode == "slow-dns":
        time.sleep(2)
    addresses = {
        "mixed": ["93.184.216.34", "10.1.2.3"],
        "fallback": ["93.184.216.34", "93.184.216.35"],
        "fallback-v6": ["2606:4700::1111", "93.184.216.35"],
        "fallback-mixed": ["93.184.216.34", "93.184.216.35", "10.1.2.3"],
        "all-unavailable": ["93.184.216.34", "93.184.216.35"],
        "slow-connect": ["93.184.216.34", "93.184.216.35"],
    }.get(mode, ["93.184.216.34"])
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET,
             socket.SOCK_STREAM, 6, "", (ip, int(service))) for ip in addresses]
def connect(sock, address):
    event("connect", list(address))
    if sock.family not in (socket.AF_INET, socket.AF_INET6):
        raise OSError("unexpected socket family")
    if mode == "slow-connect":
        time.sleep(2)
    if mode == "all-unavailable" or (
        mode in ("fallback", "fallback-v6", "fallback-mixed")
        and address[0] != "93.184.216.35"
    ):
        raise ConnectionRefusedError("synthetic public address unavailable")
    return real_connect(sock, ("127.0.0.1", port))
socket.getaddrinfo = resolve
socket.socket.connect = connect
if ca:
    original_context = ssl.create_default_context
    ssl.create_default_context = lambda **kwargs: original_context(cafile=ca)
namespace["_worker"]()
"""
        return real_popen(
            [
                sys.executable,
                "-I",
                "-c",
                bootstrap,
                command[-1],
                str(state.port),
                state.mode,
                str(state.events),
                state.ca or "",
            ],
            **kwargs,
        )

    if destination is not None:
        monkeypatch.setattr(destination.subprocess, "Popen", fixture_popen)
    else:
        real_connect = socket.socket.connect

        def resolve(host, service, *args, **kwargs):
            if host != "public.fixture.test":
                raise OSError("unexpected fixture host")
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, int(service)))
            ]

        def connect(sock, address):
            if address != (PUBLIC_IP, state.port):
                raise OSError("unexpected fixture connection")
            return real_connect(sock, ("127.0.0.1", state.port))

        def legacy_request(**kwargs):
            with requests.Session() as session:
                session.trust_env = False
                with (
                    patch.object(socket, "getaddrinfo", resolve),
                    patch.object(socket.socket, "connect", connect),
                ):
                    return session.request(**kwargs)

        monkeypatch.setattr(requests, "request", legacy_request)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
