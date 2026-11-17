"""The pairwise label the review screens create is accepted by label create.

Pairwise… in Start review and Start here: Review the regressions post the
frontend's PAIRWISE_LABEL to /model-hub/annotations-labels/ to make the
"Overall preference" label. The body lives in one file that the frontend
test pairwise-label-body.test.js compares with PAIRWISE_LABEL, so this test
posts exactly what the screens send, through the real view and serializer.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from rest_framework import status

from model_hub.models.develop_annotations import AnnotationsLabels

REPO_ROOT = Path(__file__).resolve().parents[3]
PAIRWISE_LABEL_BODY = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "sections"
    / "develop-detail"
    / "Review"
    / "__tests__"
    / "pairwise-label.fixture.json"
)
URL = "/model-hub/annotations-labels/"


@pytest.fixture(autouse=True)
def allow_label_create():
    with patch("tfc.ee_gating.check_ee_can_create"):
        yield


@pytest.mark.django_db
def test_the_pairwise_label_the_review_screens_send_is_created(auth_client):
    body = json.loads(PAIRWISE_LABEL_BODY.read_text())

    response = auth_client.post(URL, body, format="json")

    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.json()["result"]
    assert result["name"] == "Overall preference"
    assert result["type"] == "categorical"
    assert result["settings"]["kind"] == "pairwise"
    assert [o["label"] for o in result["settings"]["options"]] == ["A", "Tie", "B"]
    label = AnnotationsLabels.no_workspace_objects.get(id=result["id"])
    assert label.settings["kind"] == "pairwise"
