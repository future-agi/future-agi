"""The test inputs are exactly the pinned vendor specs: every stored spec is
listed in SOURCES.json with its URL, sha256, size and licence, every listed
file is stored, and none holds a secret-shaped value."""

from __future__ import annotations

import gzip
import hashlib

import support
from restgen.specfile import FORMATS, load_spec


def test_every_stored_spec_is_listed_and_every_listed_spec_is_stored():
    stored = sorted(p.name for p in support.SPECS.glob("*.gz"))
    listed = sorted(entry["file"] for entry in support.SOURCES["specs"])
    assert stored == listed


def test_each_listed_spec_matches_its_pin_licence_and_format():
    formats = set()
    for entry in support.SOURCES["specs"]:
        raw = gzip.decompress((support.SPECS / entry["file"]).read_bytes())
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"], entry["file"]
        assert len(raw) == entry["bytes"], entry["file"]
        assert entry["url"].startswith("https://") and entry["licence"]
        assert (support.SPECS / entry["licenceFile"]).is_file(), entry["file"]
        # The generator's own read: the pin, the secret scan and the format.
        assert load_spec(support.SPECS / entry["file"], entry["sha256"], entry["format"])
        formats.add(entry["format"])
    assert formats == set(FORMATS)


def test_every_licence_file_is_used():
    used = {entry["licenceFile"] for entry in support.SOURCES["specs"]}
    stored = {f"LICENSES/{p.name}" for p in (support.SPECS / "LICENSES").iterdir()}
    assert used == stored
