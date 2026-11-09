"""WT-28 (DESIGN.md sections 6.2 and 17.2): TLS on the send path, against real HTTPS receivers on 127.0.0.1 with
certificates made here.

- An untrusted certificate and a certificate for another name each end ``tls_error``, with the address pinned. A
  wrong-name certificate used to escape ``safe_post`` as urllib3's ``CertificateError`` (a ``ValueError``), so the
  dispatcher logged "webhooks send failed" on every attempt and the delivery retried as ``worker_lost`` (case
  M1); the dispatcher test below runs that path end to end.
- ``FI_WEBHOOKS_EXTRA_CA_FILE`` is added to the certifi bundle, never a replacement: with it set, a receiver signed by
  the private CA and one signed by a public root both deliver (case M2).
- A CA file that cannot be loaded is a ``tls_error`` too, never an exception.
- Hosted settings refuse ``http://`` (the row's first clause) at create: ``test_api_manage.py::test_wt05b_wt05c_hosted_rules``
  (``WEBHOOK_URL_NOT_HTTPS``) and the core's URL vectors.

"Public" is a root made here and installed as the certifi bundle (``certifi.where`` is pointed at it), so a receiver
whose certificate it signed stands for a public receiver. Adopted from a review probe, which
showed M1 and M2 on the old code.
"""

from __future__ import annotations

import datetime as dt
import http.server
import logging
import ssl
import threading
import time
from dataclasses import replace

import certifi
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.db import close_old_connections

from tfc.utils import ssrf_post
from webhooks.core.attempt import Sender
from webhooks.core.dispatch import Dispatcher
from webhooks.core.ipclass import Profile
from webhooks.core.tests.fakes import FakeResolver
from webhooks.models import Attempt, Delivery, EndpointState
from webhooks.store import OrmStore
from webhooks.tests.factories import make_endpoint, use_test_keys
from webhooks.tests.receiver import PUBLIC, Clock, psl
from webhooks.transport import PinnedTransport


