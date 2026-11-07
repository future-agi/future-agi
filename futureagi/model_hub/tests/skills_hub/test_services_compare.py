"""Comparison returns all file statuses without changing snapshots or drafts."""

import pytest

from model_hub.services.skills_hub.compare import compare
from model_hub.services.skills_hub.drafts import save_draft
from model_hub.services.skills_hub.errors import SkillsHubError

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_compare_all_statuses_and_both_texts(
    hub_skill, user, publish_skill, make_skill_files
):
    first = make_skill_files(
        extra=[
            {"path": "removed.txt", "content": "Previous file"},
            {"path": "modified.txt", "content": "Before"},
        ]
    )
    second = make_skill_files(
        extra=[
            {"path": "added.txt", "content": "New file"},
            {"path": "modified.txt", "content": "After"},
        ]
    )
    publish_skill(hub_skill, files=first)
    publish_skill(hub_skill, files=second)
    result = compare(skill=hub_skill, from_ref=1, to_ref="2", user=user)
    assert result["from"] == 1 and result["to"] == 2
    assert result["summary"] == {
        "added": 1,
        "removed": 1,
        "modified": 1,
        "unchanged": 1,
    }
    assert [row["path"] for row in result["files"]] == sorted(
        row["path"] for row in result["files"]
    )
    files = {row["path"]: row for row in result["files"]}
    assert "from_text" not in files["SKILL.md"] and "to_text" not in files["SKILL.md"]
    assert files["SKILL.md"]["from_sha256"] == files["SKILL.md"]["to_sha256"]
    assert files["added.txt"]["from_sha256"] is None
    assert (
        files["added.txt"]["from_text"] == ""
        and files["added.txt"]["to_text"] == "New file"
    )
    assert files["removed.txt"]["to_sha256"] is None
    assert (
        files["removed.txt"]["from_text"] == "Previous file"
        and files["removed.txt"]["to_text"] == ""
    )
    assert (
        files["modified.txt"]["from_text"] == "Before"
        and files["modified.txt"]["to_text"] == "After"
    )
    assert hub_skill.versions.count() == 2


def test_compare_draft_is_per_author(
    hub_skill, user, hub_other_user, publish_skill, make_skill_files
):
    publish_skill(hub_skill)
    save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(body="My change"),
        note="",
        base_version=1,
        revision=1,
    )
    mine = compare(skill=hub_skill, from_ref=1, to_ref="draft", user=user)
    assert mine["summary"]["modified"] == 1
    assert "My change" in mine["files"][0]["to_text"]
    assert mine["to"] == "draft"
    with pytest.raises(SkillsHubError) as exc:
        compare(skill=hub_skill, from_ref=1, to_ref="draft", user=hub_other_user)
    assert exc.value.code == "draft_not_found"
    reverse = compare(skill=hub_skill, from_ref="draft", to_ref=1, user=user)
    assert reverse["files"][0]["from_text"] == mine["files"][0]["to_text"]
    same = compare(skill=hub_skill, from_ref="draft", to_ref="draft", user=user)
    assert same["summary"] == {"added": 0, "removed": 0, "modified": 0, "unchanged": 1}


@pytest.mark.parametrize(
    "reference,code",
    [
        (0, "invalid_selector"),
        (True, "invalid_selector"),
        ("production", "invalid_selector"),
        (12, "version_not_found"),
    ],
)
def test_compare_invalid_reference(hub_skill, user, publish_skill, reference, code):
    publish_skill(hub_skill)
    with pytest.raises(SkillsHubError) as exc:
        compare(skill=hub_skill, from_ref=1, to_ref=reference, user=user)
    assert exc.value.code == code
