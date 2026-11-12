"""Synthetic fixtures for the K2 sign-in tests (W4b, G14b).

``LoopbackAuthorizationServers`` is a real HTTP server on 127.0.0.1 that
implements the OAuth 2 endpoints the four pilot providers declare, as Google
and Microsoft document them: the authorization endpoint (a 302 back to the
redirect URI, as the browser would follow it), the token endpoint for the
authorization-code grant with PKCE S256 and for the refresh grant, and
Google's RFC 7009 revocation endpoint. Microsoft declares none. Clients
authenticate with ``client_secret_post`` (the declared ``clientAuth: body``);
HTTP Basic is refused. Google's refresh tokens do not rotate, Microsoft's do.

``k2_bundle`` copies the four pilot providers and their apps from the real
Django bundle with their declared endpoints moved onto that server. The
published manifest schema requires https for those endpoints; this synthetic
copy is plain http on loopback only, reached through the operator's egress
list (``AGENTCC_MCP_EGRESS_PRIVATE_ALLOW``). Every value here is a fake and
nothing reaches a vendor.

The copy also declares what the gateway admits a pilot's calls by
(``serve_at_gateway``): the org client source and the catalog owner's
connection enablement. The shipped records declare neither, so the gateway
refuses every call to them; ``unserved_at_gateway`` takes the declaration out
again to model the bundle as it ships.
"""

import base64
import copy
import hashlib
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

REAL_BUNDLE = Path(__file__).resolve().parents[1] / "connector_catalog" / "catalog.json"
PILOTS = ("gmail", "google-calendar", "microsoft-outlook", "microsoft-teams")
RUNTIME = "http://agentcc-connectors.k2-synthetic.svc.cluster.local:8090"
GOOGLE_CLIENT_ID = "k2-synthetic-google-client.apps.example.test"
GOOGLE_SECRET = "k2-synthetic-google-client-secret-not-real"
MICROSOFT_CLIENT_ID = "k2-synthetic-microsoft-client-id"
MICROSOFT_SECRET = "k2-synthetic-microsoft-client-secret-not-real"
PLATFORM_ID = "k2-synthetic-platform-client-id"
PLATFORM_SECRET = "k2-synthetic-platform-client-secret-not-real"
SECRETS = (GOOGLE_SECRET, MICROSOFT_SECRET, PLATFORM_SECRET)
SINGLE_TENANT = "11111111-2222-4333-8444-555555555555"
# What the gateway's K2 admission reads (agentcc-gateway internal/mcp/vendors:
# served_set.go providerClientSource, client_admission.go connectionEnabled).
GATEWAY_CLIENT_SOURCES = ["org"]
GATEWAY_ENABLEMENT = {
    "schemaVersion": 1,
    "enabled": True,
    "operations": ["connect", "list_tools", "invoke"],
}


def serve_at_gateway(record):
    """Declare on a pilot provider record what the gateway admits it by."""
    record["auth"]["oauth2"]["clientSources"] = list(GATEWAY_CLIENT_SOURCES)
    record.setdefault("x-gateway", {})["connectionEnablement"] = copy.deepcopy(
        GATEWAY_ENABLEMENT
    )
    return record


def unserved_at_gateway(record):
    """A pilot provider record as the bundle ships it: no gateway declaration."""
    record["auth"]["oauth2"].pop("clientSources", None)
    gateway = record.get("x-gateway")
    if isinstance(gateway, dict):
        gateway.pop("connectionEnablement", None)
        if not gateway:
            del record["x-gateway"]
    return record


def _ref(app_id):
    return f"{app_id}/futureagi-rest@1.0.0"


def k2_entry(app_id, *, url=None, **extra):
    """An org config entry for a pilot server on the synthetic runtime."""
    entry = {
        "url": url or f"{RUNTIME}/{app_id}/mcp",
        "transport": "http",
        "catalog_ref": _ref(app_id),
        "credential": {"mode": "per_user"},
    }
    entry.update(extra)
    return entry


