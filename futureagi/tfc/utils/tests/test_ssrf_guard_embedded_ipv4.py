"""classify_ip judges an IPv6 address that carries an IPv4 one by the stricter
of the two classes, as the gateway's netguard does.

NAT64's well-known prefix (64:ff9b::/96), 6to4 (2002::/16), IPv4-compatible
addresses (::a.b.c.d, ::/96 minus :: and ::1) and Teredo (2001::/32, the
server and the inverted client) all carry an IPv4 address a translator or a
tunnel connects to. Such an address is in the stricter of its own class and
the embedded address's: one that carries a loopback or cloud-metadata address
is NEVER, so ``ensure_public_http_url`` and the provider base_url check
refuse it whether or not private URLs are allowed
(AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS), and one that carries a private address
is PRIVATE, which that flag opens. Every metadata address netguard
forbids by address is NEVER here too.

Agreement with netguard: ``NETGUARD_ROWS`` is the table of
agentcc-gateway/internal/netguard/netguard_test.go
``TestClassify_IPv6FormsThatEmbedAnIPv4Address``, row for row, with netguard's
expected class. Loopback and Forbidden map to NEVER, Private to PRIVATE and
Public to PUBLIC. Django is stricter than netguard on the rows in
``DJANGO_STRICTER``, and only there: ``ipaddress`` keeps the whole NAT64,
6to4, IPv4-compatible, Teredo and documentation ranges private or reserved
whatever they carry, so a public embedded address stays PRIVATE here.
"""

import ipaddress
import socket
from unittest.mock import patch

import pytest

from agentcc.services import url_safety
from tfc.utils.ssrf_guard import IPClass, classify_ip

NETGUARD_TO_DJANGO = {
    "Public": IPClass.PUBLIC,
    "Private": IPClass.PRIVATE,
    "Loopback": IPClass.NEVER,
    "Forbidden": IPClass.NEVER,
}


def teredo(server, client):
    """2001:0::/32 <server> <flags> <port> <client>, the client address and
    port stored inverted (RFC 4380), as netguard_test.go's helper builds it."""
    raw = bytearray(16)
    raw[0], raw[1] = 0x20, 0x01
    raw[4:8] = ipaddress.IPv4Address(server).packed
    raw[10], raw[11] = 0xFF, 0xFF  # port 0, inverted
    raw[12:16] = bytes(~b & 0xFF for b in ipaddress.IPv4Address(client).packed)
    return str(ipaddress.IPv6Address(bytes(raw)))


# netguard_test.go TestClassify_IPv6FormsThatEmbedAnIPv4Address, row for row:
# (name, address, netguard's class).
NETGUARD_ROWS = [
    # NAT64, the well-known prefix 64:ff9b::/96 (RFC 6052).
    ("nat64 loopback", "64:ff9b::127.0.0.1", "Loopback"),
    ("nat64 private", "64:ff9b::10.0.0.5", "Private"),
    ("nat64 private, hex", "64:ff9b::a00:5", "Private"),
    ("nat64 cgnat", "64:ff9b::100.64.0.1", "Private"),
    ("nat64 metadata", "64:ff9b::169.254.169.254", "Forbidden"),
    ("nat64 alibaba metadata", "64:ff9b::100.100.100.200", "Forbidden"),
    ("nat64 this network", "64:ff9b::", "Forbidden"),
    ("nat64 public", "64:ff9b::8.8.8.8", "Public"),
    # 6to4, 2002::/16 with the IPv4 address in bits 16-47 (RFC 3056).
    ("6to4 loopback", "2002:7f00:1::", "Loopback"),
    ("6to4 private", "2002:a00:5::1", "Private"),
    ("6to4 private, other host bits", "2002:c0a8:101:1::abcd", "Private"),
    ("6to4 metadata", "2002:a9fe:a9fe::", "Forbidden"),
    ("6to4 public", "2002:808:808::1", "Public"),
    # IPv4-compatible, ::/96 (RFC 4291, deprecated).
    ("ipv4-compatible loopback", "::127.0.0.1", "Loopback"),
    ("ipv4-compatible private", "::10.0.0.5", "Private"),
    ("ipv4-compatible metadata", "::169.254.169.254", "Forbidden"),
    ("ipv4-compatible public", "::8.8.8.8", "Public"),
    # The IPv6 loopback and unspecified addresses sit inside ::/96 but are
    # their own class, not 0.0.0.1 and 0.0.0.0.
    ("ipv6 loopback", "::1", "Loopback"),
    ("ipv6 unspecified", "::", "Forbidden"),
    # Teredo, 2001::/32: the client (inverted) and the server address; the
    # stricter decides.
    ("teredo client loopback", teredo("8.8.8.8", "127.0.0.1"), "Loopback"),
    ("teredo client private", teredo("8.8.8.8", "10.0.0.5"), "Private"),
    ("teredo client metadata", teredo("8.8.8.8", "169.254.169.254"), "Forbidden"),
    ("teredo server private", teredo("10.0.0.5", "8.8.4.4"), "Private"),
    ("teredo public", teredo("8.8.8.8", "8.8.4.4"), "Public"),
    # IPv4-mapped, unchanged.
    ("ipv4-mapped private", "::ffff:10.0.0.5", "Private"),
    # Ordinary IPv6 next to those prefixes.
    ("public ipv6", "2606:4700::1111", "Public"),
    ("2001:db8 is not teredo", "2001:db8::a00:5", "Public"),
]

