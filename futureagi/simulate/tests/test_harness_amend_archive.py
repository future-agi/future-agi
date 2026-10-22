"""Editing or dropping scenarios keeps every file a later run, rerun or 'Add scenarios' needs."""

import hashlib
import io
import json
import tarfile

import pytest

from simulate.models import HostedHarnessJob, HostedHarnessStageOutput
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    create_selected_harness_run,
    provision_scenarios,
    register_attempt,
)
from simulate.services.hosted_harness_gateway import (
    _rewritten_authoring_archive,
    push_scenarios_into_live_sandbox,
)
from simulate.tests.test_harness_provider import _v1_payload

NAMES = ("book_ride_airport", "cancel_active_ride", "explain_pin")


def _key(name):
    return name.replace("_", "-")


def _files(name, *, key=True, with_name=True):
    document = {"instruction": f"call about {name}", "tests": "t", "max_turns": 8}
    if with_name:
        document["name"] = name
    if key:
        document["scenario_key"] = _key(name)
    return {
        "scenario.json": json.dumps(document).encode(),
        "setup.py": b"def setup(world):\n    return None\n",
        "ready.py": b"def ready(world):\n    return True\n",
    }


def _bundle(root, names):
    entries, listed = {}, []
    for name in names:
        for file, data in _files(name).items():
            path = f"scenarios/{name}/{file}"
            entries[f"{root}/{path}"] = data
            listed.append(
                {
                    "path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                }
            )
    entries[f"{root}/manifest.json"] = json.dumps(
        {"schema_version": "futureagi.environment-bundle.v2", "files": listed}
    ).encode()
    return entries


def _archive(
    *, names=NAMES, folder=lambda name: name, bundles=True, prefix="", **flags
):
    entries = {"contract.json": b"{}"}
    entries["scenarios.json"] = json.dumps(
        [{"name": name, "tests": "t"} for name in names]
    ).encode()
    for name in names:
        for file, data in _files(name, **flags).items():
            entries[f"scenarios/{folder(name)}/{file}"] = data
    if bundles:
        entries.update(_bundle("environment-bundle", names))
        entries.update(_bundle("generic-harness/certified-bundle", names))
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for path, data in entries.items():
            member = tarfile.TarInfo(prefix + path)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return out.getvalue()


def _read(body):
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        return {
            member.name.removeprefix("./"): tar.extractfile(member).read()
            for member in tar.getmembers()
            if member.isfile()
        }


def _suite(names=NAMES, **fields):
    return [
        {"name": name, "scenario_key": _key(name), **fields.get(name, {})}
        for name in names
    ]


def _sealed(files):
    return {
        path: data
        for path, data in files.items()
        if path.startswith(("environment-bundle/", "generic-harness/"))
    }


def test_dropping_one_scenario_keeps_the_others_and_leaves_the_sealed_bundles_intact():
    before = _read(_archive())
    after = _read(_rewritten_authoring_archive(_archive(), _suite(NAMES[:2])))

    assert not any(path.startswith("scenarios/explain_pin/") for path in after)
    for name in NAMES[:2]:
        for file in ("scenario.json", "setup.py", "ready.py"):
            assert f"scenarios/{name}/{file}" in after
    assert _sealed(after) == _sealed(before)
    assert [one["name"] for one in json.loads(after["scenarios.json"])] == list(
        NAMES[:2]
    )


def test_editing_a_scenario_changes_only_its_authoring_copy():
    suite = _suite(explain_pin={"background_noise": "street", "tests": "new"})
    before = _read(_archive())
    after = _read(_rewritten_authoring_archive(_archive(), suite))

    edited = json.loads(after["scenarios/explain_pin/scenario.json"])
    assert edited["background_noise"] == "street"
    assert edited["tests"] == "new"
    assert edited["scenario_key"] == "explain-pin"
    assert edited["instruction"] == "call about explain_pin"
    assert (
        after["scenarios/explain_pin/ready.py"]
        == before["scenarios/explain_pin/ready.py"]
    )
    assert _sealed(after) == _sealed(before)


def test_edit_and_drop_then_a_second_amend_keep_every_remaining_file():
    once = _rewritten_authoring_archive(
        _archive(),
        _suite(NAMES[:2], cancel_active_ride={"background_noise": "office"}),
    )
    twice = _read(
        _rewritten_authoring_archive(
            once, _suite(NAMES[:2], book_ride_airport={"max_turns": 4})
        )
    )

    assert (
        json.loads(twice["scenarios/cancel_active_ride/scenario.json"])[
            "background_noise"
        ]
        == "office"
    )
    assert (
        json.loads(twice["scenarios/book_ride_airport/scenario.json"])["max_turns"] == 4
    )
    assert "scenarios/cancel_active_ride/setup.py" in twice
    assert _sealed(twice) == _sealed(_read(_archive()))


def test_every_file_a_bundle_manifest_lists_is_still_in_the_archive():
    after = _read(_rewritten_authoring_archive(_archive(), _suite(NAMES[:1])))

    for root in ("environment-bundle", "generic-harness/certified-bundle"):
        listed = json.loads(after[f"{root}/manifest.json"])["files"]
        assert all(f"{root}/{record['path']}" in after for record in listed)


@pytest.mark.parametrize(
    "shape",
    [
        {"bundles": False},
        {"folder": _key},
        {"with_name": False},
        {"prefix": "./"},
    ],
    ids=[
        "older-without-bundles",
        "folders-named-by-key",
        "no-name-in-document",
        "dot-slash",
    ],
)
def test_older_archive_shapes_keep_their_folders(shape):
    folder = shape.get("folder", lambda name: name)
    after = _read(_rewritten_authoring_archive(_archive(**shape), _suite(NAMES[1:])))

    assert not any(path.startswith(f"scenarios/{folder(NAMES[0])}/") for path in after)
    for name in NAMES[1:]:
        assert f"scenarios/{folder(name)}/ready.py" in after


def test_a_suite_only_archive_is_rewritten_without_touching_folders():
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        data = json.dumps([{"name": name} for name in NAMES]).encode()
        member = tarfile.TarInfo("scenarios.json")
        member.size = len(data)
        tar.addfile(member, io.BytesIO(data))
    after = _read(_rewritten_authoring_archive(out.getvalue(), _suite(NAMES[:1])))

    assert [one["name"] for one in json.loads(after["scenarios.json"])] == [NAMES[0]]


def test_a_folder_without_a_scenario_document_is_kept():
    body = _archive(names=NAMES[:1])
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
        members = [(m, tar.extractfile(m).read()) for m in tar.getmembers()]
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for member, data in members:
            tar.addfile(member, io.BytesIO(data))
        stray = tarfile.TarInfo("scenarios/notes/readme.txt")
        stray.size = 2
        tar.addfile(stray, io.BytesIO(b"hi"))

    after = _read(_rewritten_authoring_archive(out.getvalue(), _suite(NAMES[:1])))

    assert "scenarios/notes/readme.txt" in after


def test_a_rewrite_that_would_lose_a_kept_scenario_is_refused():
    suite = _suite() + [{"name": "never_written", "scenario_key": "never-written"}]

    assert _rewritten_authoring_archive(_archive(), suite) is None


class _Result:
    def __init__(self, result="", exit_code=0):
        self.result, self.exit_code = result, exit_code


class _Sandbox:
    def __init__(self, folders):
        self.folders = {
            name: json.loads(_files(name)["scenario.json"]) for name in folders
        }
        self.removed, self.uploaded = [], {}
        self.process, self.fs = self, self

    def exec(self, command, timeout=None):
        if command.startswith("ls "):
            return _Result("\n".join(self.folders))
        folder = command.split("/work/authoring/scenarios/")[1].split("/")[0].strip("'")
        if command.startswith("cat "):
            return _Result(json.dumps(self.folders[folder]))
        self.removed.append(folder)
        return _Result()

    def upload_file(self, data, path):
        self.uploaded[path] = json.loads(data)


@pytest.mark.django_db
def test_live_sandbox_keeps_the_folders_of_scenarios_still_in_the_suite(
    user, workspace, monkeypatch
):
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=3),
        idempotency_key="live-amend",
        workspace=workspace,
    )
    register_attempt(environment.id, endpoint_base_url="https://harness.example.test")
    environment.refresh_from_db()
    sandbox = _Sandbox(NAMES)

    class _Client:
        def get(self, ref, request_timeout=None):
            return sandbox

    monkeypatch.setattr(
        "simulate.services.hosted_harness_gateway.HostedHarnessGateway.__init__",
        lambda self: setattr(self, "client", _Client()),
    )
    from simulate.models import HostedHarnessAttempt

    HostedHarnessAttempt.no_workspace_objects.filter(job=environment).update(
        provider_ref="sandbox-1"
    )

    delivered = push_scenarios_into_live_sandbox(
        environment,
        _suite(NAMES[:2], cancel_active_ride={"background_noise": "street"}),
    )

    assert delivered is True
    assert sandbox.removed == ["explain_pin"]
    edited = sandbox.uploaded[
        "/work/authoring/scenarios/cancel_active_ride/scenario.json"
    ]
    assert edited["background_noise"] == "street"
    assert edited["scenario_key"] == "cancel-active-ride"