def write_k2_bundle(target: Path, origin: str) -> Path:
    """The pilots' records, their declared endpoints moved to ``origin``."""
    real = json.loads(REAL_BUNDLE.read_text())
    apps = [copy.deepcopy(a) for a in real["apps"] if a["id"] in PILOTS]
    providers = []
    for record in real["providers"]:
        if record["app"] not in PILOTS or record["id"] != "futureagi-rest":
            continue
        record = copy.deepcopy(record)
        oauth2 = record["auth"]["oauth2"]
        for field in ("authorizeUrl", "tokenUrl"):
            oauth2[field] = _moved(oauth2[field], origin)
        if isinstance(oauth2.get("revocation"), dict):
            oauth2["revocation"]["url"] = _moved(oauth2["revocation"]["url"], origin)
        providers.append(serve_at_gateway(record))
    for app in apps:
        app["defaultProvider"] = "futureagi-rest"
    bundle = {**{k: real[k] for k in ("schema", "version", "sha256", "sourceCommit")}}
    bundle.update(apps=apps, providers=providers, platforms=[])
    target.write_text(json.dumps(bundle))
    return target


def add_real_provider(target: Path, app_id: str, provider_id: str, origin: str):
    """One more app and provider, copied from the real bundle into the
    synthetic one at ``target`` with its declared OAuth endpoints moved to
    ``origin``; returns the provider record. Nothing reaches the vendor."""
    real = json.loads(REAL_BUNDLE.read_text())
    app = next(a for a in real["apps"] if a["id"] == app_id)
    record = copy.deepcopy(
        next(
            p
            for p in real["providers"]
            if p["app"] == app_id and p["id"] == provider_id
        )
    )
    oauth2 = record["auth"]["oauth2"]
    for field in ("authorizeUrl", "tokenUrl"):
        oauth2[field] = _moved(oauth2[field], origin)
    if isinstance(oauth2.get("revocation"), dict):
        oauth2["revocation"]["url"] = _moved(oauth2["revocation"]["url"], origin)
    bundle = json.loads(target.read_text())
    bundle["apps"].append(copy.deepcopy(app))
    bundle["providers"].append(record)
    target.write_text(json.dumps(bundle))
    return record


def _moved(url: str, origin: str) -> str:
    parts = urlsplit(url)
    vendor = (
        "google"
        if parts.netloc.endswith("google.com") or "googleapis" in parts.netloc
        else "microsoft"
    )
    return f"{origin}/{vendor}{parts.path}"


