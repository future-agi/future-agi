"""Bounded validation regressions; run with python -I (no DB or app settings)."""

# ruff: noqa: E402
import ast
import re
import socket
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from rest_framework import serializers

from simulate.tests._agent_api_key_offline import (
    Agent,
    class_node,
    compile_nodes,
    settings,
)


def validation_method():
    return next(
        node
        for node in class_node(
            "simulate/serializers/agent_definition.py", "AgentDefinitionSerializer"
        ).body
        if isinstance(node, ast.FunctionDef) and node.name == "validate"
    )


def request_serializer(name):
    filename = (
        "agent_version" if name.startswith("AgentVersion") else "agent_definition"
    )
    node = class_node(f"simulate/serializers/requests/{filename}.py", name)
    return compile_nodes(
        [node],
        {
            "serializers": serializers,
            "re": re,
            "AgentTypeChoices": SimpleNamespace(
                choices=[("voice", "Voice"), ("text", "Text")], VOICE="voice"
            ),
            "AgentDefinitionAuthenticationChoices": SimpleNamespace(
                choices=[("api_key", "API key")]
            ),
            "DEFAULT_ORG_LIMIT": 100,
        },
    )[name]


REQUEST_CLASSES = (
    "AgentDefinitionCreateRequestSerializer",
    "AgentDefinitionEditRequestSerializer",
    "AgentVersionCreateRequestSerializer",
)