@pytest.mark.django_db
def test_a_selected_run_carries_the_current_edits_of_its_scenarios(user, workspace):
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=2),
        idempotency_key="edited-environment",
        workspace=workspace,
    )
    attempt = register_attempt(
        environment.id, endpoint_base_url="https://harness.example.test"
    ).attempt
    provision_scenarios(
        attempt,
        {
            "operation": "provision",
            "name": "Edited suite",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": _key(name),
                    "name": name,
                    "role": "customer",
                    "situation": "s",
                    "outcome": "o",
                    "persona": {"name": name},
                }
                for name in NAMES[:2]
            ],
        },
    )
    environment.refresh_from_db()
    HostedHarnessStageOutput.no_workspace_objects.create(
        job=environment,
        title="Scenarios",
        summary="2 pre-authored scenarios",
        kind="scenarios",
        data=[
            {
                "name": NAMES[0],
                "background_noise": "street",
                "persona": {"accent": "Indian", "name": "kept out"},
                "instruction": "not editable",
            },
            {"name": NAMES[1], "tests": "t"},
        ],
    )
    payload = dict(environment.payload)
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "authoring_object_key": "harness/environments/edited.tar.gz",
    }
    environment.payload = payload
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])

    run, _ = create_selected_harness_run(
        environment,
        scenario_keys=[_key(NAMES[0])],
        trials=1,
        idempotency_key="edited-run",
    )

    edits = run.payload["metadata"]["scenario_edits"]
    assert edits == {
        _key(NAMES[0]): {"background_noise": "street", "persona": {"accent": "Indian"}}
    }


