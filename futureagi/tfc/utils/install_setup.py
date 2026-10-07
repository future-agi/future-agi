"""Which self-hosted setup this process runs in.

A command handed to an operator differs by setup: ``docker compose exec app``
in Standalone, ``docker compose exec backend`` in Distributed, ``kubectl exec``
on Helm. The places that print one ask here rather than guessing.
"""

import os

STANDALONE = "standalone"
DISTRIBUTED = "distributed"
HELM = "helm"


def current_setup() -> str:
    """``helm`` inside a Kubernetes pod (the kubelet sets
    KUBERNETES_SERVICE_HOST in every container); ``standalone`` when this API
    process runs the Temporal worker in-process, which only the Standalone
    ``app`` container does (FI_EMBEDDED_TEMPORAL_WORKER); otherwise
    ``distributed``."""
    if os.environ.get("KUBERNETES_SERVICE_HOST", "").strip():
        return HELM
    from tfc.temporal import embedded

    return STANDALONE if embedded.enabled() else DISTRIBUTED


def helm_namespace() -> str:
    """The release's namespace, which the chart passes as FI_HELM_NAMESPACE."""
    return os.environ.get("FI_HELM_NAMESPACE", "").strip() or "<namespace>"


def helm_deployment(component: str) -> str:
    """``deploy/<name>`` of a chart component. The chart passes its object-name
    prefix as FI_HELM_FULLNAME (``futureagi`` for a release named futureagi,
    ``<release>-futureagi`` otherwise); older charts get the placeholder."""
    prefix = os.environ.get("FI_HELM_FULLNAME", "").strip() or "<release>-futureagi"
    return f"deploy/{prefix}-{component}"


def manage_py_command(command: str, setup: str | None = None) -> str:
    """``python manage.py <command>`` run from the host, in the container or
    pod that holds the API."""
    setup = setup or current_setup()
    if setup == HELM:
        return (
            f"kubectl -n {helm_namespace()} exec -it {helm_deployment('backend')} "
            f"-c backend -- python manage.py {command}"
        )
    service = "app" if setup == STANDALONE else "backend"
    return f"docker compose exec {service} python manage.py {command}"
