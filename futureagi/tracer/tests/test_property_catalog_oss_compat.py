"""The shipped current inventory must not require EE or lifecycle modules."""

import ast
import os
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
_PACKAGE = _BACKEND / "tracer/services/clickhouse/v2/property_catalog"


def test_current_catalog_modules_do_not_import_ee_or_lifecycle():
    forbidden = {
        "activation",
        "activation_control",
        "coordinator",
        "projection",
        "publisher",
        "reconciler",
    }
    for path in _PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not (node.module == "ee" or node.module.startswith("ee."))
                assert node.module.rsplit(".", 1)[-1] not in forbidden


def test_current_catalog_imports_without_ee_and_without_database_clients():
    program = """
import builtins
import importlib
import pkgutil
original_find_spec = importlib.util.find_spec
importlib.util.find_spec = lambda name, *args, **kwargs: (
    None if name == 'ee' or name.startswith('ee.') else original_find_spec(name, *args, **kwargs))
original_import = builtins.__import__
def oss_import(name, *args, **kwargs):
    if name == 'ee' or name.startswith('ee.'):
        raise ImportError(f'unexpected EE import: {name}')
    return original_import(name, *args, **kwargs)
builtins.__import__ = oss_import
import django
django.setup()
from unittest.mock import patch
import tracer.services.clickhouse.v2.property_catalog as package
with patch('tracer.services.clickhouse.client.ClickHouseClient', side_effect=AssertionError('no CH during import')):
    for module in pkgutil.iter_modules(package.__path__, package.__name__ + '.'):
        importlib.import_module(module.name)
    from tracer.services.clickhouse.v2.property_catalog.source_adapters import canonical_system_definitions
    assert canonical_system_definitions()
print('CURRENT_CATALOG_OSS_OK')
"""
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "tfc.settings.test",
        "EE_LICENSE_KEY": "",
        "CLOUD_DEPLOYMENT": "",
        "NO_STARTUP_DB_MUTATIONS": "true",
    }
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=_BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "CURRENT_CATALOG_OSS_OK" in result.stdout
