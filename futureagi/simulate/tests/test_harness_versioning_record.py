import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.utils import timezone

from simulate.models import (
    HarnessEnvironmentVersion,
    HarnessSuiteMember,
    HarnessSuiteValidation,
    HarnessSuiteVersion,
    HostedHarnessJob,
)
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.versions import (
    record_authoring,
    record_authoring_quietly,
)
from simulate.services.hosted_harness import (
    HostedHarnessError,
    create_hosted_job,
    create_selected_harness_run,
)
from simulate.services.hosted_harness_gateway import (
    _authoring_archive_for,
    authoring_content_digest,
    rewrite_authoring_scenarios,
)
from simulate.tests.test_harness_amend_archive import NAMES, _key, _run_environment
from simulate.tests.test_harness_provider import _v1_payload
from simulate.tests.test_hosted_harness_conversation import (
    _chat_checkpoint,
    _put_conversation_checkpoint,
    _Storage,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


# A build session that started a minute ago, and a proof made during it.
SESSION = timezone.now() - timedelta(minutes=1)


def _proof(holds, *, at=None):
    when = at or timezone.now()
    return {"holds": holds, "proved_at": when.isoformat().replace("+00:00", "Z")}


def _scenario(name, **fields):
    return {
        "name": name,
        "scenario_key": _key(name),
        "instruction": f"call about {name}",
        "tests": "t",
        **fields,
    }


def _resealed_manifest(tree, *, compiled=True, **fields):
    """A bundle manifest stamped for ``tree`` as ALK's reseal stamps it; ``compiled``
    False when ALK left the contract edit to a build (e.g. a first tool added)."""
    if not compiled:
        return json.dumps({**fields, "metadata": {}})
    metadata = {"sealed_from": drafts.sealed_from(tree)}
    if "contract.json" in tree:
        metadata["contract_compiled"] = archives.sha256_hex(tree["contract.json"])
    return json.dumps({**fields, "metadata": metadata})


def _authoring(names=NAMES[:2], *, contract=None, proofs=None, sealed=True, **extra):
    documents = [_scenario(name) for name in names]
    files = {
        "contract.json": json.dumps(contract or {"agent": "support", "tools": []}),
        "sub_goals.json": json.dumps(
            {"sub_goals": [{"name": "refunded", "what": "a refund was issued"}]}
        ),
        "schema.sql": "create table orders(id int);",
        "handlers/lookup.py": "def handle(db):\n    return {}\n",
        "scenarios.json": json.dumps(documents),
        "cost.json": '{"usd": 3}',
        ".agent-cache/sessions/1.key": "private",
    }
    for document in documents:
        folder = f"scenarios/{document['name']}"
        files[f"{folder}/scenario.json"] = json.dumps(document)
        files[f"{folder}/ready.py"] = "def ready(world):\n    return True\n"
    if proofs is not None:
        files["proofs.json"] = json.dumps(proofs)
    if sealed:
        files["environment-bundle/manifest.json"] = json.dumps({"files": []})
        files["runtime-validation.json"] = json.dumps({"status": "passed"})
    files.update(extra)
    if any(".." in path.split("/") for path in files):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as archive:
            for path, text in files.items():
                data = text.encode() if isinstance(text, str) else text
                member = tarfile.TarInfo(path)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return out.getvalue()
    # Packed as the freeze packs it, so the archive carries tar's own "./" entries.
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for path, text in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode() if isinstance(text, str) else text)
        return subprocess.run(
            ["tar", "-czf", "-", "."],
            cwd=root,
            capture_output=True,
            check=True,
            env={**os.environ, "COPYFILE_DISABLE": "1"},
        ).stdout


def _environment(user, workspace, key):
    environment = _run_environment(user, workspace, key)
    environment.refresh_from_db()
    return environment


def _versions(model, environment):
    return list(
        model.no_workspace_objects.filter(job=environment).order_by("created_at")
    )


def _save_drafts(environment, user=None, key="save"):
    """Press Save on every open draft, as the workspace's Save bar does, once the
    chat's turns have finished."""
    from simulate.models import HostedHarnessConversationMessage
    from simulate.services.harness_versioning.drafts import (
        DRAFT_PIECES,
        SaveRequest,
        open_draft,
        save_drafts,
    )

    HostedHarnessConversationMessage.no_workspace_objects.filter(
        conversation__job=environment, role="user"
    ).update(state="completed")
    requests = {
        piece: SaveRequest(draft.parent_id, draft.revision)
        for piece in DRAFT_PIECES
        if (draft := open_draft(environment, piece)) is not None
    }
    body = save_drafts(environment, requests, idempotency_key=key, user=user)
    environment.refresh_from_db()
    return body


