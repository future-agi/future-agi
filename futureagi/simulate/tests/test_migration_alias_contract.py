"""Guard every simulation RunPython forward step, not just empty databases."""

import ast
import importlib
import inspect
from pathlib import Path

import pytest
from django.db.migrations import RunPython


def forward_steps():
    directory = Path(__file__).resolve().parents[1] / "migrations"
    for path in sorted(directory.glob("[0-9]*.py")):
        module = importlib.import_module(f"simulate.migrations.{path.stem}")
        for operation in module.Migration.operations:
            if (
                isinstance(operation, RunPython)
                and operation.code is not RunPython.noop
            ):
                yield pytest.param(
                    operation.code, id=f"{path.stem}-{operation.code.__name__}"
                )


@pytest.mark.parametrize("forward", list(forward_steps()))
def test_forward_orm_operations_are_explicitly_bound(forward):
    source = inspect.getsource(forward)
    assert "schema_editor.connection.alias" in source
    tree = ast.parse(source)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {
            "objects",
            "_base_manager",
        }:
            access = parents[node]
            assert isinstance(access, ast.Attribute) and access.attr in {
                "using",
                "db_manager",
            }, source
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "save"
        ):
            assert any(keyword.arg == "using" for keyword in node.keywords), source
