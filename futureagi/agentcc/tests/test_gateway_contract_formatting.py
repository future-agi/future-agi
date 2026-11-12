"""Contract checks accept the required formatter, never changed contract code."""

import runpy
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("existing", "matches"),
    [
        ('CONSENT = {\n    "trace_content": False,\n}\n', True),
        ('CONSENT = {"trace_content": True}\n', False),
        ('CONSENT = {"other_field": False}\n', False),
        ('CONSENT = {"trace_content": False}\nprint("changed")\n', False),
        ('CONSENT = {"trace_content": False}\nextra: bool = False\n', False),
        ('CONSENT = {"trace_content": False', False),
    ],
)
def test_python_contract_check_preserves_all_syntax(existing, matches, tmp_path):
    module = runpy.run_path(
        str(
            Path(__file__).resolve().parents[3]
            / "scripts/generate-agentcc-gateway-contracts.py"
        )
    )
    check = module["write_or_check"]
    check.__globals__["ROOT"] = tmp_path
    path = tmp_path / "contract.py"
    path.write_text(existing)
    assert check(path, "CONSENT = {'trace_content': False}\n", True) is matches


def test_go_contract_check_remains_byte_exact(tmp_path):
    module = runpy.run_path(
        str(
            Path(__file__).resolve().parents[3]
            / "scripts/generate-agentcc-gateway-contracts.py"
        )
    )
    check = module["write_or_check"]
    check.__globals__["ROOT"] = tmp_path
    path = tmp_path / "contract.go"
    path.write_text("package generated\n\n")
    assert check(path, "package generated\n", True) is False
