"""The operator probe's own paths, after ordinary Django startup.

All credentials below are inert fixtures. No database or external service is used.
"""

import importlib
import importlib.abc
import importlib.util
import io
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest
from django.core.management import find_commands, load_command_class
from django.test import override_settings
from rest_framework.test import APIRequestFactory

from model_hub.apps import ModelHubConfig, guarded_management_command
from tfc.views import deployment as deployment_view
from tfc.views.deployment import DeploymentInfoView

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _probe_environment(monkeypatch):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", "false")
    monkeypatch.setenv("EE_LICENSE_KEY", "")
    monkeypatch.setenv("ENV_TYPE", "local")
    monkeypatch.setenv("NO_STARTUP_DB_MUTATIONS", "true")
    monkeypatch.delenv("STARTUP_DB_MUTATION_MODE", raising=False)
    monkeypatch.delenv("SERVICE_TYPE", raising=False)


def _command():
    management = Path(__file__).resolve().parents[1] / "management"
    assert "deployment_status" in find_commands(str(management))
    command = load_command_class("tfc", "deployment_status")
    command.stdout = io.StringIO()
    command.stderr = io.StringIO()
    return command


def _cloud_import(monkeypatch, outcome):
    original = importlib.import_module

    def import_module(name, package=None):
        if name != "ee.cloud":
            return original(name, package)
        if isinstance(outcome, Exception):
            raise outcome
        return ModuleType("ee.cloud")

    monkeypatch.setattr(importlib, "import_module", import_module)


@pytest.mark.parametrize(
    "region,license_key,mode",
    [
        ("", "", "oss"),
        ("false", "fixture-license-marker", "ee"),
        (" us ", "fixture-license-marker", "cloud"),
    ],
    ids=["oss", "ee", "cloud"],
)
@pytest.mark.parametrize(
    "key,stripe_mode",
    [
        ("sk_test_fixture-status-only", "test"),
        ("rk_live_fixture-status-only", "live"),
        ("", "absent"),
        (None, "absent"),
    ],
    ids=["test", "live", "empty", "none"],
)
@pytest.mark.parametrize(
    "cloud_available", [True, False], ids=["cloud-package", "no-cloud-package"]
)
def test_exact_three_line_output(
    monkeypatch, capsys, region, license_key, mode, key, stripe_mode, cloud_available
):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", region)
    monkeypatch.setenv("EE_LICENSE_KEY", license_key)
    _cloud_import(
        monkeypatch,
        True if cloud_available else ModuleNotFoundError("fixture-import-marker"),
    )
    command = _command()
    with override_settings(STRIPE_SECRET_KEY=key, SECRET_KEY="fixture-settings-marker"):
        command.run_from_argv(["manage.py", "deployment_status"])
    expected = f"mode={mode}\nee-cloud={'yes' if cloud_available else 'no'}\nstripe={stripe_mode}\n"
    assert command.stdout.getvalue() == expected
    assert command.stderr.getvalue() == ""
    assert capsys.readouterr() == ("", "")
    assert "fixture" not in expected


def test_hosted_startup_admits_probe_without_mutation_authority(monkeypatch):
    monkeypatch.setenv("ENV_TYPE", "staging")
    monkeypatch.setattr(sys, "argv", ["manage.py", "deployment_status"])
    authorize = Mock(side_effect=AssertionError("must not request mutation authority"))
    connect = Mock()
    monkeypatch.setattr(
        "model_hub.apps.explicit_management_mutation_authorized", authorize
    )
    monkeypatch.setattr("model_hub.apps.post_migrate.connect", connect)
    assert guarded_management_command(sys.argv) is None
    ModelHubConfig("model_hub", sys.modules["model_hub"]).ready()
    authorize.assert_not_called()
    connect.assert_not_called()
    _cloud_import(monkeypatch, ModuleNotFoundError("fixture-import-marker"))
    command = _command()
    with override_settings(STRIPE_SECRET_KEY=""):
        command.run_from_argv(sys.argv)
    assert command.stdout.getvalue() == "mode=oss\nee-cloud=no\nstripe=absent\n"
    assert command.stderr.getvalue() == ""


