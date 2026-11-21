"""The public dependency inventory distinguishes updates from deliberate pins."""

import pytest

from model_hub.tests.skills_hub import test_i2_integration as auth

admin = auth.admin
pytestmark = pytest.mark.django_db


def test_http_dependents_reports_complete_followers_and_pins(
    admin,
    hub_skill,
    publish_skill,
    make_skill_files,
    context_factory,
    accept_links,
    publish_context,
):
    publish_skill(hub_skill, labels=["production"])
    contexts = [context_factory(f"policy-parent-{index}") for index in range(3)]
    for index, context in enumerate(contexts):
        accept_links(
            context,
            [
                {
                    "skill_id": str(hub_skill.pk),
                    "selector": (
                        {"version": 1} if index == 2 else {"label": "production"}
                    ),
                }
            ],
        )
        publish_context(context)
    publish_skill(
        hub_skill, labels=["production"], files=make_skill_files(body="New policy")
    )
    path = f"/model-hub/skills/{hub_skill.pk}/context-dependents/"
    response = admin.get(path)
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["scope"] == "published_context_references"
    assert result["count"] == 3
    rows = {row["context_id"]: row for row in result["results"]}
    assert set(rows) == {str(context.pk) for context in contexts}
    for index, context in enumerate(contexts):
        assert rows[str(context.pk)]["locked_version"] == 1
        assert rows[str(context.pk)]["context_version"] == 1
        assert rows[str(context.pk)]["update_state"] == (
            "pinned_as_intended" if index == 2 else "update_available"
        )
    first = admin.get(path, {"view": "history", "page_size": 2})
    second = admin.get(path, {"view": "history", "page_size": 2, "page": 2})
    assert first.status_code == second.status_code == 200
    assert first.data["result"]["total_pages"] == 2
    assert {
        row["context_id"]
        for row in first.data["result"]["results"] + second.data["result"]["results"]
    } == set(rows)
    contexts[0].deleted = True
    contexts[0].save(update_fields=["deleted"])
    assert admin.get(path).data["result"]["count"] == 2
    assert admin.get(path, {"view": "history"}).data["result"]["count"] == 3


@pytest.mark.parametrize(
    "query", [{"view": "all"}, {"page": 0}, {"page_size": 101}, {"unexpected": True}]
)
def test_http_dependents_rejects_invalid_queries(admin, hub_skill, query):
    response = admin.get(f"/model-hub/skills/{hub_skill.pk}/context-dependents/", query)
    assert response.status_code == 400, response.data


def test_http_dependents_rejects_context_as_leaf(admin, context_factory):
    context = context_factory()
    response = admin.get(f"/model-hub/skills/{context.pk}/context-dependents/")
    assert response.status_code == 404, response.data
