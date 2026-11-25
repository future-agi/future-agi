import pytest
from rest_framework import status
from urllib3.exceptions import HTTPError

from simulate.models import CodeArchive, HarnessAgentVersionRuntime
from simulate.services.agent_version_steps import snapshot
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_build_from_agent import _creation
from simulate.tests.test_build_from_agent import (
    agent as agent,  # noqa: F401 - fixture
)
from simulate.tests.test_build_from_agent import (
    production as production,  # noqa: F401 - fixture
)
from simulate.tests.test_code_archives import COMMIT, _Storage
from simulate.tests.test_code_archives import (
    clones as clones,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_agent_creation import _job, _post
from simulate.tests.test_harness_agent_creation import (
    client as client,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_agent_creation import (
    gates as gates,  # noqa: F401 - fixture
)
from simulate.tests.test_harness_connected_versions import _connect

pytestmark = pytest.mark.django_db

ADC = "GOOGLE_APPLICATION_CREDENTIALS"


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    settings.HARNESS_PROVIDER = "hosted"


@pytest.fixture
def archive_storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _holds(version, names):
    HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version=version
    ).update(credential_names=names)


class TestConnectedBuildCredentialFiles:
    def test_a_credential_file_the_version_holds_satisfies_its_source(
        self,
        client,  # noqa: F811
        workspace,
        gates,  # noqa: F811
        agent,  # noqa: F811
        production,  # noqa: F811
    ):
        _holds(production, [ADC])
        gates.scan.return_value = ([], [ADC], 3)

        job = _job(_post(client, workspace, _creation(agent)))

        assert job.agent_version_id == production.id
        assert set(job.payload["agent"]["secret_refs"]) == {ADC}

    def test_a_credential_file_the_version_lacks_is_still_refused(
        self,
        client,  # noqa: F811
        workspace,
        gates,  # noqa: F811
        agent,  # noqa: F811
        production,  # noqa: F811
    ):
        gates.scan.return_value = ([], [ADC], 3)

        response = _post(client, workspace, _creation(agent))

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, (
            response.content
        )
        assert response.json()["error"] == "credential_file_required"


class TestSnapshotStoresTheCode:
    def test_code_that_could_not_be_stored_is_retried(
        self,
        agent,  # noqa: F811
        archive_storage,
        clones,  # noqa: F811
        monkeypatch,
    ):
        version = _connect(agent)

        def refuse(*_args, **_kwargs):
            raise HTTPError("storage unreachable")

        monkeypatch.setattr(archive_storage, "put_object", refuse)

        with pytest.raises(HostedHarnessError) as raised:
            snapshot(version.id)

        assert raised.value.code == "code_archive_not_stored"
        assert raised.value.retryable is True
        assert not CodeArchive.no_workspace_objects.exists()

    def test_stored_code_completes_the_snapshot(
        self,
        agent,  # noqa: F811
        archive_storage,
        clones,  # noqa: F811
    ):
        version = _connect(agent)

        assert snapshot(version.id).status == "done"
        assert CodeArchive.no_workspace_objects.filter(commit_sha=COMMIT).exists()