def _ca(name):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    usage = x509.KeyUsage(
        digital_signature=True,
        key_cert_sign=True,
        crl_sign=True,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        encipher_only=False,
        decipher_only=False,
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(usage, critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def _leaf(ca_key, ca_cert, host):
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _pem(obj, key=False):
    if key:
        return obj.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    return obj.public_bytes(serialization.Encoding.PEM)


class _Ok(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.posts += 1
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


def _https_server(tmp_path, name, key, cert):
    kf, cf = tmp_path / f"{name}.key", tmp_path / f"{name}.crt"
    kf.write_bytes(_pem(key, key=True))
    cf.write_bytes(_pem(cert))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    srv.daemon_threads = True
    srv.posts = 0
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cf), str(kf))
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def pki(tmp_path, monkeypatch):
    """A public root (installed as the certifi bundle), a private root (the extra CA file) and three receivers:
    ``public`` (hooks.acme.ai, public root), ``private`` (hooks.private.ai, private root) and ``wrongname`` (a public
    certificate for other.acme.ai)."""
    pub_key, pub = _ca("wt28 public root")
    priv_key, priv = _ca("wt28 private root")
    bundle = tmp_path / "public-bundle.pem"
    bundle.write_bytes(_pem(pub))
    extra = tmp_path / "extra-ca.pem"
    extra.write_bytes(_pem(priv))
    monkeypatch.setattr(certifi, "where", lambda: str(bundle))
    servers = {
        "public": _https_server(
            tmp_path, "public", *_leaf(pub_key, pub, "hooks.acme.ai")
        ),
        "private": _https_server(
            tmp_path, "private", *_leaf(priv_key, priv, "hooks.private.ai")
        ),
        "wrongname": _https_server(
            tmp_path, "wrongname", *_leaf(pub_key, pub, "other.acme.ai")
        ),
    }
    yield {"extra": str(extra), "servers": servers, "tmp": tmp_path}
    for s in servers.values():
        s.shutdown()
        s.server_close()


def _post(srv, host, ca_certs=None):
    """``(status, error)`` of one pinned POST to ``srv`` on 127.0.0.1 for ``host``; an exception fails the test."""
    r = ssrf_post.safe_post(
        scheme="https",
        host=host,
        port=srv.server_address[1],
        target="/hook",
        addresses=("127.0.0.1",),
        headers=(("content-type", "application/json"),),
        body=b"{}",
        deadline=time.monotonic() + 5,
        ca_certs=ca_certs,
    )
    return r.status, r.error


def test_wt28_untrusted_and_wrong_hostname_are_tls_errors_with_the_ip_pinned(pki):
    s = pki["servers"]
    assert _post(s["public"], "hooks.acme.ai") == (200, None)
    assert _post(s["private"], "hooks.private.ai") == (None, "tls_error")
    # The socket goes to the pinned 127.0.0.1; the certificate names another host.
    assert _post(s["wrongname"], "hooks.acme.ai") == (None, "tls_error")
    assert s["wrongname"].posts == 0 and s["private"].posts == 0


def test_wt28_the_extra_ca_file_is_added_to_certifi_not_a_replacement(pki):
    s = pki["servers"]
    assert _post(s["private"], "hooks.private.ai", ca_certs=pki["extra"]) == (200, None)
    assert _post(s["public"], "hooks.acme.ai", ca_certs=pki["extra"]) == (200, None)
    # The extra CA adds trust for its own chain only: a wrong name is still refused.
    assert _post(s["wrongname"], "hooks.acme.ai", ca_certs=pki["extra"]) == (
        None,
        "tls_error",
    )


def test_wt28_a_ca_file_that_cannot_be_loaded_is_a_tls_error(pki):
    s = pki["servers"]
    missing = str(pki["tmp"] / "no-such-ca.pem")
    not_pem = pki["tmp"] / "not-pem.pem"
    not_pem.write_text("not a certificate\n")
    for ca in (missing, str(not_pem)):
        r = ssrf_post.safe_post(
            scheme="https",
            host="hooks.acme.ai",
            port=s["public"].server_address[1],
            target="/hook",
            addresses=("127.0.0.1",),
            headers=(),
            body=b"{}",
            deadline=time.monotonic() + 5,
            ca_certs=ca,
        )
        assert (r.status, r.error) == (None, "tls_error"), ca
        assert "FI_WEBHOOKS_EXTRA_CA_FILE" in r.detail and ca not in r.detail


def test_wt28_the_context_trusts_certifi_and_the_extra_file_and_checks_names_by_san(
    pki,
):
    ctx = ssrf_post.tls_context(pki["extra"])
    subjects = {
        dict(x[0] for x in c["subject"])["commonName"] for c in ctx.get_ca_certs()
    }
    assert subjects == {"wt28 public root", "wt28 private root"}
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.hostname_checks_common_name is False
    assert ctx.minimum_version >= ssl.TLSVersion.TLSv1_2


class _PinnedTo127(PinnedTransport):
    """The production transport with the extra CA the adapter passes (``config.extra_ca_file``), connected to
    127.0.0.1 instead of the vetted public address (the seam of ``api_support.LoopbackTransport``)."""

    def post(self, request):
        return super().post(replace(request, addresses=("127.0.0.1",)))


@pytest.mark.django_db(transaction=True)
def test_wt28_the_dispatcher_records_tls_errors_and_trusts_certifi_plus_the_extra_ca(
    pki, organization, workspace, monkeypatch, caplog
):
    """The real dispatcher on Postgres, with the extra CA set, one event to three endpoints:

    - a public receiver whose certificate names another host: one ``tls_error`` attempt, the delivery retries, its
      lease is released, nothing is logged as a crashed send and the receiver never sees a request (M1);
    - a public receiver with the right certificate, and a receiver signed by the extra CA: both delivered (M2)."""
    from webhooks.tests.api_support import pub

    use_test_keys(monkeypatch)
    s = pki["servers"]

    def endpoint(name, host, srv):
        return make_endpoint(
            organization,
            workspace,
            url=f"https://{host}:{srv.server_address[1]}/hook",
            name=name,
        )

    wrong = endpoint("wrong-name", "hooks.acme.ai", s["wrongname"])
    public = endpoint("public", "hooks.acme.ai", s["public"])
    private = endpoint("private", "hooks.private.ai", s["private"])
    pub(organization, workspace, "wt28-1")
    caplog.set_level(logging.INFO, logger="webhooks.dispatch")
    clock = Clock()
    sender = Sender(
        Profile(mode="self_hosted"),
        psl,
        FakeResolver([PUBLIC]),
        _PinnedTo127(extra_ca_file=pki["extra"]),
        clock,
    )
    d = Dispatcher(OrmStore(), sender, clock, after_send=close_old_connections)
    assert d.tick()["started"] == 3
    d.drain(10)

    def outcome(ep):
        dl = Delivery.objects.get(endpoint=ep)
        return dl.status, list(
            Attempt.objects.filter(delivery=dl).values_list("outcome", "n")
        )

    assert outcome(wrong) == ("retrying", [("tls_error", 1)])
    assert outcome(public) == ("succeeded", [("success", 1)])
    assert outcome(private) == ("succeeded", [("success", 1)])
    assert EndpointState.objects.get(endpoint=wrong).in_flight == 0
    assert not [r for r in caplog.records if "send failed" in r.getMessage()]
    assert (s["wrongname"].posts, s["public"].posts, s["private"].posts) == (0, 1, 1)
