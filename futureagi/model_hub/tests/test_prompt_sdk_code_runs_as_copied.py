"""A prompt's "use in code" examples run the prompt when copied as served.

``get-sdk-code`` writes one example per language around the request body for
``run_template``. ``run_template`` calls the model only for a request that names
a ``version`` and sends ``is_run: "prompt"``, and its ``evaluation_configs``
must be a list, also for a prompt without eval configs. The prompt name is a
string literal in every example, so a double quote or a backslash in it has to
be escaped, and the Go example has to be Go: one brace per block and Go
composite literals, not JSON, inside ``map[string]interface{}``.
"""

import ast
import json
import re
import shlex

import pytest

from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.serializers.prompt_requests import PromptRunRequestSerializer
from model_hub.tests.prompt_sdk_code_helpers import go_literal_after, json_after

PLAIN_NAME = "Weather prompt"
QUOTED_NAME = 'Say "hi" to O\'Brien from C:\\temp\\'
PROMPT_CONFIG = {
    "messages": [
        {"role": "system", "content": [{"type": "text", "text": "Be concise."}]},
        {"role": "user", "content": [{"type": "text", "text": "Hello {{name}}"}]},
    ],
    "configuration": {
        "model": "gpt-4o-mini",
        "model_detail": {"type": "chat"},
        "template_format": "mustache",
    },
}
VARIABLE_NAMES = {"name": ["Ada"]}
LANGUAGES = ["python", "typescript", "curl", "langchain", "nodejs", "go"]
POSTING_LANGUAGES = ["python", "typescript", "curl", "nodejs", "go"]
BODY_KEYS = [
    "name",
    "version",
    "prompt_config",
    "variable_names",
    "evaluation_configs",
    "is_run",
]


def _expected_body(name):
    return {
        "name": name,
        "version": "v1",
        "prompt_config": [PROMPT_CONFIG],
        "variable_names": VARIABLE_NAMES,
        "evaluation_configs": [],
        "is_run": "prompt",
    }


def _create_template(organization, workspace, user, name):
    """A prompt with one version and no eval configs."""
    template = PromptTemplate.no_workspace_objects.create(
        name=name,
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
        is_draft=True,
    )
    return template


@pytest.fixture
def plain_template(organization, workspace, user):
    return _create_template(organization, workspace, user, PLAIN_NAME)


@pytest.fixture
def quoted_template(organization, workspace, user):
    return _create_template(organization, workspace, user, QUOTED_NAME)


def _sdk_code(auth_client, template, language):
    response = auth_client.get(
        f"/model-hub/prompt-templates/{template.id}/get-sdk-code/{language}/"
    )
    assert response.status_code == 200, response.content
    return response.json()["result"][language]


def _body(code, language):
    """The request body as the example writes it, read as that language would."""
    if language in ("python", "langchain"):
        data = next(
            node.value
            for node in ast.parse(code).body
            if isinstance(node, ast.Assign) and node.targets[0].id == "data"
        )
        return ast.literal_eval(data)
    if language == "typescript":
        return json_after(code, "const data = ")
    if language == "curl":
        args = shlex.split(code.replace("\\\n", " "))
        return json.loads(args[args.index("--data") + 1])
    if language == "nodejs":
        return {key: json_after(code, f"{key}: ") for key in BODY_KEYS}
    return go_literal_after(code, "data := ")


@pytest.mark.django_db
@pytest.mark.parametrize("language", LANGUAGES)
def test_example_body_is_a_run_request(auth_client, plain_template, language):
    body = _body(_sdk_code(auth_client, plain_template, language), language)

    assert body == _expected_body(PLAIN_NAME)
    serializer = PromptRunRequestSerializer(data=body)
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
@pytest.mark.parametrize("language", POSTING_LANGUAGES)
def test_example_body_runs_the_prompt(
    auth_client, plain_template, language, monkeypatch
):
    submitted = {}

    def fake_submit_with_retry(_executor, _func, *args, **_kwargs):
        submitted["args"] = args

    monkeypatch.setattr(
        "model_hub.views.prompt_template.submit_with_retry",
        fake_submit_with_retry,
    )
    body = _body(_sdk_code(auth_client, plain_template, language), language)

    response = auth_client.post(
        f"/model-hub/prompt-templates/{plain_template.id}/run_template/",
        body,
        format="json",
    )

    assert response.status_code == 200, response.content
    assert submitted["args"][3] == "v1"
    assert submitted["args"][4] == "prompt"


@pytest.mark.django_db
@pytest.mark.parametrize("language", LANGUAGES)
def test_prompt_name_is_one_string_literal(auth_client, quoted_template, language):
    body = _body(_sdk_code(auth_client, quoted_template, language), language)

    assert body["name"] == QUOTED_NAME


@pytest.mark.django_db
def test_go_example_opens_each_block_with_one_brace(auth_client, plain_template):
    code = _sdk_code(auth_client, plain_template, "go")

    assert "func main() {\n" in code
    assert "data := map[string]interface{}{\n" in code
    assert not re.search(r"\{\{\s*$", code, re.MULTILINE)
    assert not re.search(r"^\s*\}\}\s*$", code, re.MULTILINE)
