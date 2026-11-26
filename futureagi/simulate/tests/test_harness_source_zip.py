import io
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from simulate.services.harness_source_zip import unpack_source_zip
from simulate.services.hosted_harness import HostedHarnessError


def source_zip(entries):
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, contents in entries:
            archive.writestr(path, contents)
    return SimpleUploadedFile("repo.zip", body.getvalue())


def test_zip_matches_directory_layout_and_excludes_secrets():
    archive = source_zip(
        [
            ("repo/src/agent.py", b"print(1)"),
            ("repo/.env", b"private"),
            ("repo/.git/config", b"private"),
            ("repo/.env.example", b"EXAMPLE="),
        ]
    )
    with unpack_source_zip(archive, 4096, 2) as (files, paths, name):
        assert name == "repo"
        assert paths == ["src/agent.py", ".env.example"]
        assert [file.read() for file in files] == [b"print(1)", b"EXAMPLE="]


@pytest.mark.parametrize(
    "entry", ["../escape", "/absolute", "C:/windows", "repo/../escape"]
)
def test_zip_rejects_invalid_paths(entry):
    with pytest.raises(HostedHarnessError, match="invalid repository path"):
        with unpack_source_zip(source_zip([(entry, "x")]), 4096, 10):
            pytest.fail("Invalid archive accepted")


def test_zip_bounds_uncompressed_bytes():
    with pytest.raises(HostedHarnessError, match="unpacked ZIP"):
        with unpack_source_zip(source_zip([("agent.py", "a" * 8192)]), 4096, 10):
            pytest.fail("Oversized archive accepted")


def test_zip_refuses_conflicting_file_and_directory():
    archive = source_zip([("repo/src", b"file"), ("repo/src/main.py", b"code")])
    with pytest.raises(HostedHarnessError, match="conflicting source paths"):
        with unpack_source_zip(archive, 4096, 10):
            pytest.fail("Conflicting paths accepted")
