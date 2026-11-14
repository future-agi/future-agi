import json
from types import SimpleNamespace

import pytest

from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.versions import SEALED_MANIFEST

# ALK pins the same digest for the same files (test_bundle_author_v2.py).
GOLDEN_SEALED_FROM = (
    "sha256:402ed05450abe6c943865dd538ebd78e94d15c7e7a15d9b6a934e51cf4d26d3e"
)
HASHES = ("environment-hash", "suite-hash")
BUILT = {
    "contract.json": b"{}",
    "world.sqlite": b"built",
    "simulator_prompt.md": b"built prompt",
    "scenarios/one/scenario.json": b'{"name": "one"}',
}


def _manifest(sealed_from=None, digest="chat", **metadata):
    if sealed_from is not None:
        metadata["sealed_from"] = sealed_from
    return json.dumps({"digest": digest, "metadata": metadata}).encode()


def _split(tree, manifest):
    return archives.split_authoring({**tree, SEALED_MANIFEST: manifest})


@pytest.fixture
def head(monkeypatch):
    """No open drafts; the default pair is the build, sealed without a stamp."""
    monkeypatch.setattr(drafts, "fresh_draft", lambda head, piece: None)
    default = archives.pack_archive(
        {**BUILT, SEALED_MANIFEST: _manifest(digest="built")}
    )
    monkeypatch.setattr(archives, "get_archive", lambda key: default)
    return SimpleNamespace(payload={"metadata": {"authoring_object_key": "built"}})


def test_sealed_from_digests_only_the_reseal_inputs():
    files = {
        "scenarios/réservation/scenario.json": b'{"name": "r"}',
        "scenarios/réservation/__pycache__/x.pyc": b"x",
        "scenarios/réservation/outbound-spool/x.json": b"x",
        "scenarios/réservation/telemetry/x.json": b"x",
        "scenarios/nested/deeper/ready.py": b"def ready(world): ...",
        "contract.json": b"{}",
        "sub_goals.json": b"[]",
        "world.sqlite": b"world",
        "simulator_prompt.md": b"not carried",
    }

    assert drafts.sealed_from(files) == GOLDEN_SEALED_FROM


def test_a_bundle_stamped_for_this_tree_is_resealed(head):
    tree = {**BUILT, "world.sqlite": b"edited"}

    split = _split(tree, _manifest(drafts.sealed_from(tree)))

    assert drafts._resealed(head, split, HASHES) is True


def test_a_bundle_stamped_for_an_earlier_world_is_not_resealed(head):
    first = {**BUILT, "world.sqlite": b"first edit"}
    # The second edit's reseal failed: the workspace still holds the first's bundle.
    second = {**BUILT, "world.sqlite": b"second edit"}

    split = _split(second, _manifest(drafts.sealed_from(first)))

    assert drafts._resealed(head, split, HASHES) is False


def test_an_unstamped_bundle_is_not_resealed(head):
    split = _split({**BUILT, "world.sqlite": b"edited"}, _manifest())

    assert drafts._resealed(head, split, HASHES) is False


@pytest.mark.parametrize(
    "path", ["simulator_prompt.md", "schema.sql", "handlers/refund.py"]
)
def test_a_bundle_resealed_beside_an_edit_it_does_not_carry_is_not_resealed(head, path):
    # A reseal carries the world and scenarios; the rest stays as built.
    tree = {**BUILT, "world.sqlite": b"edited", path: b"edited"}

    split = _split(tree, _manifest(drafts.sealed_from(tree)))

    assert drafts._resealed(head, split, HASHES) is False


@pytest.mark.parametrize("path", ["manifest.json", "generic-harness/world-ir.json"])
def test_files_outside_the_environment_identity_do_not_count_against_a_reseal(
    head, path
):
    # save_world rewrites manifest.json with its row counts; no bundle carries it.
    tree = {**BUILT, "world.sqlite": b"edited", path: b"rewritten"}

    split = _split(tree, _manifest(drafts.sealed_from(tree)))

    assert drafts._resealed(head, split, HASHES) is True


@pytest.mark.parametrize(
    ("compiled_for", "expected"),
    [(None, False), (b'{"tools": []}', False), (b'{"tools": ["refund"]}', True)],
)
def test_a_contract_edit_counts_only_when_the_reseal_compiled_it(
    head, compiled_for, expected
):
    # An older ALK copied an edited contract.json into the bundle without the refund
    # tool's handler; only a reseal that compiled this contract says so.
    tree = {**BUILT, "contract.json": b'{"tools": ["refund"]}'}
    compiled = (
        {}
        if compiled_for is None
        else {"contract_compiled": archives.sha256_hex(compiled_for)}
    )

    split = _split(tree, _manifest(drafts.sealed_from(tree), **compiled))

    assert drafts._resealed(head, split, HASHES) is expected


@pytest.mark.parametrize(
    ("compiled_for", "expected"),
    [(None, False), (b'{"tools": []}', False), (b'{"tools": ["refund"]}', True)],
)
def test_a_drafts_own_bundle_counts_a_contract_edit_only_when_compiled(
    head, monkeypatch, compiled_for, expected
):
    # Recorded resealed before reseals said whether they compiled the contract.
    tree = {**BUILT, "contract.json": b'{"tools": ["refund"]}'}
    compiled = (
        {}
        if compiled_for is None
        else {"contract_compiled": archives.sha256_hex(compiled_for)}
    )
    manifest = _manifest(drafts.sealed_from(tree), **compiled)
    kept = {
        "manifest_sha256": archives.sha256_hex(manifest),
        "resealed": True,
        "environment_hash": HASHES[0],
        "suite_hash": HASHES[1],
    }
    draft = SimpleNamespace(documents={"workspace_sealed": kept})
    monkeypatch.setattr(drafts, "fresh_draft", lambda head, piece: draft)

    assert drafts._resealed(head, _split(tree, manifest), HASHES) is expected


def test_an_unreadable_draft_bundle_may_be_this_very_one(head, monkeypatch):
    draft = SimpleNamespace(documents={"workspace_sealed": {"key": "gone"}})
    monkeypatch.setattr(drafts, "fresh_draft", lambda head, piece: draft)
    monkeypatch.setattr(drafts, "_sealed_files", lambda key: None)
    tree = {**BUILT, "world.sqlite": b"edited"}

    split = _split(tree, _manifest(drafts.sealed_from(tree)))

    assert drafts._resealed(head, split, HASHES) is False


@pytest.mark.parametrize(
    ("sealed_for", "resealed", "expected"),
    [
        (HASHES, True, True),
        (("other", "suite-hash"), True, False),
        (HASHES, False, False),
    ],
)
def test_a_drafts_own_bundle_counts_as_that_draft_recorded_it(
    head, monkeypatch, sealed_for, resealed, expected
):
    tree = {**BUILT, "world.sqlite": b"edited"}
    manifest = _manifest(drafts.sealed_from(tree))
    kept = {
        "manifest_sha256": archives.sha256_hex(manifest),
        "resealed": resealed,
        "environment_hash": sealed_for[0],
        "suite_hash": sealed_for[1],
    }
    draft = SimpleNamespace(documents={"workspace_sealed": kept})
    monkeypatch.setattr(drafts, "fresh_draft", lambda head, piece: draft)

    assert drafts._resealed(head, _split(tree, manifest), HASHES) is expected
