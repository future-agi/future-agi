"""Each "use in code" example for a prompt writes its values in that language.

``get-sdk-code`` fills the prompt's config, variables and eval configs into one
example per language. The Python and LangChain examples read Python literals.
The TypeScript, Node.js and cURL examples read JSON: a Python ``True``,
``None`` or single-quoted string there stops TypeScript from compiling and
makes the cURL body invalid JSON. The cURL body is also single-quoted for the
shell, so an apostrophe in a prompt must not end it. The Go example reads Go
composite literals.
"""

import ast
import json
import re
import shlex

import pytest

from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.tests.prompt_sdk_code_helpers import go_literal_after

PROMPT_NAME = "Weather prompt"
PROMPT_CONFIG = {
    "messages": [
        {
            "role": "system",
            "content": [{"type": "text", "text": 'Answer in "plain" words.'}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "Don't guess the weather in {{city}}"}],
        },
    ],
    "configuration": {
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "tools": [],
        "response_format": None,
        "stream": False,
    },
}
VARIABLE_NAMES = {"city": ["Paris", "O'Hare"]}
EVALUATION_CONFIGS = [{"name": "tone", "config": {"enabled": True, "threshold": None}}]
EXPECTED_BODY = {
    "name": PROMPT_NAME,
    "version": "v1",
    "prompt_config": [PROMPT_CONFIG],
    "variable_names": VARIABLE_NAMES,
    "evaluation_configs": EVALUATION_CONFIGS,
    "is_run": "prompt",
}
PYTHON_LITERAL = re.compile(r"\b(True|False|None)\b")


@pytest.fixture
def template(organization, workspace, user):
    template = PromptTemplate.no_workspace_objects.create(
        name=PROMPT_NAME,
        organization=organization,
        workspace=workspace,
        created_by=user,
        variable_names=VARIABLE_NAMES,
    )
    PromptVersion.no_workspace_objects.create(
        original_template=template,
        template_version="v1",
        prompt_config_snapshot=PROMPT_CONFIG,
        variable_names=VARIABLE_NAMES,
        evaluation_configs=EVALUATION_CONFIGS,
        is_draft=True,
    )
    return template


def _sdk_code(auth_client, template, language):
    response = auth_client.get(
        f"/model-hub/prompt-templates/{template.id}/get-sdk-code/{language}/"
    )
    assert response.status_code == 200, response.content
    return response.json()["result"][language]


def _json_after(code, marker):
    """The JSON value that starts right after ``marker`` in ``code``."""
    value, _ = json.JSONDecoder().raw_decode(code, code.index(marker) + len(marker))
    return value


@pytest.mark.django_db
def test_typescript_data_is_a_json_object(auth_client, template):
    code = _sdk_code(auth_client, template, "typescript")

    assert _json_after(code, "const data = ") == EXPECTED_BODY
    assert not PYTHON_LITERAL.search(code)


@pytest.mark.django_db
def test_curl_body_is_json_after_shell_quoting(auth_client, template):
    code = _sdk_code(auth_client, template, "curl")

    args = shlex.split(code.replace("\\\n", " "))
    body = args[args.index("--data") + 1]
    assert json.loads(body) == EXPECTED_BODY


@pytest.mark.django_db
def test_nodejs_values_are_json(auth_client, template):
    code = _sdk_code(auth_client, template, "nodejs")

    assert _json_after(code, "prompt_config: ") == [PROMPT_CONFIG]
    assert _json_after(code, "variable_names: ") == VARIABLE_NAMES
    assert _json_after(code, "evaluation_configs: ") == EVALUATION_CONFIGS
    assert _json_after(code, "is_run: ") == "prompt"
    assert not PYTHON_LITERAL.search(code)


@pytest.mark.django_db
def test_go_values_are_go_literals(auth_client, template):
    code = _sdk_code(auth_client, template, "go")

    assert go_literal_after(code, "data := ") == EXPECTED_BODY
    assert not PYTHON_LITERAL.search(code)


@pytest.mark.django_db
@pytest.mark.parametrize("language", ["python", "langchain"])
def test_python_examples_keep_python_literals(auth_client, template, language):
    code = _sdk_code(auth_client, template, language)

    data = next(
        node.value
        for node in ast.parse(code).body
        if isinstance(node, ast.Assign) and node.targets[0].id == "data"
    )
    assert ast.literal_eval(data) == EXPECTED_BODY
    assert "'enabled': True, 'threshold': None" in code