class LoopbackAuthorizationServers:
    def __init__(self, clients: dict, redirect_uri: str):
        self.clients = dict(clients)  # registered apps: client_id -> secret
        self.redirect_uri = redirect_uri
        self.subject = "member-1"
        self.lock = threading.Lock()
        # (method, vendor, endpoint, sorted parameter names, client auth, ok):
        # names only, never a value.
        self.requests = []
        self.codes, self.issued, self.revoked, self.refreshed = {}, [], [], []
        self._used_refresh = set()
        self._n = 0
        self.tenants = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.port = self.httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def token_for(self, subject):
        return [t for t in self.issued if t["subject"] == subject]

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # no request line reaches a log
                return

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                vendor, endpoint = fake._route(parts.path)
                fake._record("GET", vendor, endpoint, query, "none", True)
                if endpoint != "authorize":
                    return self._json(404, {"error": "not_found"})
                status, location = fake._authorize(vendor, query)
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.end_headers()

            def do_POST(self):
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode()
                form = {k: v[0] for k, v in parse_qs(raw).items()}
                vendor, endpoint = fake._route(parts.path)
                basic = self.headers.get("Authorization", "")
                status, body = fake._post(vendor, endpoint, form, basic)
                self._json(status, body)

            def _json(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    # --- Routing ------------------------------------------------------------------

    def _route(self, path):
        if path == "/google/o/oauth2/v2/auth":
            return "google", "authorize"
        if path == "/google/token":
            return "google", "token"
        if path == "/google/revoke":
            return "google", "revoke"
        parts = path.split("/")
        if (
            len(parts) == 6
            and parts[1] == "microsoft"
            and parts[3:5] == ["oauth2", "v2.0"]
        ):
            self.tenants.append(parts[2])
            return "microsoft", {"authorize": "authorize", "token": "token"}.get(
                parts[5], ""
            )
        return "", ""

    def _record(self, method, vendor, endpoint, params, auth, ok):
        with self.lock:
            self.requests.append(
                (method, vendor, endpoint, tuple(sorted(params)), auth, ok)
            )

    # --- The authorization endpoint ---------------------------------------------------

    def _authorize(self, vendor, query):
        if (
            query.get("response_type") != "code"
            or query.get("client_id") not in self.clients
            or query.get("redirect_uri") != self.redirect_uri
            or query.get("code_challenge_method") != "S256"
            or not query.get("code_challenge")
            or not query.get("state")
            or not query.get("scope")
        ):
            return 400, None
        code = "k2-synthetic-code-" + secrets.token_urlsafe(12)
        with self.lock:
            self.codes[code] = {**query, "vendor": vendor, "subject": self.subject}
        back = {"code": code, "state": query["state"]}
        if vendor == "google":
            back["iss"] = self.origin  # the issuer the declared spec implies
        return 302, f"{self.redirect_uri}?{urlencode(back)}"

    # --- The token and revocation endpoints -------------------------------------------

    def _post(self, vendor, endpoint, form, basic):
        client_id = form.get("client_id")
        secret = form.pop("client_secret", None)
        ok = (
            not basic
            and client_id in self.clients
            and secret == self.clients[client_id]
        )
        self._record(
            "POST",
            vendor,
            endpoint,
            list(form) + (["client_secret"] if secret else []),
            "basic" if basic else ("post" if secret else "none"),
            ok,
        )
        if not ok:
            return 401, {"error": "invalid_client"}
        if endpoint == "revoke" and vendor == "google":
            with self.lock:
                self.revoked.append(form.get("token"))
            return 200, {}
        if endpoint != "token":
            return 404, {"error": "not_found"}
        if form.get("grant_type") == "authorization_code":
            return self._code_grant(vendor, form)
        if form.get("grant_type") == "refresh_token":
            return self._refresh_grant(vendor, form)
        return 400, {"error": "unsupported_grant_type"}

    def _code_grant(self, vendor, form):
        with self.lock:
            asked = self.codes.pop(form.get("code"), None)
        verifier = form.get("code_verifier", "")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        if (
            asked is None
            or asked["vendor"] != vendor
            or challenge != asked["code_challenge"]
            or form.get("client_id") != asked["client_id"]
            or form.get("redirect_uri") != asked["redirect_uri"]
        ):
            return 400, {"error": "invalid_grant"}
        offline = (
            asked.get("access_type") == "offline"
            if vendor == "google"
            else "offline_access" in asked["scope"].split()
        )
        return 200, self._issue(
            vendor,
            asked["subject"],
            asked["client_id"],
            asked["scope"],
            refresh=offline,
        )

    def _refresh_grant(self, vendor, form):
        presented = form.get("refresh_token")
        with self.lock:
            self.refreshed.append(vendor)
            grant = next(
                (t for t in self.issued if t.get("refresh_token") == presented), None
            )
            bad = (
                grant is None
                or presented in self._used_refresh
                or presented in self.revoked
            )
            if not bad and vendor == "microsoft":
                self._used_refresh.add(presented)  # Microsoft's refresh tokens rotate
        if bad or grant["client_id"] != form.get("client_id"):
            return 400, {"error": "invalid_grant"}
        rotate = vendor == "microsoft"
        body = self._issue(
            vendor, grant["subject"], grant["client_id"], grant["scope"], refresh=rotate
        )
        if not rotate:
            body.pop("refresh_token", None)
        return 200, body

    def _issue(self, vendor, subject, client_id, scope, *, refresh):
        with self.lock:
            self._n += 1
            n = self._n
        body = {
            "access_token": f"k2-synthetic-{vendor}-access-{n}-{subject}",
            "token_type": "Bearer",
            "expires_in": 3599,
            "scope": scope,
        }
        if refresh:
            body["refresh_token"] = f"k2-synthetic-{vendor}-refresh-{n}-{subject}"
        with self.lock:
            self.issued.append({**body, "subject": subject, "client_id": client_id})
        return body
