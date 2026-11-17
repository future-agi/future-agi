"""Prove the refusing proxy records HTTP and CONNECT without any outbound call."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import threading

guard_source = Path(__file__).with_name('python_guard.py')
guard_spec = importlib.util.spec_from_file_location('editor_proxy_socket_guard', guard_source)
guard = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(guard)
assert guard.GUARD.active

source = Path(__file__).with_name('go-proxy.py')
spec = importlib.util.spec_from_file_location('editor_proxy_control', source)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)
with proxy.Server(('127.0.0.1', 0), proxy.Handler) as server:
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    for request in (b'GET http://proxy-control.invalid:8123/ HTTP/1.1\r\n\r\n',
                    b'CONNECT proxy-control.invalid:443 HTTP/1.1\r\n\r\n'):
        with socket.create_connection(server.server_address, timeout=3) as client:
            client.sendall(request)
            assert client.recv(256).startswith(b'HTTP/1.1 403 Forbidden')
    server.shutdown()
    thread.join(timeout=3)
    assert not thread.is_alive()
records = [json.loads(line) for path in proxy.DIRECTORY.glob('go-proxy-*.jsonl')
           for line in path.read_text().splitlines()]
assert [(r['kind'], r['hostname'], r['port']) for r in records] == [
    ('refused', 'proxy-control.invalid', 8123), ('refused', 'proxy-control.invalid', 443)]
print(json.dumps({'status': 'PASS', 'refusalControls': 2, 'loopbackRoundTrip': True,
                  'destinationOnly': True, 'forwardingImplementation': False}))
