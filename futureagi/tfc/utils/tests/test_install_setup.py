"""The setup a self-hosted API runs in, and the operator commands per setup."""

import pytest

from tfc.utils.install_setup import (
    DISTRIBUTED,
    HELM,
    STANDALONE,
    current_setup,
    manage_py_command,
)


@pytest.fixture
def setup_env(monkeypatch):
    def set_env(kubernetes=False, embedded_worker=False):
        if kubernetes:
            monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
        else:
            monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
        if embedded_worker:
            monkeypatch.setenv("FI_EMBEDDED_TEMPORAL_WORKER", "true")
        else:
            monkeypatch.delenv("FI_EMBEDDED_TEMPORAL_WORKER", raising=False)

    return set_env


@pytest.mark.unit
class TestCurrentSetup:
    def test_the_embedded_worker_marks_standalone(self, setup_env):
        setup_env(embedded_worker=True)
        assert current_setup() == STANDALONE

    def test_separate_workers_mark_distributed(self, setup_env):
        setup_env()
        assert current_setup() == DISTRIBUTED

    @pytest.mark.parametrize("embedded_worker", [False, True])
    def test_a_kubernetes_pod_is_helm(self, setup_env, embedded_worker):
        """The kubelet sets KUBERNETES_SERVICE_HOST in every container."""
        setup_env(kubernetes=True, embedded_worker=embedded_worker)
        assert current_setup() == HELM


@pytest.mark.unit
class TestManagePyCommand:
    @pytest.mark.parametrize(
        "setup, expected",
        [
            (
                STANDALONE,
                "docker compose exec app python manage.py reset_password "
                "--email <address>",
            ),
            (
                DISTRIBUTED,
                "docker compose exec backend python manage.py reset_password "
                "--email <address>",
            ),
            (
                HELM,
                "kubectl -n <namespace> exec -it deploy/<release>-futureagi-backend "
                "-c backend -- python manage.py reset_password --email <address>",
            ),
        ],
    )
    def test_names_the_container_of_the_setup(self, setup, expected, monkeypatch):
        monkeypatch.delenv("FI_HELM_NAMESPACE", raising=False)
        monkeypatch.delenv("FI_HELM_FULLNAME", raising=False)
        assert manage_py_command("reset_password --email <address>", setup) == (
            expected
        )

    def test_defaults_to_the_running_setup(self, setup_env):
        setup_env(embedded_worker=True)
        assert manage_py_command("create_user").startswith("docker compose exec app ")

    def test_helm_names_the_real_namespace_and_deployment(self, monkeypatch):
        """The chart passes both, so the command runs as printed."""
        monkeypatch.setenv("FI_HELM_NAMESPACE", "prod")
        monkeypatch.setenv("FI_HELM_FULLNAME", "acme-futureagi")

        assert manage_py_command("create_user", HELM) == (
            "kubectl -n prod exec -it deploy/acme-futureagi-backend -c backend "
            "-- python manage.py create_user"
        )