class TestFirstAuthoring:
    def test_records_e1_s1_and_makes_them_the_defaults(self, user, workspace, storage):
        environment = _environment(user, workspace, "first-authoring")

        recorded = record_authoring(environment, _authoring(), origin="authoring")

        environment.refresh_from_db()
        assert environment.environment_version_id == recorded.environment.id
        assert environment.suite_version_id == recorded.suite.id
        for version in (recorded.environment, recorded.suite):
            assert (version.version_number, version.status, version.origin) == (
                1,
                "active",
                "authoring",
            )
            assert version.parent_id is None
            assert version.summary == "First version"
        assert recorded.suite.authored_against_id == recorded.environment.id
        assert recorded.suite.scenario_count == 2

    def test_lists_each_scenario_as_a_member_of_s1(self, user, workspace, storage):
        environment = _environment(user, workspace, "first-members")

        recorded = record_authoring(environment, _authoring(), origin="authoring")

        members = {
            member.scenario_key: member
            for member in HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=recorded.suite
            )
        }
        assert set(members) == {_key(name) for name in NAMES[:2]}
        member = members[_key(NAMES[0])]
        assert member.scenario.job_id == environment.id
        assert member.content["instruction"] == f"call about {NAMES[0]}"
        assert "scenario_key" not in member.content

    def test_stores_each_piece_under_its_content_hash(self, user, workspace, storage):
        environment = _environment(user, workspace, "first-objects")

        recorded = record_authoring(environment, _authoring(), origin="authoring")

        for version in (recorded.environment, recorded.suite):
            body = storage.objects[version.archive_object_key]
            assert hashlib.sha256(body).hexdigest() == version.archive_sha256
            assert len(body) == version.archive_size
        environment_files = archives.read_archive(
            storage.objects[recorded.environment.archive_object_key]
        )
        suite_files = archives.read_archive(
            storage.objects[recorded.suite.archive_object_key]
        )
        assert "contract.json" in environment_files
        assert not any(path.startswith("scenarios") for path in environment_files)
        assert set(suite_files) >= {
            "scenarios.json",
            f"scenarios/{NAMES[0]}/scenario.json",
        }

    def test_never_stores_hidden_session_files_or_spend(self, user, workspace, storage):
        environment = _environment(user, workspace, "first-hidden")

        recorded = record_authoring(environment, _authoring(), origin="authoring")

        for key in (
            recorded.environment.archive_object_key,
            recorded.suite.archive_object_key,
            recorded.pair_key,
        ):
            files = archives.read_archive(storage.objects[key])
            assert ".agent-cache/sessions/1.key" not in files
            assert "cost.json" not in files

    def test_documents_carry_the_contract_and_sub_goal_digests(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "first-documents")

        recorded = record_authoring(environment, _authoring(), origin="authoring")

        documents = recorded.environment.documents
        assert documents["contract"]["agent"] == "support"
        assert set(documents["sub_goal_digests"]) == {"refunded"}
        assert "schema.sql" in documents["file_digests"]
        assert [one["name"] for one in recorded.suite.documents["scenarios"]] == list(
            NAMES[:2]
        )

    def test_scenario_count_follows_the_suite(self, user, workspace, storage):
        environment = _environment(user, workspace, "first-count")

        record_authoring(environment, _authoring(NAMES), origin="authoring")

        environment.refresh_from_db()
        assert environment.scenario_count == 3
        assert environment.payload["scenario_count"] == 3


class TestDefaultPair:
    def test_the_job_points_at_the_composed_pair(self, user, workspace, storage):
        environment = _environment(user, workspace, "pair-key")
        body = _authoring()

        recorded = record_authoring(environment, body, origin="authoring")

        environment.refresh_from_db()
        metadata = environment.payload["metadata"]
        assert metadata["authoring_object_key"] == recorded.pair_key
        assert _authoring_archive_for(environment) == storage.objects[recorded.pair_key]

    def test_runs_read_the_same_content_as_before(self, user, workspace, storage):
        environment = _environment(user, workspace, "pair-content")
        body = _authoring()

        record_authoring(environment, body, origin="authoring")

        environment.refresh_from_db()
        pair = storage.objects[environment.payload["metadata"]["authoring_object_key"]]
        assert authoring_content_digest(pair) == authoring_content_digest(body)
        assert environment.payload["metadata"]["authoring_revision"] == (
            authoring_content_digest(pair)
        )
        files = archives.read_archive(pair)
        assert "environment-bundle/manifest.json" in files
        assert "runtime-validation.json" in files

    def test_a_later_archive_without_a_bundle_keeps_the_sealed_copy(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "pair-sealed")
        record_authoring(environment, _authoring(), origin="authoring")

        recorded = record_authoring(
            environment,
            _authoring(NAMES, sealed=False),
            origin="chat",
        )

        files = archives.read_archive(storage.objects[recorded.pair_key])
        assert "environment-bundle/manifest.json" in files
        assert f"scenarios/{NAMES[2]}/scenario.json" in files


