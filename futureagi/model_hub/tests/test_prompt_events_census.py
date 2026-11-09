"""XP-06 WT-21: every route and service call that commits a prompt version or touches a label produces exactly one
event with the right action and cause; drafts produce none (webhooks DESIGN.md sections 14.2, 14.3 and 17).

The routes are driven through FA's whole Django stack (URL routing, middleware, DRF views, the real database) with
the authenticated test client. Graph activation is in ``agent_playground/tests/services/test_dataset_bridge_webhooks``.
The guard test (``test_prompt_label_guard``) proves that no other code path writes a label or commits a draft.
"""

from __future__ import annotations

import pytest

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptVersion
from model_hub.services import prompt_label as writer
from model_hub.services.prompt_service import create_prompt_version
from model_hub.tests.prompt_webhook_helpers import (  # noqa: F401 - fixture
    LABEL_EVENT,
    VERSION_EVENT,
    events,
    holders,
    label_events,
    make_template,
    make_version,
    system_labels,
    webhook_endpoint,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("webhook_endpoint")]

CONFIG = [
    {
        "messages": [
            {"role": "system", "content": [{"type": "text", "text": "Be brief."}]},
            {"role": "user", "content": [{"type": "text", "text": "Hi {{name}}"}]},
        ],
        "configuration": {"model": "gpt-4o-mini"},
    }
]


def _version_events(template_id) -> list:
    return [
        e for e in events(VERSION_EVENT) if e.data["prompt"]["id"] == str(template_id)
    ]


def _ok(response):
    assert response.status_code == 200, response.content
    return response.json().get("result", response.json())


def test_create_draft_route_publishes_only_when_committed(auth_client):
    draft = _ok(
        auth_client.post(
            "/model-hub/prompt-templates/create-draft/",
            {"name": "census-draft", "prompt_config": CONFIG, "is_draft": True},
            format="json",
        )
    )
    assert _version_events(draft["id"]) == []
    committed = _ok(
        auth_client.post(
            "/model-hub/prompt-templates/create-draft/",
            {"name": "census-commit", "prompt_config": CONFIG, "is_draft": False},
            format="json",
        )
    )
    [ev] = _version_events(committed["id"])
    assert ev.data["version"]["version"] == "v1"
    # The test client's force_authenticate skips FA's authentication class, which is what binds the request's user
    # to the model-layer context; so no person here. The real API-key path is test_sdk_commit_names_the_caller.
    assert ev.data["version"]["created_by"] is None
    assert ev.data["previous_version"] is None
    assert ev.producer == "model_hub.prompt_label"


def test_add_new_draft_route_publishes_nothing(auth_client, organization, workspace):
    t = make_template(organization, workspace)
    make_version(t, 1)
    before = len(_version_events(t.id))
    _ok(
        auth_client.post(
            f"/model-hub/prompt-templates/{t.id}/add-new-draft/",
            {
                "new_prompts": [
                    {
                        "prompt_config": CONFIG,
                        "variable_names": {},
                        "evaluation_configs": [],
                    },
                    {
                        "prompt_config": CONFIG,
                        "variable_names": {},
                        "evaluation_configs": [],
                    },
                ]
            },
            format="json",
        )
    )
    assert PromptVersion.objects.filter(original_template=t, is_draft=True).count() == 2
    assert len(_version_events(t.id)) == before


