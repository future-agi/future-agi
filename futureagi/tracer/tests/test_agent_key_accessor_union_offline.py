"""DB-free checks of composed production accessors and replay persistence.

Execute directly with python -I -B. Only production declarations are compiled;
no application settings are imported. ORM and HTTP boundaries are explicit mocks.
"""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, call, patch


ROOT = Path(__file__).resolve().parents[2]


def declarations(relative, names, namespace, owner=None):
    tree = ast.parse((ROOT / relative).read_text())
    body = tree.body
    if owner:
        body = next(n.body for n in body if isinstance(n, ast.ClassDef) and n.name == owner)
    selected = [n for n in body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(selected) == len(names)
    if owner:
        selected = [ast.ClassDef(name=owner, bases=[], keywords=[], body=selected, decorator_list=[])]
    module = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
        type_ignores=[],
    )
    exec(compile(ast.fix_missing_locations(module), str(ROOT / relative), "exec"), namespace)
    return namespace


class AgentKeyAccessorUnionTests(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket", "socket.create_connection"):
            self.enterContext(patch(target, side_effect=AssertionError("network forbidden")))
        self.http = Mock()
        self.agent = NS(get_api_key=Mock(return_value="synthetic-accessor-key"), assistant_id="assistant")
        self.provider = NS(agent_definition=self.agent, id="provider")
        ns = declarations(
            "tracer/services/observability_providers.py",
            {"_get_agent_definition", "_validate_agent_api_key", "_fetch_vapi_logs", "_list_eleven_labs_conversations", "_fetch_twilio_logs"},
            {
                "logger": Mock(),
                "requests": self.http,
                "VAPI_MAX_PAGES": 2,
                "VAPI_PAGE_LIMIT": 100,
                "ObservabilityRoutes": NS(
                    VAPI_CALL_URL=NS(value="https://vapi.invalid/calls"),
                    ELEVEN_LABS_CONVERSATIONS_URL=NS(value="https://eleven.invalid/conversations"),
                ),
            },
            owner="ObservabilityService",
        )
        self.service = ns["ObservabilityService"]

    def test_validator_reads_only_the_accessor(self):
        self.assertEqual(self.service._validate_agent_api_key(self.agent, self.provider, "test"), "synthetic-accessor-key")
        self.agent.get_api_key.assert_called_once_with()
        self.assertFalse(hasattr(self.agent, "api_key"))

    def test_empty_or_missing_key_refuses_without_http(self):
        for key in (None, ""):
            with self.subTest(key=key):
                self.agent.get_api_key.return_value = key
                self.assertIsNone(self.service._validate_agent_api_key(self.agent, self.provider, "test"))
                self.assertEqual(self.service._fetch_vapi_logs(self.provider), [])
        self.assertIsNone(self.service._validate_agent_api_key(None, self.provider, "test"))
        self.http.get.assert_not_called()

    def test_vapi_header_contains_the_accessor_result(self):
        self.http.get.return_value.json.return_value = []
        self.assertEqual(self.service._fetch_vapi_logs(self.provider), [])
        self.http.get.assert_called_once()
        self.assertEqual(self.http.get.call_args.kwargs["headers"]["Authorization"], "Bearer synthetic-accessor-key")
        self.agent.get_api_key.assert_called_once_with()

    def test_elevenlabs_header_contains_the_accessor_result(self):
        self.http.get.return_value.json.return_value = {"conversations": []}
        self.assertEqual(self.service._list_eleven_labs_conversations(self.provider), [])
        self.http.get.assert_called_once()
        self.assertEqual(self.http.get.call_args.kwargs["headers"]["xi-api-key"], "synthetic-accessor-key")
        self.agent.get_api_key.assert_called_once_with()

    def test_twilio_invalid_accessor_result_never_sends(self):
        self.assertEqual(self.service._fetch_twilio_logs(self.provider), [])
        self.agent.get_api_key.assert_called_once_with()
        self.http.get.assert_not_called()

    def test_replay_persists_before_creating_version(self):
        model = Mock()
        project = NS(id="project", organization=object(), workspace=object())
        ns = declarations(
            "tracer/utils/replay_session.py",
            {"get_or_create_agent_definition"},
            {
                "logger": Mock(),
                "AgentDefinition": model,
                "AgentTypeChoices": NS(VOICE="voice"),
                "AgentVersion": NS(StatusChoices=NS(ACTIVE="active")),
                "_resolve_agent_definition_for_project": Mock(return_value=None),
            },
        )
        result = ns["get_or_create_agent_definition"](project, "Agent", "Description", "text")
        self.assertIs(result, model.return_value)
        model.assert_called_once_with(
            agent_name="Agent", description="Description", agent_type="text", inbound=True,
            organization=project.organization, workspace=project.workspace, languages=["en"],
        )
        self.assertEqual(result.mock_calls, [
            call.set_api_key(None), call.save(),
            call.create_version(description="Description", commit_message="Initial version from replay session", status="active"),
        ])
        model.objects.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
