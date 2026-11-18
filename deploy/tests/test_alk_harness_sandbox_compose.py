"""The local ALK sandbox service's stop settings. Read from the overlay's YAML; no services."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
# The sandbox waits up to 170 s for its jobs' cleanup when it stops (the kit's
# _CANCEL_WAIT_SECONDS); docker would kill it after its default 10 s.
CLEANUP_WAIT_SECONDS = 170


def _seconds(value: str) -> float:
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", str(value)):
        total += float(amount) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total


class _ComposeLoader(yaml.SafeLoader):
    """Reads Compose's merge tags (!override, !reset) as their plain values."""


def _plain(loader: yaml.SafeLoader, node: yaml.Node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


for _tag in ("!override", "!reset"):
    _ComposeLoader.add_constructor(_tag, _plain)


def _sandbox() -> dict:
    overlay = yaml.load(  # noqa: S506 - a SafeLoader subclass
        (ROOT / "docker-compose.distributed.dev.yml").read_text(), Loader=_ComposeLoader
    )
    return overlay["services"]["alk-harness-sandbox"]


def test_the_sandbox_gets_time_to_stop_its_jobs_before_docker_kills_it():
    assert _seconds(_sandbox().get("stop_grace_period", "")) > CLEANUP_WAIT_SECONDS


def test_the_sandbox_runs_under_an_init_that_forwards_its_stop_signal():
    assert _sandbox().get("init") is True
