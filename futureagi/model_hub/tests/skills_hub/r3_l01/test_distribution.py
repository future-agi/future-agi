"""Quarantine must not be bypassed through an older public Skills route."""

import pytest

from model_hub.models.skills_hub_v12 import HubSkillContextQuarantine
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import set_label
from model_hub.services.skills_hub.resolve import read_file_chunk, resolve
from model_hub.services.skills_hub.versions import restore_version

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "operation", ["resolve", "file", "restore", "same_label", "same_publish"]
)
def test_legacy_distribution_and_noops_enforce_quarantine(
    hub_skill, hub_actor, publish_skill, operation
):
    version = publish_skill(hub_skill, labels=["production"]).version
    HubSkillContextQuarantine.objects.create(
        organization_id=hub_skill.organization_id,
        workspace_id=hub_skill.workspace_id,
        version=version,
        revision=1,
        active=True,
        reason="Synthetic safety control",
    )
    with pytest.raises(SkillsHubError) as error:
        if operation == "resolve":
            resolve(
                organization=hub_skill.organization,
                workspace=hub_skill.workspace,
                name=hub_skill.name,
            )
        elif operation == "file":
            read_file_chunk(version, "SKILL.md")
        elif operation == "restore":
            restore_version(skill=hub_skill, actor=hub_actor, number=1)
        elif operation == "same_label":
            set_label(
                skill=hub_skill, actor=hub_actor, name="production", version_number=1
            )
        else:
            publish_skill(hub_skill)
    assert error.value.code == "context_quarantined"
    assert hub_skill.versions.count() == 1


def test_latest_quarantine_event_controls_access(hub_skill, publish_skill):
    version = publish_skill(hub_skill, labels=["production"]).version
    for revision, active in [(1, True), (2, False)]:
        HubSkillContextQuarantine.objects.create(
            organization_id=hub_skill.organization_id,
            workspace_id=hub_skill.workspace_id,
            version=version,
            revision=revision,
            active=active,
            reason="Synthetic event",
        )
    resolved = resolve(
        organization=hub_skill.organization,
        workspace=hub_skill.workspace,
        name=hub_skill.name,
    )
    assert resolved.version.pk == version.pk