class TestReuseAndNewVersions:
    def test_identical_content_reuses_both_versions(self, user, workspace, storage):
        environment = _environment(user, workspace, "reuse-same")
        first = record_authoring(environment, _authoring(), origin="authoring")

        second = record_authoring(environment, _authoring(), origin="authoring")

        assert second.environment.id == first.environment.id
        assert second.suite.id == first.suite.id
        assert len(_versions(HarnessEnvironmentVersion, environment)) == 1
        assert len(_versions(HarnessSuiteVersion, environment)) == 1
        assert (
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=first.suite
            ).count()
            == 2
        )

    @pytest.mark.parametrize(
        ("path", "new_version"),
        [("world.sqlite", True), ("generic-harness/world-ir.json", False)],
    )
    def test_world_data_versions_by_world_sqlite_only(
        self, user, workspace, storage, path, new_version
    ):
        # A repair is written into world.sqlite; world-ir.json is derived from it.
        environment = _environment(user, workspace, f"reuse-world-{new_version}")
        first = record_authoring(
            environment, _authoring(**{path: b"before"}), origin="authoring"
        )

        second = record_authoring(
            environment, _authoring(**{path: b"after"}), origin="authoring"
        )

        assert (second.environment.id != first.environment.id) is new_version
        if new_version:
            files = archives.read_archive(
                storage.objects[second.environment.archive_object_key]
            )
            assert files[path] == b"after"

    def test_a_new_scenario_makes_s2_and_keeps_e1(self, user, workspace, storage):
        environment = _environment(user, workspace, "reuse-new-suite")
        first = record_authoring(environment, _authoring(), origin="authoring")

        second = record_authoring(environment, _authoring(NAMES), origin="chat")

        assert second.environment.id == first.environment.id
        assert second.suite.version_number == 2
        assert second.suite.parent_id == first.suite.id
        assert second.suite.origin == "chat"
        assert second.suite.diff["added"] == [_key(NAMES[2])]
        assert second.suite.summary == "1 scenario added"
        assert (
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=second.suite
            ).count()
            == 3
        )

    def test_a_contract_change_makes_e2_with_its_diff(self, user, workspace, storage):
        environment = _environment(user, workspace, "reuse-new-env")
        first = record_authoring(environment, _authoring(), origin="authoring")

        second = record_authoring(
            environment,
            _authoring(contract={"agent": "support", "tools": [{"name": "refund"}]}),
            origin="chat",
        )

        assert second.environment.version_number == 2
        assert second.environment.parent_id == first.environment.id
        assert second.environment.diff["contract"]["tools"]["added"] == ["refund"]
        assert second.suite.id == first.suite.id

    def test_returning_to_earlier_content_reuses_its_version(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "reuse-revert")
        first = record_authoring(environment, _authoring(), origin="authoring")
        record_authoring(environment, _authoring(NAMES), origin="chat")

        third = record_authoring(environment, _authoring(), origin="chat")

        environment.refresh_from_db()
        assert third.suite.id == first.suite.id
        assert environment.suite_version_id == first.suite.id
        assert len(_versions(HarnessSuiteVersion, environment)) == 2


