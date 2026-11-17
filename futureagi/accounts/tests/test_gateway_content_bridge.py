"""Mock-bound bridge tests; these do not qualify canonical database authority."""

import importlib
import json
import unittest
from types import SimpleNamespace
from uuid import UUID


class GatewayContentBridgeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.m = importlib.import_module("accounts.services.gateway_content_bridge")
            self.t = importlib.import_module(
                "accounts.services.gateway_content_bridge_types"
            )
        except ModuleNotFoundError as exc:
            self.fail(f"canonical content bridge is absent: {exc.name}")
        self.person, self.org, self.ws, self.key = [UUID(int=n) for n in range(1, 5)]
        self.body = {
            "version": "gateway-content-authorization-v1",
            "actor": {
                "kind": "gateway_key",
                "person_id": str(self.person),
                "gateway_key_id": "synthetic-gateway-key",
            },
            "org_id": str(self.org),
            "workspace_id": str(self.ws),
            "action": "RESOURCE_CONTENT_READ",
            "target": "synthetic-target",
            "phase": "INITIAL",
            "request_id": "synthetic-request",
            "unit_id": "synthetic-unit",
        }
        self.calls = []
        self.identity = self.t.ProvenIdentity(
            self.person, self.org, "personal_key", self.key, self.ws
        )
        self.decision = self.t.Decision("ALLOW", "AUTHORIZED", b"\x00\xff")
        self.target = self.t.Target(self.org, "RESOURCE", "synthetic-server")
        self.ports = self.t.Ports(
            transport=SimpleNamespace(authenticate=lambda evidence: object()),
            gateway_keys=SimpleNamespace(resolve=self.identity_lookup),
            oauth=SimpleNamespace(
                resolve=lambda *a: self.fail("OAuth must not be guessed")
            ),
            workspaces=SimpleNamespace(resolve=lambda identity, requested: requested),
            targets=SimpleNamespace(resolve=self.target_lookup),
            prompts=SimpleNamespace(resolve=lambda *a: {"topic": "synthetic text"}),
            canonical=SimpleNamespace(authorize=self.authorize),
        )
        self.bridge = self.m.ContentBridge(self.ports)

    def identity_lookup(self, transport, actor):
        self.calls.append("identity")
        return self.identity

    def target_lookup(self, handle, org, workspace, kind):
        self.calls.append(("target", handle, org, workspace, kind))
        return self.target

    def authorize(self, identity, request, resolve_target, arguments):
        self.calls.append(("canonical", request.phase, arguments))
        self.assertEqual(
            resolve_target(request.target, request.org_id, request.workspace_id),
            self.target,
        )
        return self.decision

    def send(self, body=None):
        return self.bridge.authorize(
            b"synthetic-service-proof", json.dumps(body or self.body).encode()
        )

    def test_positive_mock_uses_fresh_identity_and_target_each_phase(self):
        for phase in ("INITIAL", "RELEASE", "CONTINUATION"):
            self.body["phase"] = phase
            self.assertEqual(self.send(), self.decision)
        self.assertEqual(self.calls.count("identity"), 3)
        self.assertEqual(
            sum(isinstance(c, tuple) and c[0] == "target" for c in self.calls), 3
        )

    def test_transport_refusal_precedes_identity(self):
        self.ports.transport.authenticate = lambda evidence: None
        self.assertEqual(self.send().decision, "DENIED")
        self.assertEqual(self.calls, [])

    def test_duplicate_unknown_or_trailing_json_is_denied(self):
        raw = json.dumps(self.body)
        for bad in (
            raw[:-1] + ',"phase":"RELEASE"}',
            raw + "{}",
            raw[:-1] + ',"role":"owner"}',
        ):
            self.assertEqual(
                self.bridge.authorize(b"synthetic", bad.encode()).decision, "DENIED"
            )
        self.assertEqual(self.calls, [])

    def test_invalid_selectors_and_boolean_phase_are_denied(self):
        for field, value in (
            ("workspace_id", str(UUID(int=0))),
            ("phase", True),
            ("target", ""),
            ("action", "UNKNOWN"),
        ):
            body = dict(self.body, **{field: value})
            self.assertEqual(self.send(body).decision, "DENIED")
        self.assertEqual(self.calls, [])

    def test_body_bound_and_strict_utf8(self):
        for raw in (b"x" * 8193, b"\xff", b"null", b"[]"):
            self.assertEqual(
                self.bridge.authorize(b"synthetic", raw).decision, "DENIED"
            )

    def test_missing_bridge_is_unavailable_not_arbitrary_person_key(self):
        def unavailable(*args):
            raise self.t.PortUnavailable()

        self.ports.gateway_keys.resolve = unavailable
        self.assertEqual(self.send().decision, "UNAVAILABLE")
        self.assertFalse(any(isinstance(c, tuple) for c in self.calls))

    def test_inactive_key_is_denied(self):
        self.identity = None
        self.assertEqual(self.send().decision, "DENIED")

    def test_actor_org_and_pinned_workspace_cannot_rebind(self):
        for values in (
            (UUID(int=99), self.org, self.ws),
            (self.person, UUID(int=99), self.ws),
            (self.person, self.org, UUID(int=99)),
        ):
            self.identity = self.t.ProvenIdentity(
                values[0], values[1], "personal_key", self.key, values[2]
            )
            self.assertEqual(self.send().decision, "DENIED")

    def test_unpinned_workspace_needs_authoritative_selection(self):
        self.identity = self.t.ProvenIdentity(
            self.person, self.org, "personal_key", self.key, None
        )
        self.ports.workspaces.resolve = lambda *a: None
        self.assertEqual(self.send().decision, "DENIED")

    def test_oauth_not_relabelled_as_auth_token(self):
        self.body["actor"] = {
            "kind": "oauth",
            "person_id": str(self.person),
            "client_id": "synthetic-client",
        }
        self.assertEqual(self.send().decision, "UNAVAILABLE")
        self.assertEqual(self.calls, [])

    def test_target_resolver_is_lazy_inside_canonical_call(self):
        self.ports.canonical.authorize = lambda *args: self.decision
        self.assertEqual(self.send(), self.decision)
        self.assertEqual(self.calls, ["identity"])

    def test_target_resolver_rejects_scope_rebinding(self):
        attempted = []
        returned = []

        def rebind(identity, request, resolve_target, arguments):
            attempted.append("foreign-workspace")
            result = resolve_target(request.target, request.org_id, UUID(int=99))
            returned.append(result)
            return self.decision

        self.ports.canonical.authorize = rebind
        result = self.send()
        # Assertions live OUTSIDE the bridge's dependency-exception boundary.
        self.assertEqual(attempted, ["foreign-workspace"])
        self.assertEqual(returned, [], "wrong-scope resolver returned")
        self.assertFalse(
            any(isinstance(call, tuple) and call[0] == "target" for call in self.calls),
            "wrong-scope request reached M4 storage",
        )
        self.assertEqual(result.decision, "UNAVAILABLE")

    def test_prompt_requires_bound_values_no_empty_argument_substitution(self):
        self.body["action"] = "PROMPT_GET_RENDER"
        self.target = self.t.Target(
            self.org, "PROMPT", "synthetic-server", frozenset({"topic"}), frozenset()
        )
        self.assertEqual(self.send().decision, "ALLOW")
        self.assertIn(("canonical", "INITIAL", {"topic": "synthetic text"}), self.calls)
        self.ports.prompts.resolve = lambda *a: None
        self.assertEqual(self.send().decision, "UNAVAILABLE")

    def test_null_empty_and_binary_revisions_are_distinct(self):
        for revision in (None, b"", b"\x00\xff"):
            self.decision = self.t.Decision("ALLOW", "AUTHORIZED", revision)
            result = self.send()
            self.assertEqual(result.membership_revision, revision)
            encoded = self.m.encode_decision(result)
            self.assertEqual(
                set(encoded), {"decision", "reason_code", "membership_revision"}
            )
            self.assertEqual(encoded["membership_revision"] is None, revision is None)

    def test_invalid_decision_combination_and_raw_exception_stay_closed(self):
        for decision in (
            self.t.Decision("DENIED", "AUTHORIZED", None),
            self.t.Decision("ALLOW", "AUTHORIZED", b"x" * 4097),
            self.t.Decision("DENIED", "TARGET_NOT_ACCESSIBLE", b"revision"),
        ):
            self.decision = decision
            self.assertEqual(self.send().decision, "UNAVAILABLE")

        def fail(*args):
            raise RuntimeError("synthetic-private-detail")

        self.ports.canonical.authorize = fail
        result = self.send()
        self.assertNotIn(
            "synthetic-private-detail", json.dumps(self.m.encode_decision(result))
        )

    def test_prompt_transport_bounds_match_phase_contract(self):
        self.body["action"] = "PROMPT_GET_RENDER"
        self.target = self.t.Target(
            self.org, "PROMPT", "synthetic-server", frozenset(), frozenset()
        )
        for arguments in (
            {str(n): "x" for n in range(33)},
            {"x" * 129: "x"},
            {"x": "a" * 4097},
            {str(n): "a" * 4096 for n in range(4)},
        ):
            self.ports.prompts.resolve = lambda *a, arguments=arguments: arguments
            self.assertEqual(self.send().reason_code, "ARGUMENTS_OUT_OF_BOUNDS")

    def test_encoder_closes_over_invalid_result_field_types(self):
        result = self.t.Decision("DENIED", [], None)
        self.assertEqual(self.m.encode_decision(result)["decision"], "UNAVAILABLE")

    def test_canonical_adapter_builds_native_refs_and_forwards_explicit_phase(self):
        adapter = importlib.import_module(
            "accounts.services.gateway_content_bridge_adapter"
        )
        request = self.m.parse_request(json.dumps(self.body).encode())

        def native_authorize(
            actor, credential, org, workspace, action, target, *, phase, arguments
        ):
            self.assertEqual(actor.person_id, self.person)
            self.assertEqual(credential.key_id, self.key)
            self.assertEqual(phase, ("phase", "INITIAL"))
            self.assertEqual(action, ("action", "RESOURCE_CONTENT_READ"))
            self.assertEqual(
                target.resolve(target.handle, org, workspace).kind, "native-resource"
            )
            return SimpleNamespace(
                decision=SimpleNamespace(value="ALLOW"),
                reason_code=SimpleNamespace(value="AUTHORIZED"),
                membership_revision=b"\x00\xff",
            )

        api = SimpleNamespace(
            ActorRef=SimpleNamespace,
            PersonalKeyCredential=SimpleNamespace,
            SessionCredential=SimpleNamespace,
            TargetRef=SimpleNamespace,
            TargetBinding=SimpleNamespace,
            PromptSchema=SimpleNamespace,
            Action=lambda value: ("action", value),
            Phase=lambda value: ("phase", value),
            authorize_gateway_content=native_authorize,
        )
        native = adapter.CanonicalAccounts(api, {"RESOURCE": "native-resource"})
        result = native.authorize(self.identity, request, lambda *a: self.target, None)
        self.assertEqual(result.membership_revision, b"\x00\xff")


if __name__ == "__main__":
    unittest.main()
