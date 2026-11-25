"""Replay safety of the workflow-generation fence, both ways.

``workflow_generation`` must never change a run's commands: it only travels in
activity inputs, and the activities enforce it. So every history replays on
the current code (an in-flight execution after the upgrade) *and* on the
v1.59 release (a worker that has not upgraded yet, or a rollback), with one
exception that predates the generation: see "Closed sampling" below.

``fixtures/generation_replay`` holds histories recorded on a local Temporal
server by ``fixtures/record_generation_histories.py``:

* ``base-*`` on the v1.59 workflow code. The ``*-gen-*`` ones were started with
  the input a new starter sends (``workflow_generation`` set): what an old
  worker records when it picks up a new start mid-rollout.
* ``new-*`` on this branch, every scenario, with and without a generation.
* ``dev-*`` on dev (``dev-17fe3f6f-workflows.py.txt``): the closed-sampling
  control below.

Closed sampling: when the first status reply carries ``first_reconcile_at``
in the future, dev's first-closed delay (``historical-first-closed-reconcile-
delay-v1``) records a marker and a timer before the first reconcile. v1.59
has no such delay, so it cannot replay those histories, whether dev or this
branch recorded them and whether or not they carry a generation. Both
directions between dev and this branch replay.

``release-v1.59.0-workflows.py.txt`` (v1.59.0 = v1.59.1) and
``dev-17fe3f6f-workflows.py.txt`` are pinned by their git blob ids. The e2e test
records every ``new`` run from the current code and replays each one.
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
from temporalio.workflow import NondeterminismError

from tfc.temporal.eval_tasks import types
from tfc.temporal.eval_tasks import workflows as wf

FIXTURES = Path(__file__).parent / "fixtures" / "generation_replay"
ALL = sorted(p.name for p in FIXTURES.glob("*.json"))
RELEASE_SOURCE = FIXTURES / "release-v1.59.0-workflows.py.txt"
# ``git rev-parse v1.59.0:futureagi/tfc/temporal/eval_tasks/workflows.py``;
# v1.59.1 ships the same blob.
RELEASE_BLOB = "b331407480c0959ea93809bffdbc38ff9bae3b0e"
# ``git rev-parse 17fe3f6f:futureagi/tfc/temporal/eval_tasks/workflows.py``,
# the last dev change to the module before this branch.
DEV_SOURCE = FIXTURES / "dev-17fe3f6f-workflows.py.txt"
DEV_BLOB = "644df57516c4c9a733dc78db564141a5c600f0fe"
WITHDRAWN_FENCE_PATCH = b"historical-eval-generation-fence-v1"
FIRST_CLOSED_PATCH = b"historical-first-closed-reconcile-delay-v1"
# Runs per recorded scenario chain: three historical hops, two continuous hops.
NEW_RUNS = {
    "historical-can": 3,
    "historical-fail": 1,
    "historical-gen-can": 3,
    "historical-gen-fail": 1,
    "historical-gen-stale": 1,
    "historical-closed": 1,
    "historical-gen-closed": 1,
    "continuous-can": 2,
    "continuous-fail": 1,
    "continuous-gen-can": 2,
    "continuous-gen-fail": 1,
    "continuous-gen-stale": 1,
}
NEW = {
    f"new-{name}-run{hop}.json"
    for name, runs in NEW_RUNS.items()
    for hop in range(runs)
}
DEV = {"dev-historical-closed-run0.json", "dev-historical-gen-closed-run0.json"}
# Closed sampling with a future deadline: a timer before the first reconcile.
TIMER = DEV | {
    "new-historical-closed-run0.json",
    "new-historical-gen-closed-run0.json",
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
    return _pinned_workflows(RELEASE_SOURCE, RELEASE_BLOB, tmp_path, monkeypatch)


def _dev_workflows(tmp_path, monkeypatch):
    return _pinned_workflows(DEV_SOURCE, DEV_BLOB, tmp_path, monkeypatch)


def _pinned_workflows(source_path, expected_blob, tmp_path, monkeypatch):
    source = source_path.read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(source) + source).hexdigest()
    assert blob == expected_blob
    path = tmp_path / f"pinned_{expected_blob[:12]}_workflows.py"
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
    assert set(ALL) == expected | NEW | DEV
    assert len(NEW) == sum(NEW_RUNS.values()) == 18


@pytest.mark.parametrize("fixture", ALL)
async def test_recorded_history_replays_on_current_code(fixture):
    await _replay(fixture, (FIXTURES / fixture).read_text(), wf)


@pytest.mark.parametrize("fixture", sorted(set(ALL) - TIMER))
async def test_recorded_history_replays_on_the_v1_59_release(
    fixture, tmp_path, monkeypatch
):
    release = _release_workflows(tmp_path, monkeypatch)
    await _replay(fixture, (FIXTURES / fixture).read_text(), release)


@pytest.mark.parametrize("fixture", sorted(TIMER))
def test_a_closed_sampling_history_sleeps_on_a_timer_before_its_reconcile(fixture):
    events = _events(fixture)
    assert FIRST_CLOSED_PATCH in b"".join(_markers(events))
    (timer,) = [e for e in events if "timerStartedEventAttributes" in e]
    timeout = timer["timerStartedEventAttributes"]["startToFireTimeout"]
    assert float(timeout.removesuffix("s")) > 0
    order = [
        "timer"
        if "timerFiredEventAttributes" in e
        else e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in events
        if "timerFiredEventAttributes" in e
        or "activityTaskScheduledEventAttributes" in e
    ]
    assert order[:5] == [
        "get_workflow_labels_activity",
        "set_eval_task_status_activity",
        "timer",
        "reconcile_eval_task_activity",
        "reap_stale_running_activity",
    ]
    assert events[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"


@pytest.mark.parametrize("fixture", sorted(TIMER))
async def test_a_closed_sampling_history_replays_on_dev_and_on_this_branch(
    fixture, tmp_path, monkeypatch
):
    """Dev's timer histories replay here (upgrade), and this branch's replay
    on dev (rollback to dev), with or without a generation."""
    text = (FIXTURES / fixture).read_text()
    await _replay(fixture, text, _dev_workflows(tmp_path, monkeypatch))
    await _replay(fixture, text, wf)


@pytest.mark.parametrize("fixture", sorted(TIMER))
async def test_v1_59_cannot_take_over_a_closed_sampling_history(
    fixture, tmp_path, monkeypatch
):
    """The limit is dev's, not the generation's: v1.59 refuses dev's own
    timer histories exactly as it refuses this branch's."""
    release = _release_workflows(tmp_path, monkeypatch)
    with pytest.raises(NondeterminismError):
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
    # ``_reconcile``, ``_reap`` and ``_claim``: a new branch on the generation
    # needs review.
    assert len(branches) == 3
    for branch in branches:
        for node in ast.walk(branch):
            assert not isinstance(node, ast.Await | ast.Return | ast.Raise), node.lineno
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                assert not (isinstance(owner, ast.Name) and owner.id == "workflow"), (
                    node.lineno
                )


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


