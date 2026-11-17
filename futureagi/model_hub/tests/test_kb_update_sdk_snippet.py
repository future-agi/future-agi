"""The "Import from SDK" example for an existing knowledge base calls the SDK as published.

``GET /model-hub/knowledge-base/?type=update`` returns the Python example the
Add files drawer shows. In futureagi 0.7.0 the call is
``KnowledgeBase.update_kb(kb_name, new_name=None, file_paths=None)`` and
``KnowledgeBase(kb_name=None, fi_api_key=None, fi_secret_key=None,
fi_base_url=None, **kwargs)``; ``update_kb(name=...)`` raises TypeError, and a
``kbase=`` argument is silently dropped by ``**kwargs``.
"""

import ast
from types import SimpleNamespace

import pytest

from model_hub.constants import SDK_API_KEY_PLACEHOLDER, SDK_SECRET_KEY_PLACEHOLDER
from model_hub.views.develop_dataset import CreateKnowledgeBaseView

KB_NAME = "Support articles"
# Keyword parameters in futureagi 0.7.0 (fi/kb/client.py).
UPDATE_KB_PARAMETERS = {"kb_name", "new_name", "file_paths"}
KNOWLEDGE_BASE_PARAMETERS = {"kb_name", "fi_api_key", "fi_secret_key", "fi_base_url"}


@pytest.fixture
def code():
    request = SimpleNamespace(query_params={"type": "update", "name": KB_NAME})
    return CreateKnowledgeBaseView().get(request).data["result"]["code"]


def _calls(code, func_name):
    calls = []
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id
            if name == func_name:
                calls.append(node)
    return calls


def _keywords(call, accepted):
    names = {kw.arg for kw in call.keywords}
    assert names <= accepted, names - accepted
    return {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}


def test_update_kb_names_the_knowledge_base_with_kb_name(code):
    (call,) = _calls(code, "update_kb")
    keywords = _keywords(call, UPDATE_KB_PARAMETERS)

    assert not call.args
    assert keywords["kb_name"] == KB_NAME
    assert keywords["file_paths"]


def test_client_is_built_with_arguments_the_sdk_accepts(code):
    (call,) = _calls(code, "KnowledgeBase")
    keywords = _keywords(call, KNOWLEDGE_BASE_PARAMETERS)

    assert keywords["fi_api_key"] == SDK_API_KEY_PLACEHOLDER
    assert keywords["fi_secret_key"] == SDK_SECRET_KEY_PLACEHOLDER


def test_example_imports_only_the_client(code):
    imports = [
        node for node in ast.walk(ast.parse(code)) if isinstance(node, ast.ImportFrom)
    ]

    assert [(node.module, [alias.name for alias in node.names]) for node in imports] == [
        ("fi.kb.client", ["KnowledgeBase"])
    ]
