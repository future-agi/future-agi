"""Host publish of code-executor must not leave /execute on every interface.

Files are parsed, never run: no Docker, no container, no request to /execute.
The route still has no auth. A loopback bind is exposure reduction, not closure.
In-network clients still reach the compose DNS name.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
BACKEND_COMPOSE = ROOT / "futureagi" / "docker-compose.yml"
STANDALONE_COMPOSE = ROOT / "docker-compose.yml"
DISTRIBUTED_COMPOSE = ROOT / "docker-compose.distributed.yml"


def _compose(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _published_host(mapping: str) -> str:
    """Host side of a compose port mapping, before the container port."""
    host = mapping.split(":", 1)[0]
    if host.startswith("${") and ":-" in host:
        host = host.split(":-", 1)[1].rstrip("}")
    return host


def test_backend_code_executor_host_publish_is_loopback_only():
    service = _compose(BACKEND_COMPOSE)["services"]["code-executor"]
    assert service["privileged"] is True
    ports = service.get("ports") or []
    assert ports, "code-executor host publish disappeared; this ticket only binds it"
    for mapping in ports:
        assert str(mapping).startswith("127.0.0.1:"), mapping
        assert _published_host(str(mapping)) in {"127.0.0.1", "localhost"}


def test_other_compose_files_do_not_publish_code_executor_on_all_interfaces():
    standalone = _compose(STANDALONE_COMPOSE)["services"]["code-executor"]
    assert "ports" not in standalone
    distributed = _compose(DISTRIBUTED_COMPOSE)["services"]["code-executor"]
    for mapping in distributed.get("ports") or []:
        assert str(mapping).startswith("127.0.0.1:"), mapping
