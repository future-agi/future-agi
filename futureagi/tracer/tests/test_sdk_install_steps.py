"""Every package the in-app trace setup imports must be installed on the page.

/tracer/project/project_sdk_code/ feeds the Observe setup screens: an
"Install dependencies" command (INSTALLATION_GUIDE), the setup block, then one
copy-ready snippet per instrumentor. Each instrumentor ships as its own package
(traceai-openai, @traceai/anthropic, ...), so a snippet that imports one the
page never installs fails on the user's first run with ModuleNotFoundError
(Python) or an unresolved module (TypeScript), and no trace ever arrives.
"""

import ast
import re

import pytest

from tracer.utils.constants import (
    INSTALLATION_GUIDE,
    INSTRUMENTORS,
    OBSERVE_CODEBLOCK,
    PROTOTYPE_CODEBLOCK,
)

SETUP_CODEBLOCKS = {
    "experiment": PROTOTYPE_CODEBLOCK,
    "observe": OBSERVE_CODEBLOCK,
}

# Import name -> PyPI distribution where they differ beyond PEP 503
# normalisation (traceai_openai is already the traceai-openai distribution).
PYTHON_DISTRIBUTIONS = {"fi_instrumentation": "fi-instrumentation-otel"}

PYTHON_INSTRUMENTORS = sorted(
    key for key, entry in INSTRUMENTORS.items() if "Python" in entry
)
TYPESCRIPT_INSTRUMENTORS = sorted(
    key for key, entry in INSTRUMENTORS.items() if "TypeScript" in entry
)

PIP_INSTALL = re.compile(r"^\s*(?:#\s*)?pip install\s+(.+)$", re.M)
NPM_INSTALL = re.compile(r"^\s*(?://\s*)?npm install\s+(.+)$", re.M)
TS_IMPORT = re.compile(
    r"""^\s*import\s+(?:[^"';]*?\s+from\s+)?["']([^"']+)["']""", re.M
)


def _install_args(pattern, text):
    return [
        arg
        for match in pattern.finditer(text)
        for arg in match.group(1).split()
        if not arg.startswith("-")
    ]


def _pypi_name(requirement):
    name = re.split(r"[\[<>=!~;]", requirement, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _python_import_roots(code):
    roots = set()
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _python_not_installed(code, pip_args):
    installed = {_pypi_name(arg) for arg in pip_args}
    return {
        module
        for module in _python_import_roots(code)
        if _pypi_name(PYTHON_DISTRIBUTIONS.get(module, module)) not in installed
    }


def _npm_name(spec):
    scope, _, rest = spec.partition("/") if spec.startswith("@") else ("", "", spec)
    name = rest.split("/")[0].split("@")[0]
    return f"{scope}/{name}" if scope else name


def _typescript_not_installed(code, npm_args):
    installed = {_npm_name(arg) for arg in npm_args}
    return {_npm_name(spec) for spec in TS_IMPORT.findall(code)} - installed


def _first_line(code):
    return next(line.strip() for line in code.splitlines() if line.strip())


@pytest.mark.unit
@pytest.mark.parametrize("instrumentor", PYTHON_INSTRUMENTORS)
def test_python_instrumentor_snippet_opens_with_its_pip_install(instrumentor):
    code = INSTRUMENTORS[instrumentor]["Python"]["code"]

    assert _first_line(code).startswith("# pip install "), _first_line(code)
    assert _python_not_installed(code, _install_args(PIP_INSTALL, code)) == set()


@pytest.mark.unit
@pytest.mark.parametrize("instrumentor", TYPESCRIPT_INSTRUMENTORS)
def test_typescript_instrumentor_snippet_opens_with_its_npm_install(instrumentor):
    code = INSTRUMENTORS[instrumentor]["TypeScript"]["code"]

    assert _first_line(code).startswith("// npm install "), _first_line(code)
    assert _typescript_not_installed(code, _install_args(NPM_INSTALL, code)) == set()


@pytest.mark.unit
@pytest.mark.parametrize("project_type", sorted(SETUP_CODEBLOCKS))
def test_install_step_covers_python_setup_imports(project_type):
    code = SETUP_CODEBLOCKS[project_type]["Python"]
    pip_args = _install_args(PIP_INSTALL, INSTALLATION_GUIDE["Python"])

    assert _python_not_installed(code, pip_args) == set()


@pytest.mark.unit
@pytest.mark.parametrize("project_type", sorted(SETUP_CODEBLOCKS))
def test_install_step_covers_typescript_setup_imports(project_type):
    code = SETUP_CODEBLOCKS[project_type]["TypeScript"]
    npm_args = _install_args(NPM_INSTALL, INSTALLATION_GUIDE["TypeScript"])

    assert _typescript_not_installed(code, npm_args) == set()
