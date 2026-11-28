"""Deterministic export and hostile ZIP/loose-file import contracts."""

import hashlib
import io
import json
import stat
import struct
import subprocess
import zipfile

import pytest

from model_hub.services.skills_hub import limits, packages
from model_hub.services.skills_hub.errors import SkillsHubError

SKILL = b"---\nname: example\ndescription: Use when a task needs clear instructions and a verifiable result.\n---\nFollow the policy.\n"


def archive(entries, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as zipped:
        for path, content in entries:
            zipped.writestr(path, content)
    return output.getvalue()


def upload(entries, **kwargs):
    return packages.parse_upload([("skills.zip", archive(entries, **kwargs))])


def rejected(result, path, reason):
    assert {"path": path, "reason": reason} in result["rejected"]


def skill(name="example"):
    files = [
        {
            "path": "SKILL.md",
            "content": SKILL.decode().replace("name: example", f"name: {name}"),
        },
        {"path": "references/é.md", "content": "日本語\r\n"},
    ]
    return {
        "name": name,
        "files": files,
        "version": 3,
        "version_id": "00000000-0000-0000-0000-000000000003",
        "digest": packages.manifest_digest(files),
        "label": "production",
    }


def test_hashes_exact_bytes_and_canonical_manifest():
    files = skill()["files"]
    pairs = sorted(
        [
            [file["path"], hashlib.sha256(file["content"].encode()).hexdigest()]
            for file in files
        ]
    )
    expected = (
        "sha256:"
        + hashlib.sha256(json.dumps(pairs, separators=(",", ":")).encode()).hexdigest()
    )
    assert packages.manifest_digest(files) == expected
    assert packages.manifest_digest(list(reversed(files))) == expected
    assert (
        packages.manifest_digest(
            [{"path": path, "sha256": digest} for path, digest in pairs]
        )
        == expected
    )
    assert packages.file_sha256("é\r\n") == hashlib.sha256(b"\xc3\xa9\r\n").hexdigest()
    assert packages.file_sha256("text\r\n") != packages.file_sha256("text\n")
    assert (
        packages.manifest_digest([{**files[0], "path": "other.md"}, files[1]])
        != expected
    )


def test_zip_is_byte_identical_independent_of_input_order_and_has_exact_lock():
    skills = [skill("beta"), skill("alpha")]
    skipped = [
        {"name": "zeta", "reason": "label_not_found"},
        {"name": "delta", "reason": "label_not_found"},
    ]
    first = packages.build_zip(
        skills, origin="https://example.invalid", skipped=skipped
    )
    reordered = [
        {**entry, "files": list(reversed(entry["files"]))} for entry in reversed(skills)
    ]
    assert first == packages.build_zip(
        reordered, origin="https://example.invalid", skipped=list(reversed(skipped))
    )
    with zipfile.ZipFile(io.BytesIO(first)) as zipped:
        assert zipped.namelist() == sorted(zipped.namelist())
        assert json.loads(zipped.read(".futureagi-skills.lock.json")) == {
            "schema": 1,
            "origin": "https://example.invalid",
            "skills": [
                {
                    key: entry[key]
                    for key in ("name", "version", "version_id", "digest", "label")
                }
                for entry in reversed(skills)
            ],
            "skipped": list(reversed(skipped)),
        }
        for entry in zipped.infolist():
            assert entry.date_time == (1980, 1, 1, 0, 0, 0)
            assert stat.S_IMODE(entry.external_attr >> 16) == 0o644
            assert entry.compress_type == zipfile.ZIP_DEFLATED
        assert zipped.read("alpha/references/é.md").decode() == "日本語\r\n"
    parsed = packages.parse_upload([("download.zip", first)])
    assert parsed["rejected"] == []
    assert {bundle["name"] for bundle in parsed["bundles"]} == {"alpha", "beta"}


def test_lock_has_null_label_no_timestamps_and_default_skipped():
    entry = {**skill(), "label": None}
    with zipfile.ZipFile(
        io.BytesIO(packages.build_zip([entry], origin="local"))
    ) as zipped:
        lock = json.loads(zipped.read(".futureagi-skills.lock.json"))
    assert set(lock) == {"schema", "origin", "skills", "skipped"}
    assert lock["skipped"] == []
    assert lock["skills"][0]["label"] is None


@pytest.mark.parametrize(
    "path",
    [
        "../evil.md",
        "/evil.md",
        "C:/evil.md",
        "a\\evil.md",
        "a//evil.md",
        "a/./evil.md",
        "a/../evil.md",
        "trailing./evil.md",
        "trailing /evil.md",
        "x" * 256,
        "/".join(["a"] * 9),
    ],
)
def test_unsafe_member_paths_are_not_normalized_or_read(path):
    result = upload([("SKILL.md", SKILL), (path, b"text")])
    rejected(result, path, "path_invalid")
    assert [file["path"] for file in result["bundles"][0]["files"]] == ["SKILL.md"]


@pytest.mark.parametrize(
    "kind,reason",
    [
        (stat.S_IFLNK, "symlink"),
        (stat.S_IFIFO, "special_file"),
        (stat.S_IFCHR, "special_file"),
    ],
)
def test_links_and_special_files(kind, reason):
    entry = zipfile.ZipInfo("special.txt")
    entry.create_system = 3
    entry.external_attr = (kind | 0o777) << 16
    result = upload([("SKILL.md", SKILL), (entry, b"target")])
    rejected(result, "special.txt", reason)


@pytest.mark.parametrize("flag", [1, 64])
def test_encrypted_member_rejected_before_open(monkeypatch, flag):
    raw = bytearray(archive([("SKILL.md", SKILL)]))
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, 6, flag)
    struct.pack_into("<H", raw, central + 8, flag)
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda *args, **kwargs: pytest.fail("Encrypted files must not be opened"),
    )
    rejected(
        packages.parse_upload([("encrypted.zip", bytes(raw))]), "SKILL.md", "encrypted"
    )


