"""The Observe setup snippets must name the same project in both languages.

The name passed to register() picks the project: the first trace with a new
name creates it, and a known name adds to it. The Python and TypeScript
snippets used to name different projects (FUTURE_AGI and openai_project), so
one app set up from both tabs of the same page landed in two projects.
"""

import ast
import re

import pytest

from tracer.utils.constants import OBSERVE_CODEBLOCK

TS_PROJECT_NAME = re.compile(r"""^\s*projectName\s*:\s*["']([^"']*)["']""", re.M)


def _python_project_name(code):
    for node in ast.walk(ast.parse(code)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "register"
        ):
            for keyword in node.keywords:
                if keyword.arg == "project_name":
                    return ast.literal_eval(keyword.value)
    return None


def _typescript_project_name(code):
    match = TS_PROJECT_NAME.search(code)
    return match.group(1) if match else None


@pytest.mark.unit
def test_observe_snippets_use_one_project_name_in_both_languages():
    python_name = _python_project_name(OBSERVE_CODEBLOCK["Python"])
    typescript_name = _typescript_project_name(OBSERVE_CODEBLOCK["TypeScript"])

    assert python_name, "Python snippet should pass project_name to register()"
    assert typescript_name, "TypeScript snippet should pass projectName to register()"
    assert python_name == typescript_name