class TestValidation:
    def test_proofs_that_all_hold_pass_the_suite(self, user, workspace, storage):
        environment = _environment(user, workspace, "proofs-pass")
        proofs = {_key(name): _proof(True) for name in NAMES[:2]}

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert validation.environment_version_id == recorded.environment.id
        assert validation.status == "passed"

    def test_some_failing_proofs_make_it_partial(self, user, workspace, storage):
        environment = _environment(user, workspace, "proofs-partial")
        proofs = {_key(NAMES[0]): _proof(True), _key(NAMES[1]): _proof(False)}

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert validation.status == "partial"

    def test_proofs_for_dropped_scenarios_are_ignored(self, user, workspace, storage):
        environment = _environment(user, workspace, "proofs-stale")
        proofs = {
            **{_key(name): _proof(True) for name in NAMES[:2]},
            "long-gone": _proof(False),
        }

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert validation.status == "passed"
        assert "long-gone" not in validation.proofs

    def test_no_proofs_file_records_no_validation(self, user, workspace, storage):
        environment = _environment(user, workspace, "proofs-none")

        recorded = record_authoring(
            environment, _authoring(), origin="authoring", proved_since=SESSION
        )

        assert not HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=recorded.suite
        ).exists()

    def test_only_proofs_made_in_this_session_count(self, user, workspace, storage):
        environment = _environment(user, workspace, "proofs-session")
        proofs = {
            _key(NAMES[0]): _proof(True),
            _key(NAMES[1]): _proof(True, at=SESSION - timedelta(days=1)),
            "untimed": {"holds": True},
        }

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert set(validation.proofs) == {_key(NAMES[0])}
        assert validation.status == "partial"

    def test_proofs_from_an_earlier_session_record_no_validation(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "proofs-old")
        proofs = {
            _key(name): _proof(True, at=SESSION - timedelta(hours=3))
            for name in NAMES[:2]
        }

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="extend",
            proved_since=SESSION,
        )

        assert not HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=recorded.suite
        ).exists()

    def test_a_proof_time_without_a_zone_is_read_as_utc(self, user, workspace, storage):
        environment = _environment(user, workspace, "proofs-naive")
        naive = timezone.now().replace(tzinfo=None).isoformat()
        proofs = {_key(name): {"holds": True, "proved_at": naive} for name in NAMES[:2]}

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert validation.status == "passed"

    @pytest.mark.parametrize("minutes, counted", [(1, True), (3, False)])
    def test_the_clock_skew_allowance_is_two_minutes(
        self, user, workspace, storage, minutes, counted
    ):
        environment = _environment(user, workspace, f"proofs-skew-{minutes}")
        proofs = {
            _key(name): _proof(True, at=SESSION - timedelta(minutes=minutes))
            for name in NAMES[:2]
        }

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        assert (
            HarnessSuiteValidation.no_workspace_objects.filter(
                suite_version=recorded.suite
            ).exists()
            is counted
        )

    def test_dropping_every_proof_is_logged(self, user, workspace, storage, caplog):
        environment = _environment(user, workspace, "proofs-all-dropped")
        proofs = {
            _key(name): _proof(True, at=SESSION - timedelta(days=1))
            for name in NAMES[:2]
        }

        with caplog.at_level("WARNING"):
            record_authoring(
                environment,
                _authoring(proofs=proofs),
                origin="authoring",
                proved_since=SESSION,
            )

        assert any(
            "every proof predates this session" in record.message
            for record in caplog.records
        )

    def test_an_extend_keeps_earlier_proofs_of_unchanged_scenarios(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "proofs-carry")
        first_proofs = {_key(name): _proof(True, at=SESSION) for name in NAMES[:2]}
        first = record_authoring(
            environment,
            _authoring(proofs=first_proofs),
            origin="authoring",
            proved_since=SESSION,
        )
        environment.refresh_from_db()
        # The extend proves only its new scenario; the first two proofs are now old.
        extend_session = timezone.now() + timedelta(minutes=5)
        proofs = {**first_proofs, _key(NAMES[2]): _proof(True, at=extend_session)}

        recorded = record_authoring(
            environment,
            _authoring(NAMES, proofs=proofs),
            origin="extend",
            proved_since=extend_session,
        )

        assert recorded.environment.id == first.environment.id
        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert set(validation.proofs) == {_key(name) for name in NAMES}
        assert validation.status == "passed"

    def test_a_changed_scenario_does_not_keep_its_old_proof(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "proofs-carry-changed")
        first_proofs = {_key(name): _proof(True, at=SESSION) for name in NAMES[:2]}
        record_authoring(
            environment,
            _authoring(proofs=first_proofs),
            origin="authoring",
            proved_since=SESSION,
        )
        environment.refresh_from_db()
        extend_session = timezone.now() + timedelta(minutes=5)
        changed = _scenario(NAMES[0], instruction="call about something else")
        documents = [changed, *(_scenario(name) for name in NAMES[1:])]
        proofs = {
            **first_proofs,
            _key(NAMES[2]): _proof(True, at=extend_session),
        }

        recorded = record_authoring(
            environment,
            _authoring(
                NAMES,
                proofs=proofs,
                **{
                    "scenarios.json": json.dumps(documents),
                    f"scenarios/{NAMES[0]}/scenario.json": json.dumps(changed),
                },
            ),
            origin="extend",
            proved_since=extend_session,
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert set(validation.proofs) == {_key(NAMES[1]), _key(NAMES[2])}
        assert validation.status == "partial"

    def test_a_renamed_scenario_keeps_its_old_proof(self, user, workspace, storage):
        environment = _environment(user, workspace, "proofs-carry-renamed")
        first_proofs = {_key(name): _proof(True, at=SESSION) for name in NAMES[:2]}
        record_authoring(
            environment,
            _authoring(proofs=first_proofs),
            origin="authoring",
            proved_since=SESSION,
        )
        environment.refresh_from_db()
        extend_session = timezone.now() + timedelta(minutes=5)
        renamed = {**_scenario(NAMES[0]), "name": "renamed"}
        documents = [renamed, *(_scenario(name) for name in NAMES[1:])]
        proofs = {
            **first_proofs,
            _key(NAMES[2]): _proof(True, at=extend_session),
        }

        recorded = record_authoring(
            environment,
            _authoring(
                NAMES,
                proofs=proofs,
                **{
                    "scenarios.json": json.dumps(documents),
                    f"scenarios/{NAMES[0]}/scenario.json": json.dumps(renamed),
                },
            ),
            origin="extend",
            proved_since=extend_session,
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=recorded.suite
        )
        assert set(validation.proofs) == {_key(name) for name in NAMES}
        assert validation.status == "passed"

    def test_a_build_without_a_session_start_records_no_validation(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "proofs-no-cutoff")
        proofs = {_key(name): _proof(True) for name in NAMES[:2]}

        recorded = record_authoring(
            environment, _authoring(proofs=proofs), origin="authoring"
        )

        assert not HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=recorded.suite
        ).exists()


class TestWhatIsNotVersioned:
    def test_nothing_is_recorded_while_versioning_is_off(
        self, user, workspace, storage, settings
    ):
        settings.HARNESS_VERSIONING_ENABLED = False
        environment = _environment(user, workspace, "flag-off")

        assert record_authoring(environment, _authoring(), origin="authoring") is None
        environment.refresh_from_db()
        assert environment.environment_version_id is None
        assert not _versions(HarnessEnvironmentVersion, environment)

    def test_a_run_job_is_never_versioned(self, user, workspace, storage):
        environment = _environment(user, workspace, "run-job")
        run, _ = create_selected_harness_run(
            environment, scenario_keys=[_key(NAMES[0])], trials=1, idempotency_key="r"
        )

        assert record_authoring(run, _authoring(), origin="authoring") is None
        assert not HarnessEnvironmentVersion.no_workspace_objects.filter(
            job=run
        ).exists()

    def test_an_unsafe_member_is_refused_and_nothing_is_written(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "unsafe")

        with pytest.raises(HostedHarnessError) as error:
            record_authoring(
                environment,
                _authoring(**{"../escape.py": "print(1)"}),
                origin="authoring",
            )

        assert error.value.code == "archive_member_invalid"
        assert not _versions(HarnessEnvironmentVersion, environment)

    def test_an_archive_without_scenarios_is_refused(self, user, workspace, storage):
        environment = _environment(user, workspace, "no-scenarios")

        with pytest.raises(HostedHarnessError) as error:
            record_authoring(environment, _authoring(names=()), origin="authoring")

        assert error.value.code == "authoring_incomplete"

    def test_quiet_recording_never_raises(self, user, workspace, storage):
        environment = _environment(user, workspace, "quiet")

        assert (
            record_authoring_quietly(environment, b"garbage", origin="authoring")
            is None
        )
        assert not _versions(HarnessEnvironmentVersion, environment)


