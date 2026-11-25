"""Replay safety of the workflow-generation fence (#4199, TH-7963), both ways.

``workflow_generation`` must never change a run's commands: it only travels in
activity inputs, and the activities enforce it. So every history replays on
the current code (an in-flight execution after the upgrade) *and* on the
v1.59 release (a worker that has not upgraded yet, or a rollback).

``fixtures/generation_replay`` holds histories recorded on a local Temporal
server by ``fixtures/record_generation_histories.py``:

* ``base-*`` on dev at #4199's merge base, whose workflow code is the v1.59
  release's. The ``*-gen-*`` ones were started with the input a new starter
  sends (``workflow_generation`` set): what an old worker records when it
  picks up a new start mid-rollout.
* ``new-*`` on the branch, without a historical generation. The withdrawn
  head's generation-bearing historical histories (a leading state check behind
  a patch marker) are gone: v1.59 could not replay them.

``release-v1.59.0-workflows.py.txt`` is the v1.59.0 (= v1.59.1) workflow module,
pinned by its git blob id. The e2e test records all 16 ``new`` runs from the
current code and replays each one on it.
"""

import ast
import base64
import dataclasses
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from temporalio.client import WorkflowHistory
from temporalio.converter import DataConverter
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner

from tfc.temporal.eval_tasks import types
from tfc.temporal.eval_tasks import workflows as wf

FIXTURES = Path(__file__).parent / "fixtures" / "generation_replay"
ALL = sorted(p.name for p in FIXTURES.glob("*.json"))
RELEASE_SOURCE = FIXTURES / "release-v1.59.0-workflows.py.txt"
# ``git rev-parse v1.59.0:futureagi/tfc/temporal/eval_tasks/workflows.py``;
# v1.59.1 ships the same blob.
RELEASE_BLOB = "b331407480c0959ea93809bffdbc38ff9bae3b0e"
WITHDRAWN_FENCE_PATCH = b"historical-eval-generation-fence-v1"
# Runs per recorded scenario chain: three historical hops, two continuous hops.
NEW_RUNS = {
    "historical-can": 3,
    "historical-fail": 1,
    "historical-gen-can": 3,
    "historical-gen-fail": 1,
    "historical-gen-stale": 1,
    "continuous-can": 2,
    "continuous-fail": 1,
    "continuous-gen-can": 2,
    "continuous-gen-fail": 1,
    "continuous-gen-stale": 1,
}
# Recorded on the branch and still valid for its code; the other five ``new``
# runs are recorded live by the e2e test (re-record them with
# ``--label new`` to check them in).
CHECKED_IN_NEW = {
    "new-historical-can-run0.json",
    "new-historical-can-run1.json",
    "new-historical-can-run2.json",
    "new-historical-fail-run0.json",
    "new-continuous-can-run0.json",
    "new-continuous-can-run1.json",
    "new-continuous-fail-run0.json",
    "new-continuous-gen-can-run0.json",
    "new-continuous-gen-can-run1.json",
    "new-continuous-gen-fail-run0.json",
    "new-continuous-gen-stale-run0.json",
}


def _events(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())["events"]


def _input(name: str) -> dict:
    payload = _events(name)[0]["workflowExecutionStartedEventAttributes"]["input"]
    return json.loads(base64.b64decode(payload["payloads"][0]["data"]))


def _activities(events: list[dict]) -> list[str]:
    return [
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in events
        if "activityTaskScheduledEventAttributes" in e
    ]


def _markers(events: list[dict]) -> list[bytes]:
    return [
        base64.b64decode(p["data"])
        for e in events
        if "markerRecordedEventAttributes" in e
        for detail in e["markerRecordedEventAttributes"].get("details", {}).values()
        for p in detail.get("payloads", [])
    ]


