"""Compare-and-swap promotion, limits, history and transactional rollback."""

from unittest.mock import patch

import pytest

from model_hub.models.skills_hub import HubSkillLabelEvent
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.labels import label_history, remove_label, set_label

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


def test_label_cas_and_history(hub_skill, publish_skill, hub_actor, make_skill_files):
    publish_skill(hub_skill)
    publish_skill(hub_skill, files=make_skill_files(body="Next procedure"))
    pointer = set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=1,
        expected_version=None,
    )
    assert pointer.version.number == 1
    with pytest.raises(SkillsHubError) as exc:
        set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=2,
            expected_version=None,
        )
    assert exc.value.code == "label_conflict" and exc.value.detail == {
        "current_version": 1
    }
    with pytest.raises(SkillsHubError) as exc:
        set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=2,
            expected_version=9,
        )
    assert exc.value.code == "label_conflict"
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=2,
        expected_version=1,
        note="Ready for production",
    )
    set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=2,
        expected_version=2,
    )
    history = list(label_history(skill=hub_skill, label="production"))
    assert [(event.from_version, event.to_version) for event in history] == [
        (1, 2),
        (None, 1),
    ]
    assert history[0].actor_id == hub_actor.user.pk
    assert history[0].note == "Ready for production"
    with pytest.raises(SkillsHubError) as exc:
        remove_label(
            skill=hub_skill, actor=hub_actor, name="production", expected_version=1
        )
    assert exc.value.detail == {"current_version": 2}
    remove_label(
        skill=hub_skill, actor=hub_actor, name="production", expected_version=2
    )
    assert not hub_skill.labels.exists()
    removed = label_history(skill=hub_skill).first()
    assert removed is not None
    assert (removed.from_version, removed.to_version) == (2, None)


def test_missing_label_cas(hub_skill, publish_skill, hub_actor):
    publish_skill(hub_skill)
    with pytest.raises(SkillsHubError) as exc:
        set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=1,
            expected_version=1,
        )
    assert exc.value.code == "label_conflict" and exc.value.detail == {
        "current_version": None
    }
    with pytest.raises(SkillsHubError) as exc:
        remove_label(
            skill=hub_skill, actor=hub_actor, name="production", expected_version=None
        )
    assert exc.value.code == "label_not_found"
    set_label(skill=hub_skill, actor=hub_actor, name="production", version_number=1)
    assert hub_skill.labels.count() == 1


@pytest.mark.parametrize(
    "name", ["latest", "Production", "bad_label", "", "a" * 41, "-first"]
)
def test_invalid_label(hub_skill, publish_skill, hub_actor, name):
    publish_skill(hub_skill)
    with pytest.raises(SkillsHubError) as exc:
        set_label(skill=hub_skill, actor=hub_actor, name=name, version_number=1)
    assert exc.value.code == "invalid_label"
    assert not hub_skill.labels.exists()


def test_label_limit_allows_updates_at_capacity(
    hub_skill, publish_skill, hub_actor, make_skill_files
):
    publish_skill(hub_skill)
    publish_skill(hub_skill, files=make_skill_files(body="Updated"))
    for index in range(20):
        set_label(
            skill=hub_skill, actor=hub_actor, name=f"label-{index}", version_number=1
        )
    with pytest.raises(SkillsHubError) as exc:
        set_label(skill=hub_skill, actor=hub_actor, name="overflow", version_number=1)
    assert exc.value.code == "invalid_label"
    updated = set_label(
        skill=hub_skill, actor=hub_actor, name="label-0", version_number=2
    )
    assert updated.version.number == 2 and hub_skill.labels.count() == 20


def test_history_failure_rolls_back_pointer(
    hub_skill, publish_skill, hub_actor, make_skill_files
):
    publish_skill(hub_skill, labels=["production"])
    publish_skill(hub_skill, files=make_skill_files(body="Updated"))
    with patch.object(
        HubSkillLabelEvent.objects,
        "create",
        side_effect=RuntimeError("event unavailable"),
    ):
        with pytest.raises(RuntimeError, match="event unavailable"):
            set_label(
                skill=hub_skill, actor=hub_actor, name="production", version_number=2
            )
        with pytest.raises(RuntimeError, match="event unavailable"):
            remove_label(skill=hub_skill, actor=hub_actor, name="production")
    assert hub_skill.labels.get(name="production").version.number == 1
    assert hub_skill.label_events.count() == 1


def test_label_target_must_exist_on_same_skill(hub_skill, publish_skill, hub_actor):
    publish_skill(hub_skill)
    with pytest.raises(SkillsHubError) as exc:
        set_label(skill=hub_skill, actor=hub_actor, name="production", version_number=2)
    assert exc.value.code == "version_not_found"
    assert not hub_skill.labels.exists()
