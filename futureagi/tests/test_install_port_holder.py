"""Run the shell port-preflight regression in the regular pytest suite."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_windows_netstat_port_preflight() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "bin" / "test_install_port_holder.sh")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
