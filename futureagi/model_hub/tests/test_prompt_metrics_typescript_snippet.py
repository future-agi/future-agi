"""The TypeScript tab of the prompt Metrics empty screen runs as copied.

A prompt with no linked traces shows a Python and a TypeScript example that
send one traced OpenAI call with its prompt template. The TypeScript example
must install from npm (the OpenAI instrumentation is published as
``@traceai/openai``; ``@traceai/fi-openai`` does not exist) and, like the
Python example, register a tracer provider, instrument OpenAI and define every
name it uses.
"""

import re

import pytest

from model_hub.views.prompt_metrics import FetchPromptMetricsNullView

PUBLISHED_PACKAGES = {
    "@opentelemetry/api",
    "@traceai/fi-core",
    "@traceai/openai",
    "openai",
}
PROMPT_TEMPLATE = "Please describe the weather forecast for {city} on {date}"


@pytest.fixture
def snippets():
    return FetchPromptMetricsNullView().get(None).data["result"]


@pytest.fixture
def typescript(snippets):
    return snippets["typescript"]


def test_typescript_imports_only_published_packages(typescript):
    imported = set(re.findall(r'^import .+ from "([^"]+)";$', typescript, re.M))

    assert imported == PUBLISHED_PACKAGES
    assert "@traceai/fi-openai" not in typescript


def test_typescript_defines_every_name_it_uses(typescript):
    for name in ("client", "promptTemplate", "promptTemplateVariables"):
        assert re.search(rf"^const {name} = ", typescript, re.M), name


def test_typescript_registers_and_instruments_openai(typescript):
    assert "const tracerProvider = register({" in typescript
    assert "projectType: ProjectType.EXPERIMENT," in typescript
    assert "new OpenAIInstrumentation(" in typescript
    assert ".setTracerProvider(tracerProvider);" in typescript
    assert ".manuallyInstrument(OpenAI);" in typescript
    assert "setPromptTemplate(context.active(), {" in typescript


def test_typescript_sets_the_same_project_and_keys_as_python(snippets):
    python, typescript = snippets["python"], snippets["typescript"]

    for setting, value in (
        ("project_name", "Project_name"),
        ("project_version_name", "project_version_name"),
    ):
        assert f'{setting}="{value}"' in python
    assert 'projectName: "Project_name",' in typescript
    assert 'projectVersionName: "project_version_name",' in typescript
    for key, value in (
        ("OPENAI_API_KEY", "your-openai-api-key"),
        ("FI_API_KEY", "your-futureagi-api-key"),
        ("FI_SECRET_KEY", "your-futureagi-secret-key"),
    ):
        assert f'os.environ["{key}"] = "{value}"' in python
        assert f'process.env.{key} = "{value}";' in typescript


def test_typescript_sends_the_same_prompt_template_as_python(snippets):
    assert PROMPT_TEMPLATE in snippets["python"]
    assert f'const promptTemplate = "{PROMPT_TEMPLATE}";' in snippets["typescript"]


def test_typescript_awaits_only_inside_an_async_function(typescript):
    # Top-level await fails when the file runs as CommonJS, which is what a
    # plain `npm init -y` project gives.
    assert "async function main() {" in typescript
    assert re.search(r"^main\(\);$", typescript, re.M)
    for line in typescript.splitlines():
        if "await " in line:
            assert line.startswith(" "), line
