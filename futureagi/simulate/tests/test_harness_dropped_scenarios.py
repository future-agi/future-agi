"""Scenarios a rebuild dropped for a removed capability: the stored reason, the restore, the key.

The harness writes ``dropped.json`` beside the scenarios; these tests pin what the platform keeps
of it, how the suite diff and compare read it, and that a restore keeps the scenario's history.
"""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models import HarnessSuiteVersion, HostedHarnessScenario
from simulate.services.harness_versioning import (
    archives,
    diffs,
    nudges,
    rebuild,
)
from simulate.services.harness_versioning.versions import (
    dropped_documents,
    record_authoring,
)
from simulate.services.hosted_harness import HostedHarnessError
from simulate.tests.test_harness_amend_archive import NAMES, _key
from simulate.tests.test_harness_run_compare import (
    _by_key,
    _compare,
    _done,
    _trials,
)
from simulate.tests.test_harness_versioning_backfill import _RealisticStorage
from simulate.tests.test_harness_versioning_runs import (
    _run,
    _scenario,
    _suite_archive,
    _versioned,
)

CANCEL = NAMES[1]


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _RealisticStorage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


REASON = "agent v5 no longer has `cancel_booking`"


def _record(name, **over):
    return {
        "name": name,
        "capabilities": {"tools": ["cancel_booking"], "sub_goals": ["refunded"]},
        "missing": {"tools": ["cancel_booking"], "sub_goals": []},
        "agent_version": "v5",
        "reason": REASON,
        "last_suite_version": "v3",
        "content": _scenario(name),
        **over,
    }


def _with_drops(documents, drops):
    """The authoring archive for ``documents``, with ``dropped.json`` holding ``drops``."""
    files = archives.read_archive(_suite_archive(documents))
    files["dropped.json"] = json.dumps(
        {"format": 1, "scenarios": {_key(name): _record(name) for name in drops}}
    ).encode()
    return archives.pack_archive(files)


# The stored record and the suite diff (no database).


def test_dropped_json_belongs_to_the_suite_piece():
    split = archives.split_authoring({"dropped.json": b"{}", "contract.json": b"{}"})

    assert "dropped.json" in split.suite and "dropped.json" not in split.environment


def test_the_suite_documents_keep_each_drop_without_its_content():
    files = {
        "dropped.json": json.dumps(
            {"format": 1, "scenarios": {_key(CANCEL): _record(CANCEL)}}
        ).encode()
    }

    found = dropped_documents(files)[_key(CANCEL)]

    assert found["reason"] == REASON
    assert found["agent_version"] == "v5"
    assert found["last_suite_version"] == "v3"
    assert found["capabilities"]["tools"] == ["cancel_booking"]
    assert "content" not in found and found["content_digest"]


def test_a_removed_scenario_the_harness_dropped_carries_its_stored_reason():
    diff = diffs.suite_diff(
        [_scenario(NAMES[0]), _scenario(CANCEL)],
        [_scenario(NAMES[0])],
        removed_reason="removed when the environment was updated",
        dropped={_key(CANCEL): dropped_documents_record()},
    )

    assert diff["removed"] == [
        {
            "key": _key(CANCEL),
            "reason": REASON,
            "capabilities": {"tools": ["cancel_booking"], "sub_goals": []},
            "agent_version": "v5",
        }
    ]


def test_drops_change_the_suite_hash_and_nothing_else_does():
    from simulate.services.harness_versioning import hashing

    tree = archives.read_archive(_suite_archive([_scenario(NAMES[0])]))
    plain = hashing.suite_hash(tree)
    record = {"format": 1, "scenarios": {_key(CANCEL): _record(CANCEL)}}
    with_drop = hashing.suite_hash(
        {**tree, "dropped.json": json.dumps(record).encode()}
    )
    same_drop_reordered = hashing.suite_hash(
        {**tree, "dropped.json": json.dumps(record, sort_keys=True, indent=1).encode()}
    )
    failed = {
        "format": 1,
        "scenarios": {_key(CANCEL): _record(CANCEL, restore_failed="x")},
    }
    other_drop = hashing.suite_hash(
        {**tree, "dropped.json": json.dumps(failed).encode()}
    )

    # Without drops the hash stays ALK's scenario digest.
    assert plain == hashing.tree_digest(
        hashing.scenario_files(tree), exclude=hashing._suite_unhashed
    )
    assert len({plain, with_drop, other_drop}) == 3
    assert with_drop == same_drop_reordered


