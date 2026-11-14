"""Owned, synthetic Google-shaped OAuth boundary. Never contacts Google."""

import base64
import hashlib
import html
import json
import os
import secrets
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

CLIENT = "e2e-google-client"
CALLBACK = "http://localhost:8100/saml2_auth/auth/callback/"
MODES = {"valid", "signature", "issuer", "audience", "expired", "unknown", "rotated"}


def signing_key(kid):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private.public_key().public_numbers()

    def encoded(number):
        return (
            base64.urlsafe_b64encode(
                number.to_bytes((number.bit_length() + 7) // 8, "big")
            )
            .rstrip(b"=")
            .decode()
        )

    return private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ), {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": encoded(numbers.n),
        "e": encoded(numbers.e),
    }


class IdentityServer(ThreadingHTTPServer):
    def __init__(self, address):
        super().__init__(address, Handler)
        self.keys = [
            signing_key(name + "-" + secrets.token_hex(8))
            for name in ("initial", "rotated", "unpublished")
        ]
        self.published = [self.keys[0][1]]
        self.scenarios = {}
        self.codes = {}
        self.events = []
        self.lock = threading.Lock()

    def event(self, kind, scenario=None):
        self.events.append({"kind": kind, "scenario": scenario})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def send(self, status, body, content_type="application/json", headers=None):
        raw = (
            json.dumps(body).encode()
            if content_type == "application/json"
            else body.encode()
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        if url.path == "/health":
            return self.send(200, {"synthetic": True})
        if url.path == "/events":
            return self.send(200, self.server.events)
        if url.path == "/certs":
            self.server.event("jwks")
            return self.send(200, {"keys": self.server.published})
        if url.path == "/auth":
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            scenario = cookies.get("e2e_google_scenario")
            scenario = scenario.value if scenario else ""
            if (
                scenario not in self.server.scenarios
                or query.get("client_id") != [CLIENT]
                or query.get("redirect_uri") != [CALLBACK]
                or query.get("response_type") != ["code"]
            ):
                return self.send(
                    400, {"error": "invalid synthetic authorization request"}
                )
            self.server.event("authorization", scenario)
            return self.send(
                200,
                "<!doctype html><title>Mock Google</title><h1>Synthetic Google sign-in</h1>"
                "<p>No external identity service is contacted.</p>"
                f'<form action="/approve" method="post"><input type="hidden" name="scenario" value="{html.escape(scenario)}">'
                '<button type="submit">Authorize synthetic identity</button></form>',
                "text/html",
            )
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 16384:
            return self.send(413, {"error": "too large"})
        raw = self.rfile.read(size)
        try:
            data = (
                json.loads(raw)
                if self.headers.get("Content-Type", "").startswith("application/json")
                else {key: value[0] for key, value in parse_qs(raw.decode()).items()}
            )
        except (ValueError, UnicodeError):
            return self.send(400, {"error": "invalid request"})
        with self.server.lock:
            if self.path == "/scenario":
                email, mode = data.get("email", ""), data.get("mode")
                if (
                    not isinstance(email, str)
                    or not email.startswith("e2e-")
                    or not email.endswith("@futureagi.com")
                    or mode not in MODES
                ):
                    return self.send(400, {"error": "synthetic identity required"})
                scenario = secrets.token_hex(16)
                self.server.scenarios[scenario] = {"email": email, "mode": mode}
                if mode == "rotated":
                    rotation = signing_key("rotated-" + scenario)
                    self.server.scenarios[scenario]["rotation"] = rotation
                    self.server.published.append(rotation[1])
                return self.send(201, {"scenario": scenario})
            if self.path == "/approve":
                scenario = data.get("scenario")
                if scenario not in self.server.scenarios:
                    return self.send(400, {"error": "unknown synthetic scenario"})
                code = "synthetic-code-" + secrets.token_hex(16)
                self.server.codes[code] = scenario
                return self.send(
                    302,
                    "",
                    "text/plain",
                    {"Location": CALLBACK + "?" + urlencode({"code": code})},
                )
            if self.path == "/token":
                scenario = self.server.codes.pop(data.get("code"), None)
                if (
                    not scenario
                    or data.get("client_id") != CLIENT
                    or data.get("client_secret") != "e2e-google-secret"
                    or data.get("redirect_uri") != CALLBACK
                ):
                    return self.send(400, {"error": "invalid synthetic code"})
                case = self.server.scenarios[scenario]
                mode = case["mode"]
                self.server.event("token", scenario)
                access = "synthetic-access-" + scenario
                claims = {
                    "iss": "https://accounts.google.com",
                    "aud": CLIENT,
                    "sub": "synthetic-subject-" + scenario,
                    "email": case["email"],
                    "email_verified": True,
                    "name": "Synthetic Google User",
                    "iat": int(time.time()),
                    "exp": int(time.time()) + 300,
                    "at_hash": base64.urlsafe_b64encode(
                        hashlib.sha256(access.encode()).digest()[:16]
                    )
                    .rstrip(b"=")
                    .decode(),
                }
                index, kid = 0, self.server.keys[0][1]["kid"]
                if mode == "signature":
                    index = 2
                elif mode == "issuer":
                    claims["iss"] = "https://invalid.example.test"
                elif mode == "audience":
                    claims["aud"] = "wrong-client"
                elif mode == "expired":
                    claims["exp"] = int(time.time()) - 60
                elif mode == "unknown":
                    index, kid = 2, "unpublished-" + scenario
                elif mode == "rotated":
                    index, kid = 1, case["rotation"][1]["kid"]
                private = (
                    case["rotation"][0]
                    if mode == "rotated"
                    else self.server.keys[index][0]
                )
                token = jwt.encode(
                    claims, private, algorithm="RS256", headers={"kid": kid}
                )
                return self.send(200, {"id_token": token, "access_token": access})
            return self.send(404, {"error": "not found"})


def tls_context(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name(
        [x509.NameAttribute(x509.NameOID.COMMON_NAME, "Owned Google fixture")]
    )
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.DNSName("mock-google")]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private, hashes.SHA256())
    )
    cert, key = directory / "ca.pem", directory / "key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key.chmod(0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    key.unlink()  # Only the public trust certificate remains shared with Django.
    return context


if __name__ == "__main__":
    server = IdentityServer(("0.0.0.0", 28443))
    server.socket = tls_context(os.environ.get("MOCK_TLS_DIR", "/tls")).wrap_socket(
        server.socket, server_side=True
    )
    server.serve_forever()