@pytest.mark.parametrize(
    "args",
    [
        ["fixture-argument-marker"],
        ["--unknown=fixture-argument-marker"],
        ["--help"],
        ["-h"],
        ["--version"],
        ["--verbosity", "2"],
        ["--settings=fixture-settings-marker"],
        ["--pythonpath", "fixture-path-marker"],
        ["--traceback"],
        ["--no-color"],
        ["--force-color"],
        ["--skip-checks"],
        ["--"],
    ],
    ids=[
        "positional",
        "unknown",
        "help",
        "short-help",
        "version",
        "verbosity",
        "settings",
        "pythonpath",
        "traceback",
        "no-color",
        "force-color",
        "skip-checks",
        "terminator",
    ],
)
def test_all_arguments_exit_two_without_executing(monkeypatch, capsys, args):
    command = _command()
    execute = Mock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(command, "execute", execute)
    with pytest.raises(SystemExit) as error:
        command.run_from_argv(["manage.py", "deployment_status", *args])
    assert error.value.code == 2
    execute.assert_not_called()
    assert command.stdout.getvalue() == command.stderr.getvalue() == ""
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "failure", ["mode-raises", "mode-invalid", "stripe-raises", "stripe-invalid-result"]
)
def test_helper_failure_is_fixed_and_never_partial(monkeypatch, capsys, failure):
    command = _command()
    helper = importlib.import_module("tfc.views.deployment_mode")
    billing = importlib.import_module("tfc.settings.stripe_billing")
    if failure.endswith("raises"):
        fake = Mock(
            side_effect=RuntimeError("fixture-key-marker fixture-settings-marker")
        )
    else:
        fake = Mock(return_value="fixture-key-marker")
    target, name = (
        (helper, "get_deployment_mode")
        if failure.startswith("mode")
        else (billing, "stripe_key_mode")
    )
    monkeypatch.setattr(target, name, fake)
    _cloud_import(monkeypatch, True)
    with (
        override_settings(STRIPE_SECRET_KEY="sk_test_fixture-key-marker"),
        pytest.raises(SystemExit) as error,
    ):
        command.run_from_argv(["manage.py", "deployment_status"])
    assert error.value.code == 1
    assert command.stdout.getvalue() == ""
    assert command.stderr.getvalue() == "deployment-status-failed\n"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "key",
    ["fixture-unknown-marker", " ", "sk_test_", " sk_live_fixture-marker", 123],
    ids=["unknown", "space", "truncated", "padded", "non-string"],
)
def test_nonempty_invalid_stripe_key_never_reports_absent(capsys, key):
    command = _command()
    with override_settings(STRIPE_SECRET_KEY=key), pytest.raises(SystemExit) as error:
        command.run_from_argv(["manage.py", "deployment_status"])
    assert error.value.code == 1
    assert command.stdout.getvalue() == ""
    assert command.stderr.getvalue() == "deployment-status-failed\n"
    assert capsys.readouterr() == ("", "")


def test_cloud_import_failure_reports_no_without_exception_values(monkeypatch, capsys):
    command = _command()
    _cloud_import(monkeypatch, RuntimeError("fixture-settings-marker"))
    with override_settings(STRIPE_SECRET_KEY=""):
        command.run_from_argv(["manage.py", "deployment_status"])
    assert command.stdout.getvalue() == "mode=oss\nee-cloud=no\nstripe=absent\n"
    assert command.stderr.getvalue() == ""
    assert capsys.readouterr() == ("", "")


