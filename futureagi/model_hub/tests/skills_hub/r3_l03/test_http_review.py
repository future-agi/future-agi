"""Every review text link resolves under the human identity that requested it."""

from urllib.parse import parse_qs, urlsplit

import pytest

from model_hub.serializers.skills_context_authoring import ReleaseReview
from model_hub.tests.skills_hub import test_i2_integration as fixtures

admin = fixtures.admin
pytestmark = pytest.mark.django_db(transaction=True)


def result(response):
    assert response.status_code == 200, response.data
    return response.data["result"]


def test_paginated_release_review_links_are_human_readable_and_revision_bound(
    admin, context, publish_context
):
    published = publish_context(context).version
    root = f"/model-hub/skills/contexts/{context.pk}/"
    draft = result(
        admin.post(
            root + "draft/start/",
            {"revision": 0, "from_version": published.number},
            format="json",
        )
    )
    body = {
        "revision": draft["revision"],
        "base_version": published.number,
        "files": [{"path": "AGENTS.md", "content": "Repair café policy.\n"}]
        + [
            {"path": f"refs/{index:02}.md", "content": "Reviewed evidence"}
            for index in range(30)
        ],
        "description": "Reviewed support purpose",
        "note": "Exact tree",
    }
    saved = result(admin.put(root + "draft/", body, format="json"))
    reviews = [
        result(
            admin.post(
                root + "release/review/",
                {
                    "operation": "publish",
                    "revision": saved["revision"],
                    "page": page,
                },
                format="json",
            )
        )
        for page in (1, 2)
    ]
    assert reviews[0]["review_fingerprint"] == reviews[1]["review_fingerprint"]
    for review in reviews:
        schema = ReleaseReview(data=review)
        assert schema.is_valid(), schema.errors
    rows = [row for review in reviews for row in review["changes"]["files"]]
    assert len(rows) == 32
    agents = next(row for row in rows if row["path"] == "AGENTS.md")
    assert "/inspection/files/" in agents["from_text_url"]
    assert result(admin.get(agents["from_text_url"]))["text"] == "# Instructions\n"
    text = result(admin.get(agents["to_text_url"]))
    assert text["text"] == "Repair café policy.\n"
    assert text["revision"] == saved["revision"]
    assert parse_qs(urlsplit(agents["to_text_url"]).query)["revision"] == [
        str(saved["revision"])
    ]
    assert admin.get(agents["to_text_url"])["Cache-Control"] == "private, no-store"
    result(
        admin.put(
            root + "draft/",
            {**body, "revision": saved["revision"], "note": "Later edit"},
            format="json",
        )
    )
    stale = admin.get(agents["to_text_url"])
    assert stale.status_code == 409
    assert stale.data["code"] == "draft_conflict"
