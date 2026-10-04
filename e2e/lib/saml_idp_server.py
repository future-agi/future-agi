"""Small local SAML IdP used only by the Playwright tenant-isolation flow.

It deliberately reuses the backend's pysaml2/xmlsec signing fixture instead
of carrying a second JavaScript XML-signing implementation in the E2E tree.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import tempfile
import threading
import zlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

from saml2_auth.tests.saml_fixtures import (
    IdPKeyPair,
    build_idp_metadata,
    create_idp_keypair,
    signed_response,
)


class SamlIdpState:
    """Per-process key material and the identity returned by the local IdP."""

    def __init__(self, *, acs_url: str, audience: str) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="e2e-saml-idp-")
        self.keypair: IdPKeyPair = create_idp_keypair(
            Path(self._temporary_directory.name), "e2e-idp"
        )
        self.acs_url = acs_url
        self.audience = audience
        self.email: str | None = None
        self._lock = threading.Lock()

    def metadata(self, base_url: str) -> str:
        """Keep the signing entity ID stable while redirecting its SSO endpoints local."""

        remote_sso = f"https://{self.keypair.name}.saml.test/app/okta/sso/saml"
        return build_idp_metadata(self.keypair, "okta").replace(
            remote_sso, f"{base_url}/app/okta/sso/saml"
        )

    def set_email(self, email: str) -> None:
        with self._lock:
            self.email = email

    def response(self, saml_request: str) -> str:
        with self._lock:
            if not self.email:
                raise ValueError("the test did not configure a SAML identity")
            email = self.email
        request_xml = zlib.decompress(base64.b64decode(saml_request), -zlib.MAX_WBITS)
        request_id = ElementTree.fromstring(request_xml).attrib["ID"]
        return signed_response(
            self.keypair,
            request_id=request_id,
            recipient=self.acs_url,
            destination=self.acs_url,
            audience=self.audience,
            subject_email=email,
        )

    def close(self) -> None:
        self._temporary_directory.cleanup()


def _form_response(*, relay_state: str, response: str, acs_url: str) -> bytes:
    def hidden(name: str, value: str) -> str:
        return f'<input type="hidden" name="{name}" value="{html.escape(value)}">'

    return (
        "<!doctype html><html><body>"
        f'<form id="saml-post" method="post" action="{html.escape(acs_url)}">'
        f"{hidden('RelayState', relay_state)}{hidden('SAMLResponse', response)}"
        "</form><script>document.getElementById('saml-post').submit()</script>"
        "</body></html>"
    ).encode()


def _handler(state: SamlIdpState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FutureAGITestSamlIdP/1"

        def _json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length) or b"{}")

        def do_GET(self) -> None:
            base_url = f"http://{self.server.server_address[0]}:{self.server.server_address[1]}"
            if self.path == "/metadata":
                payload = state.metadata(base_url).encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/samlmetadata+xml")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            parsed = urlparse(self.path)
            if parsed.path not in {
                "/app/okta/sso/saml",
                "/app/okta/sso/saml/redirect",
            }:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            query = parse_qs(parsed.query)
            try:
                payload = _form_response(
                    relay_state=query["RelayState"][0],
                    response=state.response(query["SAMLRequest"][0]),
                    acs_url=state.acs_url,
                )
            except (KeyError, ValueError, zlib.error, ElementTree.ParseError):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:
            if self.path == "/__identity":
                body = self._read_json()
                email = body.get("email")
                if not isinstance(email, str) or not email:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "email is required"})
                    return
                state.set_email(email)
                self._json(HTTPStatus.NO_CONTENT, {})
                return
            if self.path == "/__shutdown":
                self._json(HTTPStatus.NO_CONTENT, {})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def log_message(self, _format: str, *_args: object) -> None:
            """Keep the Playwright output focused on request receipts."""

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--acs-url", required=True)
    parser.add_argument("--audience", required=True)
    args = parser.parse_args()
    state = SamlIdpState(acs_url=args.acs_url, audience=args.audience)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _handler(state))
    host, port = server.server_address
    print(
        json.dumps(
            {
                "ready": True,
                "base_url": f"http://{host}:{port}",
                "metadata_url": f"http://{host}:{port}/metadata",
                "metadata": state.metadata(f"http://{host}:{port}"),
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        state.close()


if __name__ == "__main__":
    main()
