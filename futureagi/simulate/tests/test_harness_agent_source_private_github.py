"""POST versions/agent/ with an uploaded folder on an environment built from a GitHub
repository: where this deployment refuses the private repository the environment
reads, the upload takes its place and the environment keeps its id, runs and
versions; every other GitHub source and every other caller is answered as before."""

import pytest
from django.utils.dateparse import parse_datetime

from accounts.models import Organization
from accounts.tests.test_rbac_regressions import (
    WORKSPACE_WRITE_DENIED,
    _member_client,
    _viewer_client,
)
from simulate.models import HarnessAgentVersionRuntime, HostedHarnessJob
from simulate.selectors.runs import user_environment_runs
from simulate.services import github_app_tenancy
from simulate.services.harness_agent_versions import apply_agent_version
from simulate.tests.test_harness_agent_source_api import (
    _archive,
    _listed,
    _made,
    _post,
    _refused,
    _uploaded,
)
from simulate.tests.test_harness_agent_versions import (
    _a1,
    _defaults,
    _environment,
    _preflight,
    _side,
)
from simulate.tests.test_harness_agent_versions import (  # noqa: F401 - fixture
    scheduled as scheduled,
)
from simulate.tests.test_harness_versioning_backfill import _run
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)

pytestmark = pytest.mark.django_db

RECORD = "agent_source_replaced"


