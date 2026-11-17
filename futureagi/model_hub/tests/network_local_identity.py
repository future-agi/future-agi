"""Read the test process's own host identity without performing DNS lookups."""

import ipaddress
import json
import os
import socket
import sys
from pathlib import Path


def own_host_snapshot():
    """Snapshot exact OS names/interface addresses, never environment allowlists."""
    name = socket.gethostname()
    names = {name, name.split(".", 1)[0]}
    addresses = set()
    if sys.platform.startswith("linux"):
        import fcntl
        import struct

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _, interface in socket.if_nameindex():
                request = struct.pack("256s", interface.encode()[:15])
                try:
                    answer = fcntl.ioctl(sock.fileno(), 0x8915, request)
                except OSError:
                    continue
                addresses.add(socket.inet_ntoa(answer[20:24]))
        try:
            lines = Path("/proc/net/if_inet6").read_text().splitlines()
        except OSError:
            lines = []
        for line in lines:
            addresses.add(str(ipaddress.IPv6Address(int(line.split()[0], 16))))
    try:
        host_lines = Path("/etc/hosts").read_text().splitlines()
    except OSError:
        host_lines = []
    for line in host_lines:
        fields = line.split("#", 1)[0].split()
        if len(fields) > 1 and names.intersection(fields[1:]):
            # Only the OS hostname's hosts-file aliases; not every local entry.
            names.update(fields[1:])
    names.discard("")
    return frozenset(names), frozenset(addresses)


def write_event(kind, event, host, port):
    """Persist one destination-only counter immediately, across workers/processes."""
    record = {
        "kind": kind,
        "event": event,
        "host": host,
        "port": port if isinstance(port, (int, str, type(None))) else None,
        "pid": os.getpid(),
    }
    line = (json.dumps(record, ensure_ascii=True) + "\n").encode()
    path = os.environ.get("TEST_NETWORK_EVENTS")
    if path:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, line) != len(line):
                raise OSError("incomplete network counter write")
        finally:
            os.close(fd)
    else:
        # Pytest/cloud runners persist stderr even without a dedicated file.
        sys.stderr.write(line.decode())
        sys.stderr.flush()
