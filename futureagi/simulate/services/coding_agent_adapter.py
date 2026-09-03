"""Coding-agent simulation adapter (SWE-bench style, local only).

Applies file operations from tool calls to a working repo copy, runs a
pytest subset in a subprocess with timeout, and scores the outcome. The
adapter reuses the CodeExecutionPass idea at trajectory level: static
similarity cannot separate a correct patch from a plausible one, but
test execution can.
"""

import os
import shutil
import subprocess
import tempfile

ALLOWED_ACTIONS = {"write_file", "patch_file", "run_tests", "read_file"}


def apply_tool_calls(repo_path, tool_calls, timeout=30):
    """Apply tool calls to repo_path and run pytest.

    Tool call shapes:
    - {"name": "write_file", "arguments": {"path": str, "content": str}}
    - {"name": "run_tests", "arguments": {"targets": [str]}}

    Returns dict with applied count, test summary, and score.
    """
    applied = []
    test_targets = []
    for call in tool_calls or []:
        name = call.get("name", "")
        args = call.get("arguments", {}) or {}
        if name not in ALLOWED_ACTIONS:
            continue
        if name == "write_file":
            rel = str(args.get("path", "")).strip().lstrip("/")
            if not rel or ".." in rel:
                continue
            dest = os.path.join(repo_path, rel)
            os.makedirs(os.path.dirname(dest) or repo_path, exist_ok=True)
            with open(dest, "w") as handle:
                handle.write(str(args.get("content", "")))
            applied.append(rel)
        elif name == "run_tests":
            targets = args.get("targets", []) or []
            test_targets.extend([str(item) for item in targets])

    passed, total, output = _run_pytest(repo_path, test_targets or None, timeout=timeout)
    score = passed / total if total else 0.0
    return {
        "applied": applied,
        "test_targets": test_targets,
        "passed": passed,
        "total": total,
        "score": score,
        "output": output[-2000:],
    }


def _run_pytest(repo_path, targets, timeout=30):
    cmd = ["python3", "-m", "pytest", "-q"]
    if targets:
        cmd.extend(targets)
    try:
        completed = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 0, 1, "pytest timeout"
    text = (completed.stdout or "") + "\n" + (completed.stderr or "")
    passed, total = _parse_pytest_summary(text)
    return passed, total, text


def _parse_pytest_summary(text):
    import re

    match = re.search(r"(\d+)\s+passed", text)
    passed = int(match.group(1)) if match else 0
    failed_match = re.search(r"(\d+)\s+failed", text)
    failed = int(failed_match.group(1)) if failed_match else 0
    errors = re.search(r"(\d+)\s+error", text)
    err_count = int(errors.group(1)) if errors else 0
    total = passed + failed + err_count
    if total == 0 and "no tests ran" in text.lower():
        return 0, 0
    if total == 0:
        return (1 if passed else 0), max(1, passed)
    return passed, total


def simulate_coding_task(repo_template, tool_calls, test_targets=None, timeout=30):
    """Copy a template repo to temp dir, apply calls, run tests, clean up."""
    workdir = tempfile.mkdtemp(prefix="coding-sim-")
    try:
        if os.path.isdir(repo_template):
            for entry in os.listdir(repo_template):
                source = os.path.join(repo_template, entry)
                dest = os.path.join(workdir, entry)
                if os.path.isdir(source):
                    shutil.copytree(source, dest)
                else:
                    shutil.copy2(source, dest)
        calls = list(tool_calls or [])
        if test_targets:
            calls.append({"name": "run_tests", "arguments": {"targets": test_targets}})
        return apply_tool_calls(workdir, calls, timeout=timeout)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
