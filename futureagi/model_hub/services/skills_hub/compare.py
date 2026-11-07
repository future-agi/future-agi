"""Whole-bundle comparison of immutable snapshots and the author's draft."""

from __future__ import annotations

from accounts.models.user import User
from model_hub.models.skills_hub import HubSkill
from model_hub.selectors.skills_hub import scoped_skill, version_for_skill
from model_hub.services.skills_hub.drafts import get_draft
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import file_sha256


def _snapshot(skill: HubSkill, ref: int | str, user: User) -> tuple[int | str, dict]:
    if ref == "draft":
        draft = get_draft(skill=skill, user=user)
        return ref, {
            file["path"]: {
                "sha256": file_sha256(file["content"]),
                "text": file["content"],
            }
            for file in draft.files
        }
    if isinstance(ref, str) and ref.isascii() and ref.isdecimal():
        ref = int(ref)
    if type(ref) is not int or ref < 1:
        raise SkillsHubError(
            "invalid_selector", "Choose a version number or your draft.", status=400
        )
    version = version_for_skill(skill, ref)
    return ref, {
        row.path: {"sha256": row.sha256, "text": row.blob.content}
        for row in version.files.select_related("blob")
    }


def compare(
    *, skill: HubSkill, from_ref: int | str, to_ref: int | str, user: User
) -> dict:
    """Return deterministic file statuses; unchanged files do not repeat text."""
    skill = scoped_skill(skill)
    from_ref, before = _snapshot(skill, from_ref, user)
    to_ref, after = _snapshot(skill, to_ref, user)
    summary = {"added": 0, "removed": 0, "modified": 0, "unchanged": 0}
    files = []
    for path in sorted(before.keys() | after.keys()):
        left, right = before.get(path), after.get(path)
        if left is None:
            status = "added"
        elif right is None:
            status = "removed"
        elif left["sha256"] == right["sha256"]:
            status = "unchanged"
        else:
            status = "modified"
        item = {
            "path": path,
            "status": status,
            "from_sha256": left["sha256"] if left else None,
            "to_sha256": right["sha256"] if right else None,
        }
        if status != "unchanged":
            item.update(
                from_text=left["text"] if left else "",
                to_text=right["text"] if right else "",
            )
        files.append(item)
        summary[status] += 1
    return {"from": from_ref, "to": to_ref, "files": files, "summary": summary}
