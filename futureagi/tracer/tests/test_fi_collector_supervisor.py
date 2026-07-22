"""Unit tests for the fi-collector subprocess supervisor.

We test the supervisor against a stub binary (a shell script that
sleeps for a configurable duration) so the tests are hermetic — no
network, no real CH, no real fi-collector binary required.
"""
from __future__ import annotations

import os
import stat
import time

import pytest

from tracer.utils import fi_collector_supervisor as sup


@pytest.fixture
def stub_binary(tmp_path):
    """Write a tiny shell script that sleeps and exits cleanly. Used to
    stand in for the real fi-collector during tests.
    """
    binary = tmp_path / "fake-collector"
    binary.write_text(
        "#!/bin/sh\n"
        "echo 'fake-collector starting' >&2\n"
        "# Exit after the FAKE_DURATION env (default 5s)\n"
        "sleep ${FAKE_DURATION:-5}\n"
        "echo 'fake-collector exiting' >&2\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return str(binary)


@pytest.fixture(autouse=True)
def reset_supervisor():
    """Each test gets a clean module state."""
    sup.stop()
    sup._stop.clear()
    with sup._lock:
        sup._proc = None
    yield
    sup.stop()


def test_start_launches_subprocess(stub_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DURATION", "10")
    sup.start(
        binary_path=stub_binary,
        ch_url="http://nowhere",
        grpc_addr="127.0.0.1:14317",
        dead_letter_dir=str(tmp_path),
    )
    # Give the supervisor thread a moment to spawn.
    deadline = time.time() + 2
    while time.time() < deadline and not sup.is_running():
        time.sleep(0.05)
    assert sup.is_running(), "supervisor never marked the child as running"


def test_stop_is_idempotent(stub_binary, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DURATION", "10")
    sup.start(
        binary_path=stub_binary,
        ch_url="http://nowhere",
        dead_letter_dir=str(tmp_path),
    )
    # Wait for spawn.
    deadline = time.time() + 2
    while time.time() < deadline and not sup.is_running():
        time.sleep(0.05)

    sup.stop()
    sup.stop()  # second call must be a no-op, no exception
    deadline = time.time() + 5
    while time.time() < deadline and sup.is_running():
        time.sleep(0.05)
    assert not sup.is_running()


def test_missing_binary_does_not_loop(tmp_path):
    """Sanity check: pointing at a path that doesn't exist must NOT cause
    an infinite restart loop. _supervise returns on FileNotFoundError.
    """
    sup.start(
        binary_path=str(tmp_path / "does-not-exist"),
        ch_url="http://nowhere",
        dead_letter_dir=str(tmp_path),
    )
    # Give the supervisor thread enough time to attempt + give up.
    time.sleep(0.5)
    assert not sup.is_running()


def test_restart_on_crash(stub_binary, tmp_path, monkeypatch):
    """Stub binary exits in 0.5 s; supervisor should restart it. After
    1.5 s the supervisor must have launched it at least twice.
    """
    monkeypatch.setenv("FAKE_DURATION", "0.3")
    sup.start(
        binary_path=stub_binary,
        ch_url="http://nowhere",
        dead_letter_dir=str(tmp_path),
        restart_backoff_initial_sec=0.05,
        restart_backoff_max_sec=0.1,
    )
    # Track PIDs across restarts
    seen_pids: set[int] = set()
    deadline = time.time() + 3
    while time.time() < deadline and len(seen_pids) < 2:
        with sup._lock:
            p = sup._proc
            if p is not None:
                seen_pids.add(p.pid)
        time.sleep(0.05)
    assert len(seen_pids) >= 2, (
        f"supervisor should have spawned the child more than once after "
        f"a crash; saw only PIDs: {seen_pids}"
    )
