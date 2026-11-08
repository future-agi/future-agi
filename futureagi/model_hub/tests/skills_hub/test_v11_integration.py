"""Shared v1/v1.1 seams: real keys, protected releases and transport fences."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models.user import OrgApiKey
from model_hub.models.skills_hub import HubSkill, HubSkillDraft
from model_hub.models.skills_hub_v11 import HubSkillJob
from model_hub.services.skills_hub import drafts, labels, versions
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.packages import manifest_digest
from model_hub.services.skills_hub.types import Actor
from model_hub.services.skills_hub.v11.jobs import finish_job
from model_hub.services.skills_hub.v11.types import SkillScope
from tfc.utils.ssrf_guard import SsrfBlocked, safe_fetch

pytestmark = pytest.mark.django_db
BASE = "/model-hub/skills/"


def key_client(user, organization, workspace, *, kind="user"):
    key = OrgApiKey.no_workspace_objects.create(
        name="Synthetic integration principal",
        type=kind,
        api_key=f"test-{uuid4()}",
        secret_key=f"test-{uuid4()}",
        user=user if kind != "system" else None,
        organization=organization,
        workspace=workspace,
    )
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client, key


def test_new_default_and_atomic_bulk_release(
    organization, workspace, hub_actor, make_skill_files
):
    skill = versions.create_skill(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        name="new-policy",
        description="Use when a reviewed policy should be applied.",
        tags=[],
    )
    assert skill.protected_label_names == ["production"]
    assert skill.release_revision == 0
    args = {
        "skill": skill,
        "actor": hub_actor,
        "files": make_skill_files(skill.name),
        "note": "",
        "base_version": None,
        "labels": ["staging", "production"],
        "acknowledge_warnings": True,
        "source": {"type": "api"},
    }
    with pytest.raises(SkillsHubError, match="release revision") as error:
        versions.publish_files(**args)
    assert error.value.code == "release_precondition_required"
    assert not skill.versions.exists()
    assert skill.drafts.exists()
    result = versions.publish_files(**args, expected_release_revision=0)
    assert result.release_revision == 2
    assert list(
        skill.label_events.order_by("release_revision").values_list(
            "release_revision", flat=True
        )
    ) == [1, 2]
    args["base_version"] = 1
    repeat = versions.publish_files(**args, expected_release_revision=2)
    assert repeat.unchanged and repeat.release_revision == 2
    with pytest.raises(SkillsHubError) as error:
        versions.publish_files(**args, expected_release_revision=0, force=True)
    assert error.value.code == "release_conflict"


def test_protected_label_noop_digest_and_aba(
    hub_skill, hub_actor, publish_skill, make_skill_files
):
    first = publish_skill(hub_skill).version
    second = publish_skill(
        hub_skill, files=make_skill_files(body="A changed reviewed instruction.")
    ).version
    HubSkill.no_workspace_objects.filter(pk=hub_skill.pk).update(
        protected_label_names=["production"]
    )
    for number, current, revision in [(1, None, 0), (2, 1, 1), (1, 2, 2)]:
        labels.set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=number,
            expected_version=current,
            expected_release_revision=revision,
        )
    with pytest.raises(SkillsHubError) as error:
        labels.set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=second.number,
            expected_version=first.number,
            expected_release_revision=1,
        )
    assert error.value.code == "release_conflict"
    with pytest.raises(SkillsHubError) as error:
        labels.set_label(
            skill=hub_skill,
            actor=hub_actor,
            name="production",
            version_number=1,
            expected_version=1,
            expected_release_revision=3,
            expected_digest=second.digest,
        )
    assert error.value.code == "candidate_changed"
    labels.set_label(
        skill=hub_skill,
        actor=hub_actor,
        name="production",
        version_number=1,
        expected_version=1,
        expected_release_revision=3,
        expected_digest=first.digest,
    )
    hub_skill.refresh_from_db()
    assert hub_skill.release_revision == 3


def test_system_key_private_metadata_and_lookups(
    user, organization, workspace, hub_skill
):
    client, _ = key_client(user, organization, workspace, kind="system")
    response = client.get(BASE)
    assert response.status_code == 200, response.data
    row = response.data["result"]["results"][0]
    assert row["has_draft"] is False
    assert row["release_revision"] == 0 and row["protected_label_names"] == []
    assert response.data["result"]["stats"]["my_drafts"] == 0
    detail = client.get(f"{BASE}{hub_skill.pk}/").data["result"]
    assert detail["drafts"] == []
    data: dict
    for identity in [hub_skill.pk, uuid4()]:
        for method, suffix, data in [
            ("get", "draft/", {}),
            ("put", "draft/", {}),
            ("post", "draft/publish/", {}),
            ("get", "compare/?from=draft&to=1", {}),
        ]:
            response = getattr(client, method)(
                f"{BASE}{identity}/{suffix}", data, format="json"
            )
            assert response.status_code == 403, response.data
            assert response.data["code"] == "principal_not_supported"


def test_key_aware_draft_publication_and_unknown_source(
    user, organization, workspace, hub_skill, make_skill_files
):
    client, key = key_client(user, organization, workspace)
    draft = hub_skill.drafts.get(user=user)
    files = make_skill_files(hub_skill.name)
    response = client.put(
        f"{BASE}{hub_skill.pk}/draft/",
        {
            "files": files,
            "note": "",
            "base_version": None,
            "revision": draft.revision,
            "source": {"provider": "github"},
        },
        format="json",
    )
    assert response.status_code == 400
    saved = drafts.save_draft(
        skill=hub_skill,
        user=user,
        files=files,
        note="",
        base_version=None,
        revision=draft.revision,
    )
    result = client.post(
        f"{BASE}{hub_skill.pk}/draft/publish/",
        {
            "revision": saved.revision,
            "expected_digest": manifest_digest(files),
            "labels": [],
            "acknowledge_warnings": True,
        },
        format="json",
    )
    assert result.status_code == 201, result.data
    version = hub_skill.versions.get(number=1)
    assert version.created_by_api_key_id == key.pk
    assert result.data["result"]["evidence"] is None
    assert result.data["result"]["release_revision"] == 0


def test_system_key_cannot_publish_another_users_draft(
    user, organization, workspace, hub_skill
):
    _, key = key_client(user, organization, workspace, kind="system")
    with pytest.raises(SkillsHubError) as error:
        drafts.publish_draft(
            skill=hub_skill,
            user=user,
            actor=Actor(user=user, api_key=key),
            labels=[],
            acknowledge_warnings=True,
        )
    assert error.value.code == "principal_not_supported"
    assert hub_skill.drafts.exists()


@pytest.mark.parametrize("base_version", [None, 1])
def test_recreated_draft_keeps_cas_sequence_but_rebases_source(
    hub_skill, hub_actor, user, make_skill_files, publish_skill, base_version
):
    version = publish_skill(hub_skill).version
    source = {"type": "import", "provider": "github", "commit_sha": "a" * 40}
    # A published provenance basis is trusted server state, never request input.
    version.source = source
    version.save(update_fields=["source"])
    original = hub_skill.drafts.get(user=user)
    original.source = {**source, "commit_sha": "b" * 40}
    original.save(update_fields=["source"])
    drafts.discard_draft(skill=hub_skill, user=user)
    tombstone = HubSkillDraft.all_objects.get(pk=original.pk)
    assert tombstone.source == {} and tombstone.files == []
    assert tombstone.revision == original.revision + 1
    replacement = drafts.save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(),
        note="New authoring session",
        base_version=base_version,
        revision=0,
    )
    assert replacement.pk == original.pk
    assert replacement.revision == original.revision + 2
    assert replacement.source == (source if base_version else {})
    with pytest.raises(SkillsHubError) as error:
        drafts.publish_draft(
            skill=hub_skill,
            user=user,
            actor=hub_actor,
            revision=original.revision,
            labels=[],
            acknowledge_warnings=True,
        )
    assert error.value.code == "draft_conflict"
    assert hub_skill.versions.count() == 1


def test_publication_tombstone_erases_source_after_release(
    hub_skill, hub_actor, user, make_skill_files
):
    original = hub_skill.drafts.get(user=user)
    original.source = {"type": "draft", "operation": "reviewed_edit"}
    original.save(update_fields=["source"])
    saved = drafts.save_draft(
        skill=hub_skill,
        user=user,
        files=make_skill_files(),
        note="Reviewed",
        base_version=None,
        revision=original.revision,
    )
    result = drafts.publish_draft(
        skill=hub_skill,
        user=user,
        actor=hub_actor,
        revision=saved.revision,
        labels=[],
        acknowledge_warnings=True,
    )
    assert result.version.source == original.source
    tombstone = HubSkillDraft.all_objects.get(pk=saved.pk)
    assert tombstone.source == {} and tombstone.files == []
    assert tombstone.revision == saved.revision + 1
    assert not hub_skill.drafts.exists()


def test_github_live_sender_retains_capacity_until_settlement(
    organization, workspace, user, hub_skill
):
    job = HubSkillJob.objects.create(
        organization=organization,
        workspace=workspace,
        owner_user=user,
        skill=hub_skill,
        kind="github",
        idempotency_key=uuid4(),
        request_digest="sha256:" + "a" * 64,
        workflow_id=f"skills-job-{uuid4()}",
        status="running",
        claim_generation=2,
        github_sender_active=True,
        started_at=timezone.now(),
        deadline_at=timezone.now() - timedelta(seconds=1),
    )
    scope = SkillScope(organization.pk, workspace.pk)
    assert finish_job(scope=scope, job_id=job.pk)["status"] == "timed_out"
    job.refresh_from_db()
    assert job.reservation_released_at is None
    HubSkillJob.objects.filter(pk=job.pk).update(github_sender_active=False)
    finish_job(scope=scope, job_id=job.pk)
    job.refresh_from_db()
    assert job.reservation_released_at is not None


def test_mounted_options_and_disabled_slice(
    user, organization, workspace, hub_skill, settings
):
    client, _ = key_client(user, organization, workspace)
    settings.SKILLS_TESTING_ENABLED = False
    options = client.get(BASE + "testing-options/")
    assert options.status_code == 200, options.data
    assert options.data["result"]["enabled"] is False
    detail = client.get(f"{BASE}{hub_skill.pk}/")
    assert detail.data["result"]["permissions"]["can_run_tests"] is False


@pytest.mark.parametrize(
    "target",
    [
        "https://example.com/file",
        "http://api.github.com/file",
        "https://api.github.com:444/file",
        "https://api.github.com/other-repository",
        "https://user@api.github.com/file",
    ],
)
def test_restricted_transport_rejects_before_dns(target):
    with patch("tfc.utils.ssrf_guard._resolve_pinned_ip") as resolve:
        with pytest.raises(SsrfBlocked):
            safe_fetch(
                target,
                allowed_hosts={"api.github.com"},
                allowed_schemes={"https"},
                allowed_ports={443},
                allowed_urls={"https://api.github.com/file"},
            )
        resolve.assert_not_called()


def response(status=200, headers=None, chunks=()):
    return SimpleNamespace(
        status=status,
        headers=headers or {},
        connection=None,
        read1=Mock(side_effect=[*chunks, b""]),
        close=Mock(),
        release_conn=Mock(),
    )


def test_restricted_redirect_rejected_before_second_connection():
    first = response(302, {"Location": "https://codeload.github.com/wrong/repo/sha"})
    pool = Mock(request=Mock(return_value=first))
    with (
        patch("tfc.utils.ssrf_guard._resolve_pinned_ip", return_value="8.8.8.8") as dns,
        patch("tfc.utils.ssrf_guard._open_pinned_pool", return_value=pool),
    ):
        with pytest.raises(SsrfBlocked):
            safe_fetch(
                "https://api.github.com/file",
                allowed_hosts={"api.github.com", "codeload.github.com"},
                allowed_urls={"https://api.github.com/file"},
            )
        assert dns.call_count == 1
        first.close.assert_called_once()


def test_restricted_encoding_rejected_before_read():
    first = response(headers={"Content-Encoding": "gzip"})
    with (
        patch("tfc.utils.ssrf_guard._resolve_pinned_ip", return_value="8.8.8.8"),
        patch(
            "tfc.utils.ssrf_guard._open_pinned_pool",
            return_value=Mock(request=Mock(return_value=first)),
        ),
    ):
        with pytest.raises(SsrfBlocked):
            safe_fetch(
                "https://api.github.com/file",
                method="GET",
                allowed_content_encodings={"", "identity"},
            )
        first.read1.assert_not_called()


def test_transport_bounds_actual_bytes_and_deadline():
    first = response(chunks=[b"abcdef"])
    with (
        patch("tfc.utils.ssrf_guard._resolve_pinned_ip", return_value="8.8.8.8"),
        patch(
            "tfc.utils.ssrf_guard._open_pinned_pool",
            return_value=Mock(request=Mock(return_value=first)),
        ),
        patch("tfc.utils.ssrf_guard.time.monotonic", return_value=0),
    ):
        with pytest.raises(ValueError, match="body exceeds"):
            safe_fetch(
                "https://api.github.com/file", method="GET", max_bytes=5, deadline=10
            )
    with (
        patch("tfc.utils.ssrf_guard._resolve_pinned_ip") as dns,
        patch("tfc.utils.ssrf_guard.time.monotonic", return_value=11),
    ):
        with pytest.raises(ValueError, match="deadline"):
            safe_fetch("https://api.github.com/file", deadline=10)
        dns.assert_not_called()


def test_deadline_watchdog_can_interrupt_headers_before_response_exists():
    import socket

    transport = SimpleNamespace(sock=Mock())
    pool = Mock()
    pool._get_conn.return_value = transport
    timers = []

    def timer_factory(interval, callback):
        timer = Mock()
        timer.callback = callback
        timers.append(timer)
        return timer

    def header_wait(*args, **kwargs):
        timers[0].callback()
        raise ValueError("Synthetic header timeout")

    pool.request.side_effect = header_wait
    with (
        patch("tfc.utils.ssrf_guard._resolve_pinned_ip", return_value="8.8.8.8"),
        patch("tfc.utils.ssrf_guard._open_pinned_pool", return_value=pool),
        patch("tfc.utils.ssrf_guard.threading.Timer", side_effect=timer_factory),
        patch("tfc.utils.ssrf_guard.time.monotonic", return_value=0),
    ):
        with pytest.raises(ValueError, match="header timeout"):
            safe_fetch("https://api.github.com/file", method="GET", deadline=10)
    transport.sock.shutdown.assert_called_once_with(socket.SHUT_RDWR)
    timers[0].cancel.assert_called_once()
    pool.close.assert_called_once()