# The rows where Django stays stricter than netguard, and why: ipaddress puts
# the whole prefix in a private or reserved range, whatever it carries.
DJANGO_STRICTER = {
    "nat64 public": IPClass.PRIVATE,  # ::/8 is reserved
    "6to4 public": IPClass.PRIVATE,  # 2002::/16 is private
    "ipv4-compatible public": IPClass.PRIVATE,  # ::/8 is reserved
    "teredo public": IPClass.PRIVATE,  # 2001::/23 is private
    "2001:db8 is not teredo": IPClass.PRIVATE,  # documentation, private
}


@pytest.mark.parametrize(
    "name,address,netguard", NETGUARD_ROWS, ids=[row[0] for row in NETGUARD_ROWS]
)
def test_classify_ip_agrees_with_netguard(name, address, netguard):
    want = DJANGO_STRICTER.get(name, NETGUARD_TO_DJANGO[netguard])
    assert classify_ip(address) is want, (address, netguard)


def test_django_is_never_less_strict_than_netguard():
    """The stricter-than table only ever raises netguard's class."""
    order = [IPClass.PUBLIC, IPClass.PRIVATE, IPClass.NEVER]
    for name, address, netguard in NETGUARD_ROWS:
        assert order.index(classify_ip(address)) >= order.index(
            NETGUARD_TO_DJANGO[netguard]
        ), (name, address)


@pytest.mark.parametrize(
    "address",
    [
        "64:ff9b::7f00:1",
        "64:ff9b::a9fe:a9fe",
        "2002:a9fe:a9fe::1",
        "::a9fe:a9fe",
        "::7f00:1",
        teredo("8.8.8.8", "169.254.169.254"),
        teredo("127.0.0.1", "8.8.4.4"),
    ],
)
def test_loopback_and_metadata_carried_in_ipv6_are_never(address):
    assert classify_ip(address) is IPClass.NEVER


@pytest.mark.parametrize(
    "address", ["::ffff:169.254.169.254", "::ffff:127.0.0.1", "169.254.169.254"]
)
def test_ipv4_and_ipv4_mapped_are_unchanged(address):
    assert classify_ip(address) is IPClass.NEVER