def dropped_documents_record():
    return {
        "reason": REASON,
        "missing": {"tools": ["cancel_booking"], "sub_goals": []},
        "agent_version": "v5",
    }


def test_a_scenario_back_from_a_drop_is_restored_not_added():
    diff = diffs.suite_diff(
        [_scenario(NAMES[0])],
        [_scenario(NAMES[0]), _scenario(CANCEL), _scenario(NAMES[2])],
        was_dropped={_key(CANCEL): dropped_documents_record()},
    )

    assert diff["added"] == [_key(NAMES[2])]
    assert diff["restored"] == [_key(CANCEL)]
    assert diffs.summarize_suite(diff) == "1 scenario added, 1 scenario restored"


def test_the_rebuild_report_keeps_restored_and_failed_restores_masked():
    value = "FAKE-CREDENTIAL-1234567890abcdef"
    nudge = SimpleNamespace(
        trigger_agent_version_id=None,
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    with patch.object(nudges, "_agent_secrets", return_value={"K": {value}}):
        report = rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None),
            nudge,
            {
                "notes": "restored",
                "checks": {"passed": True},
                "restored": [{"key": _key(CANCEL), "name": CANCEL}],
                "restore_failed": [{"key": _key(NAMES[2]), "why": "a check " + value}],
            },
        )

    assert report["restored"] == [{"key": _key(CANCEL), "name": CANCEL}]
    assert report["restore_failed"][0]["key"] == _key(NAMES[2])
    assert value[:8] not in report["restore_failed"][0]["why"]


def test_the_rebuild_card_reads_the_stored_reason_and_lists_a_restore():
    nudge = SimpleNamespace(target="suite", draft_snapshot={})
    both = [_scenario(NAMES[0]), _scenario(CANCEL)]
    drops = {
        _key(CANCEL): {**_record(CANCEL), "capabilities": {"tools": ["cancel_booking"]}}
    }
    dropped = SimpleNamespace(
        documents={"scenarios": [_scenario(NAMES[0])], "dropped": drops}
    )

    removed = rebuild._buckets(
        nudge, SimpleNamespace(documents={"scenarios": both}), dropped
    )
    back = rebuild._buckets(
        nudge, dropped, SimpleNamespace(documents={"scenarios": both})
    )

    assert removed[0]["update"]["removed"][0]["reason"] == REASON
    assert back[0]["update"]["restored"] == [_key(CANCEL)]


def test_a_suite_rebuild_names_the_agent_its_environment_was_built_for():
    agent = SimpleNamespace(version_name=None, version_number=5)
    nudge = SimpleNamespace(
        base_environment_version=SimpleNamespace(agent_version=agent)
    )

    assert (
        rebuild._suite_agent_label(nudge, SimpleNamespace(agent_version=None)) == "v5"
    )


# Through versions, runs and compare.


