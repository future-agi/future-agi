"""Candidate archives and downloaded patches must retain GitHub source symlinks."""

import io
import subprocess
import tarfile
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from simulate.services import agent_fix_engine as engine
from simulate.services import hosted_harness_gateway as gateway
from simulate.services.agent_fix_agents import VersionedSourceAgent
from simulate.services.hosted_harness import HostedHarnessError


@pytest.fixture(autouse=True)
def tree_limits(settings):
    settings.AGENT_FIX_MAX_TREE_BYTES = 1024 * 1024
    settings.AGENT_FIX_MAX_TREE_FILES = 100


def _tree(files, links, *, modes=None):
    body = io.BytesIO()
    with tarfile.open(fileobj=body, mode="w:gz") as archive:
        for path, data in files.items():
            member = tarfile.TarInfo(f"source/{path}")
            member.size = len(data)
            member.mode = (modes or {}).get(path, 0o644)
            archive.addfile(member, io.BytesIO(data))
        for path, target in links.items():
            member = tarfile.TarInfo(f"source/{path}")
            member.type = tarfile.SYMTYPE
            member.linkname = target
            archive.addfile(member)
    return body.getvalue()


def _apply(tmp_path, base, patch_body):
    """Apply using shipping's indexed path, preserving the source's types and modes."""
    with tarfile.open(fileobj=io.BytesIO(base), mode="r:gz") as archive:
        archive.extractall(tmp_path, filter="data")
    root = tmp_path / "source"

    def git(*args, **kwargs):
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            **kwargs,
        )

    git("init", "-q")
    git("config", "core.filemode", "true")
    git("config", "core.symlinks", "true")
    git("add", "--all")
    git("apply", "--index", "--binary", "-", input=patch_body)
    return root


@pytest.mark.parametrize(
    ("path", "target", "read_path"),
    [
        ("current-prompt.txt", "prompts/system.txt", "current-prompt.txt"),
        ("config/current.txt", "../prompts/system.txt", "config/current.txt"),
        ("current-prompts", "prompts", "current-prompts/system.txt"),
    ],
    ids=["file-link", "parent-relative-link", "directory-link"],
)
def test_candidate_archive_preserves_unchanged_relative_symlink(
    monkeypatch, tmp_path, path, target, read_path
):
    links = {path: target}
    base = engine.archive_files(_tree({"prompts/system.txt": b"old\n"}, links))
    files = engine.archive_files(
        _tree({"prompts/system.txt": b"new\n"}, links), candidate=True
    )
    fix_run = SimpleNamespace(
        id="fix", organization=SimpleNamespace(id="org"), request={"locked_paths": [path]}
    )
    engine._refuse_locked_changes(fix_run, base, files)
    patch_body = engine._patch(base, files)
    assert b"prompts/system.txt" in patch_body
    assert f"diff --git a/{path} b/{path}".encode() not in patch_body

    client = Mock()
    monkeypatch.setattr(gateway, "get_storage_client", lambda: client)
    monkeypatch.setattr(gateway, "ensure_bucket", Mock())
    prepared = VersionedSourceAgent().prepare_candidate(
        fix_run,
        {
            name: entry.data if isinstance(entry, engine._SourceFile) else entry
            for name, entry in files.items()
        },
        attempt=1,
    )

    assert prepared["source_id"]
    stored = client.put_object.call_args.kwargs["data"]
    with tarfile.open(fileobj=stored, mode="r:gz") as archive:
        member = archive.getmember(f"source/{path}")
        assert member.issym()
        assert member.linkname == target
        archive.extractall(tmp_path, filter="data")
    assert (tmp_path / "source" / path).is_symlink()
    assert (tmp_path / "source" / read_path).read_bytes() == b"new\n"


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, "prompts/system.txt"),
        ("prompts/system.txt", None),
        ("prompts/system.txt", "prompts/other.txt"),
        ("prompts/system.txt", b"prompts/system.txt"),
        (b"prompts/system.txt", "prompts/system.txt"),
    ],
    ids=["added", "removed", "retargeted", "link-to-file", "file-to-link"],
)
def test_patch_applies_symlink_changes_with_git_modes(tmp_path, before, after):
    path = "current-prompt.txt"
    regular = {"prompts/system.txt": b"first\n", "prompts/other.txt": b"second\n"}

    def tree(value):
        files = {**regular, **({path: value} if isinstance(value, bytes) else {})}
        links = {path: value} if isinstance(value, str) else {}
        return _tree(files, links)

    patch_body = engine._patch(
        engine.archive_files(tree(before)),
        engine.archive_files(tree(after), candidate=True),
    )
    assert b"120000" in patch_body
    root = _apply(tmp_path, tree(before), patch_body)
    changed = root / path
    if after is None:
        assert not changed.exists() and not changed.is_symlink()
    elif isinstance(after, str):
        assert changed.is_symlink()
        assert changed.readlink().as_posix() == after
        assert changed.read_bytes() == regular[after]
    else:
        assert not changed.is_symlink()
        assert changed.read_bytes() == after
    index = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--stage", "--", path]
    )
    if after is None:
        assert index == b""
    else:
        assert index.split()[0] == (b"120000" if isinstance(after, str) else b"100644")


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (None, "prompts/system.txt"),
        ("prompts/system.txt", None),
        ("prompts/system.txt", "other.txt"),
        ("prompts/system.txt", b"prompts/system.txt"),
        (b"prompts/system.txt", "prompts/system.txt"),
    ],
    ids=["added", "removed", "retargeted", "link-to-file", "file-to-link"],
)
def test_locked_symlink_changes_are_refused(before, after):
    path = "current-prompt.txt"
    regular = {"prompts/system.txt": b"prompt\n"}

    def tree(value):
        return _tree(
            {**regular, **({path: value} if isinstance(value, bytes) else {})},
            {path: value} if isinstance(value, str) else {},
        )

    base = engine.archive_files(tree(before))
    files = engine.archive_files(tree(after), candidate=True)
    with pytest.raises(HostedHarnessError) as caught:
        engine._refuse_locked_changes(
            SimpleNamespace(request={"locked_paths": [path]}), base, files
        )
    assert caught.value.code == "fix_locked_path_changed"