def test_commit_route_publishes_once_per_version(auth_client, organization, workspace):
    t = make_template(organization, workspace)
    v1 = make_version(t, 1, message="first")
    make_version(t, 2, draft=True)
    url = f"/model-hub/prompt-templates/{t.id}/commit/"
    _ok(
        auth_client.post(
            url, {"version_name": "v2", "message": "Ship it"}, format="json"
        )
    )
    evs = [e for e in _version_events(t.id) if e.data["version"]["version"] == "v2"]
    assert len(evs) == 1
    assert evs[0].data["version"]["commit_message"] == "Ship it"
    assert evs[0].data["previous_version"] == {"id": str(v1.id), "version": "v1"}
    # Committing it again, or moving it back to draft and committing again, never makes a second event.
    _ok(
        auth_client.post(url, {"version_name": "v2", "message": "Again"}, format="json")
    )
    _ok(
        auth_client.post(
            url, {"version_name": "v2", "message": "x", "is_draft": True}, format="json"
        )
    )
    _ok(auth_client.post(url, {"version_name": "v2", "message": "y"}, format="json"))
    assert (
        len([e for e in _version_events(t.id) if e.data["version"]["version"] == "v2"])
        == 1
    )


def test_the_service_create_path_publishes_only_with_a_commit_message(
    organization, workspace
):
    t = make_template(organization, workspace)
    draft = create_prompt_version(
        template_id=t.id, organization=organization, prompt_config=CONFIG
    )
    assert draft["version"].is_draft is True
    assert _version_events(t.id) == []
    done = create_prompt_version(
        template_id=t.id,
        organization=organization,
        prompt_config=CONFIG,
        commit_message="From the agent",
    )
    [ev] = _version_events(t.id)
    assert ev.data["version"]["id"] == str(done["version"].id)


def test_the_run_path_commit_is_one_event(organization, workspace):
    """The run actions (``views/prompt_template.py`` and ``utils/async_prompt_runner.py``) commit a draft by setting
    ``is_draft`` and saving with ``update_fields``; the exact save they make publishes once."""
    t = make_template(organization, workspace)
    v = make_version(t, 1, draft=True)
    v.output = ["ok"]
    v.is_draft = False
    v.save(update_fields=["output", "is_draft", "updated_at", "metadata"])
    v.save(update_fields=["output", "is_draft", "updated_at", "metadata"])
    assert len(_version_events(t.id)) == 1


def test_commit_version_publishes_once_and_respects_publish_false(
    organization, workspace
):
    t = make_template(organization, workspace)
    a = make_version(t, 1, draft=True)
    b = make_version(t, 2, draft=True)
    assert writer.commit_version(a) is True
    assert writer.commit_version(a) is False
    assert writer.commit_version(b, publish=False) is True
    assert [e.data["version"]["version"] for e in _version_events(t.id)] == ["v1"]


def test_label_routes_each_publish_exactly_one_event(
    auth_client, organization, workspace, user
):
    t = make_template(organization, workspace)
    v1, v2, v3 = (make_version(t, n) for n in (1, 2, 3))
    labels = system_labels()
    P, S = labels["Production"], labels["Staging"]

    def new_label_events(before):
        return [
            (d["label"]["name"], d["action"], d["cause"])
            for d in label_events(t)[before:]
        ]

    n = len(label_events(t))
    _ok(
        auth_client.post(
            "/model-hub/prompt-labels/assign-multiple-labels/",
            {"template_version_id": str(v1.id), "label_ids": [str(P.id), str(S.id)]},
            format="json",
        )
    )
    assert sorted(new_label_events(n)) == [
        ("Production", "assigned", "label_change"),
        ("Staging", "assigned", "label_change"),
    ]

    n = len(label_events(t))
    moved = _ok(
        auth_client.post(
            f"/model-hub/prompt-labels/{t.id}/{P.id}/assign-label-by-id/",
            {"version": "v2"},
            format="json",
        )
    )
    assert moved["moved_from_versions"] == ["v1"]
    assert new_label_events(n) == [("Production", "moved", "label_change")]
    assert holders(t, P) == ["v2"]

    # The remove route reads labels through the workspace-scoped manager, so it is driven with a custom label of
    # this workspace (system labels have no organization and are not visible to it).
    custom = PromptLabel.no_workspace_objects.create(
        name="Reviewed",
        type=LabelTypeChoices.CUSTOM.value,
        organization=organization,
        workspace=workspace,
    )
    with writer.acting_as(user):
        writer.move_label(v1, custom)
    n = len(label_events(t))
    _ok(
        auth_client.post(
            "/model-hub/prompt-labels/remove/",
            {"label_id": str(custom.id), "version_id": str(v1.id)},
            format="json",
        )
    )
    assert new_label_events(n) == [("Reviewed", "removed", "label_change")]
    assert holders(t, custom) == []

    # Set-replace through the route: v2 held Production; assigning Staging alone to v2 drops Production (replaced)
    # and moves Staging off v1.
    n = len(label_events(t))
    _ok(
        auth_client.post(
            "/model-hub/prompt-labels/assign-multiple-labels/",
            {"template_version_id": str(v2.id), "label_ids": [str(S.id)]},
            format="json",
        )
    )
    assert sorted(new_label_events(n)) == [
        ("Production", "removed", "replaced"),
        ("Staging", "moved", "label_change"),
    ]

    # The same request again changes nothing and publishes nothing.
    n = len(label_events(t))
    _ok(
        auth_client.post(
            "/model-hub/prompt-labels/assign-multiple-labels/",
            {"template_version_id": str(v2.id), "label_ids": [str(S.id)]},
            format="json",
        )
    )
    assert new_label_events(n) == []
    for d in label_events(t):
        assert d["changed_by"] is not None and "name" not in d["changed_by"]
    assert v3.labels.count() == 0


