"""Public serializer UUID identities must match immutable JSON inventory."""

import json
from uuid import uuid4

import pytest

from model_hub.serializers.skills_hub_v11 import InventoryReview
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.inventory import validate_inventory_review


def fixture():
    skill = str(uuid4())
    fingerprint = "sha256:" + "b" * 64
    file = {
        "path": "skills/benchmark-policy/SKILL.md",
        "sha256": "a" * 64,
        "size_bytes": 30,
        "flags": ["evaluation_artifact"],
    }
    raw = {
        "fingerprint": fingerprint,
        "reviewed_all_files": True,
        "no_known_answer_keys": True,
        "classifications": [
            {
                "skill_id": skill,
                "path": file["path"],
                "sha256": file["sha256"],
                "classification": "reference_material",
                "reason": "Policy reference, not answer material.",
            }
        ],
        "false_positive_secret_findings": [],
    }
    serializer = InventoryReview(data=raw)
    serializer.is_valid(raise_exception=True)
    return {
        "plan_fingerprint": fingerprint,
        "inventory": [{"skill_id": skill, "files": [file]}],
        "findings": [],
        "review": serializer.validated_data,
        "purpose": "regression",
    }


def test_public_uuid_classification_is_json_serializable_and_exact():
    args = fixture()
    accepted, reasons = validate_inventory_review(**args)
    assert reasons == []
    assert (
        json.loads(json.dumps(accepted))["classifications"][0]["skill_id"]
        == args["inventory"][0]["skill_id"]
    )


@pytest.mark.parametrize(
    "field,value", [("skill_id", uuid4()), ("path", "AGENTS.md"), ("sha256", "c" * 64)]
)
def test_uuid_normalization_does_not_allow_another_file(field, value):
    args = fixture()
    args["review"]["classifications"][0][field] = value
    with pytest.raises(SkillsHubError) as error:
        validate_inventory_review(**args)
    assert error.value.code == "invalid_inventory_review"


def test_duplicate_uuid_and_string_forms_are_the_same_review_identity():
    args = fixture()
    first = args["review"]["classifications"][0]
    args["review"]["classifications"].append(
        {**first, "skill_id": str(first["skill_id"])}
    )
    with pytest.raises(SkillsHubError):
        validate_inventory_review(**args)
