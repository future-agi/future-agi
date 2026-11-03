#!/usr/bin/env python3
"""Resolve an immutable SDK revision that actually contains the live suites."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Literal

REPOSITORY = "https://github.com/future-agi/futureagi-sdk.git"
# SDK PR #35 supplies the suites introduced by product PR #3624. Until a default
# SDK branch includes them, use this exact tested harness, not a moving feature ref.
BOOTSTRAP_REF = "855f2ad63c603a2f13054aa95fa3e5a9cf7849d6"
REQUIRED_PATHS = (
    "sdkgen/local_api_key.py",
    "sdkgen/catalog/operations.yaml",
    "python/tests/platform/conftest.py",
    "python/tests/platform/test_all_operations_live.py",
    "typescript/futureagi/src/platform/__tests__/live.test.ts",
)
PACKAGE_JSON = "typescript/futureagi/package.json"
Mode = Literal["named", "explicit", "pinned"]
Probe = Callable[[str, Mode], tuple[str, list[str]] | None]


class ResolutionError(RuntimeError):
    """The requested SDK revision cannot safely run the required suites."""


class InvalidRefError(ResolutionError):
    """A candidate is not a supported Git branch/tag/ref spelling."""


def named_refs(ref: str) -> tuple[str, ...]:
    if ref.startswith(("refs/heads/", "refs/tags/")):
        return (ref,)
    if ref.startswith("refs/"):
        raise InvalidRefError("SDK refs must name a branch, tag or full commit SHA")
    return (f"refs/heads/{ref}", f"refs/tags/{ref}")


def validate_ref(ref: str) -> None:
    if not ref or len(ref) > 1024 or ref.startswith("-") or any(ord(c) < 32 for c in ref):
        raise InvalidRefError("Invalid SDK ref spelling")
    try:
        result = subprocess.run(
            ["git", "check-ref-format", named_refs(ref)[0]],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ResolutionError("SDK ref validation timed out") from exc
    if result.returncode:
        raise InvalidRefError("Invalid SDK ref spelling")


def choose_ref(explicit: str, head: str, base: str, probe: Probe) -> tuple[str, str]:
    """Explicit requests fail closed; automatic refs must contain the harness."""
    candidates: list[tuple[str, Mode]] = (
        [(explicit, "explicit")] if explicit else
        [(ref, "named") for ref in (head, base, "dev", "main") if ref]
        + [(BOOTSTRAP_REF, "pinned")]
    )
    for candidate, mode in dict.fromkeys(candidates):
        try:
            validate_ref(candidate)
        except InvalidRefError:
            if mode == "named":
                continue  # An unsupported automatic branch must not mask valid defaults.
            raise
        result = probe(candidate, mode)  # Transport errors must not become a fallback.
        if result is None:
            if explicit:
                raise ResolutionError("Requested SDK ref does not exist")
            continue
        sha, missing = result
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ResolutionError("SDK ref did not resolve to a full commit SHA")
        if mode == "pinned" and sha != candidate:
            raise ResolutionError("Pinned SDK harness identity mismatch")
        if missing:
            if explicit:
                raise ResolutionError("Requested SDK ref lacks required live-test files or script")
            print(f"SDK ref {candidate} lacks the live-test harness; checking next candidate")
            continue
        return sha, candidate
    raise ResolutionError("No SDK revision contains the complete required live-test harness")


class GitProbe:
    def __init__(self, directory: Path):
        self.directory = directory
        self._git("init", "--quiet")

    def _git(self, *args: str, allow_missing: bool = False) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.directory), *args],
                capture_output=True, text=True, timeout=120, check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ResolutionError("SDK repository operation timed out") from exc
        if result.returncode and not allow_missing:
            # Do not echo transport diagnostics: credential helpers can include secrets.
            raise ResolutionError("SDK repository operation failed")
        return result

    def __call__(self, ref: str, mode: Mode = "explicit") -> tuple[str, list[str]] | None:
        fetch_ref = None
        if mode == "pinned" or (mode == "explicit" and re.fullmatch(r"[0-9a-fA-F]{40}", ref)):
            if not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
                raise ResolutionError("Pinned SDK harness requires a full commit SHA")
            fetch_ref = ref.lower()
        else:
            names = named_refs(ref)
            advertised = self._git(
                "ls-remote", "--exit-code", "--heads", "--tags", REPOSITORY,
                *names, allow_missing=True,
            )
            if advertised.returncode not in (0, 2):
                raise ResolutionError("Could not query SDK refs")
            available = ({line.split()[1] for line in advertised.stdout.splitlines()
                          if len(line.split()) == 2} if advertised.returncode == 0 else set())
            fetch_ref = next((name for name in names if name in available), None)
            if fetch_ref is None:
                if mode == "explicit" and re.fullmatch(r"[0-9a-fA-F]{7,39}", ref):
                    raise ResolutionError("Use a full 40-character SDK commit SHA; no branch or tag matches this abbreviation")
                return None
        self._git("fetch", "--quiet", "--depth=1", "--no-tags", REPOSITORY, fetch_ref)
        sha = self._git("rev-parse", "FETCH_HEAD^{commit}").stdout.strip()
        missing = []
        for path in (*REQUIRED_PATHS, PACKAGE_JSON):
            entry = self._git("cat-file", "-t", f"{sha}:{path}", allow_missing=True)
            if entry.returncode or entry.stdout.strip() != "blob":
                missing.append(path)
        if PACKAGE_JSON not in missing:
            try:
                package = json.loads(self._git("show", f"{sha}:{PACKAGE_JSON}").stdout)
                script = package.get("scripts", {}).get("test:live")
                if not isinstance(script, str) or not script.strip():
                    missing.append("package.json scripts.test:live")
            except (ValueError, AttributeError):
                missing.append("valid package.json scripts.test:live")
        return sha, missing


def main() -> int:
    try:
        with tempfile.TemporaryDirectory(prefix="sdk-test-ref-") as directory:
            sha, selected = choose_ref(
                os.environ.get("SDK_REF_INPUT", ""),
                os.environ.get("HEAD_REF", ""),
                os.environ.get("BASE_REF", ""),
                GitProbe(Path(directory)),
            )
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise ResolutionError("GITHUB_OUTPUT is required")
        with Path(output).open("a") as handle:
            handle.write(f"ref={sha}\n")
        print(f"futureagi-sdk live suites: {selected} resolved to {sha}")
        if selected == BOOTSTRAP_REF:
            print("::notice::Using the immutable SDK PR #35 live-harness revision; default SDK branches do not yet contain these suites.")
        return 0
    except ResolutionError as exc:
        print(f"::error::{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
