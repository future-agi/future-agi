"""Validate a repository ZIP globally, then read exactly one reviewed text bundle.

The generated archive root is not a signature or a source-verification claim.
No filesystem extraction, dependency fetching or executable interpretation occurs.
"""

from __future__ import annotations

import io
import lzma
import posixpath
import re
import stat
import zipfile
import zlib
from collections import Counter

from model_hub.services.skills_hub import limits
from model_hub.services.skills_hub.checks import path_is_valid, run_checks
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import (
    _decode_file,
    _entry_rejection,
    _ignored,
    file_sha256,
    manifest_digest,
)

# Identify a complete grant, not two phrases inside potentially modified terms.
_MIT_GRANT = """Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""
_NOTICE = re.compile(r"^(?:licen[sc]e|copying|notice)(?:[._-].*)?$", re.I)
_ROOT = re.compile(r"^[A-Za-z0-9_.-]+-[0-9a-f]{7,40}$")
_ARCHIVE_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    EOFError,
    ValueError,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    lzma.LZMAError,
    OSError,
)


def _invalid(reason: str) -> SkillsHubError:
    return SkillsHubError(
        "github_bundle_invalid",
        "The repository archive cannot be imported safely.",
        status=422,
        detail={"reason": reason},
    )


def _large() -> SkillsHubError:
    return SkillsHubError(
        "github_archive_too_large",
        "This repository exceeds the archive limit. Selecting a narrower path does not reduce the downloaded archive.",
        status=413,
    )


def validate_repository_path(path: str) -> bool:
    """Repository locations may be deeper than a selected bundle-relative path."""
    return isinstance(path, str) and (
        path == ""
        or (
            len(path) <= 1024
            and len(path.split("/")) <= 32
            and not any(
                part in {"", ".", ".."} or part.endswith((" ", "."))
                for part in path.split("/")
            )
            and not re.search(r"[\\%\x00-\x1f\x7f]", path)
        )
    )


def _members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries = archive.infolist()
    if (
        len(entries) > limits.MAX_ZIP_ENTRIES
        or sum(item.file_size for item in entries) > limits.MAX_UNZIPPED_BYTES
    ):
        raise _large()
    if not entries:
        raise _invalid("empty_archive")
    counts = Counter(entry.orig_filename.rstrip("/").casefold() for entry in entries)
    roots = set()
    files = set()
    spellings = {}
    members = {}
    for entry in entries:
        raw = entry.orig_filename
        path = raw[:-1] if entry.is_dir() else raw
        if not validate_repository_path(path) or not path:
            raise _invalid("path_invalid")
        parts = path.split("/")
        for index in range(1, len(parts) + 1):
            spelling = "/".join(parts[:index])
            previous = spellings.setdefault(spelling.casefold(), spelling)
            if previous != spelling:
                raise _invalid("path_case_collision")
        roots.add(parts[0])
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if mode == stat.S_IFDIR and not entry.is_dir():
            raise _invalid("directory_metadata_mismatch")
        if entry.is_dir() and (mode not in (0, stat.S_IFDIR) or entry.file_size):
            raise _invalid("directory_metadata_mismatch")
        reason = _entry_rejection(entry, path, counts)
        if reason and reason != "nested_zip":
            raise _invalid(reason)
        # An unrelated archive payload is never opened. A selected one is
        # rejected by the shared decoder, like any other unsupported asset.
        if not entry.is_dir():
            if len(parts) == 1:
                raise _invalid("generated_root_missing")
            files.add(path.casefold())
            members["/".join(parts[1:])] = entry
    if len(roots) != 1 or not _ROOT.fullmatch(next(iter(roots))):
        raise _invalid("generated_root_mismatch")
    for entry in entries:
        parts = entry.orig_filename.rstrip("/").casefold().split("/")
        if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
            raise _invalid("file_directory_collision")
    return members


def _roots(members: dict, path: str) -> list[str]:
    roots = []
    for name in sorted(members, key=lambda item: (item.count("/"), item)):
        if _ignored(name) or posixpath.basename(name) != "SKILL.md":
            continue
        root = posixpath.dirname(name)
        if path and root != path and not root.startswith(path + "/"):
            continue
        if not any(not outer or root.startswith(outer + "/") for outer in roots):
            roots.append(root)
    return roots


def _read(archive: zipfile.ZipFile, entry: zipfile.ZipInfo, budget: list[int]) -> str:
    with archive.open(entry) as stream:
        raw = stream.read(min(entry.file_size, limits.MAX_FILE_BYTES) + 1)
    budget[0] += len(raw)
    if budget[0] > limits.MAX_UNZIPPED_BYTES:
        raise _large()
    if len(raw) != entry.file_size:
        raise _invalid("size_mismatch")
    rejected = []
    text = _decode_file(entry.orig_filename, raw, rejected)
    if text is None:
        raise _invalid(rejected[0]["reason"])
    if text.startswith("version https://git-lfs.github.com/spec/v1"):
        raise _invalid("lfs_pointer")
    return text


def _license(notices: list[dict], checks: dict) -> dict:
    declared = (checks["frontmatter"] or {}).get("license")
    declared = declared if isinstance(declared, str) else None
    texts = "\n".join(item["text"] for item in notices)
    restricted = re.search(
        r"non[- ]commercial|no derivatives|may not (?:copy|redistribute|retain)|prohibit(?:ed|s).*distribution|outside (?:of )?the services|proprietary|source.available",
        texts,
        re.I,
    )
    identifiers = []
    for notice in notices:
        text = notice["text"]
        normalized = " ".join(text.split())
        if normalized in {
            " ".join(_MIT_GRANT.split()),
            "MIT License " + " ".join(_MIT_GRANT.split()),
        }:
            identifiers.append("MIT")
        elif not posixpath.basename(notice["path"]).lower().startswith("notice"):
            identifiers.append("custom")
    if restricted or (
        declared
        and re.search(r"proprietary|non.?commercial|CC-BY-NC|CC-BY-ND", declared, re.I)
    ):
        status = "restricted"
    elif "custom" in identifiers or (
        declared and identifiers and set(identifiers) != {declared}
    ):
        status = "custom"
    elif identifiers:
        status = "identified"
    else:
        status = "unknown"
    return {
        "status": status,
        "declared": declared,
        "files": notices,
        "requires_admin_review": status in {"unknown", "custom"},
    }


def parse_repository_zip(raw: bytes, *, path: str) -> dict:
    """Return discovery or one complete bundle; a bad selected member blocks it."""
    if not validate_repository_path(path):
        raise SkillsHubError(
            "github_source_invalid",
            "Use a relative repository folder path.",
            status=400,
        )
    if len(raw) > limits.MAX_ZIP_BYTES:
        raise _large()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = _members(archive)
            roots = _roots(members, path)
            if len(roots) != 1:
                return {
                    "roots": roots,
                    "outcome": "needs_path" if roots else "source_missing",
                    "bundle": None,
                    "license": {
                        "status": "unknown",
                        "declared": None,
                        "files": [],
                        "requires_admin_review": True,
                    },
                }
            root = roots[0]
            prefix = root + "/" if root else ""
            selected = {
                name: entry
                for name, entry in members.items()
                if name.startswith(prefix) and not _ignored(name)
            }
            if len(selected) > limits.MAX_FILES:
                raise _invalid("too_many_files")
            budget = [0]
            files = []
            notices = []
            for name, entry in sorted(selected.items()):
                relative = name[len(prefix) :]
                if not path_is_valid(relative):
                    raise _invalid("path_invalid")
                text = _read(archive, entry, budget)
                files.append({"path": relative, "content": text})
                if _NOTICE.fullmatch(posixpath.basename(name)):
                    notices.append(
                        {
                            "path": name,
                            "sha256": file_sha256(text),
                            "url_at_commit": "",
                            "text": text,
                        }
                    )
            # Ancestor LICENSE/NOTICE evidence is retained in explicit bundle paths.
            ancestors = {""}
            parts = root.split("/") if root else []
            ancestors.update(
                "/".join(parts[:index]) for index in range(1, len(parts) + 1)
            )
            for name, entry in sorted(members.items()):
                if (
                    name in selected
                    or posixpath.dirname(name) not in ancestors
                    or not _NOTICE.fullmatch(posixpath.basename(name))
                ):
                    continue
                text = _read(archive, entry, budget)
                sha = file_sha256(text)
                destination = f"references/upstream-notices/{file_sha256(name)[:12]}-{posixpath.basename(name)}"
                if any(
                    item["path"].casefold() == destination.casefold() for item in files
                ):
                    raise _invalid("notice_path_collision")
                files.append({"path": destination, "content": text})
                notices.append(
                    {"path": name, "sha256": sha, "url_at_commit": "", "text": text}
                )
            files.sort(key=lambda item: item["path"])
            checks = run_checks(files)
            if checks["summary"]["errors"]:
                raise SkillsHubError(
                    "github_bundle_invalid",
                    "Fix the bundle's structural errors before importing.",
                    status=422,
                    detail=checks,
                )
            license_evidence = _license(notices, checks)
            return {
                "roots": roots,
                "outcome": "blocked"
                if license_evidence["status"] == "restricted"
                else "ready",
                "bundle": {
                    "name": checks["frontmatter"]["name"],
                    "root": root,
                    "digest": manifest_digest(files),
                    "files": files,
                    "checks": checks,
                },
                "license": license_evidence,
            }
    except _ARCHIVE_ERRORS as exc:
        raise _invalid("archive_invalid") from exc
