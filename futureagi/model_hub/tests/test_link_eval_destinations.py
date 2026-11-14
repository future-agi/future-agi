"""Acceptance checks for the two repository-owned link evaluation programs.

Outbound traffic is either intercepted before transport or stays on loopback.
The YAML code executes directly, without a Django/LLM test double.
"""

import http.server
import os
import socket
import ssl
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
NAMES = ("contains_valid_link", "no_invalid_links")


def load(name):
    path = ROOT / "futureagi/model_hub/system_evals/function" / (name + ".yaml")
    code = path.read_text().split("  code: |\n", 1)[1]
    namespace = {}
    exec(
        "\n".join(
            line[4:] if line.startswith("    ") else line for line in code.splitlines()
        ),
        namespace,
    )  # noqa: S102
    return namespace["evaluate"]


def _routed_socket(routes, attempts, connect_delay=0.0):
    """A real socket class whose connect() maps public test addresses to loopback."""
    real = socket.socket

    class Routed(real):
        def connect(self, address):
            attempts.append(address)
            time.sleep(connect_delay)
            return super().connect(routes[address])

    return Routed


def _self_signed_certificate(directory, hostname):
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class LinkAcceptance(unittest.TestCase):
    def test_email_without_requests(self):
        for text in (
            "Contact support@acme.io",
            "Contact help@my-domain.example",
            "Contact first.last+tag@sub.acme.io",
        ):
            for name, score in zip(NAMES, (0.0, 1.0), strict=True):
                with (
                    self.subTest(text=text, name=name),
                    mock.patch("urllib.request.urlopen") as opened,
                    mock.patch("socket.getaddrinfo") as resolved,
                ):
                    opened.return_value.status = 200
                    result = load(name)(None, text, None, None)
                    self.assertEqual(result["score"], score)
                    opened.assert_not_called()
                    resolved.assert_not_called()

    def test_prose_without_requests(self):
        for text in (
            "The value increased by 3.14 percent",
            "e.g. a sample",
            "Version 1.2.3",
            "Read report.pdf and notes.txt",
        ):
            for name, score in zip(NAMES, (0.0, 1.0), strict=True):
                with (
                    self.subTest(text=text, name=name),
                    mock.patch("urllib.request.urlopen") as opened,
                    mock.patch("socket.getaddrinfo") as resolved,
                ):
                    opened.return_value.status = 200
                    result = load(name)(None, text, None, None)
                    self.assertEqual(result["score"], score)
                    opened.assert_not_called()
                    resolved.assert_not_called()

    def test_unapproved_host_refused_before_dns(self):
        for name in NAMES:
            with (
                self.subTest(name=name),
                mock.patch.dict(os.environ, {"LINK_EVAL_ALLOWED_HOSTS": ""}),
                mock.patch("urllib.request.urlopen") as opened,
                mock.patch("socket.getaddrinfo") as resolved,
            ):
                opened.return_value.status = 200
                result = load(name)(
                    None, "Read https://unapproved.example/docs", None, None
                )
                self.assertEqual(result["score"], 0.0)
                self.assertIn("not permitted", result["reason"])
                opened.assert_not_called()
                resolved.assert_not_called()

    def test_kwargs_cannot_grant_destination(self):
        for name in NAMES:
            with (
                self.subTest(name=name),
                mock.patch.dict(os.environ, {"LINK_EVAL_ALLOWED_HOSTS": ""}),
                mock.patch("urllib.request.urlopen") as opened,
                mock.patch("socket.getaddrinfo") as resolved,
            ):
                opened.return_value.status = 200
                result = load(name)(
                    None,
                    "https://unapproved.example",
                    None,
                    None,
                    allowed_hosts=["unapproved.example"],
                    allow_private=True,
                )
                self.assertEqual(result["score"], 0.0)
                opened.assert_not_called()
                resolved.assert_not_called()

    def test_private_destination_never_reaches_listener(self):
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                received.append(self.path)
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                pass

        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for name in NAMES:
                    with (
                        self.subTest(name=name),
                        mock.patch.dict(
                            os.environ, {"LINK_EVAL_ALLOWED_HOSTS": "127.0.0.1"}
                        ),
                    ):
                        result = load(name)(
                            None,
                            f"http://127.0.0.1:{server.server_port}/private-canary",
                            None,
                            None,
                        )
                        self.assertEqual(result["score"], 0.0)
                        self.assertIn("not permitted", result["reason"])
                        self.assertEqual(received, [])
            finally:
                server.shutdown()
                thread.join()
        print("PRIVATE_CANARY_REQUESTS=" + repr(received), flush=True)

    def test_identical_self_contained_policy(self):
        programs = []
        for name in NAMES:
            path = ROOT / "futureagi/model_hub/system_evals/function" / (name + ".yaml")
            programs.append(
                path.read_text()
                .split("  code: |\n", 1)[1]
                .split("    def evaluate(", 1)[0]
            )
        self.assertEqual(*programs)

    def test_explicit_www_and_bare_links(self):
        for text, expected in (
            ("See https://allowed.example/docs", "https://allowed.example/docs"),
            ("Visit www.allowed.example.", "http://www.allowed.example"),
            ("Read allowed.example/help.", "http://allowed.example/help"),
            ("See (https://allowed.example/a_(b)).", "https://allowed.example/a_(b)"),
            ("HTTPS://allowed.example/docs", "HTTPS://allowed.example/docs"),
            ("support@acme.io then https://allowed.example", "https://allowed.example"),
            (
                "allowed.example/docs?email=support@acme.io",
                "http://allowed.example/docs?email=support@acme.io",
            ),
        ):
            for name in NAMES:
                evaluate = load(name)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(
                        evaluate.__globals__,
                        {"_link_status": mock.Mock(return_value=200)},
                    ) as namespace,
                ):
                    self.assertEqual(evaluate(None, text, None, None)["score"], 1.0)
                    self.assertEqual(
                        namespace["_link_status"].call_args.args[0], expected
                    )

    def test_other_scheme_and_scheme_relative_links_are_not_valid(self):
        for text in (
            "Download ftp://allowed.example/report.zip",
            "Connect to ws://allowed.example/socket",
            "Copy sftp://allowed.example/upload",
            "See //allowed.example/docs",
            "Open htp://allowed.example/docs",
            "Use file:///etc/hosts",
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=200)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_link_status": check}),
                    mock.patch("socket.getaddrinfo") as resolved,
                ):
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not http or https", result["reason"])
                    check.assert_not_called()
                    resolved.assert_not_called()

    def test_other_scheme_link_beside_a_valid_https_link(self):
        text = "Mirror ftp://allowed.example/a.zip or https://allowed.example/a"
        expected = {"contains_valid_link": 1.0, "no_invalid_links": 0.0}
        for name in NAMES:
            evaluate = load(name)
            check = mock.Mock(return_value=200)
            with (
                self.subTest(name=name),
                mock.patch.dict(evaluate.__globals__, {"_link_status": check}),
            ):
                self.assertEqual(
                    evaluate(None, text, None, None)["score"], expected[name]
                )
                if name == "contains_valid_link":
                    self.assertEqual(
                        check.call_args.args[0], "https://allowed.example/a"
                    )

    def test_double_slashes_in_prose_are_not_links(self):
        for text in (
            "a // comment",
            "path/to//file",
            "10 // 3",
        ):
            for name, score in zip(NAMES, (0.0, 1.0), strict=True):
                evaluate = load(name)
                check = mock.Mock(return_value=200)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_link_status": check}),
                ):
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], score)
                    check.assert_not_called()

    def test_code_comments_and_build_labels_are_not_links(self):
        for text in (
            "//TODO tidy this up",
            "//nolint:errcheck",
            "//go:build linux",
            "//pkg:target",
            "//foo/bar:baz",
            "x = 1; //increment",
            "f(); //console.log(x)",
            "//eslint-disable-next-line no-console",
            "f(); //console.log",
            "//package.json",
            "see //config.yaml:3",
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=200)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_link_status": check}),
                ):
                    self.assertEqual(evaluate.__globals__["_links"](text), [])
                    result = evaluate(None, text, None, None)
                    if name == "no_invalid_links":
                        self.assertEqual(result["score"], 1.0)
                    else:
                        self.assertEqual(result["score"], 0.0)
                        self.assertEqual(result["reason"], "No link found in output")
                    check.assert_not_called()

    def test_scheme_relative_link_forms_are_found(self):
        for text, link in (
            ("//allowed.example", "//allowed.example"),
            ("See //allowed.example/docs.", "//allowed.example/docs"),
            ('href="//allowed.example/x"', "//allowed.example/x"),
            ("(//allowed.example)", "//allowed.example"),
            ("[//allowed.example/x]", "//allowed.example/x"),
            ("<//allowed.example/x>", "//allowed.example/x"),
            ("Use //allowed.example:8080/x", "//allowed.example:8080/x"),
            ("Use //allowed.example?q=1", "//allowed.example?q=1"),
            ("Use //allowed.example#top", "//allowed.example#top"),
            ("Use //10.0.0.1/x", "//10.0.0.1/x"),
            ("Use //[2001:db8::1]/x", "//[2001:db8::1]/x"),
            ("Use //user@allowed.example/x", "//user@allowed.example/x"),
            ("Use `//allowed.example`", "//allowed.example"),
            ("See \u201c//allowed.example/x\u201d", "//allowed.example/x\u201d"),
            ("See \u2018//allowed.example\u2019", "//allowed.example"),
            ("Use //allowed.xn--p1ai/x", "//allowed.xn--p1ai/x"),
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=200)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_link_status": check}),
                ):
                    self.assertEqual(evaluate.__globals__["_links"](text), [link])
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not http or https", result["reason"])
                    check.assert_not_called()

    def test_scheme_relative_link_after_equals_or_colon_is_found(self):
        for text, link in (
            ("<img src=//allowed.example/pixel>", "//allowed.example/pixel"),
            ("href=//allowed.example", "//allowed.example"),
            ("<a href=//allowed.example/x>", "//allowed.example/x"),
            ("url: //allowed.example/x", "//allowed.example/x"),
            ("Open http:://allowed.example/x", "//allowed.example/x"),
        ):
            for name in NAMES:
                evaluate = load(name)
                check = mock.Mock(return_value=200)
                with (
                    self.subTest(text=text, name=name),
                    mock.patch.dict(evaluate.__globals__, {"_link_status": check}),
                ):
                    self.assertEqual(evaluate.__globals__["_links"](text), [link])
                    result = evaluate(None, text, None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not http or https", result["reason"])
                    check.assert_not_called()

    def test_explicit_scheme_link_is_found_once(self):
        for text, links in (
            ("ftp://allowed.example/x", ["ftp://allowed.example/x"]),
            ("Connect to ws://allowed.example", ["ws://allowed.example"]),
            ("src=ftp://allowed.example/a", ["ftp://allowed.example/a"]),
            ("x:ftp://allowed.example", ["ftp://allowed.example"]),
            (
                "href=https://allowed.example/a",
                ["https://allowed.example/a"],
            ),
        ):
            for name in NAMES:
                with self.subTest(text=text, name=name):
                    self.assertEqual(load(name).__globals__["_links"](text), links)

    def test_comment_after_equals_is_not_a_link(self):
        for text in (
            "x=//increment",
            "flag=//go:build linux",
            "a=//TODO tidy",
            "path=//package.json",
        ):
            for name in NAMES:
                with self.subTest(text=text, name=name):
                    self.assertEqual(load(name).__globals__["_links"](text), [])

    def test_long_runs_of_closing_brackets_are_trimmed_quickly(self):
        for text, links in (
            ("ftp://allowed.example/" + ")" * 1_000_000, ["ftp://allowed.example/"]),
            (
                "https://allowed.example/" + ")" * 1_000_000,
                ["https://allowed.example/"],
            ),
            (" //allowed.example/" + ")" * 1_000_000, ["//allowed.example/"]),
            ("allowed.example/" + ")" * 1_000_000, ["http://allowed.example/"]),
            (
                "https://allowed.example/" + "]" * 1_000_000,
                ["https://allowed.example/"],
            ),
            (
                "https://allowed.example/a_(b" + ")" * 1_000_000,
                ["https://allowed.example/a_(b)"],
            ),
        ):
            for name in NAMES:
                links_of = load(name).__globals__["_links"]
                with self.subTest(name=name, text=text[:30]):
                    started = time.monotonic()
                    self.assertEqual(links_of(text), links)
                    self.assertLess(time.monotonic() - started, 10)

    def test_many_email_like_words_are_skipped_quickly(self):
        for text in (
            "a.bc@x " * 150_000,
            ",a.bc@x" * 150_000,
        ):
            for name in NAMES:
                links_of = load(name).__globals__["_links"]
                with self.subTest(name=name, text=text[:8]):
                    started = time.monotonic()
                    self.assertEqual(links_of(text), [])
                    self.assertLess(time.monotonic() - started, 10)

    def test_link_matching_stays_fast_on_long_input(self):
        inputs = (
            "a" * 1_000_000,
            "a+" * 500_000,
            "ftp:" * 250_000,
            "/" * 1_000_000,
            "a." * 500_000,
            "x://" * 250_000,
            " //a" * 250_000,
            " //" + "a." * 500_000,
            " //a.bc" + "." * 1_000_000 + "x",
            " //[" + ":" * 1_000_000,
            " //a@" * 250_000,
            " //a.b" * 200_000,
            " //" + "a" * 1_000_000,
        )
        for name in NAMES:
            links = load(name).__globals__["_links"]
            for text in inputs:
                with self.subTest(name=name, text=text[:8]):
                    started = time.monotonic()
                    try:
                        links(text)
                    except ValueError:
                        pass
                    self.assertLess(time.monotonic() - started, 10)

    def test_private_mixed_and_special_addresses_refused(self):
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "168.63.129.16",
            "fec0::1",
            "100.64.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "::ffff:127.0.0.1",
            "2002:7f00:1::1",
        ):
            for name in NAMES:
                records = [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("11.0.0.1", 80)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 80)),
                ]
                with (
                    self.subTest(address=address, name=name),
                    mock.patch.dict(
                        os.environ, {"LINK_EVAL_ALLOWED_HOSTS": "allowed.example"}
                    ),
                    mock.patch("socket.getaddrinfo", return_value=records),
                    mock.patch("socket.socket") as connect,
                ):
                    result = load(name)(None, "http://allowed.example", None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not permitted", result["reason"])
                    connect.assert_not_called()

    def test_no_wildcards_credentials_metadata_or_database_ports(self):
        for url, setting in (
            ("http://allowed.example:5432", "allowed.example"),
            ("https://", "allowed.example"),
            ("http://user:secret@allowed.example", "allowed.example"),
            ("http://allowed.example", "*.example"),
            ("http://metadata.google.internal", "metadata.google.internal"),
            ("http://metadata", "metadata"),
            ("http://instance-data", "instance-data"),
            ("http://allowed.example", "https://allowed.example"),
            ("http://allowed.example:0", "allowed.example"),
            ("http://allowed.example:65536", "allowed.example"),
            ("http://allowed.example\\@other.example", "allowed.example"),
        ):
            for name in NAMES:
                with (
                    self.subTest(url=url, setting=setting, name=name),
                    mock.patch.dict(os.environ, {"LINK_EVAL_ALLOWED_HOSTS": setting}),
                    mock.patch("socket.getaddrinfo") as resolve,
                ):
                    result = load(name)(None, url, None, None)
                    self.assertEqual(result["score"], 0.0)
                    self.assertIn("not permitted", result["reason"])
                    self.assertNotIn("secret", result["reason"])
                    resolve.assert_not_called()

    def test_pinned_connection_preserves_authority_and_refuses_redirects(self):
        for name in NAMES:
            for scheme, status in (
                ("http", 200),
                ("https", 200),
                ("http", 302),
                ("https", 404),
            ):
                address = ("11.0.0.1", 443 if scheme == "https" else 80)
                records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", address)]
                target = (
                    "http.client.HTTPSConnection"
                    if scheme == "https"
                    else "http.client.HTTPConnection"
                )
                with (
                    self.subTest(name=name, scheme=scheme, status=status),
                    mock.patch.dict(
                        os.environ,
                        {
                            "LINK_EVAL_ALLOWED_HOSTS": "ALLOWED.EXAMPLE.",
                            "HTTP_PROXY": "http://127.0.0.1:1",
                        },
                    ),
                    mock.patch("socket.getaddrinfo", return_value=records) as resolve,
                    mock.patch("socket.socket") as sockets,
                    mock.patch(target) as transport,
                ):
                    connection = transport.return_value
                    connection.getresponse.return_value.__enter__.return_value.status = status
                    connection.request.side_effect = (
                        lambda *a, connection=connection, **k: (
                            connection._create_connection()
                        )
                    )
                    result = load(name)(
                        None,
                        scheme + "://allowed.example/docs?view=1#section",
                        None,
                        None,
                    )
                    self.assertEqual(result["score"], 1.0 if status == 200 else 0.0)
                    self.assertEqual(
                        transport.call_args.args, ("allowed.example", address[1])
                    )
                    sockets.return_value.connect.assert_called_once_with(address)
                    resolve.assert_called_once_with(
                        "allowed.example", address[1], type=socket.SOCK_STREAM
                    )
                    connection.request.assert_called_once_with(
                        "HEAD", "/docs?view=1", headers={"Connection": "close"}
                    )
                    connection.close.assert_called_once()

    def test_resolver_error_is_not_returned(self):
        for name in NAMES:
            with (
                self.subTest(name=name),
                mock.patch.dict(
                    os.environ, {"LINK_EVAL_ALLOWED_HOSTS": "allowed.example"}
                ),
                mock.patch(
                    "socket.getaddrinfo",
                    side_effect=socket.gaierror("private diagnostic value"),
                ),
            ):
                result = load(name)(None, "https://allowed.example", None, None)
                self.assertEqual(result["score"], 0.0)
                self.assertEqual(result["reason"], "Link host could not be resolved")

    def test_all_links_share_deadline_and_first_failure_stops(self):
        evaluate = load("no_invalid_links")
        check = mock.Mock(side_effect=[200, 404])
        with mock.patch.dict(evaluate.__globals__, {"_link_status": check}):
            result = evaluate(
                None,
                "allowed.example/one allowed.example/two allowed.example/three",
                None,
                None,
            )
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(check.call_count, 2)
        self.assertEqual(
            check.call_args_list[0].args[1], check.call_args_list[1].args[1]
        )

    def test_link_budget_refuses_before_any_request(self):
        for name in NAMES:
            evaluate = load(name)
            check = mock.Mock()
            with (
                self.subTest(name=name),
                mock.patch.dict(evaluate.__globals__, {"_link_status": check}),
            ):
                result = evaluate(
                    None,
                    " ".join(f"allowed.example/{i}" for i in range(21)),
                    None,
                    None,
                )
                self.assertEqual(result["score"], 0.0)
                self.assertEqual(result["reason"], "Too many links to validate")
                check.assert_not_called()

    def test_next_validated_address_is_tried_when_first_refuses(self):
        received = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                received.append((self.headers["Host"], self.path))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                pass

        # Bound but not listening: connections are refused, and the port stays
        # reserved so the HTTP server below cannot be given the same one.
        closed = socket.socket()
        self.addCleanup(closed.close)
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        records = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("11.0.0.1", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("11.0.0.2", 80)),
        ]
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            routes = {
                ("11.0.0.1", 80): ("127.0.0.1", closed_port),
                ("11.0.0.2", 80): ("127.0.0.1", server.server_port),
            }
            try:
                for name in NAMES:
                    attempts = []
                    received.clear()
                    with (
                        self.subTest(name=name),
                        mock.patch.dict(
                            os.environ, {"LINK_EVAL_ALLOWED_HOSTS": "allowed.example"}
                        ),
                        mock.patch("socket.getaddrinfo", return_value=records),
                        mock.patch("socket.socket", _routed_socket(routes, attempts)),
                    ):
                        result = load(name)(
                            None, "http://allowed.example/docs", None, None
                        )
                    self.assertEqual(result["score"], 1.0, result)
                    self.assertEqual(attempts, [("11.0.0.1", 80), ("11.0.0.2", 80)])
                    self.assertEqual(received, [("allowed.example", "/docs")])
            finally:
                server.shutdown()
                thread.join()

    def test_https_deadline_still_applies_after_a_late_tls_handshake(self):
        # TCP uses most of the budget, TLS finishes after the deadline, then
        # the server keeps sending one header byte at a time.
        with tempfile.TemporaryDirectory() as directory:
            cert, key = _self_signed_certificate(Path(directory), "allowed.example")
            server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_context.load_cert_chain(cert, key)
            client_context = ssl.create_default_context(cafile=str(cert))
            stop = threading.Event()
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen()

            def slow_server():
                peer, _ = listener.accept()
                with peer:
                    if stop.wait(0.4):
                        return
                    try:
                        with server_context.wrap_socket(peer, server_side=True) as tls:
                            tls.recv(4096)
                            for byte in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 100:
                                if stop.wait(0.1):
                                    return
                                tls.sendall(bytes([byte]))
                    except OSError:
                        return

            server = threading.Thread(target=slow_server, daemon=True)
            server.start()
            records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("11.0.0.1", 443))]
            routes = {("11.0.0.1", 443): listener.getsockname()}
            link_status = load("no_invalid_links").__globals__["_link_status"]
            try:
                with (
                    mock.patch.dict(
                        os.environ, {"LINK_EVAL_ALLOWED_HOSTS": "allowed.example"}
                    ),
                    mock.patch("socket.getaddrinfo", return_value=records),
                    mock.patch(
                        "socket.socket", _routed_socket(routes, [], connect_delay=0.4)
                    ),
                    mock.patch(
                        "ssl._create_default_https_context", lambda: client_context
                    ),
                ):
                    started = time.monotonic()
                    with self.assertRaisesRegex(
                        TimeoutError, "Link validation timed out"
                    ):
                        link_status("https://allowed.example/", started + 0.6)
                    elapsed = time.monotonic() - started
            finally:
                stop.set()
                server.join(5)
                listener.close()
        self.assertLess(elapsed, 2.0)

    def test_system_eval_version_is_bumped(self):
        import ast

        path = ROOT / "futureagi/model_hub/management/commands/seed_system_evals.py"
        tree = ast.parse(path.read_text())
        versions = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "SYSTEM_EVALS_VERSION"
                for target in node.targets
            )
        ]
        # 30 is the version before the link-check changes. Templates seeded at
        # 30 must be refreshed, whatever number SYSTEM_EVALS_VERSION ships as.
        self.assertEqual(len(versions), 1)
        self.assertGreater(versions[0], 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