class AgentApiKeyReentryTests(unittest.TestCase):
    def setUp(self):
        for target in ("connect", "connect_ex"):
            guard = patch.object(
                socket.socket, target, side_effect=AssertionError("network forbidden")
            )
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch(
            "socket.create_connection", side_effect=AssertionError("network forbidden")
        )
        guard.start()
        self.addCleanup(guard.stop)
        settings.INTEGRATION_ENCRYPTION_KEY = (
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        )
        self.agent = Agent(
            agent_name="Synthetic reentry",
            agent_type="voice",
            provider="vapi",
            inbound=True,
            encrypted_api_key="enc::invalid",
        )
        self.agent.contact_number = "+12345678901"
        self.agent.assistant_id = "synthetic-assistant"
        self.agent.authentication_method = "api_key"
        self.agent.get_api_key = Mock(wraps=self.agent.get_api_key)
        self.serializer = SimpleNamespace(instance=self.agent, initial_data={})

    def resolve_at_site(self, index, attrs):
        # Exercise each actual production assignment independently: an earlier
        # voice branch must not hide a regression at the outbound/observability site.
        assignments = sorted(
            (
                node
                for node in ast.walk(validation_method())
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "api_key" for t in node.targets
                )
            ),
            key=lambda node: node.lineno,
        )
        self.assertEqual(len(assignments), 3)
        namespace = {"self": self.serializer, "attrs": attrs}
        return compile_nodes([assignments[index]], namespace)["api_key"]

    def validate(self, attrs):
        validate = compile_nodes(
            [validation_method()],
            {
                "serializers": serializers,
                "re": re,
                "AgentTypeChoices": SimpleNamespace(VOICE="voice"),
                "observability_is_on": lambda _: False,
            },
        )["validate"]
        return validate(self.serializer, attrs)

    def assert_replacement(self, index):
        attrs = {"api_key": "synthetic-replacement"}
        self.assertEqual(self.resolve_at_site(index, attrs), attrs["api_key"])
        self.assertIs(self.validate(attrs), attrs)
        self.agent.get_api_key.assert_not_called()
        # The accepted replacement can actually repair the corrupt stored key.
        self.agent.set_api_key(attrs["api_key"])
        self.assertEqual(self.agent.get_api_key(), attrs["api_key"])

    def test_corrupt_ciphertext_replacement_web_bridge(self):
        self.agent.contact_number = None
        self.assert_replacement(0)

    def test_corrupt_ciphertext_replacement_outbound(self):
        self.agent.inbound = False
        self.assert_replacement(1)

    def test_corrupt_ciphertext_replacement_observability(self):
        self.agent.agent_type = "text"
        self.serializer.initial_data = {"observability_enabled": True}
        self.assert_replacement(2)

    def test_explicit_null_and_empty_do_not_read_old_key_at_any_site(self):
        for index in range(3):
            for value in (None, ""):
                with self.subTest(site=index, value=value):
                    self.assertEqual(
                        self.resolve_at_site(index, {"api_key": value}), value
                    )
        self.agent.get_api_key.assert_not_called()

    def test_absent_input_requires_fail_closed_read_at_each_site(self):
        for index in range(3):
            with self.subTest(site=index):
                self.agent.get_api_key.reset_mock()
                with self.assertRaisesRegex(
                    ValueError, "Agent credential storage is unavailable"
                ):
                    self.resolve_at_site(index, {})
                self.agent.get_api_key.assert_called_once_with()
        with self.assertRaisesRegex(
            ValueError, "Agent credential storage is unavailable"
        ):
            self.validate({})

    def test_absent_input_reads_valid_key_and_creation_has_no_instance(self):
        self.agent.set_api_key("synthetic-existing")
        for index in range(3):
            with self.subTest(site=index):
                self.agent.get_api_key.reset_mock()
                self.assertEqual(self.resolve_at_site(index, {}), "synthetic-existing")
                self.agent.get_api_key.assert_called_once_with()
        self.serializer.instance = None
        for index in range(3):
            self.assertIsNone(self.resolve_at_site(index, {}))
            self.assertEqual(self.resolve_at_site(index, {"api_key": "new"}), "new")

    def test_null_and_empty_keep_existing_requiredness_semantics(self):
        for value in (None, ""):
            # A phone-backed inbound target does not require an API key.
            self.agent.inbound = True
            self.assertEqual(self.validate({"api_key": value}), {"api_key": value})
            self.agent.inbound = False
            with self.assertRaises(serializers.ValidationError) as error:
                self.validate({"api_key": value})
            self.assertEqual(set(error.exception.detail), {"api_key"})
            self.agent.inbound = True
            self.serializer.initial_data = {"observability_enabled": True}
            with self.assertRaises(serializers.ValidationError) as error:
                self.validate({"api_key": value})
            self.assertEqual(set(error.exception.detail), {"api_key"})
            self.serializer.initial_data = {}
            self.agent.contact_number = None
            with self.assertRaises(serializers.ValidationError) as error:
                self.validate({"api_key": value})
            self.assertEqual(set(error.exception.detail), {"contact_number"})
            self.agent.contact_number = "+12345678901"
        self.agent.get_api_key.assert_not_called()

    def request(self, name, **extra):
        data = {
            "agent_name": "Synthetic LiveKit target",
            "agent_type": "voice",
            "provider": "livekit",
            "commit_message": "Synthetic change",
            "livekit_url": "wss://example.invalid",
            "livekit_api_secret": "synthetic-secret",
            **extra,
        }
        return request_serializer(name)(data=data)

    def test_livekit_create_accepts_key_longer_than_255(self):
        serializer = self.request(REQUEST_CLASSES[0], livekit_api_key="K" * 256)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["livekit_api_key"], "K" * 256)

    def test_livekit_create_and_edit_agree_on_long_keys(self):
        for name in REQUEST_CLASSES[:2]:
            with self.subTest(serializer=name):
                serializer = self.request(name, livekit_api_key="K" * 1024)
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(
                    serializer.validated_data["livekit_api_key"], "K" * 1024
                )

    def test_create_edit_version_keep_legacy_api_key_limit(self):
        for name in REQUEST_CLASSES:
            with self.subTest(serializer=name):
                serializer = self.request(name, api_key="K" * 256)
                self.assertFalse(serializer.is_valid())
                self.assertEqual(set(serializer.errors), {"api_key"})
                serializer = self.request(
                    name, api_key="K" * 255, livekit_api_key="L" * 255
                )
                self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_version_livekit_limit_is_preexisting_and_outside_this_fix(self):
        # Unlike create/edit, version already had this bound at 6a1093b3cc.
        # Keep that historical contract; do not silently expand the two-item fix.
        serializer = self.request(REQUEST_CLASSES[2], livekit_api_key="K" * 256)
        self.assertFalse(serializer.is_valid())
        self.assertEqual(set(serializer.errors), {"livekit_api_key"})


if __name__ == "__main__":
    unittest.main()
