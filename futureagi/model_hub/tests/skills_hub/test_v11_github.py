"""Offline synthetic GitHub URL, transport, archive and licensing regressions."""

import io
import json
import socket
import stat
import time
import zipfile
from unittest.mock import Mock

import pytest
from django.core.cache import cache

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.github import parse_github_source
from model_hub.services.skills_hub.v11.repository_zip import (
    _MIT_GRANT,
    parse_repository_zip,
)
from tfc.utils import github_public
from tfc.utils.ssrf_guard import SsrfResponse, _resolve_pinned_ip

SHA = "a" * 40
ROOT = "synthetic-" + SHA
SKILL = "---\nname: help-desk\ndescription: Use when a supplied support policy needs careful review.\n---\nRead the supplied policy.\n"
MIT = _MIT_GRANT


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Live sockets are forbidden in GitHub tests")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    cache.clear()
    monkeypatch.setattr(github_public, "_cache_ready", lambda: None)


def archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as result:
        for name, body in entries:
            if isinstance(name, zipfile.ZipInfo):
                result.writestr(name, body)
            else:
                result.writestr(ROOT + "/" + name, body)
    return output.getvalue()


def test_repository_default_and_explicit_tree_fields():
    default = parse_github_source(
        url="https://github.com/example/synthetic", path=None, ref=None
    )
    assert (default.repo, default.ref, default.ref_kind, default.path) == (
        "example/synthetic",
        "HEAD",
        "default",
        "",
    )
    source = parse_github_source(
        url="https://github.com/example/synthetic/tree/feature/support/skills/.curated/help",
        path="skills/.curated/help",
        ref="feature/support",
    )
    assert source.path == "skills/.curated/help"
    assert source.ref == "feature/support"
    blob = parse_github_source(
        url="https://github.com/example/synthetic/blob/main/help/SKILL.md",
        path="help",
        ref="main",
    )
    assert blob.path == "help"
    assert (
        parse_github_source(
            url="https://github.com/example/synthetic", path="", ref="refs/tags/v1"
        ).ref_kind
        == "tag"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/repo",
        "https://github.com.evil.test/example/repo",
        "https://github.com:443/example/repo",
        "https://user@github.com/example/repo",
        "https://127.0.0.1/example/repo",
        "https://github.com/example/repo?url=https://evil.test",
        "https://github.com/example/../repo",
        "https://github.com/example%2frepo/name",
        "https://github.com/example/repo/tree/main/%252e%252e",
        "https://github.com/example/repo\\evil",
        "https://github.com/example/repo\n",
        "https://github.com/example/repo/issues/1",
    ],
)
def test_rejects_hostile_urls(url):
    with pytest.raises(SkillsHubError) as exc:
        parse_github_source(url=url, path=None, ref=None)
    assert exc.value.code == "github_source_invalid"


def test_tree_requires_explicit_boundary_not_network_guess():
    with pytest.raises(SkillsHubError) as exc:
        parse_github_source(
            url="https://github.com/example/repo/tree/feature/topic/help",
            path=None,
            ref=None,
        )
    assert exc.value.code == "github_source_ambiguous"
    assert exc.value.detail["suggested_ref"] == "feature"


@pytest.mark.parametrize(
    "ref", ["../main", "main%2fother", "a" * 39, "A" * 40, "refs//heads/main"]
)
def test_rejects_invalid_refs(ref):
    with pytest.raises(SkillsHubError):
        parse_github_source(url="https://github.com/example/repo", path="", ref=ref)


