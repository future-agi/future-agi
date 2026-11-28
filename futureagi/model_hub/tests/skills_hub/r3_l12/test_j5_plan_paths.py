"""Campaign review must accept SDK output paths and reject portable collisions."""

from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12 import campaigns


def make_plan(paths):
    plan = {
        "campaign_revision": 1,
        "binding_revision": 1,
        "repository_id": "100",
        "base_commit": "a" * 40,
        "previous_lock_digest": None,
        "desired_version_id": str(uuid4()),
        "materialization_digest": "sha256:" + "b" * 64,
        "plan_digest": "",
        "changes": [
            {
                "path": path,
                "action": "add",
                "before_sha256": None,
                "after_sha256": "c" * 64,
                "ownership": "managed",
                "diff_preview": "+Synthetic policy\n",
            }
            for path in paths
        ],
        "local_review_required": False,
        "blockers": [],
    }
    plan["plan_digest"] = campaigns.target_plan_digest(plan)
    return plan


def test_materialized_generation_accepts_valid_eight_component_source():
    # A source is <=255 characters/eight components. The SDK prepends
    # .futureagi/context/<digest>/, so source limits cannot be used for outputs.
    source = "/".join(["nested" * 4] * 7 + ["policy.md"])
    destination = ".futureagi/context/" + "a" * 64 + "/" + source
    assert len(destination) > 255
    assert len(destination.split("/")) == 11
    plan = make_plan([destination])
    assert campaigns.validate_target_plan(plan) == plan


@pytest.mark.parametrize(
    "path",
    [
        ".futureagi/context/a/references/policy:stream",
        ".agents/skills/synthetic/con.txt",
        ".claude/skills/synthetic/LPT1.md",
        ".futureagi/context/a/unsafe\x7f.md",
        ".futureagi/context/a/" + "/".join(["nested"] * 14),
    ],
)
def test_unportable_or_excessively_deep_output_is_rejected(path):
    with pytest.raises(SkillsHubError) as error:
        campaigns.validate_target_plan(make_plan([path]))
    assert error.value.code == "destination_forbidden"


@pytest.mark.parametrize(
    "paths",
    [
        [".agents/skills/policy/café.md", ".agents/skills/policy/café.md"],
        [".agents/skills/café", ".agents/skills/café/SKILL.md"],
    ],
)
def test_unicode_normalized_collision_matches_installer_rules(paths):
    with pytest.raises(SkillsHubError) as error:
        campaigns.validate_target_plan(make_plan(paths))
    assert error.value.code == "invalid_campaign"
