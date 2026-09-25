"""Execute only the installer's env-file stage; no Docker or real credentials."""

import os
import stat
import subprocess
from pathlib import Path

import pytest

INSTALL = Path(__file__).resolve().parents[2] / "bin/install"
SECRET = "SECRET_KEY=synthetic-do-not-print\n"


def configure_env(tmp_path, *, mask="022", path=None):
    source = INSTALL.read_text()
    stage = source.split("# ---------------- .env ----------------", 1)[1].split(
        "# Portable in-place sed", 1
    )[0]
    return subprocess.run(
        [
            "bash",
            "-c",
            f"set -euo pipefail; umask {mask}; "
            'step() { :; }; ok() { :; }; die() { echo "$*" >&2; exit 1; }; ' + stage,
        ],
        cwd=tmp_path,
        env={"PATH": path or os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("mask", ["000", "022"])
@pytest.mark.parametrize("existing", [False, True])
def test_env_creation_and_reinstall_keep_secrets_private(tmp_path, mask, existing):
    example = tmp_path / ".env.example"
    example.write_text("SECRET_KEY=example\nNEW_SETTING=default\n")
    example.chmod(0o644)
    env = tmp_path / ".env"
    if existing:
        env.write_text(SECRET)
        env.chmod(0o600)
    for _ in range(2):
        result = configure_env(tmp_path, mask=mask)
        assert result.returncode == 0, result.stderr
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
        assert env.read_text() == (
            SECRET + "NEW_SETTING=default\n" if existing else example.read_text()
        )
        assert "synthetic-do-not-print" not in result.stdout + result.stderr
        assert not list(tmp_path.glob(".env.tmp.*"))


def test_env_merge_does_not_follow_a_preexisting_temporary_symlink(tmp_path):
    (tmp_path / ".env.example").write_text("NEW_SETTING=default\n")
    env = tmp_path / ".env"
    env.write_text(SECRET)
    env.chmod(0o600)
    sentinel = tmp_path / "unrelated"
    sentinel.write_text("untouched\n")
    (tmp_path / ".env.tmp").symlink_to(sentinel)
    result = configure_env(tmp_path)
    assert result.returncode == 0, result.stderr
    assert sentinel.read_text() == "untouched\n"
    assert (tmp_path / ".env.tmp").is_symlink()
    assert env.is_file() and not env.is_symlink()
    assert env.read_text() == SECRET + "NEW_SETTING=default\n"


def test_failed_merge_preserves_original_env_and_removes_only_own_temp(tmp_path):
    (tmp_path / ".env.example").write_text("NEW_SETTING=default\n")
    env = tmp_path / ".env"
    env.write_text(SECRET)
    env.chmod(0o600)
    commands = tmp_path / "commands"
    commands.mkdir()
    awk = commands / "awk"
    awk.write_text('#!/bin/sh\nprintf "partial output\\n"\nexit 42\n')
    awk.chmod(0o700)
    result = configure_env(tmp_path, path=f"{commands}:{os.environ['PATH']}")
    assert result.returncode != 0
    assert env.read_text() == SECRET
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert not list(tmp_path.glob(".env.tmp*"))
    assert "synthetic-do-not-print" not in result.stdout + result.stderr
