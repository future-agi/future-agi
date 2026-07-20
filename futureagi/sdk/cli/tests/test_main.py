"""Tests for ``sdk.cli.main`` — CLI entry point argument parsing."""

from __future__ import annotations

import pytest

from sdk.cli.client import (
    EXIT_REGRESSION,
    EXIT_SUCCESS,
    EXIT_USAGE_ERROR,
)
from sdk.cli.main import main


class TestMainNoCommand:
    """Tests for invoking the CLI with no subcommand."""

    def test_no_args_returns_usage_error(self) -> None:
        """Verify no arguments returns EXIT_USAGE_ERROR."""
        result = main([])
        assert result == EXIT_USAGE_ERROR

    def test_help_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify --help prints usage and exits."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_version_flag(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify --version prints version and exits."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "fi-simulate" in captured.out


class TestRunSubcommand:
    """Tests for the ``run`` subcommand argument parsing."""

    def test_missing_test_id_fails(self) -> None:
        """Verify missing --test-id causes an error."""
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--api-key", "k", "--secret-key", "s"])
        assert exc_info.value.code == 2  # argparse error

    def test_missing_api_key_returns_usage_error(self) -> None:
        """Verify missing API key raises SystemExit with EXIT_USAGE_ERROR."""
        with pytest.raises(SystemExit) as exc_info:
            main([
                "run",
                "--test-id", "abc-123",
                "--secret-key", "s",
                "--api-key", "",
            ])
        assert exc_info.value.code == EXIT_USAGE_ERROR

    def test_missing_secret_key_returns_usage_error(self) -> None:
        """Verify missing secret key raises SystemExit with EXIT_USAGE_ERROR."""
        with pytest.raises(SystemExit) as exc_info:
            main([
                "run",
                "--test-id", "abc-123",
                "--api-key", "k",
                "--secret-key", "",
            ])
        assert exc_info.value.code == EXIT_USAGE_ERROR


class TestStatusSubcommand:
    """Tests for the ``status`` subcommand argument parsing."""

    def test_missing_test_id_fails(self) -> None:
        """Verify missing --test-id causes an error."""
        with pytest.raises(SystemExit) as exc_info:
            main([
                "status",
                "--execution-id", "e1",
                "--api-key", "k",
                "--secret-key", "s",
            ])
        assert exc_info.value.code == 2

    def test_missing_execution_id_fails(self) -> None:
        """Verify missing --execution-id causes an error."""
        with pytest.raises(SystemExit) as exc_info:
            main([
                "status",
                "--test-id", "t1",
                "--api-key", "k",
                "--secret-key", "s",
            ])
        assert exc_info.value.code == 2


class TestRunSubcommandHelp:
    """Tests for the ``run`` subcommand help output."""

    def test_run_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verify ``run --help`` prints usage and exits."""
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "--test-id" in captured.out
        assert "--threshold" in captured.out
        assert "--output" in captured.out
