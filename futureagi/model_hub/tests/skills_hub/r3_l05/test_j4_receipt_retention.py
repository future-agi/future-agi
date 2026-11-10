"""A released receipt remains metadata-only after evidence becomes unavailable."""

from datetime import timedelta

import pytest
from django.utils import timezone

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.r3.release_requests import (
    get_release_receipt,
    get_release_request,
)
from model_hub.tests.skills_hub.r3_l05 import test_release_requests as fixtures

actors = fixtures.actors
candidate = fixtures.candidate
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("change", ["expiry", "visibility"])
def test_released_receipt_survives_evidence_revocation_without_packet_leak(
    actors, hub_skill, candidate, change
):
    author, reviewer = actors
    run = fixtures.text_run(author, hub_skill, candidate[1], baseline=candidate[0])
    packet = fixtures.preview(
        author,
        hub_skill,
        candidate[1],
        evidence=[
            {"source": "skills_text", "source_id": run.pk, "annotation_revision": 0}
        ],
    )
    request = fixtures.submit(author, reviewer, packet)
    fixtures.approve(reviewer, request, packet)
    receipt = fixtures.execute(reviewer, request, packet)
    if change == "expiry":
        run.payload_expires_at = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=["payload_expires_at"])
    else:
        run.visibility = "private"
        run.save(update_fields=["visibility"])
    with pytest.raises(SkillsHubError):
        get_release_request(principal=reviewer, request_id=request["id"])
    assert get_release_receipt(principal=reviewer, request_id=request["id"]) == receipt
    assert receipt["currentness_at_action"]["release_revision"] == 1
    assert receipt["post_move_state"]["release_revision"] == 2
    assert "packet" not in receipt
    assert "configuration" not in receipt