def test_multi_root_then_selected_category_preserves_inherited_notices():
    raw = archive(
        [
            ("LICENSE", MIT),
            ("skills/.curated/help/SKILL.md", SKILL),
            ("skills/.curated/help/policy.md", "Local policy"),
            ("skills/other/SKILL.md", SKILL),
        ]
    )
    discovery = parse_repository_zip(raw, path="skills")
    assert discovery["outcome"] == "needs_path"
    assert discovery["roots"] == ["skills/other", "skills/.curated/help"]
    assert discovery["bundle"] is None
    selected = parse_repository_zip(raw, path="skills/.curated/help")
    assert selected["outcome"] == "ready"
    assert selected["license"]["status"] == "identified"
    files = selected["bundle"]["files"]
    assert any(
        row["path"].startswith("references/upstream-notices/") and row["content"] == MIT
        for row in files
    )
    assert all("other" not in row["path"] for row in files)
    assert (
        parse_repository_zip(raw, path="skills/.curated/hel")["outcome"]
        == "source_missing"
    )


def test_unrelated_binary_and_nested_zip_payloads_are_not_opened():
    raw = archive(
        [
            ("safe/SKILL.md", SKILL),
            ("unrelated/asset.bin", b"\xff\x00"),
            ("unrelated/archive.zip", b"PK\x03\x04not-a-real-zip"),
        ]
    )
    result = parse_repository_zip(raw, path="safe")
    assert result["outcome"] == "ready"
    assert [item["path"] for item in result["bundle"]["files"]] == ["SKILL.md"]


def test_discovery_does_not_open_unselected_payloads(monkeypatch):
    raw = archive([("a/SKILL.md", SKILL), ("b/SKILL.md", SKILL)])
    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        Mock(side_effect=AssertionError("No payload on discovery")),
    )
    assert parse_repository_zip(raw, path="")["outcome"] == "needs_path"


@pytest.mark.parametrize(
    "name",
    ["../evil", "/absolute", "bad\\name", "a/../evil", "a/./evil", "bad\x00name"],
)
def test_unsafe_unrelated_metadata_blocks_selected_bundle(name):
    info = zipfile.ZipInfo(ROOT + "/" + name)
    # ZipInfo truncates NUL on creation; setting orig/filename preserves this fixture.
    if "\x00" in name:
        info.filename = ROOT + "/" + name
    raw = archive([("safe/SKILL.md", SKILL), (info, "evil")])
    with pytest.raises(SkillsHubError) as exc:
        parse_repository_zip(raw, path="safe")
    assert exc.value.code == "github_bundle_invalid"


@pytest.mark.parametrize("kind", [stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO])
def test_global_links_and_devices_block(kind):
    info = zipfile.ZipInfo(ROOT + "/unrelated/link")
    info.create_system = 3
    info.external_attr = (kind | 0o644) << 16
    with pytest.raises(SkillsHubError):
        parse_repository_zip(
            archive([("safe/SKILL.md", SKILL), (info, "elsewhere")]), path="safe"
        )


@pytest.mark.parametrize(
    "entries",
    [
        [("a/SKILL.md", SKILL), ("a/POLICY.md", "x"), ("a/policy.md", "y")],
        [("a/SKILL.md", SKILL), ("a/policy", "x"), ("a/policy/child", "y")],
        [("a/SKILL.md", SKILL), ("a/file.bin", b"\xff\x00")],
        [
            ("a/SKILL.md", SKILL),
            ("a/data", "version https://git-lfs.github.com/spec/v1\noid sha256:test"),
        ],
        [("a/SKILL.md", SKILL), ("a/archive.zip", b"PK\x03\x04")],
    ],
)
def test_bad_member_never_produces_partial_bundle(entries):
    with pytest.raises(SkillsHubError) as exc:
        parse_repository_zip(archive(entries), path="a")
    assert exc.value.code == "github_bundle_invalid"


def test_encryption_ratio_and_selected_crc_are_rejected():
    raw = bytearray(archive([("SKILL.md", SKILL)]))
    central = raw.index(b"PK\x01\x02")
    raw[central + 8] |= 1
    with pytest.raises(SkillsHubError):
        parse_repository_zip(bytes(raw), path="")
    with pytest.raises(SkillsHubError) as exc:
        parse_repository_zip(
            archive([("SKILL.md", SKILL), ("huge.txt", "a" * (512 * 1024))]), path=""
        )
    assert exc.value.detail["reason"] == "compression_ratio"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as result:
        result.writestr(ROOT + "/SKILL.md", SKILL)
    broken = bytearray(output.getvalue())
    index = broken.index(SKILL.encode())
    broken[index] ^= 1
    with pytest.raises(SkillsHubError) as exc:
        parse_repository_zip(bytes(broken), path="")
    assert exc.value.code == "github_bundle_invalid"


