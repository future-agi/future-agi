# ruff: noqa: E402
"""DB-free production-method tests; run directly with python -I this_file.

Like the hosted-child offline suites, compile selected production declarations
without importing application settings, .env, workers or provider SDKs. Django
fields/DRF's PATCH response and Fernet are real; persistence/locks are mocked.
PostgreSQL transaction evidence lives in test_agent_api_key_migration.py.
"""

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from django.conf import settings

if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[],
        INTEGRATION_ENCRYPTION_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
        SECRET_KEY="offline-test-only",
    )
    import django

    django.setup()

from django.db import models
from rest_framework import serializers, viewsets
from rest_framework.test import APIRequestFactory

from agentcc.services.credential_manager import mask_key
from integrations.services.credentials import CredentialManager


def tree(relative):
    return ast.parse((ROOT / relative).read_text())


def class_node(relative, name):
    return next(
        n for n in tree(relative).body if isinstance(n, ast.ClassDef) and n.name == name
    )


def compile_nodes(nodes, namespace):
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])),
            "<production declarations>",
            "exec",
        ),
        namespace,
    )
    return namespace


def model_class():
    source = class_node("simulate/models/agent_definition.py", "AgentDefinition")
    fields = {
        "id",
        "agent_name",
        "contact_number",
        "agent_type",
        "inbound",
        "description",
        "provider",
        "api_key",
        "encrypted_api_key",
    }
    body = [
        n
        for n in source.body
        if (
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in fields for t in n.targets)
        )
        or (
            isinstance(n, ast.FunctionDef)
            and n.name in {"__init__", "set_api_key", "get_api_key", "__str__"}
        )
    ]
    body += ast.parse('class Meta:\n    app_label = "at_rest_offline"').body
    import uuid

    ns = compile_nodes(
        [
            ast.ClassDef(
                name="AgentDefinition",
                bases=[
                    ast.Attribute(
                        value=ast.Name(id="models", ctx=ast.Load()),
                        attr="Model",
                        ctx=ast.Load(),
                    )
                ],
                keywords=[],
                body=body,
                decorator_list=[],
            )
        ],
        {
            "models": models,
            "uuid": uuid,
            "AgentTypeChoices": SimpleNamespace(
                choices=[("text", "Text")], VOICE="voice"
            ),
        },
    )
    return ns["AgentDefinition"]


Agent = model_class()
Agent.active_version = None
Agent.latest_version = None


def generic_serializer():
    source = class_node(
        "simulate/serializers/agent_definition.py", "AgentDefinitionSerializer"
    )
    # Keep input, representation, update and validation code; remove unrelated
    # LiveKit validators/relations and replace only Meta's model/field inventory.
    body = []
    for node in source.body:
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []  # no DB transaction in this offline test
            body.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "api_key" for t in node.targets
        ):
            body.append(node)
    body += ast.parse(
        'class Meta:\n    model = Agent\n    fields = ["agent_name", "agent_type", "inbound", "description", "provider", "api_key"]'
    ).body
    helper = next(
        n
        for n in tree("simulate/serializers/agent_definition.py").body
        if isinstance(n, ast.FunctionDef) and n.name == "_extract_credentials_input"
    )
    ns = compile_nodes(
        [
            helper,
            ast.ClassDef(
                name="AgentDefinitionSerializer",
                bases=[
                    ast.Attribute(
                        value=ast.Name(id="serializers", ctx=ast.Load()),
                        attr="ModelSerializer",
                        ctx=ast.Load(),
                    )
                ],
                keywords=[],
                body=body,
                decorator_list=[],
            ),
        ],
        {
            "serializers": serializers,
            "Agent": Agent,
            "AgentDefinition": Agent,
            "AgentVersion": SimpleNamespace(),
            "ProviderCredentialsInput": lambda **kw: SimpleNamespace(**kw),
            "is_masked": lambda v: "..." in v or v == "****",
            "sync_provider_credentials": Mock(),
            "mask_key": mask_key,
            "observability_is_on": lambda _: False,
            "AgentTypeChoices": SimpleNamespace(VOICE="voice"),
        },
    )
    return ns["AgentDefinitionSerializer"]


__all__ = [
    "ROOT",
    "Agent",
    "CredentialManager",
    "APIRequestFactory",
    "settings",
    "models",
    "viewsets",
    "mask_key",
    "tree",
    "class_node",
    "compile_nodes",
    "generic_serializer",
]