@pytest.mark.parametrize(
    "path,data",
    [("nested.zip", b"not even a valid zip"), ("disguised.txt", b"PK\x03\x04anything")],
)
def test_nested_zip_is_reported_not_opened(path, data):
    rejected(upload([("SKILL.md", SKILL), (path, data)]), path, "nested_zip")


def test_case_insensitive_duplicates_remove_both_members():
    result = upload(
        [("SKILL.md", SKILL), ("references/A.md", b"one"), ("references/a.MD", b"two")]
    )
    rejected(result, "references/A.md", "path_duplicate")
    rejected(result, "references/a.MD", "path_duplicate")
    assert [file["path"] for file in result["bundles"][0]["files"]] == ["SKILL.md"]


def test_exact_duplicate_entries_remove_both_members():
    with pytest.warns(UserWarning, match="Duplicate name"):
        raw = archive([("SKILL.md", SKILL), ("SKILL.md", SKILL)])
    result = packages.parse_upload([("duplicate.zip", raw)])
    assert result["bundles"] == []
    assert result["rejected"] == [{"path": "SKILL.md", "reason": "path_duplicate"}] * 2


def test_too_many_entries_includes_directories_and_skipped_metadata(monkeypatch):
    raw = archive([(f"__MACOSX/{i}/", b"") for i in range(1001)])
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda *args, **kwargs: pytest.fail("Count limit must precede reads"),
    )
    with pytest.raises(SkillsHubError) as exc:
        packages.parse_upload([("entries.zip", raw)])
    assert (exc.value.code, exc.value.status) == ("payload_too_large", 413)


def test_entry_budget_is_shared_across_uploads():
    raw = archive([(f"folder-{i}/", b"") for i in range(501)])
    with pytest.raises(SkillsHubError, match="1,000"):
        packages.parse_upload([("one.zip", raw), ("two.zip", raw)])


def test_exact_entry_limit_is_accepted():
    result = upload([("SKILL.md", SKILL)] + [(f"folder-{i}/", b"") for i in range(999)])
    assert len(result["bundles"]) == 1
    assert result["rejected"] == []