def test_whole_archive_limits_apply_to_narrow_path():
    with pytest.raises(SkillsHubError) as exc:
        parse_repository_zip(
            archive(
                [("safe/SKILL.md", SKILL)] + [(f"other/{n}", "x") for n in range(1000)]
            ),
            path="safe",
        )
    assert exc.value.code == "github_archive_too_large"


def test_root_mismatch_and_crc_are_rejected():
    other = zipfile.ZipInfo("other-" + SHA + "/file")
    with pytest.raises(SkillsHubError):
        parse_repository_zip(archive([("SKILL.md", SKILL), (other, "x")]), path="")
    with pytest.raises(SkillsHubError):
        parse_repository_zip(archive([("SKILL.md", SKILL)])[:-25], path="")


@pytest.mark.parametrize(
    "text,status",
    [
        ("Custom conditions for use", "custom"),
        ("Do not retain outside the Services", "restricted"),
    ],
)
def test_license_is_not_inferred_from_public_visibility(text, status):
    result = parse_repository_zip(
        archive([("SKILL.md", SKILL), ("LICENSE", text)]), path=""
    )
    assert result["license"]["status"] == status
    assert result["outcome"] == ("blocked" if status == "restricted" else "ready")


def test_modified_permissive_text_requires_admin_review():
    result = parse_repository_zip(
        archive(
            [
                ("SKILL.md", SKILL),
                (
                    "LICENSE",
                    MIT + "\nAdditional approval is required for this package.",
                ),
            ]
        ),
        path="",
    )
    assert result["license"]["status"] == "custom"
    assert result["license"]["requires_admin_review"] is True


def restricted_transport(response, seen):
    def fetch(
        url,
        *,
        method,
        timeout,
        max_bytes,
        headers,
        allowed_hosts,
        allowed_schemes,
        allowed_ports,
        deadline,
        allowed_urls,
        allowed_content_encodings,
    ):
        seen.append((url, headers))
        assert allowed_hosts == {"api.github.com", "codeload.github.com"}
        assert allowed_schemes == {"https"} and allowed_ports == {443}
        assert allowed_urls == {url} and deadline > time.monotonic()
        assert allowed_content_encodings == {"identity", ""}
        assert not ({"Authorization", "Cookie", "Proxy-Authorization"} & headers.keys())
        return response

    return fetch


def test_sha_cache_pair_and_anonymous_conditional_request(monkeypatch):
    seen = []
    monkeypatch.setattr(
        github_public,
        "safe_fetch",
        restricted_transport(
            SsrfResponse(200, {"ETag": '"synthetic"'}, SHA.encode(), ""), seen
        ),
    )
    args = {
        "repo": "example/repo",
        "ref": "main",
        "organization_id": "org-1",
        "deadline": time.monotonic() + 60,
    }
    assert github_public.resolve_sha(**args)["sha"] == SHA
    assert github_public.resolve_sha(**args)["sha"] == SHA
    assert len(seen) == 1
    key = github_public._key("sha", "example/repo:main")
    cached = cache.get(key)
    cached["fresh_until"] = 0
    cache.set(key, cached)
    monkeypatch.setattr(
        github_public,
        "safe_fetch",
        restricted_transport(SsrfResponse(304, {}, b"", ""), seen),
    )
    assert github_public.resolve_sha(**args)["sha"] == SHA
    assert seen[-1][1]["If-None-Match"] == '"synthetic"'
    assert cache.get(github_public._hour_key()) == 2
    cache.delete(key)
    with pytest.raises(SkillsHubError) as exc:
        github_public.resolve_sha(**args)
    assert exc.value.code == "github_source_unavailable"


