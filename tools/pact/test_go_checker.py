"""
Tests for tools/pact/go_checker.py

These tests exercise the Python bridge that invokes the pact-go binary and
converts JSON output into FailureEvidence objects.  The tests are designed to
work even when the Go toolchain is absent — they mock subprocess.run so no
binary is required in CI.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .failure_mode import FailureEvidence
from .go_checker import _find_binary, _can_go_run, run_go_checker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_completed_process(violations: list[dict], returncode: int = 0) -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = returncode
    m.stdout = json.dumps(violations)
    m.stderr = ""
    return m


# ---------------------------------------------------------------------------
# Unit: JSON → FailureEvidence conversion
# ---------------------------------------------------------------------------

class TestRunGoCheckerConversion:
    """run_go_checker converts raw JSON into FailureEvidence objects."""

    def _run(self, records: list[dict]) -> list[FailureEvidence]:
        proc = _make_completed_process(records)
        with patch("subprocess.run", return_value=proc), \
             patch("pact.go_checker._find_binary", return_value="/fake/pact-go"):
            return run_go_checker(["/fake/dir"])

    def test_empty_array_returns_no_evidence(self):
        assert self._run([]) == []

    def test_single_violation_fields(self):
        rec = {
            "mode": "go_ignored_error",
            "file": "pkg/server.go",
            "line": 42,
            "call": "Open",
            "message": "error return from Open() discarded with '_'",
        }
        result = self._run([rec])
        assert len(result) == 1
        ev = result[0]
        assert ev.mode_name == "go_ignored_error"
        assert ev.file == "pkg/server.go"
        assert ev.line == 42
        assert ev.call == "Open"
        assert "Open" in ev.message

    def test_multiple_violations(self):
        records = [
            {"mode": "go_bare_recover", "file": "a.go", "line": 10,
             "call": "recover()", "message": "bare recover"},
            {"mode": "go_unchecked_assertion", "file": "b.go", "line": 20,
             "call": ".(string)", "message": "unchecked assertion"},
        ]
        result = self._run(records)
        assert len(result) == 2
        assert result[0].mode_name == "go_bare_recover"
        assert result[1].mode_name == "go_unchecked_assertion"

    def test_missing_fields_default_gracefully(self):
        result = self._run([{"mode": "go_goroutine_no_sync"}])
        assert len(result) == 1
        assert result[0].file == ""
        assert result[0].line == 0
        assert result[0].call == ""


# ---------------------------------------------------------------------------
# Unit: subprocess plumbing
# ---------------------------------------------------------------------------

class TestRunGoCheckerSubprocess:

    def test_empty_paths_returns_no_evidence(self):
        with patch("subprocess.run") as mock_run:
            result = run_go_checker([])
        mock_run.assert_not_called()
        assert result == []

    def test_file_path_uses_file_flag(self, tmp_path):
        go_file = tmp_path / "main.go"
        go_file.write_text("package main\n")
        proc = _make_completed_process([])
        with patch("subprocess.run", return_value=proc) as mock_run, \
             patch("pact.go_checker._find_binary", return_value="/fake/pact-go"):
            run_go_checker([go_file])
        args = mock_run.call_args[0][0]
        assert "--file" in args
        assert str(go_file) in args
        assert "--dir" not in args

    def test_directory_path_uses_dir_flag(self, tmp_path):
        proc = _make_completed_process([])
        with patch("subprocess.run", return_value=proc) as mock_run, \
             patch("pact.go_checker._find_binary", return_value="/fake/pact-go"):
            run_go_checker([tmp_path])
        args = mock_run.call_args[0][0]
        assert "--dir" in args
        assert "--file" not in args

    def test_timeout_returns_empty(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 60)), \
             patch("pact.go_checker._find_binary", return_value="/fake/pact-go"):
            result = run_go_checker(["/some/dir"])
        assert result == []

    def test_oserror_returns_empty(self):
        with patch("subprocess.run", side_effect=OSError("not found")), \
             patch("pact.go_checker._find_binary", return_value="/fake/pact-go"):
            result = run_go_checker(["/some/dir"])
        assert result == []

    def test_bad_json_stdout_returns_empty(self):
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = "not json {"
        with patch("subprocess.run", return_value=proc), \
             patch("pact.go_checker._find_binary", return_value="/fake/pact-go"):
            result = run_go_checker(["/some/dir"])
        assert result == []

    def test_no_binary_no_go_returns_empty(self):
        with patch("pact.go_checker._find_binary", return_value=None), \
             patch("pact.go_checker._can_go_run", return_value=False):
            result = run_go_checker(["/some/dir"])
        assert result == []

    def test_no_binary_falls_back_to_go_run(self, tmp_path):
        proc = _make_completed_process([])
        with patch("subprocess.run", return_value=proc) as mock_run, \
             patch("pact.go_checker._find_binary", return_value=None), \
             patch("pact.go_checker._can_go_run", return_value=True):
            run_go_checker([tmp_path])
        args = mock_run.call_args[0][0]
        assert args[0] == "go"
        assert args[1] == "run"

    def test_explicit_binary_overrides_auto_resolve(self, tmp_path):
        proc = _make_completed_process([])
        with patch("subprocess.run", return_value=proc) as mock_run:
            run_go_checker([tmp_path], binary="/custom/pact-go")
        args = mock_run.call_args[0][0]
        assert args[0] == "/custom/pact-go"

    def test_extra_args_forwarded(self, tmp_path):
        proc = _make_completed_process([])
        with patch("subprocess.run", return_value=proc) as mock_run, \
             patch("pact.go_checker._find_binary", return_value="/fake/pact-go"):
            run_go_checker([tmp_path], extra_args=["--verbose"])
        args = mock_run.call_args[0][0]
        assert "--verbose" in args


# ---------------------------------------------------------------------------
# Integration: round-trip with real Go toolchain (skipped if go absent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _can_go_run(),
    reason="Go toolchain not available",
)
class TestGoCheckerIntegration:
    """Real subprocess calls — skipped if `go` is not on PATH."""

    def _write_go_file(self, tmp_path: Path, name: str, src: str) -> Path:
        f = tmp_path / name
        f.write_text(src)
        return f

    def test_ignored_error_detected(self, tmp_path):
        src = """package main