def test_oversize_entry_is_rejected_before_read(monkeypatch):
    raw = archive([("large.txt", b"x" * (limits.MAX_FILE_BYTES + 1))])
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda *args, **kwargs: pytest.fail("Oversize file must not be opened"),
    )
    rejected(packages.parse_upload([("large.zip", raw)]), "large.txt", "file_too_large")


def test_exact_file_size_is_accepted():
    result = upload([("SKILL.md", SKILL), ("large.txt", b"x" * limits.MAX_FILE_BYTES)])
    assert result["rejected"] == []
    assert len(result["bundles"][0]["files"][1]["content"]) == limits.MAX_FILE_BYTES


def test_compressed_upload_limit():
    result = packages.parse_upload([("large.zip", b"x" * (limits.MAX_ZIP_BYTES + 1))])
    rejected(result, "large.zip", "compressed_too_large")


def test_uncompressed_total_is_checked_before_read(monkeypatch):
    raw = bytearray(archive([("large.txt", b"x")]))
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<I", raw, central + 24, limits.MAX_UNZIPPED_BYTES + 1)
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda *args, **kwargs: pytest.fail("Total cap must precede reads"),
    )
    with pytest.raises(SkillsHubError, match="20 MiB"):
        packages.parse_upload([("large.zip", bytes(raw))])


def test_compression_bomb_ratio_is_checked_before_read(monkeypatch):
    raw = archive([("bomb.txt", b"x" * 100000)], compression=zipfile.ZIP_DEFLATED)
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda *args, **kwargs: pytest.fail("Bombs must not be opened"),
    )
    rejected(
        packages.parse_upload([("bomb.zip", raw)]), "bomb.txt", "compression_ratio"
    )


def test_reads_are_capped_to_declared_size_plus_one(monkeypatch):
    original_open = zipfile.ZipFile.open
    reads = []

    class CappedReader:
        def __init__(self, source, expected):
            self.source, self.expected = source, expected

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.source.close()

        def read(self, size=-1):
            assert size == self.expected + 1
            reads.append(size)
            return self.source.read(size)

    def open_capped(zipped, entry, *args, **kwargs):
        return CappedReader(
            original_open(zipped, entry, *args, **kwargs), entry.file_size
        )

    raw = archive([("SKILL.md", SKILL)])
    monkeypatch.setattr(zipfile.ZipFile, "open", open_capped)
    result = packages.parse_upload([("skill.zip", raw)])
    assert result["rejected"] == []
    assert reads == [len(SKILL) + 1]


@pytest.mark.parametrize("content", [b"\xff", b"a\0b", b"a\x1bb"])
def test_binary_files_are_rejected(content):
    rejected(
        upload([("SKILL.md", SKILL), ("binary.txt", content)]),
        "binary.txt",
        "file_not_text",
    )


def test_metadata_skipped_but_other_hidden_files_preserved():
    skipped = [
        "__MACOSX/._SKILL.md",
        ".DS_Store",
        "folder/.DS_Store",
        ".git/config",
        "example/.git/config",
    ]
    result = upload(
        [("SKILL.md", SKILL), (".hidden", b"keep")]
        + [(path, b"\xff") for path in skipped]
    )
    assert set(result["skipped"]) == set(skipped)
    assert result["rejected"] == []
    assert {file["path"] for file in result["bundles"][0]["files"]} == {
        "SKILL.md",
        ".hidden",
    }


def test_outermost_skill_wins_without_losing_inner_files():
    result = upload(
        [
            ("one/SKILL.md", SKILL),
            ("one/nested/SKILL.md", SKILL),
            ("one/nested/context.md", b"text"),
            ("two/SKILL.md", SKILL),
        ]
    )
    assert [bundle["root"] for bundle in result["bundles"]] == ["one", "two"]
    rejected(result, "one/nested/SKILL.md", "ignored_nested")
    assert {file["path"] for file in result["bundles"][0]["files"]} == {
        "SKILL.md",
        "nested/SKILL.md",
        "nested/context.md",
    }