def test_deleting_a_prompt_through_the_api_releases_each_label_once(
    auth_client, organization, workspace
):
    t = make_template(organization, workspace)
    v1, v2 = make_version(t, 1), make_version(t, 2)
    labels = system_labels()
    writer.move_label(v1, labels["Production"])
    writer.move_label(v2, labels["Staging"])
    resp = auth_client.delete(f"/model-hub/prompt-templates/{t.id}/")
    assert resp.status_code in (200, 204), resp.content
    released = [d for d in label_events(t) if d["cause"] == "version_deleted"]
    assert sorted(
        (d["label"]["name"], d["from_version"]["version"]) for d in released
    ) == [
        ("Production", "v1"),
        ("Staging", "v2"),
    ]
    assert not PromptVersion.objects.filter(original_template=t, deleted=False).exists()


def test_bulk_delete_releases_labels_once_per_prompt(
    auth_client, organization, workspace
):
    t1 = make_template(organization, workspace, name="bulk-1")
    t2 = make_template(organization, workspace, name="bulk-2")
    labels = system_labels()
    writer.move_label(make_version(t1, 1), labels["Production"])
    writer.move_label(make_version(t2, 1), labels["Production"])
    _ok(
        auth_client.post(
            "/model-hub/prompt-templates/bulk-delete/",
            {"ids": [str(t1.id), str(t2.id)]},
            format="json",
        )
    )
    for t in (t1, t2):
        released = [d for d in label_events(t) if d["cause"] == "version_deleted"]
        assert len(released) == 1


def test_no_endpoint_means_no_event_rows_but_the_write_still_happens(
    organization, workspace
):
    from webhooks.models import Endpoint

    Endpoint.objects.filter(organization=organization).update(deleted=True)
    t = make_template(organization, workspace)
    v = make_version(t, 1)
    writer.move_label(v, system_labels()["Production"])
    assert _version_events(t.id) == [] and label_events(t) == []
    assert holders(t, system_labels()["Production"]) == ["v1"]


def test_deleting_a_folder_releases_the_labels_of_every_prompt_in_it(
    auth_client, organization, workspace
):
    from model_hub.models.prompt_folders import PromptFolder

    folder = PromptFolder.objects.create(
        name="census-folder", organization=organization, workspace=workspace
    )
    t = make_template(organization, workspace, name="in-folder")
    t.prompt_folder = folder
    t.save(update_fields=["prompt_folder"])
    labels = system_labels()
    writer.move_label(make_version(t, 1), labels["Production"])
    writer.move_label(make_version(t, 2), labels["Staging"])
    _ok(auth_client.delete(f"/model-hub/prompt-folders/{folder.id}/"))
    released = [d for d in label_events(t) if d["cause"] == "version_deleted"]
    assert sorted(
        (d["label"]["name"], d["from_version"]["version"]) for d in released
    ) == [
        ("Production", "v1"),
        ("Staging", "v2"),
    ]
    assert not PromptVersion.objects.filter(original_template=t, deleted=False).exists()


