"""Replay safety of the workflow-generation fence (#4199, TH-7963).

``fixtures/generation_replay`` holds real histories recorded on a local
Temporal server by ``fixtures/record_generation_histories.py``:

* ``base-*`` on dev at #4199's merge base (8ddeb72a), the code an in-flight
  execution or a not-yet-upgraded worker runs. The ``*-gen-*`` ones were
  started with the input a new starter sends (``workflow_generation`` set),
  i.e. what an old worker records when it picks up a new start mid-rollout.
* ``new-*`` on this branch, with and without a generation, so the patched
  branches replay too.

Each scenario covers a continue-as-new chain (one file per run) and a failed
start. Every history must replay on the current workflow code.
"""

import base64
import dataclasses
import json
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from tfc.temporal.eval_tasks import types
from tfc.temporal.eval_tasks import workflows as wf

FIXTURES = Path(__file__).parent / "fixtures" / "generation_replay"
ALL = sorted(p.name for p in FIXTURES.glob("*.json"))
FENCE_MARKER = wf._HISTORICAL_GENERATION_FENCE_PATCH.encode()


def _events(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())["events"]


def _input(name: str) -> dict:
    payload = _events(name)[0]["workflowExecutionStartedEventAttributes"]["input"]
    return json.loads(base64.b64decode(payload["payloads"][0]["data"]))


def _activities(name: str) -> list[str]:
    return [
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in _events(name)
        if "activityTaskScheduledEventAttributes" in e
    ]


def _has_fence_marker(name: str) -> bool:
    return any(
        FENCE_MARKER in base64.b64decode(p["data"])
        for e in _events(name)
        if "markerRecordedEventAttributes" in e
        for detail in e["markerRecordedEventAttributes"].get("details", {}).values()
        for p in detail.get("payloads", [])
    )


def test_fixture_set_is_complete():
    expected = set()
    for label in ("base", "new"):
        for kind in ("historical", "continuous"):
            for gen in ("", "gen-"):
                runs = 3 if kind == "historical" else 2
                expected |= {
                    f"{label}-{kind}-{gen}can-run{i}.json" for i in range(runs)
                }
                expected.add(f"{label}-{kind}-{gen}fail-run0.json")
    expected |= {
        "new-historical-gen-stale-run0.json",
        "new-continuous-gen-stale-run0.json",
    }
    assert set(ALL) == expected


@pytest.mark.parametrize("fixture", ALL)
async def test_recorded_history_replays_on_current_code(fixture):
    history = WorkflowHistory.from_json(
        fixture.removesuffix(".json"), (FIXTURES / fixture).read_text()
    )
    await Replayer(
        workflows=[wf.HistoricalEvalTaskWorkflow, wf.ContinuousEvalTaskWorkflow],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


@pytest.mark.parametrize("kind", ["historical", "continuous"])
@pytest.mark.parametrize("scenario", ["can", "fail"])
def test_old_worker_ran_a_new_start_as_if_it_had_no_generation(kind, scenario):
    """An old worker accepts a generation-carrying input and ignores the field.

    The start is not rejected: the dev workflow issued exactly the commands it
    issues for an input without a generation, and its continue-as-new hop
    dropped the field (so later hops of that execution run unfenced).
    """
    gen = f"base-{kind}-gen-{scenario}-run0.json"
    plain = f"base-{kind}-{scenario}-run0.json"
    assert _input(gen)["workflow_generation"] == "g-1"
    assert _activities(gen) == _activities(plain)
    assert _events(gen)[-1]["eventType"] == _events(plain)[-1]["eventType"]
    if scenario == "can":
        assert "workflow_generation" not in _input(f"base-{kind}-gen-can-run1.json")


def test_old_worker_history_with_generation_skips_the_fence():
    # The history the finding is about: generation set, labels first.
    first = _activities("base-historical-gen-can-run0.json")[0]
    assert first == "get_workflow_labels_activity"
    assert not _has_fence_marker("base-historical-gen-can-run0.json")


@pytest.mark.parametrize("run", [0, 1, 2])
def test_new_generation_runs_check_the_fence_first_behind_the_patch(run):
    name = f"new-historical-gen-can-run{run}.json"
    assert _input(name)["workflow_generation"] == "g-1"
    assert _has_fence_marker(name)
    assert _activities(name)[0] == "get_eval_task_state_activity"


def test_new_stale_generation_exits_after_the_fence():
    name = "new-historical-gen-stale-run0.json"
    assert _has_fence_marker(name)
    assert _activities(name) == ["get_eval_task_state_activity"]
    assert _events(name)[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"


@pytest.mark.parametrize(
    "name",
    [n for n in ALL if "-gen-" not in n or n.startswith("base-")]
    + [n for n in ALL if n.startswith("new-continuous-gen-")],
)
def test_runs_without_a_fenced_historical_start_record_no_fence_marker(name):
    assert not _has_fence_marker(name)


def _without(cls, *names):
    """``cls`` as dev declared it: the same dataclass minus #4199's fields."""
    return dataclasses.make_dataclass(
        f"Dev{cls.__name__}",
        [(f.name, f.type, f) for f in dataclasses.fields(cls) if f.name not in names],
    )


NEW_FIELDS = [
    (types.EvalTaskWorkflowInput, ("workflow_generation",)),
    (types.ContinuousDrainState, ("workflow_generation",)),
    (types.TaskStateInput, ("workflow_generation",)),
    (types.SetStatusInput, ("failure_reason", "workflow_generation")),
    (types.FinalizeInput, ("workflow_generation",)),
]


@pytest.mark.parametrize(
    "cls,names", NEW_FIELDS, ids=[c.__name__ for c, _ in NEW_FIELDS]
)
def test_dev_types_decode_new_payloads_and_new_types_decode_dev_payloads(cls, names):
    """The default converter (the repo installs no custom one) ignores unknown
    dataclass fields, and every new field defaults to None."""
    converter = DataConverter.default.payload_converter
    dev_cls = _without(cls, *names)
    required = {
        f.name: f"v-{f.name}"
        for f in dataclasses.fields(cls)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    new_value = cls(**required, **{n: f"new-{n}" for n in names})

    (payload,) = converter.to_payloads([new_value])
    (decoded,) = converter.from_payloads([payload], [dev_cls])
    assert dataclasses.asdict(decoded) == {
        k: v for k, v in dataclasses.asdict(new_value).items() if k not in names
    }

    (payload,) = converter.to_payloads([dev_cls(**required)])
    (decoded,) = converter.from_payloads([payload], [cls])
    assert all(getattr(decoded, n) is None for n in names)
