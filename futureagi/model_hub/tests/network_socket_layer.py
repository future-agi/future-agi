"""Pre-native adapters for Python's resolver and socket transport entry points."""

import socket
from functools import wraps

from model_hub.tests.network_service_hosts import host_ip, host_text


def install_socket_layer(guard):
    """Keep native transport arguments numeric while the guard is watching.

    CPython resolves transport hostnames *before* emitting its socket audit
    event. The audit hook alone therefore cannot refuse or count those lookups.
    """
    original_resolve = socket.getaddrinfo

    @wraps(original_resolve)
    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if not guard.active:
            return original_resolve(host, port, family, type, proto, flags)
        guard.check_target("socket.getaddrinfo", host, port)
        if guard.resolving_service(host):
            # Only the exact preselected compose name during watch bootstrap.
            # All answers are checked by _refresh_services before becoming usable.
            return original_resolve(host_text(host), port, family, type, proto, flags)
        answers = []
        for numeric in guard.numeric_hosts(host, family):
            try:
                answers.extend(
                    original_resolve(
                        numeric,
                        port,
                        family,
                        type,
                        proto,
                        flags | socket.AI_NUMERICHOST,
                    )
                )
            except socket.gaierror:
                continue
        if not answers:
            raise socket.gaierror(socket.EAI_NONAME, "no local fixture address")
        return answers

    def address(event, sock, value):
        if not guard.active or sock.family not in (socket.AF_INET, socket.AF_INET6):
            return value
        if not isinstance(value, tuple) or len(value) < 2:
            return value  # Native type validation; there is no hostname to resolve.
        host, port = value[:2]
        guard.check_target(event, host, port)
        infos = getaddrinfo(host, port, sock.family, sock.type, sock.proto)
        numeric = infos[0][4]
        # Preserve explicit IPv6 flowinfo/scope_id supplied by the caller.
        return numeric[:2] + value[2:] if len(value) > 2 else numeric

    def transport(name, event):
        original = getattr(socket.socket, name)

        @wraps(original)
        def call(sock, value):
            return original(sock, address(event, sock, value))

        setattr(socket.socket, name, call)

    transport("connect", "socket.connect")
    transport("connect_ex", "socket.connect")
    original_sendto = socket.socket.sendto

    @wraps(original_sendto)
    def sendto(sock, data, *args):
        if len(args) in (1, 2):
            args = (*args[:-1], address("socket.sendto", sock, args[-1]))
        return original_sendto(sock, data, *args)

    socket.socket.sendto = sendto
    if hasattr(socket.socket, "sendmsg"):
        original_sendmsg = socket.socket.sendmsg

        @wraps(original_sendmsg)
        def sendmsg(sock, buffers, ancdata=(), flags=0, address_value=None):
            if address_value is None:
                return original_sendmsg(sock, buffers, ancdata, flags)
            return original_sendmsg(
                sock,
                buffers,
                ancdata,
                flags,
                address("socket.sendmsg", sock, address_value),
            )

        socket.socket.sendmsg = sendmsg

    original_create = socket.create_connection

    @wraps(original_create)
    def create_connection(value, *args, **kwargs):
        if guard.active:
            guard.check_target("socket.create_connection", value[0], value[1])
            # The stdlib implementation uses the wrapped getaddrinfo/connect.
            # Also validate a hostname in its optional local source bind.
            source = args[1] if len(args) > 1 else kwargs.get("source_address")
            if source:
                guard.check_target("socket.bind", source[0], source[1])
                numeric = getaddrinfo(source[0], source[1], type=socket.SOCK_STREAM)[0][
                    4
                ]
                if len(args) > 1:
                    args = (args[0], numeric, *args[2:])
                else:
                    kwargs["source_address"] = numeric
        return original_create(value, *args, **kwargs)

    socket.create_connection = create_connection
    socket.getaddrinfo = getaddrinfo

    def hostname_lookup(name):
        original = getattr(socket, name)

        @wraps(original)
        def call(host):
            if not guard.active:
                return original(host)
            guard.check_target("socket." + name, host, None)
            infos = getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
            ips = list(dict.fromkeys(info[4][0] for info in infos))
            return (host_text(host), [], ips) if name.endswith("_ex") else ips[0]

        setattr(socket, name, call)

    hostname_lookup("gethostbyname")
    hostname_lookup("gethostbyname_ex")
    original_reverse = socket.gethostbyaddr

    @wraps(original_reverse)
    def gethostbyaddr(host):
        if not guard.active:
            return original_reverse(host)
        guard.check_target("socket.gethostbyaddr", host, None)
        ips = list(guard.numeric_hosts(host, socket.AF_UNSPEC))
        if not ips:
            raise socket.herror("no local fixture address")
        # No native PTR lookup is needed for the fixture's known identity.
        return (guard.local_name(host), [], ips)

    socket.gethostbyaddr = gethostbyaddr
    original_nameinfo = socket.getnameinfo

    @wraps(original_nameinfo)
    def getnameinfo(value, flags):
        if not guard.active:
            return original_nameinfo(value, flags)
        guard.check_target("socket.getnameinfo", value[0], value[1])
        family = socket.AF_INET6 if len(value) > 2 else socket.AF_INET
        numeric = getaddrinfo(value[0], value[1], family, socket.SOCK_DGRAM)[0][4]
        host, service = original_nameinfo(numeric, flags | socket.NI_NUMERICHOST)
        if not flags & socket.NI_NUMERICHOST:
            host = guard.local_name(value[0])
            if flags & socket.NI_NAMEREQD and host_ip(host_text(host)) is not None:
                raise socket.gaierror(socket.EAI_NONAME, "no local fixture name")
        return host, service

    socket.getnameinfo = getnameinfo
