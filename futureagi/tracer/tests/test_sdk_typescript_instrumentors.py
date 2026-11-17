"""The TypeScript tab of the trace setup guide offers the published traceAI packages.

/tracer/project/project_sdk_code/ returns INSTRUMENTORS, and the Add Project
page (ObserveInstuments.jsx) draws one card per entry, with a TypeScript tab
whenever the entry carries a "TypeScript" block. Only OpenAI and Anthropic had
one, so a Node.js user saw 2 TypeScript integrations against 25 Python ones,
although traceAI publishes @traceai packages for most of the same frameworks.

Each TypeScript snippet follows the guide's setup step, which already ran
`const tracerProvider = register({...})` from @traceai/fi-core, so a snippet
attaches its instrumentation to that provider and never creates another one.
"""

import re

import pytest

from tracer.utils.constants import (
    INSTRUMENTORS,
    OBSERVE_CODEBLOCK,
    PROTOTYPE_CODEBLOCK,
)

TRACEAI_TYPESCRIPT_TREE = (
    "https://github.com/future-agi/traceAI/tree/main/typescript/packages/"
)

# INSTRUMENTORS key -> (npm package, traceAI source directory).
TYPESCRIPT_PACKAGES = {
    "bedrock": ("@traceai/bedrock", "traceai_bedrock"),
    "google_genai": ("@traceai/google-genai-sdk", "traceai_google_genai_sdk"),
    "groq": ("@traceai/groq", "traceai_groq"),
    "guardrails": ("@traceai/guardrails", "traceai_guardrails"),
    "instructor": ("@traceai/instructor", "traceai_instructor"),
    "lang_graph": ("@traceai/langchain", "traceai_langchain"),
    "langchain": ("@traceai/langchain", "traceai_langchain"),
    "mcp": ("@traceai/mcp", "traceai_mcp"),
    "mistral_ai": ("@traceai/mistral", "traceai_mistral"),
    "ollama": ("@traceai/ollama", "traceai_ollama"),
    "openai_agents": ("@traceai/openai-agents", "traceai_openai_agents"),
    "portkey": ("@traceai/portkey", "traceai_portkey"),
    "vercel": ("@traceai/vercel", "traceai_vercel"),
    "vertex_ai": ("@traceai/fi-instrumentation-vertexai", "traceai_vertexai"),
}

# @traceai packages whose npm registry entry and traceAI README were checked
# (registry.npmjs.org, 2026-11-11). A snippet naming any other @traceai
# package must be checked the same way before it is added here.
VERIFIED_TRACEAI_PACKAGES = frozenset(
    {
        "@traceai/anthropic",
        "@traceai/bedrock",
        "@traceai/fi-core",
        "@traceai/fi-instrumentation-vertexai",
        "@traceai/google-genai-sdk",
        "@traceai/groq",
        "@traceai/guardrails",
        "@traceai/instructor",
        "@traceai/langchain",
        "@traceai/mcp",
        "@traceai/mistral",
        "@traceai/ollama",
        "@traceai/openai",
        "@traceai/openai-agents",
        "@traceai/portkey",
        "@traceai/vercel",
    }
)

# Published, but their own READMEs say not to start new applications on them:
# LlamaIndex.TS is deprecated upstream, and @traceai/google-genai only covers
# @google/generative-ai, which Google no longer supports.
RETIRED_TRACEAI_PACKAGES = frozenset({"@traceai/llamaindex", "@traceai/google-genai"})

# MCP's README attaches to the global provider, which register() installs.
USES_GLOBAL_PROVIDER = {"mcp"}

NPM_INSTALL = re.compile(r"^\s*(?://\s*)?npm install\s+(.+)$", re.M)
TS_IMPORT = re.compile(
    r"""^\s*import\s+(?:[^"';]*?\s+from\s+)?["']([^"']+)["']""", re.M
)
TRACEAI_NAME = re.compile(r"@traceai/[a-z0-9][a-z0-9._-]*")
SECOND_PROVIDER = re.compile(
    r"\bregister\(|\bNodeTracerProvider\b|\bFITracerProvider\b"
)
SETUP_PROVIDER = re.compile(r"^const tracerProvider = register\(\{", re.M)

TYPESCRIPT_SNIPPETS = sorted(
    key for key, entry in INSTRUMENTORS.items() if "TypeScript" in entry
)


def _npm_name(spec):
    scope, _, rest = spec.partition("/") if spec.startswith("@") else ("", "", spec)
    name = rest.split("/")[0].split("@")[0]
    return f"{scope}/{name}" if scope else name


def _install_names(code):
    return {
        _npm_name(arg)
        for match in NPM_INSTALL.finditer(code)
        for arg in match.group(1).split()
        if not arg.startswith("-")
    }


def _import_names(code):
    return {_npm_name(spec) for spec in TS_IMPORT.findall(code)}


def _first_line(code):
    return next(line.strip() for line in code.splitlines() if line.strip())


@pytest.mark.unit
@pytest.mark.parametrize("instrumentor", sorted(TYPESCRIPT_PACKAGES))
def test_instrumentor_offers_its_published_typescript_package(instrumentor):
    package, directory = TYPESCRIPT_PACKAGES[instrumentor]
    typescript = INSTRUMENTORS.get(instrumentor, {}).get("TypeScript")

    assert isinstance(typescript, dict), f"{instrumentor} has no TypeScript snippet"
    code = typescript["code"]
    install_line = _first_line(code)
    assert install_line.startswith(f"// npm install {package} "), install_line
    assert package in _install_names(code)
    assert package in _import_names(code)
    assert typescript["github"] == TRACEAI_TYPESCRIPT_TREE + directory


@pytest.mark.unit
@pytest.mark.parametrize("instrumentor", sorted(TYPESCRIPT_PACKAGES))
def test_typescript_snippet_uses_the_setup_step_provider(instrumentor):
    typescript = INSTRUMENTORS.get(instrumentor, {}).get("TypeScript")
    assert isinstance(typescript, dict), f"{instrumentor} has no TypeScript snippet"
    code = typescript["code"]

    # The setup step defines tracerProvider; a second provider would split traces.
    assert SECOND_PROVIDER.search(code) is None, code
    if instrumentor not in USES_GLOBAL_PROVIDER:
        assert re.search(r"\btracerProvider\b", code), code


@pytest.mark.unit
@pytest.mark.parametrize("instrumentor", TYPESCRIPT_SNIPPETS)
def test_typescript_snippet_names_only_verified_traceai_packages(instrumentor):
    code = INSTRUMENTORS[instrumentor]["TypeScript"]["code"]
    named = set(TRACEAI_NAME.findall(code))

    assert named, f"{instrumentor} names no @traceai package"
    assert named <= VERIFIED_TRACEAI_PACKAGES, named - VERIFIED_TRACEAI_PACKAGES
    assert not named & RETIRED_TRACEAI_PACKAGES, named & RETIRED_TRACEAI_PACKAGES


@pytest.mark.unit
@pytest.mark.parametrize(
    "setup", [OBSERVE_CODEBLOCK, PROTOTYPE_CODEBLOCK], ids=["observe", "experiment"]
)
def test_setup_step_defines_the_provider_the_snippets_use(setup):
    assert SETUP_PROVIDER.search(setup["TypeScript"]), setup["TypeScript"]