def test_new_sha_without_etag_does_not_reuse_the_old_pair():
    result = github_public.sha_response(
        status_code=200,
        body=("b" * 40).encode(),
        etag=None,
        cached={"sha": SHA, "etag": '"old-commit"'},
    )
    assert result == {"sha": "b" * 40, "etag": None}
    with pytest.raises(SkillsHubError):
        github_public.sha_response(status_code=304, body=b"", etag=None, cached=result)


def test_shared_harness_budget_and_organization_cap():
    cache.set(github_public._hour_key(), 49)
    assert github_public.take_call(0, "skills", "org-1")
    assert not github_public.take_call(0, "compare", "org-2")
    cache.clear()
    assert all(github_public.take_call(0, "skills", "org-1") for _ in range(3))
    assert not github_public.take_call(0, "skills", "org-1")
    assert github_public.take_call(0, "skills", "org-2")


def test_missing_transport_restrictions_fail_closed(monkeypatch):
    monkeypatch.setattr(
        github_public,
        "safe_fetch",
        lambda url: pytest.fail("Must not call unrestricted transport"),
    )
    with pytest.raises(SkillsHubError) as exc:
        github_public.download_archive(
            repo="example/repo",
            sha=SHA,
            organization_id="org",
            deadline=time.monotonic() + 60,
        )
    assert exc.value.code == "skills_unavailable"


def test_secondary_rate_limit_waits_at_least_one_minute(monkeypatch):
    monkeypatch.setattr(
        github_public,
        "safe_fetch",
        restricted_transport(SsrfResponse(403, {}, b"", ""), []),
    )
    with pytest.raises(SkillsHubError) as exc:
        github_public.resolve_sha(
            repo="example/repo",
            ref="main",
            organization_id="org",
            deadline=time.monotonic() + 60,
        )
    assert exc.value.code == "github_rate_limited"
    assert cache.get(github_public.RATE_RESET_KEY) >= time.time() + 59


def test_last_successful_rate_slot_blocks_the_next_request(monkeypatch):
    seen = []
    response = SsrfResponse(
        200,
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(time.time() + 120)},
        SHA.encode(),
        "",
    )
    monkeypatch.setattr(
        github_public, "safe_fetch", restricted_transport(response, seen)
    )
    assert (
        github_public.resolve_sha(
            repo="example/repo",
            ref="main",
            organization_id="org",
            deadline=time.monotonic() + 60,
        )["sha"]
        == SHA
    )
    with pytest.raises(SkillsHubError) as exc:
        github_public.resolve_sha(
            repo="example/other",
            ref="main",
            organization_id="org",
            deadline=time.monotonic() + 60,
        )
    assert exc.value.code == "github_rate_limited" and len(seen) == 1


def test_mixed_dns_answers_fail_before_connection(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args: [
            (None, None, None, None, ("8.8.8.8", 0)),
            (None, None, None, None, ("127.0.0.1", 0)),
        ],
    )
    with pytest.raises(ValueError):
        _resolve_pinned_ip("api.github.com")


def test_counter_failure_is_not_free_egress(monkeypatch):
    monkeypatch.setattr(
        cache, "incr", Mock(side_effect=ValueError("counter unavailable"))
    )
    with pytest.raises(SkillsHubError) as exc:
        github_public._admit("org")
    assert exc.value.code == "github_budget_unavailable"


def test_metadata_rejects_private_or_wrong_identity_shape(monkeypatch):
    response = SsrfResponse(
        200,
        {},
        json.dumps(
            {
                "full_name": "example/repo",
                "id": 4,
                "default_branch": "main",
                "private": True,
            }
        ).encode(),
        "",
    )
    monkeypatch.setattr(github_public, "safe_fetch", restricted_transport(response, []))
    with pytest.raises(SkillsHubError):
        github_public.repository_metadata(
            repo="example/repo", organization_id="org", deadline=time.monotonic() + 60
        )
