#!/usr/bin/env python3
"""Pin CAT-2 default-off rendering and its restart boundary; no cluster calls."""

import os
from pathlib import Path
import subprocess

import yaml

CHART = Path(__file__).resolve().parents[1]


def render(*args):
    result = subprocess.run(
        [
            os.environ.get("HELM", "helm"),
            "template",
            "cat2-test",
            str(CHART),
            "-f",
            str(CHART / "examples/bundled.yaml"),
            "--set", "secrets.allowGeneratedKeysWithoutCluster=true",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    docs = [doc for doc in yaml.safe_load_all(result.stdout) if doc]
    gateway = [
        doc
        for doc in docs
        if doc.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component")
        == "agentcc-gateway"
    ]
    deployment = next(doc for doc in gateway if doc["kind"] == "Deployment")
    maps = [doc for doc in gateway if doc["kind"] == "ConfigMap"]
    config = yaml.safe_load(maps[0]["data"]["config.yaml"]) if maps else None
    return config, deployment["spec"]["template"]


def main():
    off, old = render()
    on, new = render("--set", "agentccGateway.config.managed.catalogue_families[0]=cat2")
    assert off["managed"]["catalogue_families"] == [], "CAT-2 default must remain off"
    assert on["managed"]["catalogue_families"] == ["cat2"], "opt-in lost in ConfigMap"
    assert (
        old["metadata"]["annotations"]["checksum/config"]
        != new["metadata"]["annotations"]["checksum/config"]
    ), "node policy change must trigger a rollout"
    _, override = render(
        "--set-string", "agentccGateway.extraEnv.AGENTCC_MANAGED_CATALOGUE_FAMILIES="
    )
    env = override["spec"]["containers"][0]["env"]
    assert any(
        row["name"] == "AGENTCC_MANAGED_CATALOGUE_FAMILIES" and row["value"] == ""
        for row in env
    ), "explicitly empty environment override must not be dropped"
    config, external = render("--set", "agentccGateway.existingConfigMap=external-cat2")
    assert config is None, "existing ConfigMap must not be replaced"
    assert "checksum/config" not in external["metadata"]["annotations"], (
        "external ConfigMap needs the documented explicit restart"
    )
    print("ok   CAT-2 default off, opt-in, rollout checksum and empty env override")


if __name__ == "__main__":
    main()