def test_root_skill_contains_all_nested_files():
    result = upload([("SKILL.md", SKILL), ("nested/SKILL.md", SKILL)])
    assert len(result["bundles"]) == 1
    assert result["bundles"][0]["root"] == ""
    rejected(result, "nested/SKILL.md", "ignored_nested")


def test_bundle_fallback_names_and_stable_keys():
    raw = archive(
        [
            ("fallback/SKILL.md", b"---\nname: INVALID\n---\ntext"),
            ("valid/SKILL.md", SKILL),
        ]
    )
    first = packages.parse_upload([("example.zip", raw)])
    second = packages.parse_upload([("example.zip", raw)])
    assert first == second
    assert [bundle["name"] for bundle in first["bundles"]] == ["fallback", "example"]
    assert first["bundles"][0]["checks"]["summary"]["errors"] > 0
    assert len({bundle["key"] for bundle in first["bundles"]}) == 2


def test_mixed_zip_and_loose_files():
    result = packages.parse_upload(
        [
            ("one.zip", archive([("SKILL.md", SKILL)])),
            ("loose/SKILL.md", SKILL),
            ("loose/references/policy.md", b"text"),
            ("loose/.DS_Store", b"\xff"),
        ]
    )
    assert len(result["bundles"]) == 2
    assert result["skipped"] == ["loose/.DS_Store"]
    assert result["rejected"] == []


def test_loose_duplicates_and_bad_paths():
    result = packages.parse_upload(
        [
            ("SKILL.md", SKILL),
            ("dup.txt", b"one"),
            ("DUP.txt", b"two"),
            ("../bad", b"bad"),
        ]
    )
    rejected(result, "dup.txt", "path_duplicate")
    rejected(result, "DUP.txt", "path_duplicate")
    rejected(result, "../bad", "path_invalid")


def test_loose_file_count_limit():
    with pytest.raises(SkillsHubError, match="200"):
        packages.parse_upload([(f"{i}.txt", b"x") for i in range(201)])


def test_bundle_file_count_is_reported_in_checks():
    result = upload([("SKILL.md", SKILL)] + [(f"{i}.txt", b"x") for i in range(200)])
    assert "too_many_files" in {
        item["code"] for item in result["bundles"][0]["checks"]["items"]
    }


def test_bundle_total_limit_is_reported_in_checks():
    result = upload(
        [("SKILL.md", SKILL)]
        + [(f"{i}.txt", b"x" * limits.MAX_FILE_BYTES) for i in range(4)]
    )
    assert "bundle_too_large" in {
        item["code"] for item in result["bundles"][0]["checks"]["items"]
    }


def test_fifty_skills_maximum():
    entries = [(f"skill-{i}/SKILL.md", SKILL) for i in range(50)]
    assert len(upload(entries)["bundles"]) == 50
    with pytest.raises(SkillsHubError, match="50"):
        upload(entries + [("overflow/SKILL.md", SKILL)])


def test_invalid_archive_and_missing_root():
    rejected(
        packages.parse_upload([("invalid.zip", b"not a zip")]),
        "invalid.zip",
        "archive_invalid",
    )
    rejected(upload([("readme.md", b"text")]), "0:skills.zip", "root_missing")
    assert packages.parse_upload([]) == {"bundles": [], "rejected": [], "skipped": []}


def test_bad_crc_is_reported():
    raw = bytearray(archive([("SKILL.md", SKILL)]))
    raw[raw.index(SKILL)] ^= 1
    rejected(
        packages.parse_upload([("crc.zip", bytes(raw))]), "SKILL.md", "archive_invalid"
    )


@pytest.mark.parametrize(
    "compression", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA]
)
def test_corrupt_compressed_stream_is_reported(compression):
    raw = bytearray(archive([("SKILL.md", SKILL)], compression=compression))
    start = 30 + len("SKILL.md")
    central = raw.index(b"PK\x01\x02")
    raw[start:central] = b"\xff" * (central - start)
    rejected(
        packages.parse_upload([("corrupt.zip", bytes(raw))]),
        "SKILL.md",
        "archive_invalid",
    )