def _release_workflows(tmp_path, monkeypatch):
    """The v1.59 workflow module, loaded like dev's unguarded-copy replay."""
    source = RELEASE_SOURCE.read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(source) + source).hexdigest()
    assert blob == RELEASE_BLOB
    path = tmp_path / "release_v1_59_0_workflows.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


async def _replay(name: str, text: str, module) -> None:
    history = WorkflowHistory.from_json(name.removesuffix(".json"), text)
    await Replayer(
        workflows=[
            module.HistoricalEvalTaskWorkflow,
            module.ContinuousEvalTaskWorkflow,
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ).replay_workflow(history)


def test_fixture_set_is_complete():
    expected = set()
    for kind in ("historical", "continuous"):
        for gen in ("", "gen-"):
            runs = 3 if kind == "historical" else 2
            expected |= {f"base-{kind}-{gen}can-run{i}.json" for i in range(runs)}
            expected.add(f"base-{kind}-{gen}fail-run0.json")
    assert set(ALL) == expected | CHECKED_IN_NEW
    assert sum(NEW_RUNS.values()) == 16


@pytest.mark.parametrize("fixture", ALL)
async def test_recorded_history_replays_on_current_code(fixture):
    await _replay(fixture, (FIXTURES / fixture).read_text(), wf)


@pytest.mark.parametrize("fixture", ALL)
async def test_recorded_history_replays_on_the_v1_59_release(
    fixture, tmp_path, monkeypatch
):
    release = _release_workflows(tmp_path, monkeypatch)
    await _replay(fixture, (FIXTURES / fixture).read_text(), release)


def test_no_history_or_workflow_code_uses_the_withdrawn_fence_patch():
    """The generation adds no patch marker in either direction."""
    assert WITHDRAWN_FENCE_PATCH not in Path(wf.__file__).read_bytes()
    for name in ALL:
        assert WITHDRAWN_FENCE_PATCH not in b"".join(_markers(_events(name))), name


_GENERATION_NAMES = {"generation", "workflow_generation"}


def _reads_generation(node: ast.AST) -> bool:
    """Whether ``node`` reads the generation itself. Passing it to a call (an
    activity input) does not count: that call's result is recorded."""
    if isinstance(node, ast.Call):
        return _reads_generation(node.func)
    if isinstance(node, ast.Name):
        return node.id in _GENERATION_NAMES
    if isinstance(node, ast.Attribute) and node.attr in _GENERATION_NAMES:
        return True
    return any(_reads_generation(child) for child in ast.iter_child_nodes(node))


def test_the_generation_only_chooses_an_activity_payload_class():
    """Structural, not a replay: CI deselects the e2e recorder below.

    Every branch the workflow module takes on a generation only builds an
    activity input. It awaits nothing, calls no ``workflow`` API, and neither
    returns nor raises, so given the same activity results a run with a
    generation (now every first run, since create stamps one) issues the
    commands of a run without one. 3ef6b91b's ``workflow.patched`` fence fails
    this.
    """
    tree = ast.parse(Path(wf.__file__).read_text())
    branches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If | ast.IfExp | ast.While)
        and _reads_generation(node.test)
    ]
    # ``_reconcile`` and ``_reap``: a new branch on the generation needs review.
    assert len(branches) == 2
    for branch in branches:
        for node in ast.walk(branch):
            assert not isinstance(node, ast.Await | ast.Return | ast.Raise), node.lineno
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                assert not (
                    isinstance(owner, ast.Name) and owner.id == "workflow"
                ), node.lineno


@pytest.mark.parametrize("kind", ["historical", "continuous"])
@pytest.mark.parametrize("scenario", ["can", "fail"])
def test_old_worker_ran_a_new_start_as_if_it_had_no_generation(kind, scenario):
    """An old worker accepts a generation-carrying input and ignores the field.

    The start is not rejected: the dev workflow issued exactly the commands it
    issues for an input without a generation, and its continue-as-new hop
    dropped the field (so later hops of that execution run unfenced, which
    the activities accept as a legacy run).
    """
    gen = f"base-{kind}-gen-{scenario}-run0.json"
    plain = f"base-{kind}-{scenario}-run0.json"
    assert _input(gen)["workflow_generation"] == "g-1"
    assert _activities(_events(gen)) == _activities(_events(plain))
    assert _events(gen)[-1]["eventType"] == _events(plain)[-1]["eventType"]
    if scenario == "can":
        assert "workflow_generation" not in _input(f"base-{kind}-gen-can-run1.json")


