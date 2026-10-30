"""CI must never fall back to a download when its configured binary is missing."""

import pytest


def test_local_runs_keep_sdk_default(monkeypatch, request):
    monkeypatch.delenv("TEMPORAL_TEST_SERVER", raising=False)
    assert request.getfixturevalue("temporal_test_server_options") == {}


def test_ci_passes_existing_binary_to_sdk(monkeypatch, tmp_path, request):
    binary = tmp_path / "temporal-test-server"
    binary.touch(mode=0o755)
    monkeypatch.setenv("TEMPORAL_TEST_SERVER", str(binary))
    assert request.getfixturevalue("temporal_test_server_options") == {
        "test_server_existing_path": str(binary)
    }


@pytest.mark.parametrize("kind", ["missing", "directory", "not-executable", "empty"])
def test_bad_ci_binary_fails_instead_of_downloading(
    kind, monkeypatch, tmp_path, request
):
    binary = tmp_path / "temporal-test-server"
    if kind == "directory":
        binary.mkdir()
    elif kind == "not-executable":
        binary.touch(mode=0o644)
    monkeypatch.setenv("TEMPORAL_TEST_SERVER", "" if kind == "empty" else str(binary))
    with pytest.raises(pytest.UsageError, match="not an executable file"):
        request.getfixturevalue("temporal_test_server_options")
