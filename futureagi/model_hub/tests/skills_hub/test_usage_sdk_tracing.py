"""SDK retrieval accounting never claims a load or counts native verification."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models.query import QuerySet
from django.urls import include, path
from django.utils import timezone
from rest_framework.test import APIClient

from model_hub.models.skills_hub import HubSkill, HubSkillUsageDaily
from model_hub.models.skills_hub_v12 import HubSkillContextCredential
from model_hub.services.skills_hub import usage
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v11.types import SkillPrincipal, SkillScope
from model_hub.services.skills_hub.v12.credentials import issue_credential
from model_hub.services.skills_hub.versions import persist_immutable_version_locked
from model_hub.views.skills_hub import fetch
from simulate.models.hosted_harness import HostedHarnessJob

pytestmark = [pytest.mark.django_db, pytest.mark.integration]
urlpatterns = [path("model-hub/skills/", include("model_hub.urls_skills_hub"))]
BASE = "/model-hub/skills/"
HEADERS = {
    "HTTP_USER_AGENT": "futureagi-python/1.2.3",
    "HTTP_X_FI_SKILLS_TRACING": "on",
}


@pytest.fixture(autouse=True)
def routes(settings):
    settings.ROOT_URLCONF = __name__
    settings.SKILLS_CONTEXT_CONSUMPTION_ENABLED = True
    settings.MIDDLEWARE = [
        "tfc.middleware.workspace_context.WorkspaceContextMiddleware",
        *settings.MIDDLEWARE,
    ]


@pytest.fixture
def principal(user, organization, workspace):
    return SkillPrincipal(
        SkillScope(organization.pk, workspace.pk), user.pk, None, "human"
    )


@pytest.fixture(params=["skill", "agent_context"])
def released(request, organization, workspace, hub_actor, make_skill_files):
    kind = request.param
    resource = HubSkill.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        kind=kind,
        name="fetch-fixture",
        description="Synthetic fetch accounting fixture",
        protected_label_names=[],
    )
    if kind == "skill":
        files = make_skill_files(name=resource.name)
        frontmatter = {"name": resource.name, "description": resource.description}
    else:
        files = [
            {"path": "AGENTS.md", "content": "Read synthetic policy.\n"},
            {
                "path": ".futureagi/context.json",
                "content": json.dumps({
                    "schema": 1,
                    "kind": kind,
                    "name": resource.name,
                    "description": resource.description,
                    "entrypoint": "AGENTS.md",
                    "owned_paths": ["AGENTS.md"],
                    "links": [],
                }),
            },
        ]
        frontmatter = {}
    with transaction.atomic():
        locked = HubSkill.no_workspace_objects.select_for_update().get(pk=resource.pk)
        version, _ = persist_immutable_version_locked(
            skill=locked,
            actor=hub_actor,
            files=files,
            note="",
            parent_number=None,
            origin_number=None,
            description=resource.description,
            frontmatter=frontmatter,
            checks={"summary": {"errors": 0}},
            source={},
        )
    return version


@pytest.fixture
def consumer(principal, released):
    issued = issue_credential(
        principal=principal,
        request={
            "purpose": "consume",
            "grants": [{
                "kind": released.skill.kind,
                "resource_id": str(released.skill_id),
                "labels": [],
                "version_ids": [str(released.pk)],
            }],
            "binding_ids": [],
            "expires_at": timezone.now() + timedelta(days=1),
        },
        idempotency_key=uuid4(),
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Context {issued['secret']}")
    credential_id = issued["secret"].split(".", 1)[0]
    return client, credential_id


def resolve(client, version, **headers):
    prefix = "contexts/" if version.skill.kind == "agent_context" else "consumption/"
    return client.get(
        BASE + prefix + "resolve/",
        {"name": version.skill.name, "version": version.number},
        **headers,
    )


@pytest.mark.parametrize("agent,expected", [
    ("futureagi-python/1.2.3", "1.2.3"),
    ("futureagi-typescript/2.0.0", "2.0.0"),
    ("futureagi-python/1.2.3rc1", "1.2.3rc1"),
    ("futureagi-typescript/1.2.3-beta.1", "1.2.3-beta.1"),
    ("futureagi-python/not-a-version", None),
    ("futureagi-python/1.2.3/private/path", None),
    ("futureagi-python/1.2.3 email@example.test", None),
    ("futureagi-python/" + "1" * 33, None),
    ("futureagi-python/1.2.3\n", None),
    ("futureagi-ruby/1.2.3", None),
    ("curl/8.0.0", None),
    ("", None),
])
def test_only_sdk_version_is_retained(agent, expected):
    assert usage.fetch_metadata({"User-Agent": agent}) == {
        "sdk_version": expected, "tracing_mode": None,
    }


@pytest.mark.parametrize("mode", ["on", "load_only", "off", "no_otel"])
def test_known_modes(mode):
    assert usage.fetch_metadata({"X-FI-Skills-Tracing": mode}) == {
        "sdk_version": None, "tracing_mode": mode,
    }


@pytest.mark.parametrize("mode", [None, "", "ON", "true", "on,off", " off ", "private/path"])
def test_unknown_modes_are_null(mode):
    assert usage.fetch_metadata({"X-FI-Skills-Tracing": mode})["tracing_mode"] is None


@pytest.mark.parametrize("version,mode,expected", [
    ("1.2.3", "on", True),
    ("1.2.3", "load_only", True),
    ("1.10.0", "on", True),
    ("1.2.2", "on", False),
    ("1.2.3rc1", "on", False),
    ("1.2.3", "off", False),
    ("1.2.3", "no_otel", False),
    ("1.2.3", None, False),
    (None, "on", False),
    ("unknown", "on", False),
])
def test_can_record_requires_a_supported_version_and_mode(version, mode, expected):
    with patch.object(usage, "MIN_TRACING_SDK", "1.2.3"):
        assert usage.can_record(version, mode) is expected


def test_unconfirmed_release_never_claims_recording_support():
    with patch.object(usage, "MIN_TRACING_SDK", None):
        assert usage.can_record("999.0.0", "on") is False


def test_sdk_bucket_updates_and_clears_metadata(hub_skill, publish_skill):
    version = publish_skill(hub_skill).version
    args = dict(version=version, channel="sdk", consumer_key="credential:fixture")
    usage.record_fetch(**args, consumer_label="First", sdk_version="1.2.3", tracing_mode="on")
    usage.record_fetch(**args, consumer_label="Renamed", sdk_version="1.3.0", tracing_mode="off")
    bucket = hub_skill.usage_days.get()
    assert (bucket.count, bucket.consumer_label, bucket.sdk_version, bucket.tracing_mode) == (
        2, "Renamed", "1.3.0", "off",
    )
    usage.record_fetch(**args, consumer_label="Renamed")
    bucket.refresh_from_db()
    assert (bucket.count, bucket.sdk_version, bucket.tracing_mode) == (3, None, None)


def test_metadata_create_race_keeps_the_last_values(hub_skill, publish_skill):
    args = dict(
        version=publish_skill(hub_skill).version,
        channel="sdk", consumer_key="credential:fixture", consumer_label="Fixture",
    )
    usage.record_fetch(**args)
    actual_update = QuerySet.update
    calls = []

    def miss_once(queryset, **values):
        calls.append(values)
        return 0 if len(calls) == 1 else actual_update(queryset, **values)

    with (
        patch.object(QuerySet, "update", new=miss_once),
        patch.object(HubSkillUsageDaily.objects, "create", side_effect=IntegrityError("race")),
    ):
        usage.record_fetch(**args, sdk_version="1.2.3", tracing_mode="load_only")
    bucket = hub_skill.usage_days.get()
    assert (bucket.count, bucket.sdk_version, bucket.tracing_mode) == (2, "1.2.3", "load_only")


def test_consumer_uses_latest_bucket_not_lexical_max(hub_skill, publish_skill, make_skill_files):
    first = publish_skill(hub_skill).version
    second = publish_skill(hub_skill, files=make_skill_files(body="Second")).version
    now = timezone.now()
    for version, days, label, sdk, mode in [
        (first, 1, "Zulu", "9.0.0", "on"),
        (second, 0, "Alpha", "2.0.0", "off"),
    ]:
        hub_skill.usage_days.create(
            version=version, day=(now - timedelta(days=days)).date(),
            channel="sdk", consumer_key="credential:same", consumer_label=label,
            sdk_version=sdk, tracing_mode=mode, count=2, last_at=now - timedelta(days=days),
        )
    with patch.object(usage, "MIN_TRACING_SDK", "1.2.3"):
        summary = usage.usage_summary(hub_skill, 7)
        batch = usage.consumers_7d([hub_skill.pk])
    row, = summary["consumers"]
    assert row == {
        "label": "Alpha", "channel": "sdk", "count": 4, "last_at": now,
        "sdk_version": "2.0.0", "tracing_mode": "off", "can_record": False,
    }
    assert batch == {hub_skill.pk: [row]}
    assert summary["by_channel"]["sdk"] == summary["total"] == 4
    assert usage.fetches_7d_map([hub_skill.pk]) == {hub_skill.pk: 4}


def test_batch_window_live_scope_and_empty_inputs(hub_skill, publish_skill, hub_other_workspace):
    version = publish_skill(hub_skill).version
    now = timezone.now()
    for days in (0, 6, 7, -1):
        hub_skill.usage_days.create(
            version=version, day=(now - timedelta(days=days)).date(),
            channel="sdk", consumer_key="credential:fixture", consumer_label="Fixture",
            count=1, last_at=now - timedelta(days=days),
        )
    unknown = uuid4()
    assert usage.consumers_7d([]) == {}
    assert usage.consumers_7d([unknown]) == {}
    assert usage.consumers_7d([hub_skill.pk])[hub_skill.pk][0]["count"] == 2
    original_workspace = hub_skill.workspace_id
    # Revalidating an incorrectly scoped in-memory resource cannot retrieve rows.
    hub_skill.workspace_id = hub_other_workspace.pk
    with pytest.raises(SkillsHubError):
        usage.usage_summary(hub_skill, 7)
    hub_skill.workspace_id = original_workspace
    HubSkill.no_workspace_objects.filter(pk=hub_skill.pk).update(deleted=True)
    assert usage.consumers_7d([hub_skill.pk]) == {}
    assert usage.fetches_7d_map([hub_skill.pk]) == {}
    with pytest.raises(SkillsHubError):
        usage.usage_summary(hub_skill, 7)


def test_consumer_batch_does_not_query_per_consumer(
    hub_skill, publish_skill, django_assert_num_queries
):
    version = publish_skill(hub_skill).version
    now = timezone.now()
    HubSkillUsageDaily.objects.bulk_create([
        HubSkillUsageDaily(
            skill=hub_skill,
            version=version,
            day=now.date(),
            channel="sdk",
            consumer_key=f"credential:{index}",
            consumer_label="Synthetic consumer",
            sdk_version="1.2.3",
            tracing_mode="load_only",
            count=1,
            last_at=now,
        )
        for index in range(25)
    ])
    with django_assert_num_queries(2):
        result = usage.consumers_7d([hub_skill.pk])
    assert len(result[hub_skill.pk]) == 25
    assert all(row["tracing_mode"] == "load_only" for row in result[hub_skill.pk])


def test_existing_fetch_path_parses_metadata(hub_skill, publish_skill, user):
    request = SimpleNamespace(
        user=user,
        headers={"User-Agent": "futureagi-typescript/1.2.3", "X-FI-Skills-Tracing": "load_only"},
    )
    fetch(request, publish_skill(hub_skill).version)
    bucket = hub_skill.usage_days.get()
    assert (bucket.channel, bucket.sdk_version, bucket.tracing_mode) == ("api", "1.2.3", "load_only")


def test_one_fetch_per_successful_resolve_and_none_for_continuations(released, consumer):
    client, credential_id = consumer
    response = resolve(client, released, **HEADERS)
    assert response.status_code == 200, response.data
    result = response.data["result"]
    prefix = "contexts/" if released.skill.kind == "agent_context" else "consumption/"
    root = BASE + prefix + f"{released.skill_id}/versions/{released.number}/"
    headers = {**HEADERS, "HTTP_X_CONTEXT_RESOLUTION": result["resolution_id"]}
    manifest = client.get(root + "manifest/", **headers)
    assert manifest.status_code == 200, manifest.data
    cached = client.get(root + "manifest/", HTTP_IF_NONE_MATCH=response["ETag"], **headers)
    assert cached.status_code == 304
    file = client.get(root + "files/", {"path": result["entrypoint"]}, **headers)
    assert file.status_code == 200, file.data
    access = client.post(root + "access-check/", {}, format="json", **headers)
    assert access.status_code == 200, access.data
    bucket = released.skill.usage_days.get()
    assert bucket.count == 1
    assert bucket.consumer_key == f"credential:{credential_id}"
    assert bucket.consumer_label == ""  # Foundation credentials have no name yet.
    assert (bucket.channel, bucket.sdk_version, bucket.tracing_mode) == ("sdk", "1.2.3", "on")
    with patch.object(usage, "MIN_TRACING_SDK", "1.2.3"):
        assert usage.usage_summary(released.skill, 7)["consumers"][0]["can_record"] is True
        assert usage.consumers_7d([released.skill_id])[released.skill_id][0]["count"] == 1
    again = resolve(client, released, HTTP_IF_NONE_MATCH=response["ETag"], **HEADERS)
    assert again.status_code == 200
    assert again.data["result"]["resolution_id"] != result["resolution_id"]
    bucket.refresh_from_db()
    assert bucket.count == 2


@pytest.mark.parametrize("headers", [
    {},
    {"HTTP_USER_AGENT": "other-client/1.2.3", "HTTP_X_FI_SKILLS_TRACING": "unknown"},
])
def test_raw_resolve_counts_without_claiming_tracing(released, consumer, headers):
    client, _ = consumer
    response = resolve(client, released, HTTP_X_FI_NATIVE_TEST="true", **headers)
    assert response.status_code == 200, response.data
    bucket = released.skill.usage_days.get()
    assert (bucket.count, bucket.sdk_version, bucket.tracing_mode) == (1, None, None)
    assert usage.usage_summary(released.skill, 7)["consumers"][0]["can_record"] is False


def test_denied_resolve_does_not_record(released, consumer):
    client, credential_id = consumer
    HubSkillContextCredential.objects.filter(pk=credential_id).update(revoked_at=timezone.now())
    response = resolve(client, released, **HEADERS)
    assert response.status_code in (401, 403), response.data
    assert not released.skill.usage_days.exists()


def test_ungranted_version_does_not_record(released, consumer):
    client, _ = consumer
    released.number += 1
    response = resolve(client, released, **HEADERS)
    assert response.status_code == 404, response.data
    assert not released.skill.usage_days.exists()


@pytest.mark.parametrize("released", ["agent_context"], indirect=True)
@pytest.mark.parametrize("variant", ["candidate", "production", "reference"])
def test_native_arm_credentials_never_count(released, principal, variant):
    from model_hub.services.skills_hub.r3.native_runs import _arm_access

    _, credential, secret = _arm_access(
        principal=principal, context=released.skill, variant=variant,
        version=released, expires_at=timezone.now() + timedelta(hours=1),
    )
    HostedHarnessJob.no_workspace_objects.create(
        organization_id=principal.scope.organization_id,
        workspace_id=principal.scope.workspace_id,
        run_id=uuid4(), idempotency_key=str(uuid4()), request_digest="sha256:" + "a" * 64,
        schema_version="fixture", seed=0, scenario_count=1,
        artifact_level="none", max_artifact_bytes=0,
        deadline_at=timezone.now() + timedelta(hours=1),
        payload={"skills_native_control": {"arms": {variant: {"credential_id": str(credential.pk)}}}},
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Context {secret}")
    response = resolve(client, released, **HEADERS)
    assert response.status_code == 200, response.data
    assert not released.skill.usage_days.exists()
    assert usage.consumers_7d([released.skill_id]) == {released.skill_id: []}


def test_accounting_lookup_failure_does_not_break_resolve(released, consumer):
    client, _ = consumer
    with transaction.atomic():
        with patch.object(
            HostedHarnessJob._base_manager, "filter", side_effect=DatabaseError("synthetic failure")
        ):
            response = resolve(client, released, **HEADERS)
        assert response.status_code == 200, response.data
        assert released.skill.versions.count() == 1
    assert not released.skill.usage_days.exists()