def test_probe_skips_system_and_migration_checks(monkeypatch):
    command = _command()
    check = Mock(side_effect=AssertionError("must not run checks"))
    monkeypatch.setattr(command, "check", check)
    monkeypatch.setattr(command, "check_migrations", check)
    _cloud_import(monkeypatch, True)
    with override_settings(STRIPE_SECRET_KEY=""):
        command.run_from_argv(["manage.py", "deployment_status"])
    check.assert_not_called()
    assert command.stdout.getvalue() == "mode=oss\nee-cloud=yes\nstripe=absent\n"


@pytest.mark.parametrize(
    "region,license_key,mode",
    [
        ("", "", "oss"),
        ("false", "fixture-license", "ee"),
        ("EU", "fixture-license", "cloud"),
    ],
    ids=["oss", "ee", "cloud"],
)
def test_public_view_response_remains_byte_identical(
    monkeypatch, region, license_key, mode
):
    monkeypatch.setenv("CLOUD_DEPLOYMENT", region)
    monkeypatch.setenv("EE_LICENSE_KEY", license_key)
    # No billing package: its two budget answers are false.
    for name in (
        "ee.cloud.billing.budget_enforcement",
        "ee.cloud.billing.notifications",
    ):
        monkeypatch.setitem(sys.modules, name, None)
    budget_check = getattr(deployment_view, "budget_support", None)
    if budget_check is not None:
        budget_check.cache_clear()
    try:
        response = DeploymentInfoView.as_view()(
            APIRequestFactory().get("/api/deployment-info/")
        )
    finally:
        if budget_check is not None:
            budget_check.cache_clear()
    response.render()
    assert response.status_code == 200
    assert (
        response.content
        == (
            '{"status":true,"result":{"mode":"'
            + mode
            + '","total_spend_budgets_enforced":false,"budget_notices_delivered":false}}'
        ).encode()
    )


def test_view_and_command_share_the_mode_rule(monkeypatch):
    command = _command()
    helper = importlib.import_module("tfc.views.deployment_mode")
    fake = Mock(return_value="ee")
    monkeypatch.setattr(helper, "get_deployment_mode", fake)
    _cloud_import(monkeypatch, True)
    response = DeploymentInfoView.as_view()(
        APIRequestFactory().get("/api/deployment-info/")
    )
    with override_settings(STRIPE_SECRET_KEY=""):
        command.run_from_argv(["manage.py", "deployment_status"])
    assert response.data["result"]["mode"] == "ee"
    assert command.stdout.getvalue() == "mode=ee\nee-cloud=yes\nstripe=absent\n"
    assert fake.call_count == 2


