"""T6 #4: resolved dependency upgrades must reach base-derived runtime images.

Read Dockerfiles only; no image builds, registry access or application imports.
Run with pytest's --noconftest for a DB-free packaging check.
"""

import re
import shlex
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
FLOORS = {"certifi": Version("2026.10.19"), "anyio": Version("4.14.2")}


@pytest.mark.parametrize("dockerfile", ["Dockerfile", "futureagi/Dockerfile"])
@pytest.mark.parametrize("package", FLOORS)
def test_backend_app_layer_dependency_floor(dockerfile, package):
    path = ROOT / dockerfile
    text = re.sub(r"\\\r?\n", " ", path.read_text(encoding="utf-8"))
    blocks = re.findall(r"(?m)^RUN pip install --no-cache-dir ([^\n]+)$", text)
    assert len(blocks) == 1, f"{path}: expected one app-layer pip upgrade block"
    requirements = [Requirement(token) for token in shlex.split(blocks[0])]
    matches = [req for req in requirements if req.name == package]
    assert len(matches) == 1, f"{path}: missing unique app-layer {package} pin"
    pins = list(matches[0].specifier)
    assert len(pins) == 1 and pins[0].operator == "==", (
        f"{path}: {package} must have an exact app-layer pin"
    )
    version = Version(pins[0].version)
    assert version >= FLOORS[package], (
        f"{path}: {package}=={version} is below {FLOORS[package]}"
    )
    if package == "anyio":
        assert version.major == 4, f"{path}: AnyIO must stay on reviewed major 4"


@pytest.mark.parametrize(
    ("dockerfile", "instruction", "image", "old_version"),
    [
        (
            "futureagi/code-executor/Dockerfile",
            "ARG CODE_EXECUTOR_BASE=",
            "futureagi/code-executor-base",
            "1.1.0",
        ),
        (
            "futureagi/model_serving/Dockerfile",
            "FROM ",
            "futureagi/serving-base",
            "1.0.0",
        ),
    ],
)
def test_dependency_base_has_new_version(dockerfile, instruction, image, old_version):
    path = ROOT / dockerfile
    refs = re.findall(
        rf"(?m)^{re.escape(instruction + image)}:v(\d+\.\d+\.\d+)"
        r"(?:@sha256:[a-f0-9]{64})?\s*$",
        path.read_text(encoding="utf-8"),
    )
    assert len(refs) == 1, f"{path}: expected one immutable {image} version pin"
    assert Version(refs[0]) > Version(old_version), (
        f"{path}: changed base dependencies require a new tag after v{old_version}"
    )
