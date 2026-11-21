"""Uniform selector rules and lossless UTF-8 character-offset chunk reads."""

import json

import pytest

from mcp_server.response_limits import bounded_response
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256
from model_hub.services.skills_hub.resolve import read_file_chunk, resolve
from model_hub.services.skills_hub.versions import archive_skill

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_resolve_defaults_production_not_latest(
    hub_skill, organization, workspace, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill, labels=["production"]).version
    latest = publish_skill(
        hub_skill, files=make_skill_files(body="Latest only")
    ).version
    scope = {
        "organization": organization,
        "workspace": workspace,
        "name": hub_skill.name,
    }
    default = resolve(**scope)
    assert default.version.pk == first.pk
    assert default.requested == {"label": "production"}
    assert resolve(**scope, label="latest").version.pk == latest.pk
    pinned = resolve(**scope, version=1)
    assert pinned.version.pk == first.pk and pinned.requested == {"version": 1}
    assert hub_skill.drafts.count() == 1
    assert not hub_skill.usage_days.exists()


def test_missing_label_never_falls_back(
    hub_skill, organization, workspace, publish_skill
):
    publish_skill(hub_skill)
    scope = {
        "organization": organization,
        "workspace": workspace,
        "name": hub_skill.name,
    }
    for label in (None, "production", "staging"):
        with pytest.raises(SkillsHubError) as exc:
            resolve(**scope, label=label)
        assert (exc.value.code, exc.value.status) == ("label_not_found", 404)
    with pytest.raises(SkillsHubError) as exc:
        resolve(**scope, version=123)
    assert exc.value.code == "version_not_found"


def test_unpublished_and_archived(
    hub_skill, organization, workspace, publish_skill, hub_actor
):
    scope = {
        "organization": organization,
        "workspace": workspace,
        "name": hub_skill.name,
    }
    for selector in ({}, {"label": "latest"}, {"version": 1}):
        with pytest.raises(SkillsHubError) as exc:
            resolve(**scope, **selector)
        assert exc.value.code == "version_not_found"
    version = publish_skill(hub_skill, labels=["production"]).version
    archive_skill(skill=hub_skill, actor=hub_actor)
    with pytest.raises(SkillsHubError) as exc:
        resolve(**scope)
    assert exc.value.code == "skill_not_found"
    with pytest.raises(SkillsHubError) as exc:
        read_file_chunk(version, "SKILL.md")
    assert exc.value.code == "skill_not_found"


@pytest.mark.parametrize(
    "selector",
    [
        {"label": "production", "version": 1},
        {"version": 0},
        {"version": -1},
        {"version": True},
    ],
)
def test_invalid_selector(organization, workspace, selector):
    with pytest.raises(SkillsHubError) as exc:
        resolve(
            organization=organization, workspace=workspace, name="absent", **selector
        )
    assert exc.value.code == "invalid_selector"


def test_unicode_chunks_are_longest_utf8_prefix(
    hub_skill, publish_skill, make_skill_files
):
    content = "abé𐍈xyz\n"
    version = publish_skill(
        hub_skill,
        files=make_skill_files(extra=[{"path": "unicode.txt", "content": content}]),
    ).version
    first = read_file_chunk(version, "unicode.txt", 0, 7)
    assert first["text"] == "abé" and first["next_offset"] == 3
    second = read_file_chunk(version, "unicode.txt", 3, 7)
    assert second["text"] == "𐍈xyz" and second["next_offset"] == 7
    last = read_file_chunk(version, "unicode.txt", 7, 7)
    assert last["text"] == "\n" and last["eof"] and last["next_offset"] is None
    end = read_file_chunk(version, "unicode.txt", 8, 7)
    assert end["text"] == "" and end["eof"]
    assert first["total_chars"] == len(content)
    assert first["size_bytes"] == len(content.encode("utf-8"))
    assert first["sha256"] == file_sha256(content)
    assert not hub_skill.usage_days.exists()


@pytest.mark.parametrize(
    "offset,max_bytes", [(-1, 100), (0, 0), (0, 49153), (100000, 100), (True, 100)]
)
def test_invalid_chunk_bounds(hub_skill, publish_skill, offset, max_bytes):
    version = publish_skill(hub_skill).version
    with pytest.raises(SkillsHubError) as exc:
        read_file_chunk(version, "SKILL.md", offset, max_bytes)
    assert exc.value.code == "invalid_selector"


def test_too_small_chunk_does_not_return_nonadvancing_cursor(
    hub_skill, publish_skill, make_skill_files
):
    version = publish_skill(
        hub_skill,
        files=make_skill_files(extra=[{"path": "unicode.txt", "content": "é"}]),
    ).version
    with pytest.raises(SkillsHubError) as exc:
        read_file_chunk(version, "unicode.txt", 0, 1)
    assert exc.value.code == "invalid_selector"


def test_large_skill_reassembles_through_mcp_without_truncation(
    hub_skill, publish_skill, make_skill_files
):
    files = make_skill_files(body="Repeatable guidance for a safe next step.\n" * 3000)
    version = publish_skill(hub_skill, files=files).version
    assert len(files[0]["content"].encode("utf-8")) > 100 * 1024
    offset, chunks = 0, []
    while True:
        wire = bounded_response(
            {
                "status": True,
                "result": read_file_chunk(version, "SKILL.md", offset, 24000),
            }
        )
        assert "_mcp" not in wire
        chunk = wire["result"]
        assert chunk["sha256"] == file_sha256(files[0]["content"])
        chunks.append(chunk["text"])
        if chunk["eof"]:
            break
        assert chunk["next_offset"] > offset
        offset = chunk["next_offset"]
    assert "".join(chunks).encode("utf-8") == files[0]["content"].encode("utf-8")


def test_json_escaped_chunk_stays_below_transport_limit(
    hub_skill, publish_skill, make_skill_files
):
    content = '\t\n\\"' * 30000
    version = publish_skill(
        hub_skill,
        files=make_skill_files(extra=[{"path": "escaped.txt", "content": content}]),
    ).version
    chunk = read_file_chunk(version, "escaped.txt", 0, 49152)
    wire = {"status": True, "result": chunk}
    assert len(json.dumps(wire, ensure_ascii=False).encode("utf-8")) < 65536
    assert bounded_response(wire) == wire
    assert content.startswith(chunk["text"])
    assert chunk["next_offset"] == len(chunk["text"])
