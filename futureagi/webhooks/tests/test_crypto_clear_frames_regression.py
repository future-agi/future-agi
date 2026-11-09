"""Regression guard for F73-W1-S-SEC-01: an exception handler in webhooks.crypto that binds the error must clear
the failed call's frames before anything logs or raises, because those frames hold the ciphertext (and, for
encrypt_json, the value being encrypted). Removing any one clear_exception_frames call fails this test."""

from __future__ import annotations

import ast
import inspect

import pytest

from webhooks import crypto
from webhooks.core import redact

GUARDED = ("encrypt_json", "decrypt", "rotate")


def _function_node(name: str) -> ast.FunctionDef:
    tree = ast.parse(inspect.getsource(crypto))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"webhooks.crypto.{name} is not a module-level function")


def _is_clear_call(stmt: ast.stmt, exc_name: str) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and isinstance(stmt.value.func, ast.Name)
        and stmt.value.func.id == "clear_exception_frames"
        and len(stmt.value.args) == 1
        and isinstance(stmt.value.args[0], ast.Name)
        and stmt.value.args[0].id == exc_name
    )


def _logs_or_raises(stmt: ast.stmt) -> bool:
    for node in ast.walk(stmt):
        if isinstance(node, ast.Raise):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("log", "logger", "logging")
        ):
            return True
    return False


def test_the_helper_is_the_redaction_module_one():
    assert crypto.clear_exception_frames is redact.clear_exception_frames


@pytest.mark.parametrize("name", GUARDED)
def test_every_bound_handler_clears_frames_before_it_logs_or_raises(name):
    handlers = [
        node
        for node in ast.walk(_function_node(name))
        if isinstance(node, ast.ExceptHandler) and node.name
    ]
    assert handlers, f"{name} has no handler that binds its exception"
    for handler in handlers:
        first_clear = next(
            (i for i, stmt in enumerate(handler.body) if _is_clear_call(stmt, handler.name)),
            None,
        )
        assert first_clear is not None, (
            f"{name}: the handler at line {handler.lineno} never calls clear_exception_frames({handler.name})"
        )
        assert not any(_logs_or_raises(stmt) for stmt in handler.body[:first_clear]), (
            f"{name}: the handler at line {handler.lineno} logs or raises before clearing frames"
        )