@pytest.mark.parametrize("hop", [0, 1])
def test_new_generation_run_issues_the_same_commands_as_a_legacy_run(hop):
    gen = f"new-continuous-gen-can-run{hop}.json"
    assert _input(gen)["workflow_generation"] == "g-1"
    assert _activities(_events(gen)) == _activities(
        _events(f"new-continuous-can-run{hop}.json")
    )


@pytest.mark.e2e
@pytest.mark.xdist_group("temporal_eval_task_e2e")
async def test_every_new_history_replays_on_the_v1_59_release(
    workflow_environment, tmp_path, monkeypatch
):
    """Record all 16 ``new`` runs from this code, then replay each on v1.59.

    By design the generation never adds a command, so a replaced run (the
    ``*-stale`` scenarios) and a generation-bearing historical start replay on
    a worker that never heard of the field.
    """
    recorder = _load_recorder(monkeypatch)
    histories = await recorder.record_histories(workflow_environment.client, "new")
    assert set(histories) == {
        f"new-{name}-run{hop}.json"
        for name, runs in NEW_RUNS.items()
        for hop in range(runs)
    }
    release = _release_workflows(tmp_path, monkeypatch)
    for name, text in sorted(histories.items()):
        events = json.loads(text)["events"]
        assert WITHDRAWN_FENCE_PATCH not in b"".join(_markers(events)), name
        await _replay(name, text, release)
        await _replay(name, text, wf)
    for hop in range(3):
        gen, plain = (
            json.loads(histories[f"new-historical-{g}can-run{hop}.json"])["events"]
            for g in ("gen-", "")
        )
        assert _activities(gen) == _activities(plain)
    stale = json.loads(histories["new-historical-gen-stale-run0.json"])["events"]
    assert _activities(stale) == [
        "get_workflow_labels_activity",
        "set_eval_task_status_activity",
        "reconcile_eval_task_activity",
        "reap_stale_running_activity",
        "get_eval_task_state_activity",
    ]
    assert stale[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"


def _load_recorder(monkeypatch):
    path = FIXTURES.parent / "record_generation_histories.py"
    spec = importlib.util.spec_from_file_location("record_generation_histories", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


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
    (types.FencedReconcileInput, ("workflow_generation",)),
    (types.FencedReapInput, ("workflow_generation",)),
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


@pytest.mark.parametrize("first_closed", [False, True])
async def test_a_run_without_generation_sends_the_dev_payload_classes(
    monkeypatch, first_closed
):
    """No-generation reconcile/reap inputs stay byte-identical to dev's."""
    sent = []

    async def execute(name, arg, **_kwargs):
        sent.append(arg)
        return {}

    monkeypatch.setattr(wf.workflow, "execute_activity", execute)
    await wf._reconcile("t", first_closed=first_closed)
    await wf._reap("t", first_closed=first_closed)
    await wf._reconcile("t", first_closed=first_closed, generation="g")
    await wf._reap("t", first_closed=first_closed, generation="g")
    plain = (
        (types.CheckedReconcileInput, types.CheckedReapInput)
        if first_closed
        else (types.ReconcileActivityInput, types.ReapInput)
    )
    assert [type(arg) for arg in sent] == [
        *plain,
        types.FencedReconcileInput,
        types.FencedReapInput,
    ]
    assert all(arg.require_running is first_closed for arg in sent[2:])
    assert all(arg.workflow_generation == "g" for arg in sent[2:])