import "os"
func main() {
    f, _ := os.Open("x")
    _ = f
}
"""
        go_file = self._write_go_file(tmp_path, "main.go", src)
        evidence = run_go_checker([go_file])
        modes = [e.mode_name for e in evidence]
        assert "go_ignored_error" in modes

    def test_bare_recover_detected(self, tmp_path):
        src = """package main
func risky() {
    defer func() { recover() }()
    panic("oops")
}
"""
        go_file = self._write_go_file(tmp_path, "risky.go", src)
        evidence = run_go_checker([go_file])
        assert any(e.mode_name == "go_bare_recover" for e in evidence)

    def test_unchecked_assertion_detected(self, tmp_path):
        src = """package main
func cast(v interface{}) string {
    s := v.(string)
    return s
}
"""
        go_file = self._write_go_file(tmp_path, "cast.go", src)
        evidence = run_go_checker([go_file])
        assert any(e.mode_name == "go_unchecked_assertion" for e in evidence)

    def test_goroutine_no_sync_detected(self, tmp_path):
        src = """package main
func fire() {
    go func() {
        doWork()
    }()
}
func doWork() {}
"""
        go_file = self._write_go_file(tmp_path, "fire.go", src)
        evidence = run_go_checker([go_file])
        assert any(e.mode_name == "go_goroutine_no_sync" for e in evidence)

    def test_goroutine_with_waitgroup_not_flagged(self, tmp_path):
        src = """package main
import "sync"
func safe() {
    var wg sync.WaitGroup
    wg.Add(1)
    go func() {
        defer wg.Done()
    }()
    wg.Wait()
}
"""
        go_file = self._write_go_file(tmp_path, "safe.go", src)
        evidence = run_go_checker([go_file])
        sync_violations = [e for e in evidence if e.mode_name == "go_goroutine_no_sync"]
        assert len(sync_violations) == 0

    def test_clean_file_returns_no_violations(self, tmp_path):
        src = """package main
import (
    "fmt"
    "os"
)
func main() {
    f, err := os.Open("x")
    if err != nil {
        fmt.Println(err)
        return
    }
    defer f.Close()
}
"""
        go_file = self._write_go_file(tmp_path, "clean.go", src)
        evidence = run_go_checker([go_file])
        assert evidence == []

    def test_directory_scan(self, tmp_path):
        (tmp_path / "a.go").write_text(
            "package p\nimport \"os\"\nfunc f() { _, _ = os.Open(\"x\") }\n"
        )
        # The above has 2-value open (safe), just checking dir mode doesn't crash
        evidence = run_go_checker([tmp_path])
        assert isinstance(evidence, list)
