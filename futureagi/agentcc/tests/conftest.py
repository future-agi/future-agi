import ipaddress
import socket
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def provider_dns():
    """Stub the DNS lookups url_safety makes: ``provider_dns(answers)``.

    IP literals resolve to themselves, the names in ``answers`` to their
    addresses, and ``*.invalid`` fails. Any other name fails as well, unless
    ``passthrough`` sends it to the real resolver, for request tests that
    also reach localhost and the test services. Returns a mock of the
    lookups the stub answered, which leaves out the ones passed on.
    """
    real_getaddrinfo = socket.getaddrinfo
    answered = MagicMock()

    def install(answers, *, passthrough=False):
        def fake_getaddrinfo(host, *args, **kwargs):
            try:
                addrs = [str(ipaddress.ip_address(host))]
            except ValueError:
                known = isinstance(host, str) and host in answers
                invalid = isinstance(host, str) and host.endswith(".invalid")
                if passthrough and not known and not invalid:
                    return real_getaddrinfo(host, *args, **kwargs)
                if not known:
                    answered(host, *args, **kwargs)
                    raise socket.gaierror(socket.EAI_NONAME, "no such host") from None
                addrs = answers[host]
            answered(host, *args, **kwargs)
            return [
                (
                    socket.AF_INET6 if ":" in addr else socket.AF_INET,
                    socket.SOCK_STREAM,
                    socket.IPPROTO_TCP,
                    "",
                    (addr, 0),
                )
                for addr in addrs
            ]

        stack.enter_context(
            patch("agentcc.services.url_safety.socket.getaddrinfo", fake_getaddrinfo)
        )
        return answered

    with ExitStack() as stack:
        yield install