class TestAmend:
    def _versioned(self, user, workspace, storage, key):
        environment = _environment(user, workspace, key)
        first = record_authoring(environment, _authoring(), origin="authoring")
        environment.refresh_from_db()
        return environment, first

    def test_an_amend_makes_s2_and_never_rewrites_s1(self, user, workspace, storage):
        environment, first = self._versioned(user, workspace, storage, "amend-s2")
        s1_bytes = storage.objects[first.suite.archive_object_key]
        first_pair = environment.payload["metadata"]["authoring_object_key"]
        first_pair_bytes = storage.objects[first_pair]

        key = rewrite_authoring_scenarios(
            environment,
            [_scenario(NAMES[0], background_noise="street"), _scenario(NAMES[1])],
        )

        environment.refresh_from_db()
        suite = environment.suite_version
        assert suite.version_number == 2
        assert suite.origin == "manual"
        assert suite.parent_id == first.suite.id
        assert suite.diff["changed"] == {_key(NAMES[0]): ["background_noise"]}
        assert environment.environment_version_id == first.environment.id
        assert key == environment.payload["metadata"]["authoring_object_key"]
        assert key != first_pair
        assert storage.objects[first.suite.archive_object_key] == s1_bytes
        assert hashlib.sha256(s1_bytes).hexdigest() == first.suite.archive_sha256
        assert storage.objects[first_pair] == first_pair_bytes

    def test_a_run_after_an_amend_replays_the_amended_scenario(
        self, user, workspace, storage
    ):
        environment, _first = self._versioned(user, workspace, storage, "amend-run")
        rewrite_authoring_scenarios(
            environment,
            [_scenario(NAMES[0], background_noise="street"), _scenario(NAMES[1])],
        )
        environment.refresh_from_db()

        run, _ = create_selected_harness_run(
            environment,
            scenario_keys=[_key(NAMES[0])],
            trials=1,
            idempotency_key="after-amend",
        )

        key = run.payload["metadata"]["authoring_object_key"]
        assert key == environment.payload["metadata"]["authoring_object_key"]
        replayed = archives.read_archive(storage.objects[key])
        document = json.loads(replayed[f"scenarios/{NAMES[0]}/scenario.json"])
        assert document["background_noise"] == "street"

    def test_an_amend_before_versions_exist_writes_a_new_unversioned_object(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "amend-legacy")
        key = environment.payload["metadata"]["authoring_object_key"]
        original = storage.objects[key] = _authoring()

        returned = rewrite_authoring_scenarios(
            environment,
            [_scenario(NAMES[0], background_noise="street"), _scenario(NAMES[1])],
        )

        assert returned != key
        assert returned == environment.payload["metadata"]["authoring_object_key"]
        assert storage.objects[key] == original
        assert not _versions(HarnessSuiteVersion, environment)


class TestChatPromote:
    def _promote(self, environment, storage, names, *, sequence=100):
        from simulate.services.hosted_harness_conversation import (
            enqueue_message,
            promote_conversation_checkpoint,
        )
        from simulate.services.hosted_harness_gateway import with_authoring_basis

        conversation, command, _ = enqueue_message(
            environment,
            content="Add one",
            client_request_id=f"chat-{len(names)}-{sequence}",
        )
        # The workspace holds the environment it was composed from.
        files = {
            **archives.split_authoring(archives.read_archive(_authoring())).environment,
            **archives.read_archive(_chat_checkpoint(names, one_liner="chat")),
        }
        # The bundle the chat resealed for its edit.
        files["environment-bundle/manifest.json"] = _resealed_manifest(
            files, scenarios=names
        ).encode()
        body = with_authoring_basis(
            archives.pack_archive(files), command.payload["_environment_revision"]
        )
        key, digest = _put_conversation_checkpoint(conversation, storage, body)
        promote_conversation_checkpoint(
            conversation,
            object_key=key,
            digest=digest,
            sequence=sequence,
            expected_revision=command.payload["_environment_revision"],
        )
        environment.refresh_from_db()

    def test_a_promoted_chat_edit_becomes_the_next_version_on_save(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "chat-promote")
        first = record_authoring(environment, _authoring(), origin="authoring")
        environment.refresh_from_db()

        self._promote(environment, storage, NAMES)

        assert environment.suite_version_id == first.suite.id
        draft = HarnessSuiteVersion.no_workspace_objects.get(
            job=environment, status="draft"
        )
        assert (draft.revision, draft.parent_id) == (1, first.suite.id)
        _save_drafts(environment)
        suite = environment.suite_version
        assert suite.version_number == 2
        assert suite.origin == "chat"
        assert suite.parent_id == first.suite.id
        assert suite.scenario_count == 3
        pair = storage.objects[environment.payload["metadata"]["authoring_object_key"]]
        assert f"scenarios/{NAMES[2]}/scenario.json" in archives.read_archive(pair)

    def test_a_promote_on_an_unversioned_environment_records_nothing(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "chat-legacy")
        storage.objects[environment.payload["metadata"]["authoring_object_key"]] = (
            _chat_checkpoint(NAMES[:2], one_liner="original")
        )

        self._promote(environment, storage, NAMES)

        assert environment.environment_version_id is None
        assert not _versions(HarnessSuiteVersion, environment)


