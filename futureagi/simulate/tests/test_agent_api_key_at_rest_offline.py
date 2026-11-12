"""Offline production-method regressions; python -I this_file (no settings files)."""

# ruff: noqa: E402
import ast
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from simulate.tests._agent_api_key_offline import (
    ROOT,
    Agent,
    APIRequestFactory,
    CredentialManager,
    class_node,
    compile_nodes,
    generic_serializer,
    mask_key,
    models,
    settings,
    tree,
    viewsets,
)


class AgentApiKeyAtRestTests(unittest.TestCase):
    def setUp(self):
        for name in ("socket.socket", "socket.create_connection"):
            guard = patch(name, side_effect=AssertionError("network forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        settings.INTEGRATION_ENCRYPTION_KEY = (
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )

    def agent(self):
        return Agent(
            agent_name="Safe name",
            description="Description",
            inbound=True,
            agent_type="text",
        )

    def test_constructor_input_is_routed_to_setter_not_a_plain_attribute(self):
        agent = Agent(agent_name="Constructor", api_key="enc::constructor-input")
        self.assertFalse(hasattr(agent, "api_key"))
        self.assertEqual(agent.get_api_key(), "enc::constructor-input")
        self.assertNotIn("enc::constructor-input", agent.encrypted_api_key)

    def test_persisted_field_is_encrypted_not_plaintext(self):
        names = {field.name for field in Agent._meta.fields}
        self.assertNotIn("api_key", names)
        field = Agent._meta.get_field("encrypted_api_key")
        self.assertIsInstance(field, models.TextField)
        self.assertFalse(field.editable)
        agent = self.agent()
        agent.set_api_key("unique-synthetic-marker-at-rest")
        raw = field.get_prep_value(agent.encrypted_api_key)
        self.assertNotIn("unique-synthetic-marker-at-rest", raw)
        self.assertEqual(
            CredentialManager.decrypt(raw[5:].encode())["v"],
            "unique-synthetic-marker-at-rest",
        )

    def test_exact_round_trip_and_untrusted_prefix(self):
        agent = self.agent()
        for value in [
            "ordinary-marker",
            "enc::literal-client-string",
            " 空白 unicode ",
            "k" * 255,
            "",
            None,
        ]:
            with self.subTest(kind=type(value).__name__):
                agent.set_api_key(value)
                self.assertEqual(agent.get_api_key(), value or "")
        agent.set_api_key("original-synthetic-marker")
        other_ciphertext = agent.encrypted_api_key
        agent.set_api_key(other_ciphertext)
        self.assertNotEqual(agent.encrypted_api_key, other_ciphertext)
        self.assertEqual(agent.get_api_key(), other_ciphertext)

    def test_missing_malformed_wrong_keys_and_invalid_storage_fail_closed(self):
        agent = self.agent()
        agent.set_api_key("unlogged-synthetic-marker")
        original = agent.encrypted_api_key
        for key in ["", "bad-key", "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA="]:
            settings.INTEGRATION_ENCRYPTION_KEY = key
            with self.assertRaises(ValueError) as error:
                agent.get_api_key()
            self.assertNotIn(original, str(error.exception))
            self.assertNotIn("unlogged-synthetic-marker", str(error.exception))
        for key in ["", "bad-key"]:
            settings.INTEGRATION_ENCRYPTION_KEY = key
            with self.assertRaises(ValueError):
                agent.set_api_key("replacement-marker")
            self.assertEqual(agent.encrypted_api_key, original)
        settings.INTEGRATION_ENCRYPTION_KEY = (
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )
        for stored in [
            "plaintext-must-not-work",
            "enc::invalid",
            "enc::" + CredentialManager.encrypt({"v": 42}).decode(),
        ]:
            agent.encrypted_api_key = stored
            with self.assertRaises(ValueError):
                agent.get_api_key()

    def test_no_marker_or_ciphertext_in_repr_str_or_logs(self):
        agent = self.agent()
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            agent.set_api_key("repr-and-log-synthetic-marker")
            agent.get_api_key()
            rendered = repr(agent) + str(agent)
        for value in ["repr-and-log-synthetic-marker", agent.encrypted_api_key]:
            self.assertNotIn(value, rendered + output.getvalue())

    def test_metadata_only_patch_no_version_uses_established_mask(self):
        agent = self.agent()
        marker = "patch-no-version-synthetic-marker"
        # Baseline must reach the actual response, not fail on missing accessors.
        if hasattr(agent, "set_api_key"):
            agent.set_api_key(marker)
        else:
            agent.api_key = marker
        agent.save = Mock()
        serializer_class = generic_serializer()
        serializer_class_alias = serializer_class

        class Operations(viewsets.ModelViewSet):
            authentication_classes = []
            permission_classes = []
            serializer_class = serializer_class_alias

            def get_object(self):
                return agent

        # Match the production operations serializer selection and DRF update
        # route without importing the entire application's workers/settings.
        Operations.serializer_class = serializer_class
        request = APIRequestFactory().patch(
            "/agent-definition-operations/offline/",
            {"description": "Edited"},
            format="json",
        )
        with patch.object(Agent.objects, "select_for_update") as lock:
            lock.return_value.get.return_value = agent
            response = Operations.as_view({"patch": "partial_update"})(
                request, pk=str(agent.pk)
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["api_key"], mask_key(marker))
        self.assertNotIn(marker, json.dumps(response.data))
        self.assertNotIn("encrypted_api_key", response.data)
        self.assertEqual(agent.description, "Edited")
        source = (ROOT / "simulate/views/agent_definition.py").read_text()
        self.assertIn("class AgentDefinitionOperationsViewSet", source)
        self.assertIn("return AgentDefinitionSerializer", source)

    def test_canonical_sync_also_encrypts_untrusted_prefixed_input(self):
        node = next(
            n
            for n in tree("simulate/services/agent_definition.py").body
            if isinstance(n, ast.FunctionDef) and n.name == "_apply_secrets"
        )
        from simulate.services.agent_api_key_storage import encrypt_api_key

        apply = compile_nodes(
            [node], {"is_masked": lambda _: False, "encrypt_api_key": encrypt_api_key}
        )["_apply_secrets"]
        creds = SimpleNamespace(api_key="", api_secret="")
        apply(
            creds,
            api_key="enc::literal-client-key",
            api_secret="",
            provider_changed=False,
        )
        self.assertNotEqual(creds.api_key, "enc::literal-client-key")
        self.assertEqual(
            CredentialManager.decrypt(creds.api_key[5:].encode())["v"],
            "enc::literal-client-key",
        )

    def test_additional_tracer_selectors_use_authorized_accessor(self):
        for relative in ["tracer/selectors.py", "tracer/selectors/__init__.py"]:
            node = next(
                n
                for n in tree(relative).body
                if isinstance(n, ast.FunctionDef) and n.name == "get_agent_api_key"
            )
            agent = SimpleNamespace(
                get_api_key=Mock(return_value="selector-synthetic-marker")
            )
            ns = {
                "Optional": __import__("typing").Optional,
                "ProviderChoices": SimpleNamespace(VAPI="vapi"),
                "get_observability_provider": lambda *_, agent=agent: SimpleNamespace(
                    agent_definition=agent
                ),
                "logger": Mock(),
            }
            resolve = compile_nodes([node], ns)["get_agent_api_key"]
            self.assertEqual(resolve("project", "retell"), "selector-synthetic-marker")
            agent.get_api_key.assert_called_once_with()

    def test_generic_create_update_preserve_masks_and_clear_explicit_null(self):
        serializer = generic_serializer()()
        marker = "enc::client-not-storage"
        with (
            patch.object(Agent, "save"),
            patch.object(Agent, "create_version", create=True) as version,
        ):
            version.return_value = SimpleNamespace()
            agent = serializer.create(
                {
                    "agent_name": "New",
                    "agent_type": "text",
                    "inbound": True,
                    "description": "",
                    "api_key": marker,
                }
            )
        self.assertEqual(agent.get_api_key(), marker)
        blob = agent.encrypted_api_key
        with (
            patch.object(Agent.objects, "select_for_update") as lock,
            patch.object(Agent, "save"),
        ):
            lock.return_value.get.return_value = agent
            serializer.update(
                agent, {"api_key": mask_key(marker), "description": "kept"}
            )
            self.assertEqual(agent.encrypted_api_key, blob)
            serializer.update(agent, {"api_key": None})
            self.assertEqual(agent.get_api_key(), "")
            self.assertIsNone(agent.encrypted_api_key)

    def test_response_uses_version_precedence_and_encrypted_fallback(self):
        cls = class_node(
            "simulate/serializers/response/agent_definition.py",
            "AgentDefinitionResponseSerializer",
        )
        method = next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == "get_api_key"
        )
        render = compile_nodes([method], {"mask_key": mask_key})["get_api_key"]
        agent = self.agent()
        agent.set_api_key("response-synthetic-marker")
        serializer = SimpleNamespace(_get_latest_creds=lambda _: None)
        self.assertEqual(
            render(serializer, agent), mask_key("response-synthetic-marker")
        )
        serializer._get_latest_creds = lambda _: SimpleNamespace(provider_type="retell")
        serializer._get_masked_api_key = lambda _: "canonical-mask"
        self.assertEqual(render(serializer, agent), "canonical-mask")
        serializer._get_latest_creds = lambda _: SimpleNamespace(
            provider_type="livekit"
        )
        self.assertEqual(render(serializer, agent), "")

    def test_observability_reads_accessor_and_decryption_failure_never_falls_back(self):
        cls = class_node(
            "tracer/services/observability_providers.py", "ObservabilityService"
        )
        method = next(
            n
            for n in cls.body
            if isinstance(n, ast.FunctionDef) and n.name == "_validate_agent_api_key"
        )
        method.decorator_list = []
        logger = Mock()
        resolve = compile_nodes(
            [method], {"ObservabilityProvider": object, "logger": logger}
        )["_validate_agent_api_key"]
        agent = self.agent()
        agent.set_api_key("provider-synthetic-marker")
        self.assertEqual(resolve(agent, None, "Retell"), "provider-synthetic-marker")
        agent.encrypted_api_key = "enc::invalid"
        with self.assertRaisesRegex(
            ValueError, "Agent credential storage is unavailable"
        ):
            resolve(agent, None, "Retell")
        logger.warning.assert_not_called()

    def test_replay_copies_clear_via_accessor_and_encrypts_before_save(self):
        method = next(
            n
            for n in tree("tracer/utils/replay_session.py").body
            if isinstance(n, ast.FunctionDef)
            and n.name == "get_or_create_agent_definition"
        )
        source = self.agent()
        source.set_api_key("replay-synthetic-marker")
        logger = Mock()
        ns = {
            "AgentDefinition": Agent,
            "AgentTypeChoices": SimpleNamespace(VOICE="voice"),
            "AgentVersion": SimpleNamespace(
                StatusChoices=SimpleNamespace(ACTIVE="active")
            ),
            "Project": object,
            "logger": logger,
            "_resolve_agent_definition_for_project": lambda _: None,
        }
        copy = compile_nodes([method], ns)["get_or_create_agent_definition"]
        project = SimpleNamespace(organization=None, workspace=None, id="project")

        # The replay function constructs real model kwargs; this stand-in keeps
        # unrelated FK declarations out of this DB-free test, but uses the real
        # model's encrypted accessors and records its storage at save time.
        class ReplayAgent:
            objects = Mock()
            get_api_key = Agent.get_api_key
            set_api_key = Agent.set_api_key

            def __init__(self, **kwargs):
                self.id = "copied"
                self.encrypted_api_key = None

            def save(self):
                self.saved_ciphertext = self.encrypted_api_key

            def create_version(self, **kwargs):
                pass

        ReplayAgent.objects.filter.return_value.first.return_value = source
        ns["AgentDefinition"] = ReplayAgent
        copied = copy(project, "Copy", "Description", "voice", {"provider": "vapi"})
        self.assertEqual(copied.get_api_key(), "replay-synthetic-marker")
        self.assertNotEqual(copied.saved_ciphertext, source.encrypted_api_key)
        self.assertNotIn("replay-synthetic-marker", copied.saved_ciphertext)
        self.assertNotIn("replay-synthetic-marker", str(logger.mock_calls))

    def test_snapshot_restore_cannot_inject_credentials(self):
        method = next(
            n
            for n in class_node("simulate/models/agent_version.py", "AgentVersion").body
            if isinstance(n, ast.FunctionDef) and n.name == "restore_from_snapshot"
        )
        restore = compile_nodes([method], {})["restore_from_snapshot"]
        agent = SimpleNamespace(
            api_key="untouched",
            encrypted_api_key="untouched-ciphertext",
            livekit_api_key="untouched",
            livekit_api_secret="untouched",
            agent_name="before",
            save=Mock(),
        )
        snapshot = dict.fromkeys(
            ["api_key", "encrypted_api_key", "livekit_api_key", "livekit_api_secret"],
            "attacker-injected",
        )
        snapshot["agent_name"] = "after"
        restore(
            SimpleNamespace(configuration_snapshot=snapshot, agent_definition=agent)
        )
        self.assertEqual(agent.api_key, "untouched")
        self.assertEqual(agent.encrypted_api_key, "untouched-ciphertext")
        self.assertEqual(agent.livekit_api_key, "untouched")
        self.assertEqual(agent.livekit_api_secret, "untouched")
        self.assertEqual(agent.agent_name, "after")
        self.assertEqual(
            agent.save.call_args.kwargs["update_fields"], ["agent_name", "updated_at"]
        )

    def test_runtime_reader_inventory_no_legacy_attribute(self):
        files = [
            "simulate/serializers/agent_definition.py",
            "simulate/serializers/response/agent_definition.py",
            "simulate/services/agent_setup.py",
            "tracer/services/observability_providers.py",
            "tracer/utils/replay_session.py",
        ]
        for relative in files:
            with self.subTest(file=relative):
                nodes = tree(relative)
                offending = [
                    n.lineno
                    for n in ast.walk(nodes)
                    if isinstance(n, ast.Attribute)
                    and n.attr == "api_key"
                    and isinstance(n.value, ast.Name)
                    and n.value.id in {"agent", "obj", "instance", "obs_agent"}
                ]
                self.assertEqual(offending, [])
                self.assertIn("get_api_key()", (ROOT / relative).read_text())
        recording = (ROOT / "tracer/utils/vapi_recording.py").read_text()
        self.assertNotIn('.only("id", "api_key")', recording)
        for relative in [
            "simulate/views/agent_definition.py",
            "simulate/views/agent_version.py",
            "tracer/utils/replay_session.py",
        ]:
            self.assertIn("set_api_key(", (ROOT / relative).read_text())


if __name__ == "__main__":
    unittest.main()
