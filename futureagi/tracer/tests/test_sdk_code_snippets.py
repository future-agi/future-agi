"""The in-app SDK setup snippets must run against the SDK users install.

`pip install fi-instrumentation-otel` / `npm install @traceai/fi-core` users copy
these snippets verbatim, so a keyword the SDK does not accept (the Observe
snippet used to pass `session_name=`, a TypeError on fi-instrumentation-otel
1.x) or a name the snippet never imports breaks the very first step of setup.
"""

import ast
import builtins
import inspect
import re

import pytest
from fi_instrumentation import register as sdk_register
from rest_framework import status

from tracer.utils.constants import (
    INSTRUMENTORS,
    OBSERVE_CODEBLOCK,
    ORG_KEYS,
    PROTOTYPE_CODEBLOCK,
)

# RegisterOptions from @traceai/fi-core 1.0.0 (dist/src/otel.d.ts).
TS_REGISTER_OPTIONS = {
    "projectName",
    "projectType",
    "projectVersionName",
    "evalTags",
    "sessionName",
    "metadata",
    "batch",
    "setGlobalTracerProvider",
    "headers",
    "verbose",
    "endpoint",
    "idGenerator",
    "transport",
}

SETUP_CODEBLOCKS = {
    "experiment": PROTOTYPE_CODEBLOCK,
    "observe": OBSERVE_CODEBLOCK,
}


def _undefined_names(code):
    tree = ast.parse(code)
    defined = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            defined.update(
                (alias.asname or alias.name).split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Load)
        and node.id not in defined
    }


def _register_kwargs(code):
    return [
        keyword.arg
        for node in ast.walk(ast.parse(code))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register"
        for keyword in node.keywords
    ]


def assert_python_snippet_runs_against_sdk(code):
    assert _undefined_names(code) == set()
    kwargs = _register_kwargs(code)
    assert kwargs, "snippet should call register()"
    accepted = set(inspect.signature(sdk_register).parameters)
    assert set(kwargs) <= accepted, set(kwargs) - accepted


@pytest.mark.unit
@pytest.mark.parametrize("project_type", sorted(SETUP_CODEBLOCKS))
@pytest.mark.parametrize("instrumentor", sorted(INSTRUMENTORS))
def test_python_setup_and_instrumentor_snippets_run_against_sdk(
    project_type, instrumentor
):
    python_code = INSTRUMENTORS[instrumentor].get("Python")
    if python_code is None:
        pytest.skip(f"{instrumentor} has no Python snippet")

    # The Add Project page shows keys -> setup -> instrumentor, in that order.
    code = "\n".join(
        [
            ORG_KEYS["Python"].format("key", "secret"),
            SETUP_CODEBLOCKS[project_type]["Python"],
            python_code["code"],
        ]
    )
    assert_python_snippet_runs_against_sdk(code)


@pytest.mark.unit
@pytest.mark.parametrize("project_type", sorted(SETUP_CODEBLOCKS))
def test_typescript_setup_snippets_use_fi_core_register_options(project_type):
    code = SETUP_CODEBLOCKS[project_type]["TypeScript"]
    options = re.search(r"register\(\{(.*?)\}\)", code, re.S)
    assert options, "snippet should call register({...})"
    keys = set(re.findall(r"^\s*(\w+)\s*:", options.group(1), re.M))
    assert keys and keys <= TS_REGISTER_OPTIONS, keys - TS_REGISTER_OPTIONS


@pytest.mark.django_db
def test_user_code_example_runs_against_sdk(auth_client):
    response = auth_client.get("/tracer/users/get_code_example/")

    assert response.status_code == status.HTTP_200_OK
    assert_python_snippet_runs_against_sdk(response.json()["result"])


@pytest.mark.django_db
def test_prompt_metrics_empty_screen_python_snippet_runs_against_sdk(auth_client):
    response = auth_client.get("/model-hub/prompt/metrics/empty-screen")

    assert response.status_code == status.HTTP_200_OK
    assert_python_snippet_runs_against_sdk(response.json()["result"]["python"])