class TestFreezeHook:
    def _sandbox(self, body):
        files = {
            "/work/authoring/contract.json": b'{"modality":"voice"}',
            "/work/authoring/scenarios.json": json.dumps(
                [_scenario(name) for name in NAMES[:2]]
            ).encode(),
            "/work/bundle/manifest.json": b'{"schema_version":"v2"}',
            "/tmp/authoring-rerun.tar.gz": body,
        }

        def download(path, timeout=None):
            if path not in files:
                raise FileNotFoundError(path)
            return files[path]

        return SimpleNamespace(
            fs=SimpleNamespace(download_file=download),
            process=SimpleNamespace(
                exec=lambda command, **kwargs: SimpleNamespace(exit_code=0, result="")
            ),
        )

    def _freeze(self, environment, body, monkeypatch, sandbox=None, **attempt_fields):
        from simulate.services.hosted_harness_gateway import HostedHarnessGateway

        monkeypatch.setattr(
            "simulate.services.harness_usage.record_sandbox_runtime",
            lambda *args, **kwargs: None,
        )
        attempt = SimpleNamespace(
            **{
                "id": "attempt-1",
                "job_id": environment.id,
                "attempt_number": 1,
                **attempt_fields,
            }
        )
        HostedHarnessGateway._sync_authoring_progress(
            attempt, sandbox or self._sandbox(body)
        )
        environment.refresh_from_db()

    def _building(self, user, workspace, key):
        environment, _ = create_hosted_job(
            user.organization,
            _v1_payload(scenario_count=2),
            idempotency_key=key,
            workspace=workspace,
        )
        return environment

    def test_the_build_freeze_records_e1_and_s1(
        self, user, workspace, storage, monkeypatch
    ):
        environment = self._building(user, workspace, "freeze-first")
        body = _authoring()

        self._freeze(environment, body, monkeypatch)

        assert environment.environment_version.origin == "authoring"
        assert environment.suite_version.version_number == 1
        key = environment.payload["metadata"]["authoring_object_key"]
        frozen = (
            f"harness-authoring/{environment.organization_id}/{environment.id}/"
            f"{hashlib.sha256(body).hexdigest()}.tar.gz"
        )
        assert key != frozen
        assert archives.read_archive(storage.objects[key])["contract.json"]

    def test_a_freeze_records_this_sessions_proofs(
        self, user, workspace, storage, monkeypatch
    ):
        environment = self._building(user, workspace, "freeze-proofs")
        proofs = {_key(name): _proof(True) for name in NAMES[:2]}

        self._freeze(
            environment, _authoring(proofs=proofs), monkeypatch, created_at=SESSION
        )

        validation = HarnessSuiteValidation.no_workspace_objects.get(
            suite_version=environment.suite_version
        )
        assert validation.status == "passed"

    def test_an_extend_freeze_records_an_extend_version(
        self, user, workspace, storage, monkeypatch
    ):
        environment = self._building(user, workspace, "freeze-extend")
        self._freeze(environment, _authoring(), monkeypatch)
        payload = dict(environment.payload)
        payload["metadata"] = {**payload["metadata"], "scenario_extend": {"count": 1}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()

        self._freeze(environment, _authoring(NAMES), monkeypatch)

        assert environment.suite_version.version_number == 2
        assert environment.suite_version.origin == "extend"
        assert "scenario_extend" not in environment.payload["metadata"]

    def test_an_extend_before_any_version_records_nothing(
        self, user, workspace, storage, monkeypatch
    ):
        environment = self._building(user, workspace, "freeze-extend-legacy")
        payload = dict(environment.payload)
        payload["metadata"] = {**payload["metadata"], "scenario_extend": {"count": 1}}
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            payload=payload
        )
        environment.refresh_from_db()

        self._freeze(environment, _authoring(), monkeypatch)

        assert environment.payload["metadata"]["authoring_object_key"]
        assert environment.environment_version_id is None

    def test_a_freeze_that_cannot_be_versioned_still_freezes(
        self, user, workspace, storage, monkeypatch
    ):
        environment = self._building(user, workspace, "freeze-broken")

        self._freeze(environment, _authoring(names=()), monkeypatch)

        assert environment.payload["metadata"]["authoring_object_key"]
        assert environment.environment_version_id is None


