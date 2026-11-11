"""Packaged Simulation application adapter, protocol skills-native-v1.

The product's NativeExecutionSpec.guest_payload() supplies immutable tasks and
arms. The existing hosted lifecycle must provide launch_cell and authenticated
observation ingestion. This module neither provisions another executor nor
claims mounts/egress are isolated. It cannot certify itself or emit evaluator
verdicts. A local module launch is an application smoke, not a native pass.
"""

from __future__ import annotations

from copy import deepcopy

from ctxhub_support.store import digest

PROTOCOL = "skills-native-v1"
MODULE = "fi.context.adapters.simulation_entrypoint"


def execute(payload, *, launch_cell, ingest):
    """Execute admitted cells and ingest observations, not self-graded NativeTrials.

    launch_cell(*, task, cell, snapshot, configuration) -> application result.
    It must use the mounted packaged application and the original attempt-bound
    credentials; the host is responsible for matching the per-arm SDK lock.
    ingest(observation) must use the existing authenticated attempt channel.
    Neither callback may silently fall back to a local/test executor.
    """
    if (
        payload.get("schema") != 1
        or payload.get("protocol") != PROTOCOL
        or payload.get("adapter_module") != MODULE
    ):
        raise ValueError("invalid_native_protocol")
    if digest(payload["configuration"]) != payload["configuration_digest"]:
        raise ValueError("configuration_digest_mismatch")
    tasks = {task["id"]: task for task in payload["tasks"]}
    if len(tasks) != len(payload["tasks"]) or not payload["matrix"]:
        raise ValueError("invalid_native_tasks")
    seen = set()
    for cell in payload["matrix"]:
        key = (cell["case_id"], cell["variant"], cell["repeat_index"])
        if (
            key in seen
            or cell["case_id"] not in tasks
            or cell["variant"] not in payload["snapshots"]
        ):
            raise ValueError("invalid_native_matrix")
        seen.add(key)
    arms = set(payload["snapshots"])
    repeats = {cell["repeat_index"] for cell in payload["matrix"]}
    if (
        not 2 <= len(arms) <= 3
        or "candidate" not in arms
        or not arms <= {"candidate", "reference", "production", "without_target"}
        or repeats not in ({0}, {0, 1, 2})
        or any(type(cell["repeat_index"]) is not int for cell in payload["matrix"])
        or seen
        != {
            (case_id, arm, repeat)
            for case_id in tasks
            for arm in arms
            for repeat in repeats
        }
    ):
        raise ValueError("incomplete_native_matrix")
    observations = []
    for cell in payload["matrix"]:
        result = launch_cell(
            task=deepcopy(tasks[cell["case_id"]]),
            cell=deepcopy(cell),
            snapshot=deepcopy(payload["snapshots"][cell["variant"]]),
            configuration=deepcopy(payload["configuration"]),
        )
        if (
            result.get("status") not in {"completed", "paused"}
            or result.get("evidence_class") != "synthetic fixture"
        ):
            raise ValueError("invalid_application_observation")
        snapshot = payload["snapshots"][cell["variant"]]
        if result["status"] == "completed" and result.get("context_digest") != (
            snapshot["digest"] if snapshot else None
        ):
            raise ValueError("application_context_identity_mismatch")
        observation = {
            "schema": 1,
            "protocol": PROTOCOL,
            "execution_fingerprint": payload["execution_fingerprint"],
            "case_id": cell["case_id"],
            "variant": cell["variant"],
            "repeat_index": cell["repeat_index"],
            "application": result,
            "observation_digest": digest(result),
        }
        # An ingestion failure stops the run. The host must reconcile the attempt;
        # this adapter does not invent a second execution on an unknown response.
        ingest(deepcopy(observation))
        observations.append(observation)
    return observations


if __name__ == "__main__":
    from ctxhub_support.entrypoint import main

    raise SystemExit(main())
