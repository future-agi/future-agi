"""A test that binds a Sentry client must leave none bound for the next test.

A client left bound after a test keeps its logging integration active, so a
later test's ``logger.exception`` serialises frame locals; ``repr()`` of a lazy
queryset or relation then runs SQL inside that test's query counter.
"""

import ast
import logging
import os
from pathlib import Path

import pytest
import sentry_sdk
from sentry_sdk.integrations.dedupe import DedupeIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from tfc.tests.sentry_capture import sentry_capture

GUARD_LOGGER = "tfc.tests.sentry_capture_guard"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
HELPER = Path(__file__).with_name("sentry_capture.py")
SKIPPED_DIRS = {"node_modules", "__pycache__"}


class CountingRepr:
    """Stands in for a lazy queryset: counts how often Sentry serialises it."""

    def __init__(self):
        self.calls = 0

    def __repr__(self):
        self.calls += 1
        return "<CountingRepr>"


def log_exception_holding(probe):
    local_value = probe  # a frame local, as the victims' querysets were
    try:
        raise ValueError("synthetic failure")
    except ValueError:
        logging.getLogger(GUARD_LOGGER).exception("guard_probe")
    return local_value


def capture_client():
    return sentry_capture(
        LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        DedupeIntegration(),
    )


def assert_nothing_bound(previous_client, isolation_scope):
    assert sentry_sdk.get_global_scope().client is previous_client
    assert sentry_sdk.get_isolation_scope() is isolation_scope
    assert sentry_sdk.get_client().get_integration(LoggingIntegration) is None
    assert sentry_sdk.get_client().get_integration(DedupeIntegration) is None


def test_capture_leaves_no_client_or_integration_bound():
    previous_client = sentry_sdk.get_global_scope().client
    isolation_scope = sentry_sdk.get_isolation_scope()
    probe = CountingRepr()

    with capture_client() as transport:
        assert sentry_sdk.get_client().is_active()
        log_exception_holding(probe)
    # The probe is sensitive: inside the block Sentry serialised the local.
    assert probe.calls >= 1
    assert len(transport.events) == 1

    assert_nothing_bound(previous_client, isolation_scope)
    calls_inside = probe.calls
    log_exception_holding(probe)
    assert probe.calls == calls_inside
    assert len(transport.events) == 1


def test_capture_rebinds_the_previous_client_when_the_block_fails():
    previous_client = sentry_sdk.get_global_scope().client
    isolation_scope = sentry_sdk.get_isolation_scope()

    with pytest.raises(RuntimeError, match="synthetic test failure"):
        with capture_client():
            raise RuntimeError("synthetic test failure")

    assert_nothing_bound(previous_client, isolation_scope)
    probe = CountingRepr()
    log_exception_holding(probe)
    assert probe.calls == 0


def _is_test_module(path):
    return (
        path.name.startswith("test_")
        or path.name == "conftest.py"
        or "tests" in path.relative_to(BACKEND_ROOT).parts
    )


def _global_client_bindings(source, filename):
    """Line numbers of ``sentry_sdk.init`` calls, which bind to the global scope.

    A client set on a forked scope (``with sentry_sdk.isolation_scope() as
    scope: scope.set_client(client)``) is discarded with the fork, so it is
    not reported.
    """
    tree = ast.parse(source, filename)
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "sentry_sdk"
        for alias in node.names
        if alias.name == "init"
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "init"
            and isinstance(func.value, ast.Name)
            and func.value.id == "sentry_sdk"
        ) or (isinstance(func, ast.Name) and func.id in imported):
            yield node.lineno


def test_tests_bind_sentry_clients_only_through_sentry_capture():
    offenders = []
    for directory, subdirs, files in os.walk(BACKEND_ROOT):
        subdirs[:] = [
            name
            for name in subdirs
            if not name.startswith(".") and name not in SKIPPED_DIRS
        ]
        for name in files:
            path = Path(directory, name)
            if not name.endswith(".py") or path == HELPER:
                continue
            if not _is_test_module(path):
                continue
            source = path.read_text(encoding="utf-8", errors="replace")
            if "sentry_sdk" not in source:
                continue
            offenders.extend(
                f"{path.relative_to(BACKEND_ROOT)}:{line}"
                for line in _global_client_bindings(source, str(path))
            )
    # Each of these leaves its client bound after the test; bind it through
    # tfc.tests.sentry_capture.sentry_capture instead.
    assert offenders == []