class TestEditsAgainstAMovedDefault:
    def test_an_edit_against_an_older_pair_is_refused(self, user, workspace, storage):
        environment = _environment(user, workspace, "stale-edit")
        first = record_authoring(environment, _authoring(), origin="authoring")
        record_authoring(environment, _authoring(NAMES), origin="chat")

        with pytest.raises(HostedHarnessError) as error:
            record_authoring(
                environment,
                _authoring(),
                origin="manual",
                expected_pair_key=first.pair_key,
            )

        assert (error.value.code, error.value.status_code) == ("version_conflict", 409)
        assert len(_versions(HarnessSuiteVersion, environment)) == 2

    @pytest.mark.parametrize("versioning", [True, False])
    def test_editing_a_run_is_refused_and_changes_nothing(
        self, user, workspace, storage, settings, versioning
    ):
        from simulate.services.hosted_harness_gateway import AuthoringArchiveKept

        environment = _environment(user, workspace, f"amend-from-run-{versioning}")
        record_authoring(environment, _authoring(), origin="authoring")
        environment.refresh_from_db()
        run, _ = create_selected_harness_run(
            environment, scenario_keys=[_key(NAMES[0])], trials=1, idempotency_key="r1"
        )
        record_authoring(environment, _authoring(NAMES), origin="chat")
        environment.refresh_from_db()
        newer = environment.suite_version
        run_key = run.payload["metadata"]["authoring_object_key"]
        settings.HARNESS_VERSIONING_ENABLED = versioning

        with pytest.raises(AuthoringArchiveKept) as kept:
            rewrite_authoring_scenarios(
                run, [_scenario(NAMES[0], background_noise="street")]
            )

        assert (kept.value.code, kept.value.status_code) == (
            "scenario_edit_on_run",
            409,
        )
        environment.refresh_from_db()
        run.refresh_from_db()
        assert environment.suite_version_id == newer.id
        assert run.payload["metadata"]["authoring_object_key"] == run_key

    def test_editing_a_run_of_a_deleted_environment_is_refused_not_an_error(
        self, user, workspace, storage
    ):
        from simulate.services.harness_versioning.versions import (
            environment_head,
            is_versioned,
        )
        from simulate.services.hosted_harness_gateway import AuthoringArchiveKept

        environment = _environment(user, workspace, "amend-deleted-env")
        record_authoring(environment, _authoring(), origin="authoring")
        run, _ = create_selected_harness_run(
            environment, scenario_keys=[_key(NAMES[0])], trials=1, idempotency_key="r1"
        )
        HostedHarnessJob.no_workspace_objects.filter(id=environment.id).update(
            deleted=True
        )

        assert environment_head(run) is None
        assert is_versioned(run) is False
        with pytest.raises(AuthoringArchiveKept) as kept:
            rewrite_authoring_scenarios(run, [_scenario(NAMES[0])])
        assert kept.value.code == "scenario_edit_on_run"

    def test_a_version_conflict_keeps_its_code_and_status(
        self, user, workspace, storage, monkeypatch
    ):
        from simulate.services.harness_versioning import versions
        from simulate.services.hosted_harness_gateway import AuthoringArchiveKept

        environment = _environment(user, workspace, "amend-conflict")
        record_authoring(environment, _authoring(), origin="authoring")
        environment.refresh_from_db()

        def conflict(*args, **kwargs):
            raise HostedHarnessError(
                "version_conflict", "the environment changed", status_code=409
            )

        monkeypatch.setattr(versions, "record_authoring", conflict)
        with pytest.raises(AuthoringArchiveKept) as kept:
            rewrite_authoring_scenarios(environment, [_scenario(NAMES[0])])

        assert (kept.value.code, kept.value.status_code) == ("version_conflict", 409)

    def test_an_edit_records_who_made_it(self, user, workspace, storage):
        environment = _environment(user, workspace, "amend-author")
        record_authoring(environment, _authoring(), origin="authoring")
        environment.refresh_from_db()

        rewrite_authoring_scenarios(
            environment,
            [_scenario(NAMES[0], background_noise="street"), _scenario(NAMES[1])],
            user=user,
        )

        environment.refresh_from_db()
        assert environment.suite_version.created_by_id == user.id

    def test_an_amend_that_drops_a_scenario_leaves_it_out_of_the_new_suite(
        self, user, workspace, storage
    ):
        from simulate.models import HostedHarnessScenario

        environment = _environment(user, workspace, "amend-drop")
        record_authoring(environment, _authoring(), origin="authoring")
        environment.refresh_from_db()

        rewrite_authoring_scenarios(environment, [_scenario(NAMES[0])])

        environment.refresh_from_db()
        suite = environment.suite_version
        assert suite.diff["removed"] == [
            {"key": _key(NAMES[1]), "reason": "removed by manual edit"}
        ]
        assert list(
            HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=suite
            ).values_list("scenario_key", flat=True)
        ) == [_key(NAMES[0])]
        dropped = HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=_key(NAMES[1])
        )
        assert dropped.deleted is True

    def test_a_failure_while_saving_an_amend_changes_nothing(
        self, user, workspace, storage, monkeypatch
    ):
        from simulate.services.hosted_harness_gateway import AuthoringArchiveKept

        environment = _environment(user, workspace, "amend-broken-store")
        record_authoring(environment, _authoring(), origin="authoring")
        environment.refresh_from_db()
        before = environment.payload["metadata"]["authoring_object_key"]

        def broken(key, body):
            raise RuntimeError("storage down")

        monkeypatch.setattr(archives, "put_archive", broken)
        with pytest.raises(AuthoringArchiveKept):
            rewrite_authoring_scenarios(environment, [_scenario(NAMES[0])])

        environment.refresh_from_db()
        assert environment.payload["metadata"]["authoring_object_key"] == before
        assert len(_versions(HarnessSuiteVersion, environment)) == 1