@pytest.mark.parametrize("candidate", [False, True], ids=["base", "candidate"])
@pytest.mark.parametrize("limit", ["bytes", "files"])
def test_symlinks_count_towards_candidate_tree_limits(settings, limit, candidate):
    if limit == "bytes":
        settings.AGENT_FIX_MAX_TREE_BYTES = 2
    else:
        settings.AGENT_FIX_MAX_TREE_FILES = 1
    with pytest.raises(HostedHarnessError) as caught:
        engine.archive_files(
            _tree({"prompt.txt": b"ok"}, {"current.txt": "prompt.txt"}),
            candidate=candidate,
        )
    assert caught.value.code == "fix_tree_too_large"


@pytest.mark.parametrize(
    ("before", "after", "base_mode", "expected_mode"),
    [
        (b"#!/bin/sh\nexit 0\n", b"#!/bin/sh\nexit 0\n", 0o644, 0o755),
        (b"#!/bin/sh\nexit 1\n", b"#!/bin/sh\nexit 0\n", 0o755, 0o755),
        (b"#!/bin/sh\nexit 1\n", b"exit 0\n", 0o755, 0o644),
    ],
    ids=["mode-only-change", "edit-executable", "remove-shebang"],
)
def test_add_candidate_keeps_symlink_and_canonical_file_modes(
    monkeypatch, tmp_path, before, after, base_mode, expected_mode
):
    """Exercise archive parsing through real candidate packing, mocking only boundaries."""
    path = "tools/run.sh"
    link = "run-current"
    links = {link: path}
    base = _tree({path: before}, links, modes={path: base_mode})
    # Incoming candidate permissions must not override the shared canonical mode rule.
    tree = _tree(
        {path: after}, links, modes={path: 0o644 if expected_mode == 0o755 else 0o755}
    )
    fix_run = SimpleNamespace(
        id="fix",
        organization=SimpleNamespace(id="org"),
        environment=object(),
        candidates=Mock(),
        request={"locked_paths": [link]},
    )
    fix_run.candidates.filter.return_value.first.return_value = None
    version = SimpleNamespace(id="version")
    candidate = SimpleNamespace(agent_version=version, agent_version_id=version.id)
    kind = VersionedSourceAgent()
    monkeypatch.setattr(kind, "candidate_version", Mock(return_value=version))
    monkeypatch.setattr(kind, "check", Mock(return_value="passed"))
    monkeypatch.setattr(engine, "agent_kind", lambda environment: kind)
    monkeypatch.setattr(engine, "source_archive", lambda run: base)
    monkeypatch.setattr(engine, "_locked_running", lambda run_id: fix_run)
    monkeypatch.setattr(engine.transaction, "atomic", nullcontext)
    monkeypatch.setattr(engine, "_object_key", lambda run, name: name)
    put = Mock()
    monkeypatch.setattr(engine, "_put", put)
    create = Mock(return_value=candidate)
    monkeypatch.setattr(engine.AgentFixCandidate.no_workspace_objects, "create", create)
    client = Mock()
    monkeypatch.setattr(gateway, "get_storage_client", lambda: client)
    monkeypatch.setattr(gateway, "ensure_bucket", Mock())

    assert engine.add_candidate(fix_run, attempt=1, summary="update", tree=tree) == (
        candidate,
        "passed",
    )
    create.assert_called_once()
    kind.check.assert_called_once_with(version)
    stored = client.put_object.call_args.kwargs["data"].getvalue()
    with tarfile.open(fileobj=io.BytesIO(stored), mode="r:gz") as archive:
        member = archive.getmember(f"source/{link}")
        assert member.issym()
        assert member.linkname == path
        assert archive.getmember(f"source/{path}").mode == expected_mode
        assert archive.extractfile(f"source/{path}").read() == after

    put.assert_called_once()
    patch_body = put.call_args.args[1]
    assert f"diff --git a/{link} b/{link}".encode() not in patch_body
    root = _apply(tmp_path, base, patch_body)
    assert (root / link).is_symlink()
    assert (root / link).readlink().as_posix() == path
    assert (root / link).read_bytes() == after
    assert bool((root / path).stat().st_mode & 0o111) == (expected_mode == 0o755)
    index = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "--stage", "--", path]
    )
    assert index.split()[0] == f"{0o100000 | expected_mode:o}".encode()