@pytest.fixture(autouse=True)
def flags(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = True


def _private(payload):
    payload["source"].update(visibility="private", installation_id="inst-private")


def _refuse_private_github(settings, monkeypatch):
    """A deployment whose platform GitHub App serves two organizations: the real
    tenancy check refuses private repositories there."""
    settings.CLOUD_DEPLOYMENT = ""
    settings.GITHUB_APP_ID = "1"
    settings.GITHUB_APP_PRIVATE_KEY = "configured"
    monkeypatch.setattr(
        github_app_tenancy, "_live_org_ids", lambda: ["org-one", "org-two"]
    )
    assert github_app_tenancy.platform_github_app_refused() is True


def _offer_private_github(settings):
    """A deployment without the platform GitHub App: the tenancy check refuses
    nothing there."""
    settings.GITHUB_APP_ID = ""
    settings.GITHUB_APP_PRIVATE_KEY = ""
    assert github_app_tenancy.platform_github_app_refused() is False


def _uploads():
    return HarnessAgentVersionRuntime.no_workspace_objects.filter(
        trigger="upload"
    ).count()


def _record(environment):
    environment.refresh_from_db()
    return ((environment.payload or {}).get("metadata") or {}).get(RECORD)


def _environments():
    return HostedHarnessJob.no_workspace_objects.filter(
        environment__isnull=True
    ).count()


def _run_ids(environment):
    return set(user_environment_runs(environment).values_list("id", flat=True))


class TestRefusedPrivateRepository:
    def test_an_upload_takes_its_place_and_the_environment_keeps_its_runs(
        self, auth_client, settings, monkeypatch, user, workspace, storage, scheduled
    ):
        environment = _environment(
            user, workspace, "src-private-up", storage, edit=_private
        )
        repository_version = _a1(environment)
        run = _run(environment, key="src-private-up-run")
        runs = _run_ids(environment)
        assert run.id in runs
        environments = _environments()
        _refuse_private_github(settings, monkeypatch)
        artifact = _uploaded(storage, user)

        item, version = _made(
            _post(auth_client, environment, {"archive_artifact_id": str(artifact)})
        )

        assert (item["origin"], item["status"], item["preflight_status"]) == (
            "upload",
            "draft",
            "pending",
        )
        side = _side(version)
        assert (side.source_kind, side.source) == (
            "archive",
            {"archive_artifact_id": str(artifact)},
        )
        assert side.trigger == "upload"
        assert side.parent.agent_version_id == repository_version.id
        assert side.diff["source"] is True
        # Reached as an upload: nothing of how the repository was reached is kept.
        assert side.agent_payload["source_access"] == {"kind": "archive"}
        assert side.agent_payload["requested_by_environments"] == [str(environment.id)]

        # The same environment, with its runs and its earlier version.
        assert _environments() == environments
        assert _run_ids(environment) == runs
        listed = {one["id"] for one in _listed(auth_client, environment)["agent"]}
        assert {str(repository_version.id), str(version.id)} <= listed
        assert _defaults(environment) == [repository_version.id]

        record = _record(environment)
        assert record["from"] == {"kind": "github", "repository": "acme/agent"}
        assert record["to"] == {"kind": "archive", "agent_version_id": str(version.id)}
        assert record["requested_default"] is True
        assert parse_datetime(record["at"]) is not None
        assert "inst-private" not in str(record)

        # Once checked it is the default, and a run reads the upload.
        assert _preflight(version).status == "passed"
        assert _defaults(environment) == [version.id]
        [default] = [
            one
            for one in _listed(auth_client, environment)["agent"]
            if one["is_default"]
        ]
        assert default["source"] == {"kind": "archive"}
        payload = dict(environment.payload)
        apply_agent_version(payload, _side(version), environment_id=environment.id)
        assert payload["source"]["kind"] == "archive"
        assert payload["source"]["archive_artifact_id"] == str(artifact)
        assert "installation_id" not in payload["source"]
        assert _run_ids(environment) == runs

    def test_a_workspace_member_may_upload_in_place_too(
        self, settings, monkeypatch, user, workspace, storage, scheduled
    ):
        environment = _environment(
            user, workspace, "src-private-member", storage, edit=_private
        )
        _refuse_private_github(settings, monkeypatch)
        member = _member_client(user.organization, workspace)

        _, version = _made(
            _post(
                member,
                environment,
                {"archive_artifact_id": str(_uploaded(storage, user))},
            )
        )

        assert _side(version).source_kind == "archive"
        assert _record(environment)["to"]["agent_version_id"] == str(version.id)


class TestAnsweredAsBefore:
    def test_a_public_repository_still_refuses_an_upload(
        self, auth_client, settings, monkeypatch, user, workspace, storage, scheduled
    ):
        environment = _environment(user, workspace, "src-public-no", storage)
        repository_version = _a1(environment)
        _refuse_private_github(settings, monkeypatch)

        body = _refused(
            _post(
                auth_client,
                environment,
                {"archive_artifact_id": str(_uploaded(storage, user))},
            ),
            "agent_change_unsupported",
        )

        assert body["field"] == "archive_artifact_id"
        assert _uploads() == 0
        assert _record(environment) is None
        assert _defaults(environment) == [repository_version.id]

    def test_a_private_repository_this_deployment_reads_still_refuses_an_upload(
        self, auth_client, settings, user, workspace, storage, scheduled
    ):
        environment = _environment(
            user, workspace, "src-private-offered", storage, edit=_private
        )
        _offer_private_github(settings)

        body = _refused(
            _post(
                auth_client,
                environment,
                {"archive_artifact_id": str(_uploaded(storage, user))},
            ),
            "agent_change_unsupported",
        )

        assert body["field"] == "archive_artifact_id"
        assert _uploads() == 0
        assert _record(environment) is None

    def test_a_workspace_viewer_is_refused(
        self, settings, monkeypatch, user, workspace, storage, scheduled
    ):
        environment = _environment(
            user, workspace, "src-private-viewer", storage, edit=_private
        )
        repository_version = _a1(environment)
        _refuse_private_github(settings, monkeypatch)
        viewer = _viewer_client(user.organization, workspace)

        response = _post(
            viewer, environment, {"archive_artifact_id": str(_uploaded(storage, user))}
        )

        assert response.status_code == 403, response.content
        assert WORKSPACE_WRITE_DENIED in str(response.data)
        assert _uploads() == 0
        assert _record(environment) is None
        assert _defaults(environment) == [repository_version.id]

    def test_another_organizations_environment_is_not_found(
        self, auth_client, settings, monkeypatch, user, workspace, storage, scheduled
    ):
        environment = _environment(
            user, workspace, "src-private-elsewhere", storage, edit=_private
        )
        other = Organization.objects.create(name="Other org for uploads")
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            organization=other
        )
        _refuse_private_github(settings, monkeypatch)

        body = _refused(
            _post(
                auth_client,
                environment,
                {"archive_artifact_id": str(_uploaded(storage, user))},
            ),
            None,
            404,
        )

        assert body == {"detail": "Environment not found"}
        assert _uploads() == 0
        assert _record(environment) is None

    def test_an_uploaded_source_records_no_change_of_source(
        self, auth_client, settings, monkeypatch, user, workspace, storage, scheduled
    ):
        environment = _environment(
            user, workspace, "src-archive-again", storage, edit=_archive
        )
        _refuse_private_github(settings, monkeypatch)

        _, version = _made(
            _post(
                auth_client,
                environment,
                {"archive_artifact_id": str(_uploaded(storage, user))},
            )
        )

        assert _side(version).source_kind == "archive"
        assert _record(environment) is None