def _run_environment(user, workspace, key):
    environment, _ = create_hosted_job(
        user.organization,
        _v1_payload(scenario_count=2),
        idempotency_key=key,
        workspace=workspace,
    )
    attempt = register_attempt(
        environment.id, endpoint_base_url="https://harness.example.test"
    ).attempt
    provision_scenarios(
        attempt,
        {
            "operation": "provision",
            "name": "Suite",
            "modality": "voice",
            "personas": [
                {
                    "scenario_key": _key(name),
                    "name": name,
                    "role": "customer",
                    "situation": "s",
                    "outcome": "o",
                    "persona": {"name": name},
                }
                for name in NAMES[:2]
            ],
        },
    )
    environment.refresh_from_db()
    payload = dict(environment.payload)
    payload["metadata"] = {
        **(payload.get("metadata") or {}),
        "authoring_object_key": f"harness/environments/{key}.tar.gz",
    }
    environment.payload = payload
    environment.state = HostedHarnessJob.State.COMPLETED
    environment.current_stage = "completed"
    environment.save(update_fields=["payload", "state", "current_stage", "updated_at"])
    return environment


@pytest.mark.django_db
def test_dropping_a_scenario_that_has_run_keeps_its_row_for_the_history(
    user, workspace
):
    from simulate.models import HostedHarnessExecution, HostedHarnessScenario
    from simulate.services.harness_scenarios import index_scenarios

    environment = _run_environment(user, workspace, "dropped-after-run")
    create_selected_harness_run(
        environment, scenario_keys=[_key(NAMES[1])], trials=1, idempotency_key="ran"
    )

    index_scenarios(environment, [{"name": NAMES[0]}], prune=True)

    # The historical row remains for the old Run, but it is hidden from the active suite
    # and cannot be selected for another Run.
    visible = HostedHarnessScenario.no_workspace_objects.filter(job=environment)
    assert list(visible.values_list("scenario_key", flat=True)) == [_key(NAMES[0])]
    historical = HostedHarnessScenario.all_objects.get(
        job=environment, scenario_key=_key(NAMES[1])
    )
    assert historical.deleted is True
    assert HostedHarnessExecution.no_workspace_objects.filter(
        source_scenario=historical
    ).exists()
    with pytest.raises(HostedHarnessError) as error:
        create_selected_harness_run(
            environment,
            scenario_keys=[_key(NAMES[1])],
            trials=1,
            idempotency_key="dropped-again",
        )
    assert error.value.code == "scenario_selection_unknown"

    index_scenarios(environment, [{"name": NAMES[0]}, {"name": NAMES[1]}], prune=True)

    assert sorted(
        HostedHarnessScenario.no_workspace_objects.filter(job=environment).values_list(
            "scenario_key", flat=True
        )
    ) == sorted(_key(name) for name in NAMES[:2])
    assert HostedHarnessScenario.all_objects.filter(job=environment).count() == 2