def test_malformed_utf8_zip_filename_is_reported_not_a_server_error():
    raw = bytearray(archive([("SKILL.md", SKILL)]))
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<H", raw, 6, 0x800)
    struct.pack_into("<H", raw, central + 8, 0x800)
    raw[30] = 0xFF
    raw[central + 46] = 0xFF
    rejected(
        packages.parse_upload([("bad-name.zip", bytes(raw))]),
        "bad-name.zip",
        "archive_invalid",
    )


def test_nul_zip_filename_is_rejected_using_original_name():
    raw = archive([("SKILL.md", SKILL), ("nulXname", b"text")]).replace(
        b"nulXname", b"nul\0name"
    )
    rejected(packages.parse_upload([("nul.zip", raw)]), "nul\0name", "path_invalid")


@pytest.mark.parametrize(
    "mutation", ["name", "path", "duplicate", "text", "file_size", "count", "total"]
)
def test_download_revalidates_unsafe_or_oversized_inputs(mutation):
    entry = skill()
    if mutation == "name":
        entry["name"] = "../outside"
    elif mutation == "path":
        entry["files"][0]["path"] = "../outside"
    elif mutation == "duplicate":
        entry["files"].append({"path": "skill.MD", "content": "text"})
    elif mutation == "text":
        entry["files"][0]["content"] = "\0"
    elif mutation == "file_size":
        entry["files"][0]["content"] = "x" * (limits.MAX_FILE_BYTES + 1)
    elif mutation == "count":
        entry["files"] = [{"path": f"{i}.txt", "content": "x"} for i in range(201)]
    else:
        entry["files"] = [
            {"path": f"{i}.txt", "content": "x" * limits.MAX_FILE_BYTES}
            for i in range(5)
        ]
    with pytest.raises(SkillsHubError):
        packages.build_zip([entry], origin="local")


def test_download_skill_count_and_duplicate_names():
    with pytest.raises(SkillsHubError, match="50"):
        packages.build_zip([skill(f"skill-{i}") for i in range(51)], origin="local")
    with pytest.raises(SkillsHubError, match="unique"):
        packages.build_zip([skill(), skill()], origin="local")


@pytest.mark.parametrize("parent", ["references", "REFERENCES", "Straße"])
@pytest.mark.parametrize("reverse", [False, True])
def test_file_directory_collision_is_invalid_on_import_and_export(parent, reverse):
    child = "STRASSE/guide.md" if parent == "Straße" else "references/guide.md"
    extra = [{"path": parent, "content": "File"}, {"path": child, "content": "Guide"}]
    entry = skill()
    entry["files"] = entry["files"][:1] + (extra[::-1] if reverse else extra)
    members = [(file["path"], file["content"].encode()) for file in entry["files"]]
    for parsed in (upload(members), packages.parse_upload(members)):
        checks = parsed["bundles"][0]["checks"]
        assert checks["summary"]["errors"] > 0
        assert any(
            item["code"] == "path_invalid" and item["severity"] == "error"
            for item in checks["items"]
        )
    with pytest.raises(SkillsHubError) as error:
        packages.build_zip([entry], origin="local")
    assert (error.value.code, error.value.status) == ("import_invalid", 400)


def test_export_rejects_unextractable_bundle_and_corrected_bundle_installs(tmp_path):
    entry = skill()
    entry["files"].append({"path": "references", "content": "Not a directory"})
    with pytest.raises(SkillsHubError, match="directory"):
        packages.build_zip([entry], origin="local")
    entry["files"][-1]["path"] = "references.txt"
    archive_path = tmp_path / "skills.zip"
    archive_path.write_bytes(packages.build_zip([entry], origin="local"))
    destination = tmp_path / "installed"
    subprocess.run(
        ["unzip", "-oq", str(archive_path), "-d", str(destination)],
        check=True,
        timeout=10,
    )
    for file in entry["files"]:
        assert (destination / entry["name"] / file["path"]).read_bytes() == file[
            "content"
        ].encode()