@pytest.mark.django_db
class TestDropAndRestore:
    def _three(self, user, workspace, storage, name):
        environment = _versioned(user, workspace, name, storage)
        record_authoring(
            environment,
            _suite_archive([_scenario(one) for one in NAMES]),
            origin="chat",
        )
        environment.refresh_from_db()
        return environment

    def _drop(self, environment):
        kept = [_scenario(NAMES[0]), _scenario(NAMES[2])]
        record_authoring(environment, _with_drops(kept, [CANCEL]), origin="nudge")
        environment.refresh_from_db()

    def _restore(self, environment):
        record_authoring(
            environment,
            _suite_archive([_scenario(one) for one in NAMES]),
            origin="nudge",
        )
        environment.refresh_from_db()

    def test_the_drop_keeps_the_stored_reason_on_the_suite_version(
        self, user, workspace, storage
    ):
        environment = self._three(user, workspace, storage, "drop-reason")

        self._drop(environment)

        suite = environment.suite_version
        assert suite.documents["dropped"][_key(CANCEL)]["reason"] == REASON
        assert suite.diff["removed"][0]["reason"] == REASON

    def test_compare_reads_the_stored_reason_for_a_scenario_in_one_run_only(
        self, user, workspace, storage
    ):
        environment = self._three(user, workspace, storage, "drop-compare")
        before = _run(environment, [_key(one) for one in NAMES], key="before")
        _trials(before, [(_key(one), True) for one in NAMES])
        self._drop(environment)
        after = _run(environment, [_key(NAMES[0]), _key(NAMES[2])], key="after")
        _trials(after, [(_key(NAMES[0]), True), (_key(NAMES[2]), True)])
        _done(before, ago=timedelta(minutes=5))

        gone = _by_key(_compare(_done(after)))[_key(CANCEL)]

        assert gone["band"] == "not_applicable"
        assert gone["reason"] == REASON
        assert gone["removed_in"]["id"] == str(environment.suite_version_id)

    def test_a_restore_keeps_the_scenario_row_so_its_history_stays_linked(
        self, user, workspace, storage
    ):
        environment = self._three(user, workspace, storage, "drop-history")
        held = environment.suite_version_id
        row = HostedHarnessScenario.all_objects.get(
            job=environment, scenario_key=_key(CANCEL)
        )
        before = _run(environment, [_key(one) for one in NAMES], key="before")
        _trials(before, [(_key(one), True) for one in NAMES])
        self._drop(environment)
        assert HostedHarnessScenario.all_objects.get(id=row.id).deleted is True

        self._restore(environment)

        restored = HostedHarnessScenario.all_objects.get(id=row.id)
        assert restored.deleted is False
        assert (restored.scenario_key, restored.number) == (
            row.scenario_key,
            row.number,
        )
        # Every scenario is back as it was: the suite it left is the default again.
        assert environment.suite_version_id == held
        after = _run(environment, [_key(one) for one in NAMES], key="after")
        _trials(after, [(_key(one), True) for one in NAMES])
        _done(before, ago=timedelta(minutes=5))
        paired = _by_key(_compare(_done(after)))[_key(CANCEL)]
        assert paired["band"] == "paired"
        assert paired["against"]["trials"] == 1

    def test_a_person_adding_a_scenario_with_a_dropped_key_is_still_refused(
        self, user, workspace, storage
    ):
        environment = self._three(user, workspace, storage, "drop-person")
        self._drop(environment)

        with pytest.raises(HostedHarnessError) as refused:
            record_authoring(
                environment,
                _suite_archive([_scenario(one) for one in NAMES]),
                origin="chat",
            )

        assert refused.value.code == "scenario_key_retired"

    def test_a_suite_saved_before_drops_existed_keeps_its_version(
        self, user, workspace, storage
    ):
        """A version hashed before dropped.json existed is the one a save without drops lands on."""
        from simulate.services.harness_versioning import hashing

        environment = _versioned(user, workspace, "drop-old-hash", storage)
        two = [_scenario(NAMES[0]), _scenario(NAMES[2])]
        record_authoring(environment, _suite_archive(two), origin="chat")
        environment.refresh_from_db()
        old = environment.suite_version
        suite = archives.split_authoring(
            archives.read_archive(_suite_archive(two))
        ).suite
        assert old.hash == hashing.tree_digest(
            hashing.scenario_files(suite), exclude=hashing._suite_unhashed
        )
        count = HarnessSuiteVersion._base_manager.filter(job=environment).count()

        record_authoring(environment, _suite_archive(two), origin="chat")
        record_authoring(environment, _with_drops(two, []), origin="nudge")
        environment.refresh_from_db()

        assert environment.suite_version_id == old.id
        assert (
            HarnessSuiteVersion._base_manager.filter(job=environment).count() == count
        )

    def test_a_version_with_the_same_scenarios_but_other_drops_is_not_reused(
        self, user, workspace, storage
    ):
        """v1 {A, C}; v2 adds B; a rebuild drops B for a removed capability: {A, C} again, but with
        B's record. Reusing v1 would lose the record a later rebuild restores B from."""
        environment = _versioned(user, workspace, "drop-twin", storage)
        two = [_scenario(NAMES[0]), _scenario(NAMES[2])]
        record_authoring(environment, _suite_archive(two), origin="chat")
        environment.refresh_from_db()
        first = environment.suite_version
        # B is a case new to this environment, so a person may add it.
        added = "cancel_late_booking"
        record_authoring(
            environment, _suite_archive([*two, _scenario(added)]), origin="chat"
        )

        record_authoring(environment, _with_drops(two, [added]), origin="nudge")
        environment.refresh_from_db()

        held = environment.suite_version
        assert held.id != first.id
        assert held.documents["dropped"][_key(added)]["reason"] == REASON
        assert HarnessSuiteVersion._base_manager.get(id=first.id).documents.get(
            "dropped"
        ) in (None, {})
