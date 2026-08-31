from pathlib import Path

from slots.registry import RegistryStore, discover_state_dir


def test_state_dir_uses_primary_worktree_and_override(tmp_path: Path):
    primary = tmp_path / "primary"
    primary.mkdir()
    output = f"worktree {primary}\nHEAD abc\n\nworktree {tmp_path / 'child'}\n"
    assert (
        discover_state_dir(tmp_path, {}, lambda _command, _cwd: output)
        == primary / ".slots"
    )
    assert (
        discover_state_dir(tmp_path, {"SLOTS_STATE_DIR": str(tmp_path / "custom")})
        == tmp_path / "custom"
    )


def test_registry_is_versioned_atomic_and_private(tmp_path: Path):
    store = RegistryStore(tmp_path / ".slots")
    with store.locked() as registry:
        store.save(registry)
    assert store.load().version == 1
    assert (tmp_path / ".slots" / "registry.json").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / ".slots" / "registry.json.tmp").exists()
