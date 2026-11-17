"""Contained real-route launcher, AUTHORING ONLY until accepted OS1-OS6 inputs.

No CLI, env bypass, force_authenticate, mocked probe/listing, or stack command.
The accepted supervisor must load this module through its pinned isolated
59l bootstrap and call run_case in a fresh synthetic Django database/process.
Its adapter is intentionally NOT implemented here: a JSON `accepted: true`
would not enforce an OS boundary. See CONTRACT.txt for the adapter contract.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import urlsplit


def loopback_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("registered numeric loopback HTTP URL required")
    return value.rstrip("/")


def run_case(boundary, case, output, helpers):
    """helpers are digest-verified checks/actors module objects, not path imports."""
    if boundary is None:
        raise RuntimeError("accepted OS1-OS6 native supervisor required")
    boundary.require_before_imports(tuple("OS" + str(i) for i in range(1, 7)))
    if "django" in sys.modules or "agentcc.services.gateway_client" in sys.modules:
        raise RuntimeError("fresh guarded child required before Django startup")
    if case not in ("positive", "tls-wrong-san", "tls-untrusted-ca", "allow-absent", "allow-wrong-port", "allow-other-name", "metadata", "synthetic-metadata"):
        raise ValueError("unknown case")
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    admin = secrets.token_urlsafe(40)
    # The supervisor invokes LaunchCatalogueRoute in its registered Go child.
    # It must pin the exact binary, test tag, static input, and policy arm.
    with boundary.start_gateway(case=case, admin_token=admin, output=output) as gateway:
        base = loopback_url(gateway.url)
        os.environ["AGENTCC_GATEWAY_INTERNAL_URL"] = base
        os.environ["AGENTCC_GATEWAY_URL"] = base
        os.environ["AGENTCC_ADMIN_TOKEN"] = admin
        import django
        django.setup()
        from django.db import connection
        if not connection.settings_dict["NAME"].startswith("catalogue_733_") or connection.settings_dict["HOST"] != "127.0.0.1":
            raise RuntimeError("dedicated synthetic loopback database required")
        boundary.verify_database(connection.settings_dict["NAME"])
        from agentcc.services import connector_catalog
        connector_catalog.BUNDLE_PATH = Path(gateway.bundle)
        from django.core.wsgi import get_wsgi_application
        from wsgiref.simple_server import WSGIRequestHandler, make_server
        import httpx

        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *_args):
                pass  # Request/response hashes are recorded below, no raw credentials.

        people = helpers.actors.create_people()
        application = get_wsgi_application()
        route_calls = []

        def witnessed(environ, start_response):
            if environ.get("REQUEST_METHOD") == "POST" and environ.get("PATH_INFO") == "/agentcc/connectors/apps/cn30a-docs/add/":
                route_calls.append(environ.get("PATH_INFO"))
            return application(environ, start_response)

        with make_server("127.0.0.1", 0, witnessed, handler_class=QuietHandler) as server:
            local = "http://127.0.0.1:" + str(server.server_port)
            seal = boundary.seal_django(server, gateway, people)
            thread = threading.Thread(target=server.serve_forever, daemon=False)
            thread.start()
            try:
                with httpx.Client(timeout=30, follow_redirects=False) as client:
                    def request(person, method, path, body=None):
                        boundary.before_action(seal, gateway.seal)
                        return client.request(method, local + path, headers=people[person]["headers"], json=body)

                    def observation():
                        boundary.before_action(seal, gateway.seal)
                        response = client.get(base + "/__catalogue-observe", headers={"Authorization": "Bearer " + admin})
                        response.raise_for_status()
                        data = response.json()
                        if data["seal"] != gateway.seal:
                            raise AssertionError("gateway registration changed")
                        return data

                    def snapshot(person):
                        state = helpers.actors.database_snapshot(people[person]["org"], helpers.checks.digest)
                        for tag in ("A", "B"):
                            api = {}
                            for path in ("/agentcc/connectors/catalog/", "/agentcc/mcp/credentials/", "/agentcc/org-configs/active/"):
                                response = request(tag + "-owner", "GET", path)
                                if response.status_code not in (200, 404):
                                    raise AssertionError("state read failed")
                                api[path] = {"status": response.status_code, "digest": helpers.checks.digest(response.json())}
                            group = "own" if people[tag + "-owner"]["org"] == people[person]["org"] else "foreign"
                            state[group]["api"] = api
                        return state

                    receipts = []
                    virtual_ids = []

                    def action(label, person, host):
                        credential = "catalogue-synthetic-" + secrets.token_urlsafe(24)
                        before, first = snapshot(person), observation()
                        route_offset = len(route_calls)
                        body = {"url_variables": {"mockHost": host}, "credential": {"value": credential}}
                        response, transport_error = None, None
                        try:
                            response = request(person, "POST", "/agentcc/connectors/apps/cn30a-docs/add/", body)
                        except Exception as exc:
                            transport_error = type(exc).__name__
                        finally:
                            # Same invocation, even on a failed request or assertion-red.
                            after, last = snapshot(person), observation()
                        status = response.status_code if response is not None else 0
                        try:
                            data = response.json() if response is not None else {}
                        except ValueError:
                            data, transport_error = {}, "NonJSONResponse"
                        if not isinstance(data, dict):
                            data, transport_error = {}, "NonObjectResponse"
                        failures = helpers.checks.evaluate(label, status, data, before, after, first, last, credential)
                        if transport_error:
                            failures.append("route_transport_" + transport_error)
                        if len(route_calls) != route_offset + 1:
                            failures.append("real_authenticated_catalogue_route")
                        native = boundary.reconcile_action(seal, gateway.seal)
                        if native["ordinary_nonloopback_attempts"] != 0 or not native["accepted"] or native["dropped_events"] != 0:
                            failures.append("native_boundary_reconciliation")
                        receipt = {"case": label, "intent": boundary.static_intent_digest,
                            "status": status, "response_sha256": helpers.checks.digest(data),
                            "credential_sha256": helpers.checks.credential_fingerprint(credential),
                            "before": before, "after": after, "first": first, "last": last,
                            "route": "/agentcc/connectors/apps/cn30a-docs/add/", "failures": failures,
                            "ordinary_nonloopback_attempts": native["ordinary_nonloopback_attempts"],
                            "native_reconciliation": native, "productAccepted": False}
                        path = output / (str(len(receipts)) + "-" + label + ".json")
                        with path.open("x") as stream:
                            json.dump(receipt, stream, sort_keys=True, indent=2)
                            stream.flush()
                            os.fsync(stream.fileno())
                        receipts.append(receipt)
                        if failures:
                            raise AssertionError("catalogue assertions failed: " + ", ".join(failures))
                        if label == "positive":
                            virtual_ids.append(data["result"]["virtual_server"]["id"])

                    if case == "positive":
                        action("member", "A-member", gateway.name)
                        action("localhost", "A-owner", "localhost")
                        action("positive", "A-owner", gateway.name)
                        action("positive", "B-owner", gateway.name)
                        if len(set(virtual_ids)) != 2 or not all(virtual_ids):
                            raise AssertionError("A/B virtual identities are not distinct")
                    else:
                        action(case, "A-owner", gateway.name)
                    return receipts
            finally:
                server.shutdown()
                thread.join(timeout=10)
                if thread.is_alive():
                    raise RuntimeError("Django child thread did not close")
