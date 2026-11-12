"""T6 #4: every resolved trust-store / async-runtime pin meets the reviewed floor.

Parse files, never install packages or import the application. The deprecated
serving requirements and the test resolution are covered too, so neither can
silently reintroduce the old pins. Run with pytest's --noconftest for a DB-free
packaging check (the root conftest starts Django and service fixtures).
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = [
    "requirements.txt",
    "ee/requirements.txt",
    "model_serving/pyproject.toml",
    "model_serving/requirements.txt",
    "code-executor/requirements.sandbox.txt",
    "requirements-test.txt",
    "uv.lock",
]
# Serving currently has no lockfile; cover it if one is added later.
if (ROOT / "model_serving/uv.lock").exists():
    MANIFESTS.append("model_serving/uv.lock")
FLOORS = {"certifi": Version("2026.10.19"), "anyio": Version("4.14.2")}


def _versions(path, package):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".lock":
        return [
            Version(entry["version"])
            for entry in tomllib.loads(text)["package"]
            if entry["name"] == package
        ]
    if path.name == "pyproject.toml":
        lines = tomllib.loads(text)["project"]["dependencies"]
    else:
        lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    versions = []
    for line in lines:
        if not re.match(rf"^{package}(?=\s|[<>=!~\[])", line, re.IGNORECASE):
            continue
        requirement = Requirement(line.rstrip("\\").strip())
        pins = list(requirement.specifier)
        assert len(pins) == 1 and pins[0].operator == "==", (
            f"{path}: {package} must have an exact resolved pin, got {line!r}"
        )
        versions.append(Version(pins[0].version))
    assert versions, f"{path}: missing {package} resolution"
    return versions


@pytest.mark.parametrize("manifest", MANIFESTS)
@pytest.mark.parametrize("package", FLOORS)
def test_resolved_dependency_floor(manifest, package):
    path = ROOT / manifest
    versions = _versions(path, package)
    assert versions, f"{path}: missing {package} resolution"
    for version in versions:
        assert version >= FLOORS[package], (
            f"{path}: {package}=={version} is below {FLOORS[package]}"
        )
        if package == "anyio":
            assert version.major == 4, f"{path}: AnyIO must stay on reviewed major 4"
