"""Install before Django imports; test processes may contact only loopback sockets."""

import ipaddress
import os
import socket
import sys


def _loopback(host):
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="strict")
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        return False
    return address.is_loopback or bool(
        getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback
    )


def _deny(event):
    # Never record addresses, query strings, tokens, or credential values.
    path = os.environ.get("KEY_CHECK_SOCKET_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as log:
            log.write(f"blocked {event}\n")
    raise PermissionError("Key-role tests permit loopback sockets only")


def _audit(event, args):
    if event in ("socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr"):
        if not _loopback(args[0]):
            _deny(event)
    elif event in ("socket.connect", "socket.sendto", "socket.sendmsg"):
        sock, address = args[0], args[-1]
        if sock.family == socket.AF_UNIX:
            return
        if not isinstance(address, tuple) or not _loopback(address[0]):
            _deny(event)


def install():
    sys.addaudithook(_audit)