def test_a_producer_failure_loses_the_event_loudly_but_never_the_users_change(
    organization, workspace, monkeypatch, caplog
):
    from model_hub.services import prompt_events

    def broken(*args, **kwargs):
        raise ValueError("builder bug with prompt text inside")

    t = make_template(organization, workspace)
    v = make_version(t, 1)
    monkeypatch.setattr(prompt_events, "_thin_data", broken)
    with caplog.at_level("ERROR", logger="model_hub.prompt_events"):
        writer.move_label(v, system_labels()["Production"])
        make_version(t, 2, message="still saved")
    assert holders(t, system_labels()["Production"]) == ["v1"]
    assert PromptVersion.objects.filter(
        original_template=t, template_version="v2"
    ).exists()
    assert (
        label_events(t) == [] and len(_version_events(t.id)) == 1
    )  # v1's, from before
    failed = [
        r
        for r in caplog.records
        if getattr(r, "metric", "") == "webhooks.producer.failed"
    ]
    assert [(r.type, r.error_type) for r in failed] == [
        ("prompt.label.changed", "ValueError"),
        ("prompt.version.created", "ValueError"),
    ]
    # Ops alert WA11 (webhooks.core.alerts): an error line carrying the alert id, as the error pipeline routes it.
    assert [(r.levelname, r.alert) for r in failed] == [("ERROR", "WA11")] * 2
    assert all("prompt text" not in r.getMessage() for r in caplog.records)


def test_run_template_from_a_dataset_commits_one_version_and_a_draft_edit_none(
    auth_client, organization, workspace
):
    """``run_template`` with ``source: dataset`` stores a new committed version (no model call); editing a draft
    through the same route without ``is_run`` keeps it a draft."""
    t = make_template(organization, workspace, name="census-run")
    make_version(t, 1, draft=True)
    url = f"/model-hub/prompt-templates/{t.id}/run_template/"
    _ok(
        auth_client.post(url, {"prompt_config": CONFIG, "version": "v1"}, format="json")
    )
    assert _version_events(t.id) == []
    created = _ok(
        auth_client.post(
            url, {"prompt_config": CONFIG, "source": "dataset"}, format="json"
        )
    )
    [ev] = _version_events(t.id)
    assert ev.data["version"]["version"] == created["created_version"]


def _key_headers(key) -> dict:
    return {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}


def test_sdk_commit_names_the_caller_through_real_api_key_authentication(
    api_client, user, organization, workspace, monkeypatch
):
    """The SDK commits with a user API key: FA's own authentication class binds the key's user to the request, and
    the event names them (display name in the builder input; the stored data has names off)."""
    from accounts.models.user import OrgApiKey
    from model_hub.views import prompt_template as views

    monkeypatch.setattr(
        views, "track_mixpanel_event", lambda *a, **k: None
    )  # no analytics call leaves the test
    key = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="user", user=user
    )
    t = make_template(organization, workspace, name="census-sdk")
    make_version(t, 1, draft=True)
    response = api_client.post(
        f"/model-hub/prompt-templates/{t.id}/commit/",
        {"version_name": "v1", "message": "From the SDK"},
        format="json",
        HTTP_X_API_KEY=key.api_key,
        HTTP_X_SECRET_KEY=key.secret_key,
    )
    _ok(response)
    [ev] = _version_events(t.id)
    assert ev.data["version"]["created_by"] == {"id": str(user.id)}
    assert ev.builder_input["version"]["created_by"] == {
        "id": str(user.id),
        "name": "Test User",
    }


