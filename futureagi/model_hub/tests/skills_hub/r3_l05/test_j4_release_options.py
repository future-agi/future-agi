"""Synthetic fixtures: author discovery never grants access or shares evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.release_options import (
    authored_requests,
    release_options,
)
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as fixtures

actors = fixtures.actors
candidate = fixtures.candidate
preview = fixtures.preview
submit = fixtures.submit
text_run = fixtures.text_run

pytestmark = pytest.mark.django_db(transaction=True)


def options(author, skill, version):
    return release_options(
        principal=author,
        resource_id=skill.pk,
        kind=skill.kind,
        candidate_version_id=version.pk,
    )


def test_discovery_keeps_private_evidence_owner_only(actors, hub_skill, candidate):
    author, reviewer = actors
    own = text_run(author, hub_skill, candidate[1])
    own.visibility = "private"
    own.save(update_fields=["visibility"])
    other = text_run(reviewer, hub_skill, candidate[1])
    other.visibility = "private"
    other.save(update_fields=["visibility"])
    value = options(author, hub_skill, candidate[1])
    assert [ref["source_id"] for ref in value["evidence"]] == [str(own.pk)]
    assert value["evidence"][0]["visibility"] == "private"
    assert author.user_id not in [row["id"] for row in value["recipients"]]
    assert reviewer.user_id in [row["id"] for row in value["recipients"]]
    assert [
        ref["source_id"]
        for ref in options(reviewer, hub_skill, candidate[1])["evidence"]
    ] == [str(other.pk)]


def test_discovery_excludes_expired_and_wrong_candidate(actors, hub_skill, candidate):
    author, _ = actors
    run = text_run(author, hub_skill, candidate[1])
    run.payload_expires_at = timezone.now() - timedelta(seconds=1)
    run.save(update_fields=["payload_expires_at"])
    text_run(author, hub_skill, candidate[0])
    assert options(author, hub_skill, candidate[1])["evidence"] == []
    with pytest.raises(SkillsHubError, match="Candidate not found"):
        release_options(
            principal=author,
            resource_id=hub_skill.pk,
            kind=hub_skill.kind,
            candidate_version_id=uuid4(),
        )


def test_author_can_discover_renewable_request_without_recipient_inbox(
    actors, hub_skill, candidate
):
    author, reviewer = actors
    packet = preview(author, hub_skill, candidate[1])
    request = submit(author, reviewer, packet)
    own = authored_requests(principal=author)
    assert own["count"] == 1
    assert str(own["results"][0]["id"]) == request["id"]
    assert own["results"][0]["revision"] == request["revision"]
    assert "packet" not in own["results"][0]
    assert authored_requests(principal=reviewer)["count"] == 0
