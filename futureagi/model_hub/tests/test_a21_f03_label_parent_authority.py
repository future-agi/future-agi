"""F03 native routed parent-write authority regressions; isolated VM only."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from django.db import close_old_connections, connection, transaction
from rest_framework.exceptions import PermissionDenied

from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel, PromptLabelHead
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.services.prompt_label import assign_labels, assign_labels_to_version
from model_hub.tests.a21_authority_support import (
    assert_admitted,
    grant,
    post,
    session_client,
    snapshot,
)
from tfc.constants.levels import Level

pytest_plugins = ["model_hub.tests.a21_authority_support"]
pytestmark = pytest.mark.django_db(transaction=True)
ROUTE = "/model-hub/prompt-labels/assign-multiple-labels/"


@pytest.fixture
def publications(monkeypatch):
    attempts = []

    def publish(**kwargs):
        attempts.append(kwargs)
        return None

    # Only the final inert delivery/outbox boundary. All real builders, writer
    # locks, version saves, FK/M2M operations, heads and signals still execute.
    monkeypatch.setattr("webhooks.publish.publish", publish)
    return attempts


def label(data, workspace, *, shared=False):
    return PromptLabel.no_workspace_objects.create(
        name=f"synthetic-label-{uuid4().hex}",
        type=LabelTypeChoices.SYSTEM.value if shared else LabelTypeChoices.CUSTOM.value,
        organization=None if shared else data.org,
        workspace=None if shared else workspace,
    )


def prompt(data, workspace, *, legacy=False):
    template = PromptTemplate.no_workspace_objects.create(
        name=f"synthetic-private-parent-{uuid4().hex}",
        description="synthetic private parent content",
        organization=data.org,
        workspace=None if legacy else workspace,
        created_by=data.user,
    )
    old = PromptVersion.objects.create(
        original_template=template,
        template_version="1",
        is_default=False,
        prompt_config_snapshot=[],
    )
    target = PromptVersion.objects.create(
        original_template=template,
        template_version="2",
        is_default=False,
        prompt_config_snapshot=[],
    )
    existing = label(data, workspace)
    assign_labels(target, [existing])
    assert PromptTemplate._base_manager.get(pk=template.pk).workspace_id == (
        None if legacy else workspace.pk
    )
    return template, old, target, existing


def state():
    return snapshot(
        PromptTemplate,
        PromptVersion,
        PromptLabel,
        PromptLabelHead,
        PromptVersion.labels.through,
    )


@pytest.mark.parametrize(
    "shared", [False, True], ids=["custom-a-label", "shared-system-label"]
)
def test_a_writer_cannot_change_b_parent_with_any_label(
    authority_scope, publications, shared
):
    data = authority_scope
    grant(data, data.a)
    template, old, target, existing = prompt(data, data.b)
    allowed_label = label(data, data.a, shared=shared)
    # A stale head is an additional write trap: authorization must precede repair.
    PromptLabelHead.objects.filter(template=template, label=existing).update(
        version=old
    )
    client = session_client(data)
    publications.clear()
    before = state()
    response = post(
        client,
        ROUTE,
        {"template_version_id": str(target.pk), "label_ids": [str(allowed_label.pk)]},
        data.a,
    )
    assert_admitted(response, data, data.a, "PromptLabelViewSet")
    assert response.status_code in {400, 403, 404}
    assert state() == before
    assert publications == []
    assert template.name not in response.content.decode()
    assert template.description not in response.content.decode()


@pytest.mark.parametrize("workspace_name", ["a", "b", "default"])
@pytest.mark.parametrize("shared", [False, True])
def test_authorized_parent_preserves_native_heads_revisions_and_events(
    authority_scope, publications, workspace_name, shared
):
    data = authority_scope
    workspace = getattr(data, workspace_name)
    grant(data, workspace)
    template, old, target, existing = prompt(
        data, workspace, legacy=workspace_name == "default"
    )
    selected_label = label(data, workspace, shared=shared)
    assign_labels(old, [selected_label])
    old_revision = PromptLabelHead.objects.get(
        template=template, label=selected_label
    ).revision
    client = session_client(data)
    publications.clear()
    response = post(
        client,
        ROUTE,
        {"template_version_id": str(target.pk), "label_ids": [str(selected_label.pk)]},
        workspace,
    )
    assert_admitted(response, data, workspace, "PromptLabelViewSet")
    assert response.status_code == 200
    attached = PromptVersion.labels.through.objects.filter(promptversion=target)
    assert set(attached.values_list("promptlabel_id", flat=True)) == {selected_label.pk}
    head = PromptLabelHead.objects.get(template=template, label=selected_label)
    assert head.version_id == target.pk
    assert head.revision == old_revision + 1
    assert not PromptVersion.labels.through.objects.filter(
        promptversion=old, promptlabel=selected_label
    ).exists()
    assert publications
    assert all(row["subject_id"] == str(template.pk) for row in publications)
    assert PromptTemplate._base_manager.get(pk=template.pk).workspace_id == (
        None if workspace_name == "default" else workspace.pk
    )


@pytest.mark.parametrize(
    "bad",
    [
        "foreign_parent",
        "deleted_parent",
        "deleted_version",
        "foreign_label",
        "deleted_label",
        "other_workspace_label",
    ],
)
def test_routed_live_parent_and_separate_label_denials_have_zero_effects(
    authority_scope, publications, bad
):
    data = authority_scope
    grant(data, data.a)
    template, _, target, _ = prompt(data, data.a)
    selected_label = label(data, data.a)
    if bad == "foreign_parent":
        PromptTemplate._base_manager.filter(pk=template.pk).update(
            organization=data.foreign_org
        )
    elif bad == "deleted_parent":
        PromptTemplate._base_manager.filter(pk=template.pk).update(deleted=True)
    elif bad == "deleted_version":
        PromptVersion._base_manager.filter(pk=target.pk).update(deleted=True)
    elif bad == "foreign_label":
        PromptLabel._base_manager.filter(pk=selected_label.pk).update(
            organization=data.foreign_org
        )
    elif bad == "deleted_label":
        PromptLabel._base_manager.filter(pk=selected_label.pk).update(deleted=True)
    else:
        PromptLabel._base_manager.filter(pk=selected_label.pk).update(workspace=data.b)
    client = session_client(data)
    publications.clear()
    before = state()
    response = post(
        client,
        ROUTE,
        {"template_version_id": str(target.pk), "label_ids": [str(selected_label.pk)]},
        data.a,
    )
    assert response.status_code in {400, 403, 404}
    assert state() == before
    assert publications == []


@pytest.mark.parametrize(
    "bad",
    [
        "missing_workspace",
        "deleted_workspace",
        "inactive_workspace",
        "foreign_workspace",
        "viewer",
        "revoked",
        "inactive_membership",
        "inactive_actor",
        "missing_actor",
    ],
)
def test_direct_service_rechecks_live_selected_authority_without_ambient_scope(
    authority_scope, publications, bad
):
    data = authority_scope
    membership = grant(data, data.a)
    _, _, target, _ = prompt(data, data.a)
    selected_label = label(data, data.a)
    user, workspace = data.user, data.a
    if bad == "missing_workspace":
        workspace = None
    elif bad == "deleted_workspace":
        Workspace._base_manager.filter(pk=workspace.pk).update(deleted=True)
    elif bad == "inactive_workspace":
        Workspace._base_manager.filter(pk=workspace.pk).update(is_active=False)
    elif bad == "foreign_workspace":
        Workspace._base_manager.filter(pk=workspace.pk).update(
            organization=data.foreign_org
        )
    elif bad == "viewer":
        WorkspaceMembership._base_manager.filter(pk=membership.pk).update(
            level=Level.WORKSPACE_VIEWER
        )
    elif bad == "revoked":
        WorkspaceMembership._base_manager.filter(pk=membership.pk).update(
            is_active=False
        )
    elif bad == "inactive_membership":
        type(data.membership)._base_manager.filter(pk=data.membership.pk).update(
            is_active=False
        )
    elif bad == "inactive_actor":
        User.objects.filter(pk=user.pk).update(is_active=False)
    else:
        user = None
    publications.clear()
    before = state()
    with pytest.raises(PermissionDenied):
        assign_labels_to_version(target.pk, [str(selected_label.pk)], user, workspace)
    assert state() == before
    assert publications == []


@pytest.mark.parametrize("change", ["move_parent", "revoke", "delete_version"])
def test_authority_is_rechecked_after_waiting_for_real_parent_lock(
    authority_scope, publications, change
):
    from model_hub.services.prompt_locks import lock_prompts

    data = authority_scope
    membership = grant(data, data.a)
    template, _, target, _ = prompt(data, data.a)
    selected_label = label(data, data.a)
    started = Event()
    worker_pid = []

    def assign():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                worker_pid.append(cursor.fetchone()[0])
            started.set()
            assign_labels_to_version(
                target.pk, [str(selected_label.pk)], data.user, data.a
            )
        finally:
            connection.close()

    publications.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            lock_prompts([template.pk])
            future = pool.submit(assign)
            assert started.wait(5)
            deadline = time.monotonic() + 5
            while True:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [worker_pid[0]])
                    blocked = cursor.fetchone()[0]
                if blocked:
                    break
                assert time.monotonic() < deadline, (
                    "Writer never waited for the parent lock"
                )
                time.sleep(0.01)
            if change == "move_parent":
                PromptTemplate._base_manager.filter(pk=template.pk).update(
                    workspace=data.b
                )
            elif change == "revoke":
                WorkspaceMembership._base_manager.filter(pk=membership.pk).update(
                    is_active=False
                )
            else:
                PromptVersion._base_manager.filter(pk=target.pk).update(deleted=True)
            before = state()
        with pytest.raises(PermissionDenied):
            future.result(timeout=10)
    assert state() == before
    assert publications == []


def test_direct_service_authorized_without_ambient_scope(authority_scope, publications):
    data = authority_scope
    grant(data, data.b)
    _, _, target, _ = prompt(data, data.b)
    selected_label = label(data, data.b, shared=True)
    publications.clear()
    assign_labels_to_version(target.pk, [str(selected_label.pk)], data.user, data.b)
    assert PromptVersion.labels.through.objects.filter(
        promptversion=target, promptlabel=selected_label
    ).exists()
    assert publications