class TestEditsCarryNoProof:
    def test_a_chat_version_records_no_validation(self, user, workspace, storage):
        environment = _environment(user, workspace, "chat-no-proof")
        proofs = {_key(name): _proof(True) for name in NAMES}
        record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        second = record_authoring(
            environment, _authoring(NAMES, proofs=proofs), origin="chat"
        )

        assert not HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=second.suite
        ).exists()

    def test_a_contract_change_reuses_s1_without_proving_it_again(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "chat-contract")
        proofs = {_key(name): _proof(True) for name in NAMES[:2]}
        first = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        second = record_authoring(
            environment,
            _authoring(
                proofs=proofs, contract={"agent": "x", "tools": [{"name": "t"}]}
            ),
            origin="chat",
        )

        assert second.suite.id == first.suite.id
        assert second.environment.id != first.environment.id
        assert list(
            HarnessSuiteValidation.no_workspace_objects.filter(
                suite_version=first.suite
            ).values_list("environment_version_id", flat=True)
        ) == [first.environment.id]

    def test_proofs_keyed_by_another_spelling_still_count(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "proof-spelling")
        proofs = {_key(name).replace("-", "_"): _proof(True) for name in NAMES[:2]}

        recorded = record_authoring(
            environment,
            _authoring(proofs=proofs),
            origin="authoring",
            proved_since=SESSION,
        )

        assert (
            HarnessSuiteValidation.no_workspace_objects.get(
                suite_version=recorded.suite
            ).status
            == "passed"
        )

    def test_an_empty_proofs_file_records_no_validation(self, user, workspace, storage):
        environment = _environment(user, workspace, "proof-empty")

        recorded = record_authoring(
            environment,
            _authoring(proofs={}),
            origin="authoring",
            proved_since=SESSION,
        )

        assert not HarnessSuiteValidation.no_workspace_objects.filter(
            suite_version=recorded.suite
        ).exists()


class TestSealedCopy:
    def test_a_partial_bundle_falls_back_to_the_kept_copy(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "sealed-partial")
        record_authoring(
            environment, _authoring(), origin="authoring", proved_since=SESSION
        )

        recorded = record_authoring(
            environment,
            _authoring(
                NAMES,
                sealed=False,
                **{"runtime-validation.json": '{"status": "partial"}'},
            ),
            origin="chat",
        )

        files = archives.read_archive(storage.objects[recorded.pair_key])
        assert "environment-bundle/manifest.json" in files
        assert json.loads(files["runtime-validation.json"]) == {"status": "partial"}

    def test_a_missing_kept_copy_fails_instead_of_dropping_the_bundle(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "sealed-missing")
        record_authoring(
            environment, _authoring(), origin="authoring", proved_since=SESSION
        )
        environment.refresh_from_db()
        storage.objects.pop(environment.payload["metadata"]["sealed_bundle_object_key"])

        with pytest.raises(KeyError):
            record_authoring(
                environment, _authoring(NAMES, sealed=False), origin="chat"
            )


class TestMembers:
    def test_a_renamed_scenario_keeps_its_key_and_row(self, user, workspace, storage):
        environment = _environment(user, workspace, "member-rename")
        first = record_authoring(
            environment, _authoring(), origin="authoring", proved_since=SESSION
        )
        renamed = {**_scenario(NAMES[0]), "name": "renamed scenario"}
        body = _authoring(
            **{
                "scenarios.json": json.dumps([renamed, _scenario(NAMES[1])]),
                f"scenarios/{NAMES[0]}/scenario.json": json.dumps(renamed),
            }
        )

        second = record_authoring(environment, body, origin="chat")

        members = {
            member.scenario_key: member
            for member in HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=second.suite
            )
        }
        old = {
            member.scenario_key: member
            for member in HarnessSuiteMember.no_workspace_objects.filter(
                suite_version=first.suite
            )
        }
        assert set(members) == set(old)
        assert members[_key(NAMES[0])].scenario_id == old[_key(NAMES[0])].scenario_id
        assert members[_key(NAMES[0])].content["name"] == "renamed scenario"


class TestPromoteNoOp:
    def test_promoting_the_same_checkpoint_twice_changes_nothing(
        self, user, workspace, storage
    ):
        environment = _environment(user, workspace, "promote-twice")
        record_authoring(
            environment, _authoring(), origin="authoring", proved_since=SESSION
        )
        environment.refresh_from_db()
        chat = TestChatPromote()

        key = environment.payload["metadata"]["authoring_object_key"]
        chat._promote(environment, storage, NAMES)
        chat._promote(environment, storage, NAMES, sequence=101)

        assert environment.payload["metadata"]["authoring_object_key"] == key
        suites = _versions(HarnessSuiteVersion, environment)
        assert [(one.status, one.revision) for one in suites] == [
            ("active", 0),
            ("draft", 1),
        ]