def _resolves_to_itself(host, *_args, **_kwargs):
    """getaddrinfo for an IP literal, with no lookup."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 6, "", (host, 0))]


# A loopback or metadata address carried in each IPv6 form: refused whether
# or not private URLs are allowed.
REFUSED_EMBEDDED_URLS = [
    "http://[64:ff9b::a9fe:a9fe]/",
    "http://[64:ff9b::7f00:1]/",
    "http://[2002:a9fe:a9fe::1]/",
    "http://[::a9fe:a9fe]/",
]


@pytest.mark.parametrize("url", REFUSED_EMBEDDED_URLS)
@pytest.mark.parametrize("allow_private", [False, True])
def test_ensure_public_http_url_refuses_them_with_private_allowed(url, allow_private):
    with (
        patch.object(url_safety.socket, "getaddrinfo", _resolves_to_itself),
        pytest.raises(ValueError),
    ):
        url_safety.ensure_public_http_url(url, "refused", allow_private=allow_private)


@pytest.mark.parametrize("url", REFUSED_EMBEDDED_URLS)
def test_a_provider_base_url_is_refused_with_private_provider_urls_allowed(
    url, monkeypatch
):
    monkeypatch.setenv(url_safety.ALLOW_PRIVATE_PROVIDER_URLS_ENV, "true")
    with (
        patch.object(url_safety.socket, "getaddrinfo", _resolves_to_itself),
        pytest.raises(ValueError, match="never allowed"),
    ):
        url_safety.ensure_provider_base_url_allowed(url)


@pytest.mark.parametrize(
    "url",
    ["http://[64:ff9b::a00:5]/", "http://[2002:a00:5::1]/", "http://[::a00:5]/"],
)
def test_private_carried_in_ipv6_still_opens_with_the_flag(url, monkeypatch):
    """A private embedded address stays PRIVATE: a self-host that allows
    private provider URLs saves one."""
    monkeypatch.setenv(url_safety.ALLOW_PRIVATE_PROVIDER_URLS_ENV, "true")
    with patch.object(url_safety.socket, "getaddrinfo", _resolves_to_itself):
        url_safety.ensure_provider_base_url_allowed(url)


# The cloud metadata services netguard forbids by address (netguard.go
# forbiddenNets; netguard_test.go's TestCheck rows, each still refused with
# private addresses allowed). Two of them sit inside ranges an operator can
# open: AWS IMDS (fd00:ec2::254) and GCP's metadata server on IPv6-only
# instances (fd20:ce::254) in fc00::/7, Alibaba's in CGNAT. Django classes
# each as NEVER, so a provider base_url (or fetch_models in its raw mode) is
# refused there with AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS=true as without it.
NETGUARD_FORBIDDEN_METADATA = [
    "fd00:ec2::254",
    "fd20:ce::254",
    "100.100.100.200",
    "168.63.129.16",
]


@pytest.mark.parametrize("address", NETGUARD_FORBIDDEN_METADATA)
def test_netguards_forbidden_metadata_addresses_are_never(address):
    assert classify_ip(address) is IPClass.NEVER


@pytest.mark.parametrize("address", NETGUARD_FORBIDDEN_METADATA)
def test_a_metadata_provider_url_is_refused_with_private_provider_urls_allowed(
    address, monkeypatch
):
    host = f"[{address}]" if ":" in address else address
    monkeypatch.setenv(url_safety.ALLOW_PRIVATE_PROVIDER_URLS_ENV, "true")
    with (
        patch.object(url_safety.socket, "getaddrinfo", _resolves_to_itself),
        pytest.raises(ValueError, match="never allowed"),
    ):
        url_safety.ensure_provider_base_url_allowed(f"http://{host}/")
    with (
        patch.object(url_safety.socket, "getaddrinfo", _resolves_to_itself),
        pytest.raises(ValueError),
    ):
        url_safety.ensure_public_http_url(
            f"http://{host}/", "refused", allow_private=True
        )


def test_the_rest_of_fc00_7_stays_private():
    """Only the metadata address is NEVER: the rest of GCP's and AWS's
    unique-local ranges stay PRIVATE, so a self-host's private provider
    still opens with the flag."""
    for address in ("fd20:ce::253", "fd20:ce::1", "fd00:ec2::253", "fd12:3456::1"):
        assert classify_ip(address) is IPClass.PRIVATE, address
