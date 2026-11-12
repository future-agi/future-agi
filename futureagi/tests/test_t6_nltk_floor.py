"""Keep every NLTK installation surface on the reviewed, pinned candidate.

These are source-only tests: no Django setup, package install or corpus download
is needed. Include the legacy test export so it cannot silently downgrade NLTK.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
MINIMUM = Version("3.10.4")


def _assert_reviewed_pin(declaration: str) -> None:
    requirement = Requirement(declaration)
    assert requirement.name == "nltk"
    specifiers = list(requirement.specifier)
    assert len(specifiers) == 1, f"NLTK must be pinned: {declaration}"
    pin = specifiers[0]
    assert pin.operator == "==", f"NLTK must be pinned: {declaration}"
    assert Version(pin.version) >= MINIMUM, declaration


@pytest.mark.parametrize(
    "relative_path",
    ["futureagi/pyproject.toml", "futureagi/model_serving/pyproject.toml"],
)
def test_nltk_project_pins(relative_path: str) -> None:
    project = tomllib.loads((REPO_ROOT / relative_path).read_text())
    declarations = [
        dependency
        for dependency in project["project"]["dependencies"]
        if Requirement(dependency).name == "nltk"
    ]
    assert len(declarations) == 1
    _assert_reviewed_pin(declarations[0])


@pytest.mark.parametrize(
    "relative_path",
    [
        "futureagi/requirements.txt",
        "futureagi/ee/requirements.txt",
        "futureagi/model_serving/requirements.txt",
        "futureagi/code-executor/requirements.sandbox.txt",
        "futureagi/requirements-test.txt",
    ],
)
def test_nltk_requirements_pins(relative_path: str) -> None:
    declarations = re.findall(
        r"^nltk\b[^\n#\\]*", (REPO_ROOT / relative_path).read_text(), re.MULTILINE
    )
    assert len(declarations) == 1
    _assert_reviewed_pin(declarations[0].strip())


@pytest.mark.parametrize("relative_path", ["Dockerfile", "futureagi/Dockerfile"])
def test_nltk_docker_pins(relative_path: str) -> None:
    declarations = re.findall(
        r'^\s*"(nltk[^"\n]*)"',
        (REPO_ROOT / relative_path).read_text(),
        re.MULTILINE,
    )
    assert len(declarations) == 1
    _assert_reviewed_pin(declarations[0])


def test_nltk_lock_resolution() -> None:
    lock = tomllib.loads((REPO_ROOT / "futureagi/uv.lock").read_text())
    packages = [package for package in lock["package"] if package["name"] == "nltk"]
    assert packages
    for package in packages:
        assert Version(package["version"]) >= MINIMUM
        artifacts = [package["sdist"], *package["wheels"]]
        for artifact in artifacts:
            assert f"nltk-{package['version']}" in artifact["url"]
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["hash"])


def test_nltk_lock_declared_requirement() -> None:
    lock = tomllib.loads((REPO_ROOT / "futureagi/uv.lock").read_text())
    declarations = [
        requirement
        for package in lock["package"]
        for requirement in package.get("metadata", {}).get("requires-dist", [])
        if requirement["name"] == "nltk"
    ]
    assert declarations
    for requirement in declarations:
        _assert_reviewed_pin("nltk" + requirement.get("specifier", ""))


def test_executor_and_serving_bases_are_rebuilt_for_the_nltk_pin() -> None:
    """requirements.sandbox.txt and model_serving's pyproject now pin nltk 3.10.4, but
    those images build FROM pinned base tags. Base tags are immutable (backend-ci.yml's
    code-executor-base-pin gate), so the change only ships under new tags. Same tags
    as T6-4b (certifi/AnyIO), so the two picks share one rebuild."""
    executor = (REPO_ROOT / "futureagi/code-executor/Dockerfile").read_text()
    match = re.search(
        r"^ARG CODE_EXECUTOR_BASE=futureagi/code-executor-base:(\S+)$", executor, re.M
    )
    assert match, "CODE_EXECUTOR_BASE pin not found"
    assert match.group(1) != "v1.1.0", (
        "executor base still v1.1.0: the NLTK pin would not ship"
    )
    serving = (REPO_ROOT / "futureagi/model_serving/Dockerfile").read_text()
    match = re.search(r"^FROM futureagi/serving-base:(\S+)$", serving, re.M)
    assert match, "serving base pin not found"
    assert match.group(1) != "v1.0.0", (
        "serving base still v1.0.0: the NLTK pin would not ship"
    )
