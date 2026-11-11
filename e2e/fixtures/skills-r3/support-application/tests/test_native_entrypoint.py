from copy import deepcopy

import pytest
from ctxhub_support.store import digest
from fi.context.adapters.simulation_entrypoint import MODULE, PROTOCOL, execute


def payload():
    config = {
        "entrypoint": "ctxhub_support.entrypoint:run",
        "reset_entrypoint": "ctxhub_support.entrypoint:reset",
    }
    return {
        "schema": 1,
        "protocol": PROTOCOL,
        "adapter_module": MODULE,
        "configuration": config,
        "configuration_digest": digest(config),
        "execution_fingerprint": "sha256:" + "c" * 64,
        "tasks": [{"id": "case-1", "prompt": "Synthetic refund", "fixtures": []}],
        "snapshots": {
            "candidate": {"digest": "candidate"},
            "reference": {"digest": "reference"},
        },
        "matrix": [
            {"case_id": "case-1", "variant": variant, "repeat_index": 0}
            for variant in ("candidate", "reference")
        ],
    }


def test_native_dispatch_preserves_arms_and_does_not_self_grade():
    job = payload()
    before = deepcopy(job)
    calls, ingested = [], []

    def launch(**request):
        calls.append(deepcopy(request))
        request["configuration"].clear()
        return {
            "status": "completed",
            "evidence_class": "synthetic fixture",
            "checkpoint": 2,
            "context_digest": request["snapshot"]["digest"],
        }

    observations = execute(job, launch_cell=launch, ingest=ingested.append)
    assert job == before
    assert [call["snapshot"]["digest"] for call in calls] == ["candidate", "reference"]
    assert observations == ingested and len(observations) == 2
    assert all("checks" not in row and "verdict" not in row for row in observations)


def test_ingestion_failure_stops_without_reexecution():
    calls = []

    def launch(**request):
        calls.append(request)
        return {
            "status": "completed",
            "evidence_class": "synthetic fixture",
            "context_digest": request["snapshot"]["digest"],
        }

    def lost(_):
        raise RuntimeError("unknown_ingestion_response")

    with pytest.raises(RuntimeError, match="unknown_ingestion"):
        execute(payload(), launch_cell=launch, ingest=lost)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        lambda job: job.update(protocol="other"),
        lambda job: job["configuration"].update(entrypoint="other:run"),
        lambda job: job["matrix"].append(job["matrix"][0]),
        lambda job: job["matrix"][0].update(case_id="missing"),
        lambda job: job["matrix"].pop(),
        lambda job: job["matrix"][0].update(repeat_index=True),
    ],
)
def test_invalid_native_job_never_launches(mutation):
    job = payload()
    mutation(job)

    def unexpected(**_):
        pytest.fail("Invalid native payload launched")

    with pytest.raises(ValueError):
        execute(job, launch_cell=unexpected, ingest=unexpected)
