"""The code-eval sandbox module imports where the POSIX-only ``resource`` is absent.

``fi_evals.function.functions`` imports ``SETUP_ERROR_MESSAGES`` from the
sandbox at module scope, and Django loads that module at startup through
``model_hub.models.choices``. A module-level ``import resource`` in the sandbox
therefore made the whole backend unimportable wherever the module is missing.
Only the forked child that applies rlimits needs it.
"""

import importlib
import sys

SANDBOX = "agentic_eval.core_evals.fi_utils.sandbox"


def test_sandbox_imports_without_the_resource_module(monkeypatch):
    # A ``None`` entry makes ``import resource`` raise ModuleNotFoundError, as
    # it does on a platform that does not ship the module.
    monkeypatch.setitem(sys.modules, "resource", None)
    monkeypatch.delitem(sys.modules, SANDBOX, raising=False)

    sandbox = importlib.import_module(SANDBOX)

    assert sandbox.EXECUTOR_NO_NODE_MESSAGE in sandbox.SETUP_ERROR_MESSAGES