@pytest.mark.parametrize("plain", sorted(name for name in NEW if "-gen-" not in name))
def test_new_generation_run_issues_the_same_commands_as_a_legacy_run(plain):
    gen = plain.replace("-historical-", "-historical-gen-").replace(
        "-continuous-", "-continuous-gen-"
    )
    assert _input(gen)["workflow_generation"] == "g-1"
    assert "workflow_generation" not in _input(plain)
    assert _activities(_events(gen)) == _activities(_events(plain))
    assert _events(gen)[-1]["eventType"] == _events(plain)[-1]["eventType"]


@pytest.mark.parametrize(
    "name", sorted(n for n in NEW if "-gen-can-" in n and not n.endswith("run0.json"))
)
def test_a_continue_as_new_hop_keeps_the_runs_generation(name):
    """Each hop of a generation-carrying chain is still fenced as that run."""
    hop = _input(name)
    assert hop["workflow_generation"] == "g-1"
    if "historical" in name:
        assert hop["already_reconciled"] is True


@pytest.mark.parametrize("kind", ["historical", "continuous"])
def test_a_replaced_run_issues_its_ordinary_first_commands_and_ends(kind):
    stale = _events(f"new-{kind}-gen-stale-run0.json")
    assert _input(f"new-{kind}-gen-stale-run0.json")["workflow_generation"] == (
        "stale-generation"
    )
    assert stale[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"
    if kind == "historical":
        assert _activities(stale) == [
            "get_workflow_labels_activity",
            "set_eval_task_status_activity",
            "reconcile_eval_task_activity",
            "reap_stale_running_activity",
            "get_eval_task_state_activity",
        ]


@pytest.mark.e2e
@pytest.mark.xdist_group("temporal_eval_task_e2e")
async def test_every_new_history_replays_on_the_v1_59_release(
    workflow_environment, tmp_path, monkeypatch
):
    """Record every ``new`` run from this code, then replay each on v1.59.

    By design the generation never adds a command, so a replaced run (the
    ``*-stale`` scenarios) and a generation-bearing historical start replay on
    a worker that never heard of the field. The closed-sampling runs replay on
    dev, and v1.59 refuses them as it refuses dev's own.
    """
    recorder = _load_recorder(monkeypatch)
    histories = await recorder.record_histories(workflow_environment.client, "new")
    assert set(histories) == NEW
    release = _release_workflows(tmp_path, monkeypatch)
    dev = _dev_workflows(tmp_path, monkeypatch)
    for name, text in sorted(histories.items()):
        events = json.loads(text)["events"]
        assert WITHDRAWN_FENCE_PATCH not in b"".join(_markers(events)), name
        if name in TIMER:
            with pytest.raises(NondeterminismError):
                await _replay(name, text, release)
        else:
            await _replay(name, text, release)
        await _replay(name, text, dev)
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
    (types.FencedClaimInput, ("workflow_generation",)),
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
    """No-generation reconcile/reap/claim inputs stay byte-identical to dev's."""
    sent = []

    async def execute(name, arg, **_kwargs):
        sent.append(arg)
        return {}

    monkeypatch.setattr(wf.workflow, "execute_activity", execute)
    await wf._reconcile("t", first_closed=first_closed)
    await wf._reap("t", first_closed=first_closed)
    await wf._claim("t", 2)
    await wf._reconcile("t", first_closed=first_closed, generation="g")
    await wf._reap("t", first_closed=first_closed, generation="g")
    await wf._claim("t", 2, "g")
    plain = (
        (types.CheckedReconcileInput, types.CheckedReapInput)
        if first_closed
        else (types.ReconcileActivityInput, types.ReapInput)
    )
    assert [type(arg) for arg in sent] == [
        *plain,
        types.ClaimBatchInput,
        types.FencedReconcileInput,
        types.FencedReapInput,
        types.FencedClaimInput,
    ]
    assert all(arg.require_running is first_closed for arg in sent[3:5])
    assert all(arg.workflow_generation == "g" for arg in sent[3:])