@pytest.mark.parametrize("source", ["mode", "stripe", "cloud"])
@pytest.mark.parametrize("failure", ["runtime", "exit-string", "exit-int", "interrupt"])
def test_noisy_derivation_is_suppressed_and_exit_is_fixed(
    monkeypatch, capfd, source, failure
):
    canary = "sk_test_fixture-command-boundary-canary"
    command = _command()
    mode = importlib.import_module("tfc.views.deployment_mode")
    billing = importlib.import_module("tfc.settings.stripe_billing")
    log_output = io.StringIO()
    logger = logging.getLogger("deployment_status_fixture")
    handler = logging.StreamHandler(log_output)
    monkeypatch.setattr(logger, "handlers", [handler])
    monkeypatch.setattr(logger, "propagate", False)
    monkeypatch.setattr(logger, "level", logging.DEBUG)
    old_disable = logging.root.manager.disable
    logging.disable(logging.NOTSET)
    original_stdout, original_stderr = sys.stdout, sys.stderr
    old_bytecode = sys.dont_write_bytecode

    def noisy_failure(*args, **kwargs):
        print(canary)
        print(canary, file=sys.stderr)
        # Pre-bound streams and direct descriptor writes exercise both layers.
        print(canary, file=original_stdout, flush=True)
        print(canary, file=original_stderr, flush=True)
        os.write(1, canary.encode())
        os.write(2, canary.encode())
        for level in (
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        ):
            logger.log(level, "%s", canary)
        if failure == "exit-string":
            raise SystemExit(canary)
        if failure == "exit-int":
            raise SystemExit(23)
        if failure == "interrupt":
            raise KeyboardInterrupt(canary)
        raise RuntimeError(canary)

    _cloud_import(monkeypatch, True)
    if source == "mode":
        monkeypatch.setattr(mode, "get_deployment_mode", noisy_failure)
    elif source == "stripe":
        monkeypatch.setattr(billing, "stripe_key_mode", noisy_failure)
    else:
        original_import = importlib.import_module

        def importing(name, package=None):
            if name == "ee.cloud":
                return noisy_failure()
            return original_import(name, package)

        monkeypatch.setattr(importlib, "import_module", importing)
    try:
        with override_settings(STRIPE_SECRET_KEY=canary):
            if source == "cloud" and failure == "runtime":
                command.run_from_argv(["manage.py", "deployment_status"])
                assert (
                    command.stdout.getvalue() == "mode=oss\nee-cloud=no\nstripe=test\n"
                )
                assert command.stderr.getvalue() == ""
            else:
                # Catch the injected interrupt too so RED is a per-id failure,
                # not a pytest session abort that hides the remaining controls.
                with pytest.raises(BaseException) as error:
                    command.run_from_argv(["manage.py", "deployment_status"])
                assert isinstance(error.value, SystemExit)
                assert error.value.code == 1
                assert command.stdout.getvalue() == ""
                assert command.stderr.getvalue() == "deployment-status-failed\n"
        assert capfd.readouterr() == ("", "")
        assert log_output.getvalue() == ""
        assert logging.root.manager.disable == logging.NOTSET
        assert sys.stdout is original_stdout and sys.stderr is original_stderr
        assert sys.dont_write_bytecode is old_bytecode
    finally:
        logging.disable(old_disable)
        handler.close()


def test_command_import_creates_no_source_files(monkeypatch, tmp_path):
    # A cold exact-byte source copy avoids touching checkout caches while
    # exercising the normal source loader with bytecode writing enabled.
    name = "tfc.views.deployment_mode"
    original_module = importlib.import_module(name)
    source_tree = tmp_path / "source"
    source_tree.mkdir()
    source = source_tree / "deployment_mode.py"
    source.write_bytes(Path(original_module.__file__).read_bytes())

    class CopyFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == name:
                return importlib.util.spec_from_file_location(fullname, source)
            return None

    command = _command()
    _cloud_import(monkeypatch, True)
    monkeypatch.delitem(sys.modules, name)
    monkeypatch.setattr(sys, "meta_path", [CopyFinder(), *sys.meta_path])
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    before = {
        p.relative_to(source_tree): p.read_bytes()
        for p in source_tree.rglob("*")
        if p.is_file()
    }
    try:
        with override_settings(STRIPE_SECRET_KEY=""):
            command.run_from_argv(["manage.py", "deployment_status"])
        after = {
            p.relative_to(source_tree): p.read_bytes()
            for p in source_tree.rglob("*")
            if p.is_file()
        }
        assert after == before
        assert command.stdout.getvalue() == "mode=oss\nee-cloud=yes\nstripe=absent\n"
        assert command.stderr.getvalue() == ""
        assert sys.dont_write_bytecode is False
    finally:
        sys.modules.pop(name, None)


def test_success_restores_logging_and_bytecode_state(monkeypatch):
    command = _command()
    _cloud_import(monkeypatch, True)
    old_disable = logging.root.manager.disable
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    try:
        logging.disable(logging.ERROR)
        with override_settings(STRIPE_SECRET_KEY=""):
            command.run_from_argv(["manage.py", "deployment_status"])
        assert logging.root.manager.disable == logging.ERROR
        assert sys.dont_write_bytecode is False
        assert command.stdout.getvalue() == "mode=oss\nee-cloud=yes\nstripe=absent\n"
    finally:
        logging.disable(old_disable)
