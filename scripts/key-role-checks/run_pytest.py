"""Cloud-only pytest entry point with the network boundary installed first."""

import os
from pathlib import Path
import sys

from socket_guard import install

if sys.platform != "linux" or not os.environ.get("OMEGA_EXEC_SOURCE_HEAD"):
    raise SystemExit("Run key-role Django checks through cloud exec, not locally")

install()
project = Path(sys.argv[1]).resolve()
os.chdir(project)
sys.path.insert(0, str(project))
# Inherit sitecustomize in Python subprocesses as well.
os.environ["PYTHONPATH"] = (
    str(Path(__file__).resolve().parent) + os.pathsep + str(project)
)

import pytest  # noqa: E402

raise SystemExit(pytest.main(sys.argv[2:]))
