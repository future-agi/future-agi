"""The knowledge base "Import from SDK" examples keep the KB name one string literal.

``GET /model-hub/knowledge-base/`` writes the knowledge base name into
``create_kb(name=...)`` for a new knowledge base and ``update_kb(kb_name=...)``
for an existing one. A double quote or a backslash in the name has to be
escaped there, or the Python example does not parse.
"""

import ast
from types import SimpleNamespace

import pytest

from model_hub.views.develop_dataset import CreateKnowledgeBaseView

QUOTED_NAME = 'Tom\'s "FAQ" from C:\\docs\\'
PLAIN_NAME = "Support articles"
SDK_CALL = {"create": "create_kb", "update": "update_kb"}
NAME_KEYWORD = {"create": "name", "update": "kb_name"}


def _code(kb_type, name):
    request = SimpleNamespace(query_params={"type": kb_type, "name": name})
    return CreateKnowledgeBaseView().get(request).data["result"]["code"]


def _name_argument(code, kb_type):
    (call,) = [
        node
        for node in ast.walk(ast.parse(code))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == SDK_CALL[kb_type]
    ]
    (keyword,) = [kw for kw in call.keywords if kw.arg == NAME_KEYWORD[kb_type]]
    return ast.literal_eval(keyword.value)


@pytest.mark.parametrize("kb_type", ["create", "update"])
def test_name_with_quotes_and_backslashes_stays_one_string(kb_type):
    code = _code(kb_type, QUOTED_NAME)

    assert _name_argument(code, kb_type) == QUOTED_NAME


@pytest.mark.parametrize("kb_type", ["create", "update"])
def test_plain_name_is_written_as_before(kb_type):
    code = _code(kb_type, PLAIN_NAME)

    assert f'{NAME_KEYWORD[kb_type]}="{PLAIN_NAME}"' in code
    assert _name_argument(code, kb_type) == PLAIN_NAME