def test_the_remove_route_takes_a_system_label_off_a_version(
    auth_client, organization, workspace, user
):
    """The Workbench's label dropdown removes labels through this route. A system label (Production) was invisible
    to it (400 "Invalid label or version"); it is now removed, with one event."""
    t = make_template(organization, workspace, name="census-remove-system")
    v1 = make_version(t, 1)
    P = system_labels()["Production"]
    with writer.acting_as(user):
        writer.move_label(v1, P)
    n = len(label_events(t))
    _ok(
        auth_client.post(
            "/model-hub/prompt-labels/remove/",
            {"label_id": str(P.id), "version_id": str(v1.id)},
            format="json",
        )
    )
    [d] = label_events(t)[n:]
    assert (d["label"]["name"], d["action"], d["cause"]) == (
        "Production",
        "removed",
        "label_change",
    )
    assert holders(t, P) == []


def test_a_system_api_key_names_no_person_on_the_label_routes(
    api_client, user, organization, workspace
):
    """FA's authentication resolves a ``system`` key to the organization's oldest user so permissions
    work, but that user did not act, so the label routes name no person for it. A user key names its user."""
    from accounts.models.user import OrgApiKey

    system = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="system"
    )
    personal = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="user", user=user
    )
    t = make_template(organization, workspace, name="census-keys")
    make_version(t, 1)
    v2 = make_version(t, 2)
    labels = system_labels()
    P, S = labels["Production"], labels["Staging"]
    calls = [
        (
            system,
            f"/model-hub/prompt-labels/{t.id}/{P.id}/assign-label-by-id/",
            {"version": "v1"},
        ),
        (
            system,
            "/model-hub/prompt-labels/assign-multiple-labels/",
            {"template_version_id": str(v2.id), "label_ids": [str(S.id)]},
        ),
        (
            system,
            "/model-hub/prompt-labels/remove/",
            {"label_id": str(S.id), "version_id": str(v2.id)},
        ),
        (
            personal,
            f"/model-hub/prompt-labels/{t.id}/{P.id}/assign-label-by-id/",
            {"version": "v2"},
        ),
    ]
    for key, url, body in calls:
        _ok(api_client.post(url, body, format="json", **_key_headers(key)))
    got = [
        (
            e.data["label"]["name"],
            e.data["action"],
            e.builder_input["changed_by"],
            e.data["changed_by"],
        )
        for e in events(LABEL_EVENT)
        if e.data["prompt"]["id"] == str(t.id)
    ]
    assert got == [
        ("Production", "assigned", None, None),
        ("Staging", "assigned", None, None),
        ("Staging", "removed", None, None),
        (
            "Production",
            "moved",
            {"id": str(user.id), "name": "Test User"},
            {"id": str(user.id)},
        ),
    ]


def test_a_database_error_while_publishing_fails_the_change_with_its_event(
    organization, workspace, monkeypatch, caplog
):
    """Only a producer bug is isolated (the event is lost loudly, the change kept). A database error while
    publishing (a timeout, a serialization failure, a lost connection) fails the change together with its event, as
    the transactional outbox requires, so the caller can retry both."""
    from django.db import OperationalError

    from model_hub.services import prompt_events

    t = make_template(organization, workspace)
    v = make_version(t, 1)
    P = system_labels()["Production"]

    def timeout(*args, **kwargs):
        raise OperationalError("canceling statement due to statement timeout")

    monkeypatch.setattr(prompt_events, "_thin_data", timeout)
    with caplog.at_level("ERROR", logger="model_hub.prompt_events"):
        with pytest.raises(OperationalError):
            writer.move_label(v, P)
        with pytest.raises(OperationalError):
            make_version(t, 2, message="not saved without its event")
    assert holders(t, P) == [] and label_events(t) == []
    assert not PromptVersion.objects.filter(
        original_template=t, template_version="v2"
    ).exists()
    assert not [
        r
        for r in caplog.records
        if getattr(r, "metric", "") == "webhooks.producer.failed"
    ]
