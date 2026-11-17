"""T6 urllib3: every shipped resolution pins urllib3 at or above 2.8.0.

urllib3 < 2.8.0 reads a chunked response's chunk-size line without a bound
(GHSA-vxq7-64xx-v4gw), which lets one response buffer past safe_fetch's
max_bytes check; serving's 2.2.2 is also open to the unbounded decompression
chain (GHSA-gm62-xv2j-4w53, GHSA-2xpw-w6gg-jr37). The behaviour is covered by
test_t6_urllib3_chunked_line.py; this file covers where the pin is declared.

Parse files only: no package install, image build or application import. Run
with pytest's --noconftest for a DB-free packaging check (the root conftest
starts Django and service fixtures).
"""

import re
import shlex
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
FLOOR = Version("2.8.0")
MANIFESTS = [
    "requirements.txt",
    "ee/requirements.txt",
    "model_serving/pyproject.toml",
    "model_serving/requirements.txt",
    "code-executor/requirements.sandbox.txt",
    "requirements-test.txt",
]
# The backend images built FROM future-agi-base reinstall these pins over the
# base's site-packages, so the base's older urllib3 does not ship.
DOCKERFILES = ["Dockerfile", "futureagi/Dockerfile"]


def _assert_reviewed_pin(declaration, where):
    requirement = Requirement(declaration)
    assert requirement.name == "urllib3", f"{where}: {declaration!r}"
    pins = list(requirement.specifier)
    assert len(pins) == 1 and pins[0].operator == "==", (
        f"{where}: urllib3 must have an exact pin, got {declaration!r}"
    )
    version = Version(pins[0].version)
    assert version >= FLOOR, f"{where}: urllib3=={version} is below {FLOOR}"
    assert version.major == 2, f"{where}: urllib3 must stay on reviewed major 2"


@pytest.mark.parametrize("manifest", MANIFESTS)
def test_manifest_pins_urllib3_floor(manifest):
    path = BACKEND / manifest
    text = path.read_text(encoding="utf-8")
    if path.name == "pyproject.toml":
        lines = tomllib.loads(text)["project"]["dependencies"]
    else:
        lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    declarations = [
        line.rstrip("\\").strip()
        for line in lines
        if re.match(r"^urllib3(?=\s|[<>=!~\[;]|$)", line, re.IGNORECASE)
    ]
    assert len(declarations) == 1, f"{path}: expected one urllib3 resolution"
    _assert_reviewed_pin(declarations[0], path)


def test_lock_resolves_urllib3_floor():
    lock = tomllib.loads((BACKEND / "uv.lock").read_text(encoding="utf-8"))
    packages = [package for package in lock["package"] if package["name"] == "urllib3"]
    assert packages, "uv.lock: missing urllib3 resolution"
    for package in packages:
        version = Version(package["version"])
        assert version >= FLOOR, f"uv.lock: urllib3=={version} is below {FLOOR}"
        assert version.major == 2, "uv.lock: urllib3 must stay on reviewed major 2"
        for artifact in [package["sdist"], *package["wheels"]]:
            assert f"urllib3-{package['version']}" in artifact["url"]
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["hash"])


@pytest.mark.parametrize("dockerfile", DOCKERFILES)
def test_backend_app_layer_pins_urllib3_floor(dockerfile):
    path = REPO_ROOT / dockerfile
    text = re.sub(r"\\\r?\n", " ", path.read_text(encoding="utf-8"))
    blocks = re.findall(r"(?m)^RUN pip install --no-cache-dir ([^\n]+)$", text)
    assert len(blocks) == 1, f"{path}: expected one app-layer pip upgrade block"
    declarations = [
        token
        for token in shlex.split(blocks[0])
        if Requirement(token).name == "urllib3"
    ]
    assert len(declarations) == 1, f"{path}: missing unique app-layer urllib3 pin"
    _assert_reviewed_pin(declarations[0], path)
