"""A rejected descendant invalidates only its source member, not its siblings."""

import io
import json
import zipfile

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from model_hub.models.skills_hub import HubSkill
from model_hub.services.skills_hub.packages import parse_upload
from model_hub.tests.skills_hub import test_i2_integration as fixtures

admin = fixtures.admin


def archive():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zip_file:
        for index in range(15):
            name = f"member-{index}"
            zip_file.writestr(
                f"{name}/SKILL.md",
                f"---\nname: {name}\ndescription: Use when a support issue needs a clear next step.\n---\nFollow the safe policy.\n",
            )
            zip_file.writestr(
                f"{name}/references/example.md", "A complete text reference."
            )
        zip_file.writestr("member-7/assets/example.bin", b"\x00\xffbinary")
    return output.getvalue()


def upload():
    return SimpleUploadedFile("skills.zip", archive(), content_type="application/zip")


def test_binary_descendant_blocks_one_of_fifteen_members():
    parsed = parse_upload([("skills.zip", archive())])
    assert len(parsed["bundles"]) == 15
    invalid = [row for row in parsed["bundles"] if row["checks"]["summary"]["errors"]]
    assert [row["name"] for row in invalid] == ["member-7"]
    assert invalid[0]["omissions"] == [
        {"path": "member-7/assets/example.bin", "reason": "file_not_text"}
    ]
    assert invalid[0]["checks"]["items"][-1]["code"] == "import_incomplete"


def test_same_paths_in_separate_archives_do_not_share_rejections():
    clean = io.BytesIO()
    with zipfile.ZipFile(clean, "w") as output:
        output.writestr(
            "member-7/SKILL.md",
            "---\nname: member-7\ndescription: Use when a support issue needs a clear next step.\n---\nSafe policy.",
        )
    parsed = parse_upload([("bad.zip", archive()), ("clean.zip", clean.getvalue())])
    clean_member = parsed["bundles"][-1]
    assert clean_member["omissions"] == []
    assert clean_member["checks"]["summary"]["errors"] == 0


@pytest.mark.django_db(transaction=True)
def test_public_import_requires_exact_subset_acknowledgment_and_retains_omissions(
    admin,
):
    preview = admin.post(
        "/model-hub/skills/import/preview/", {"files": [upload()]}, format="multipart"
    )
    assert preview.status_code == 200, preview.data
    bundle = next(
        row for row in preview.data["result"]["bundles"] if row["name"] == "member-7"
    )
    assert bundle["status"] == "invalid"
    assert bundle["omissions"] == [
        {"path": "member-7/assets/example.bin", "reason": "file_not_text"}
    ]
    assert bundle["checks"]["summary"]["errors"] == 1
    body = {"selected": json.dumps([bundle["key"]]), "acknowledge_warnings": True}
    rejected = admin.post(
        "/model-hub/skills/import/", {**body, "files": [upload()]}, format="multipart"
    )
    assert rejected.status_code == 200, rejected.data
    assert rejected.data["result"]["failed"][0]["code"] == "import_incomplete"
    assert not HubSkill.no_workspace_objects.filter(name="member-7").exists()
    omissions = [{"path": "member-7/assets/example.bin", "reason": "file_not_text"}]
    accepted = admin.post(
        "/model-hub/skills/import/",
        {
            **body,
            "files": [upload()],
            "acknowledged_omissions": json.dumps({bundle["key"]: omissions}),
        },
        format="multipart",
    )
    assert accepted.status_code == 200, accepted.data
    assert len(accepted.data["result"]["created"]) == 1
    version = HubSkill.no_workspace_objects.get(name="member-7").versions.get(number=1)
    assert version.source["omissions"] == omissions
    assert version.source["supported_subset"] is True
