"""Every reader takes the contract of the version it acts on, as that version's build sealed it.

The contracts are a real returns build's, reduced to the fields that differ: the builder amended the authored
contract while it built, serving the four tools from the agent's tools service instead of importing them. The
authored one reads all four as reaching production; the sealed one, the one the bundle certified and runs, none.
"""

import json
from types import SimpleNamespace

import pytest

from simulate.models import HarnessEnvironmentVersion
from simulate.selectors.environment_documents import recorded_contract
from simulate.services.harness_environment import environment_detail
from simulate.services.harness_tool_reach import (
    blocked_tools,
    production_only,
    tool_status,
)
from simulate.services.harness_versioning import archives, drafts
from simulate.services.harness_versioning.versions import record_authoring
from simulate.tests.test_harness_versioning_record import (
    _authoring,
    _environment,
    _resealed_manifest,
    _save_drafts,
)
from simulate.tests.test_hosted_harness_conversation import _Storage

pytestmark = pytest.mark.django_db

TOOLS = ("get_order", "list_refunds", "update_order_status", "issue_refund")
ROUTES = {
    "get_order": ("_lookup_tools.get_order", "orders/get", "read"),
    "list_refunds": ("_lookup_tools.list_refunds", "refunds/list", "read"),
    "update_order_status": (
        "_lookup_tools.update_order_status",
        "orders/update_status",
        "write",
    ),
    "issue_refund": ("_refund_tools.issue_refund", "refunds/issue", "write"),
}
LEDGER = {
    "name": "returns-ledger",
    "kind": "service",
    "what": "HTTP API providing orders lookup, status update, and refund management.",
    "used_by": list(TOOLS),
    "engine": "http",
    "version": "",
    "reached": {
        "dsn_env": "RETURNS_API_URL",
        "config_key": "",
        "host": "",
        "port": None,
        "database": "",
        "user": "",
        "password_from": "",
        "loader_module": "",
        "loader_function": "",
    },
}


def _entrypoints(mode: str, service: str) -> list[dict]:
    return [
        {
            "tool": tool,
            "mode": mode,
            "module": "agent",
            "callable": callable_,
            "service": service,
            "endpoint": endpoint,
            "method": "POST",
            "effect": effect,
        }
        for tool, (callable_, endpoint, effect) in ROUTES.items()
    ]


AUTHORED = {
    "agent": "returns_coordinator",
    "modality": "chat",
    "tools": [{"name": tool} for tool in TOOLS],
    "dependencies": [LEDGER],
    "tool_entrypoints": _entrypoints("import", ""),
}
SEALED = {**AUTHORED, "tool_entrypoints": _entrypoints("service", "tools-api")}


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True


@pytest.fixture
def storage(monkeypatch):
    from tfc.utils import storage_client

    fake = _Storage()
    monkeypatch.setattr(storage_client, "_client", fake)
    return fake


def _built(contract=AUTHORED, sealed=SEALED, **extra) -> bytes:
    """A build's archive: the authored contract at the root, and the bundle it sealed with ``sealed``."""
    files = {"environment-bundle/contract.json": json.dumps(sealed)} if sealed else {}
    return _authoring(contract=contract, **files, **extra)


def _blocked(environment, version=None, agent_version_id=None, suite=None) -> list[str]:
    """What the run gate holds back for a run of ``version`` by the agent and suite named, else the defaults; no
    pins at all without a version."""
    pins = (
        SimpleNamespace(
            environment_version=version,
            agent_version_id=agent_version_id or environment.agent_version_id,
            suite_version=suite or environment.suite_version,
        )
        if version is not None
        else None
    )
    return [entry["tool"] for entry in blocked_tools(environment, pins)]


def _versions(environment) -> list[HarnessEnvironmentVersion]:
    return list(
        HarnessEnvironmentVersion.no_workspace_objects.filter(
            job=environment, status="active"
        ).order_by("version_number")
    )


def test_the_real_authored_contract_reads_every_tool_as_production_and_the_sealed_one_none():
    assert production_only(AUTHORED) == sorted(TOOLS)
    assert production_only(SEALED) == []


def test_a_build_s_version_runs_and_shows_the_contract_its_bundle_sealed(
    user, workspace, storage
):
    environment = _environment(user, workspace, "rc-built")
    recorded = record_authoring(environment, _built(), origin="authoring")
    environment.refresh_from_db()

    # The authored contract stays what diffs compare; the sealed one is recorded beside it.
    assert recorded.environment.documents["contract"] == AUTHORED
    assert recorded_contract(recorded.environment.documents) == SEALED
    # The run gate, pinned and unpinned, and the Contract tab's Tools card.
    assert _blocked(environment, recorded.environment) == []
    assert _blocked(environment) == []
    contract = environment_detail(environment)["contract"]
    assert contract["production_only"] == []
    assert {one["status"] for one in contract["tool_status"]} != {"unsupported"}


def test_an_edit_carrying_an_older_bundle_keeps_its_own_contract(
    user, workspace, storage
):
    environment = _environment(user, workspace, "rc-edit")

    recorded = record_authoring(environment, _built(), origin="chat")

    assert recorded_contract(recorded.environment.documents) == AUTHORED


def test_an_older_version_and_a_rerun_on_it_keep_its_own_contract(
    user, workspace, storage
):
    environment = _environment(user, workspace, "rc-older")
    # Built before the builder served the tools: its bundle sealed the authored contract.
    older = record_authoring(
        environment, _built(sealed=AUTHORED), origin="authoring"
    ).environment
    environment.refresh_from_db()
    newer = record_authoring(
        environment,
        _built(**{"schema.sql": "create table orders(id int, state text);"}),
        origin="authoring",
    ).environment
    environment.refresh_from_db()

    assert [one.id for one in _versions(environment)] == [older.id, newer.id]
    assert _blocked(environment, older) == sorted(TOOLS)
    assert _blocked(environment, newer) == []
    assert recorded_contract(older.documents) == AUTHORED
    # The newer version's diff compares authored contracts: the build's amendment is no change of the author's.
    assert "tool_entrypoints" not in newer.diff["contract"]["fields"]


def test_a_draft_the_chat_resealed_with_an_amended_contract_shows_it_as_the_draft_s(
    user, workspace, storage
):
    environment = _environment(user, workspace, "rc-draft")
    record_authoring(environment, _built(sealed=AUTHORED), origin="authoring")
    environment.refresh_from_db()
    drafts.record_draft(environment, _resealed(), origin="chat")

    draft = drafts.open_draft(environment, "environment")
    assert recorded_contract(draft.documents) == SEALED
    assert environment_detail(environment)["contract"]["production_only"] == []
    # The default version, the one runs use until the draft is saved, keeps its own.
    assert _blocked(environment) == sorted(TOOLS)


def test_a_draft_whose_bundle_was_not_sealed_from_it_keeps_its_authored_contract(
    user, workspace, storage
):
    environment = _environment(user, workspace, "rc-draft-stale")
    record_authoring(environment, _built(sealed=AUTHORED), origin="authoring")
    environment.refresh_from_db()
    files = archives.read_archive(
        _authoring(contract=AUTHORED, sealed=False, **{"handlers/refund.py": "x = 1\n"})
    )
    files["environment-bundle/contract.json"] = json.dumps(SEALED).encode()
    files["environment-bundle/manifest.json"] = json.dumps({"files": []}).encode()

    drafts.record_draft(environment, archives.pack_archive(files), origin="chat")

    assert (
        recorded_contract(drafts.open_draft(environment, "environment").documents)
        == AUTHORED
    )


def _resealed(contract=AUTHORED, sealed=SEALED) -> bytes:
    """A chat edit to a handler, with the bundle the chat resealed from it."""
    files = archives.read_archive(
        _authoring(contract=contract, sealed=False, **{"handlers/refund.py": "x = 1\n"})
    )
    files["environment-bundle/contract.json"] = json.dumps(sealed).encode()
    files["environment-bundle/manifest.json"] = _resealed_manifest(files).encode()
    return archives.pack_archive(files)


def test_a_draft_that_leaves_the_contract_alone_runs_with_the_build_s_until_the_contract_is_edited(
    user, workspace, storage
):
    environment = _environment(user, workspace, "rc-inherit")
    record_authoring(environment, _built(), origin="authoring")
    environment.refresh_from_db()

    drafts.record_draft(
        environment,
        _authoring(
            contract=AUTHORED, sealed=False, **{"handlers/refund.py": "x = 1\n"}
        ),
        origin="chat",
    )
    assert (
        recorded_contract(drafts.open_draft(environment, "environment").documents)
        == SEALED
    )

    edited = {**AUTHORED, "one_liner": "returns desk"}
    drafts.record_draft(
        environment,
        _authoring(contract=edited, sealed=False, **{"handlers/refund.py": "x = 1\n"}),
        origin="chat",
    )
    assert (
        recorded_contract(drafts.open_draft(environment, "environment").documents)
        == edited
    )


def test_a_new_version_saved_from_a_chat_reseal_runs_with_the_contract_it_sealed(
    user, workspace, storage
):
    from simulate.tests.test_harness_versioning_drafts import (
        NAMES,
        WORLD_EDIT,
        _publish,
    )
    from simulate.tests.test_harness_versioning_rollback import (
        _built as _built_environment,
    )

    environment = _built_environment(user, workspace, "rc-save")
    first = environment.environment_version
    sealed = {
        "environment-bundle/contract.json": json.dumps(SEALED),
        "world.sqlite": WORLD_EDIT,
    }
    _publish(environment, storage, NAMES[:2], sequence=10, contract=AUTHORED, **sealed)
    assert (
        recorded_contract(drafts.open_draft(environment, "environment").documents)
        == SEALED
    )

    _save_drafts(environment)

    saved = environment.environment_version
    assert saved.id != first.id and recorded_contract(saved.documents) == SEALED
    assert _blocked(environment) == [] and _blocked(environment, saved) == []
    assert recorded_contract(first.documents) != SEALED


class TestVersionRecordedBeforeItsSealedContract:
    """A version recorded before its documents carried the sealed contract still holds the authored one; its ready
    bundle, the one its runs replay, decides."""

    def _version(self, user, workspace, key, monkeypatch):
        from simulate.tests.test_harness_versioning_backfill import _RealisticStorage
        from simulate.tests.test_harness_versioning_picks import (
            _environment as _versioned,
        )
        from tfc.utils import storage_client

        # Missing objects raise as the real store does.
        storage = _RealisticStorage()
        monkeypatch.setattr(storage_client, "_client", storage)
        environment = _versioned(user, workspace, key, storage)
        version = environment.environment_version
        HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
            documents={**(version.documents or {}), "contract": AUTHORED}
        )
        version.refresh_from_db()
        return environment, version, storage

    def _bundle(
        self, environment, version, storage, contract=SEALED, agent_version_id=None
    ):
        from simulate.tests.test_harness_versioning_picks import _ready

        row = _ready(
            environment,
            agent_version_id or environment.agent_version_id,
            version,
            environment.suite_version,
        )
        if contract is not None:
            storage.objects[row.object_key] = archives.pack_archive(
                {
                    "environment-bundle/manifest.json": b"{}",
                    "environment-bundle/contract.json": json.dumps(contract).encode(),
                }
            )
        return row

    def test_its_ready_bundle_s_sealed_contract_is_read_for_the_gate_and_the_tools_card(
        self, user, workspace, monkeypatch
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-legacy", monkeypatch
        )
        self._bundle(environment, version, storage)

        assert version.documents["contract"] == AUTHORED
        assert _blocked(environment, version) == []
        assert _blocked(environment) == []
        assert environment_detail(environment)["contract"]["production_only"] == []

    def test_a_version_with_no_ready_bundle_keeps_its_recorded_contract(
        self, user, workspace, monkeypatch
    ):
        environment, version, _ = self._version(
            user, workspace, "rc-no-bundle", monkeypatch
        )

        assert _blocked(environment, version) == sorted(TOOLS)

    def test_an_unreadable_bundle_leaves_the_recorded_contract(
        self, user, workspace, monkeypatch
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-unreadable", monkeypatch
        )
        self._bundle(environment, version, storage, contract=None)

        assert _blocked(environment, version) == sorted(TOOLS)

    def test_a_truncated_bundle_leaves_the_recorded_contract_and_is_not_read_again_at_once(
        self, user, workspace, monkeypatch
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-truncated", monkeypatch
        )
        row = self._bundle(environment, version, storage)
        storage.objects[row.object_key] = storage.objects[row.object_key][:40]
        reads = []
        read = storage.get_object
        monkeypatch.setattr(
            storage,
            "get_object",
            lambda bucket, key: reads.append(key) or read(bucket, key),
        )

        assert _blocked(environment, version) == sorted(TOOLS)
        assert environment_detail(environment)["contract"]["production_only"] == sorted(
            TOOLS
        )
        assert reads == [row.object_key]

    def test_a_run_reads_the_bundle_of_its_own_agent_never_another_agent_s(
        self, user, workspace, monkeypatch
    ):
        from simulate.tests.test_harness_versioning_picks import _second_agent

        environment, version, storage = self._version(
            user, workspace, "rc-pair", monkeypatch
        )
        self._bundle(environment, version, storage)
        other = _second_agent(environment)
        # Agent B's bundle, sealed later, still imports the tools.
        self._bundle(
            environment, version, storage, contract=AUTHORED, agent_version_id=other.id
        )

        assert _blocked(environment, version) == []
        assert _blocked(environment, version, agent_version_id=other.id) == sorted(
            TOOLS
        )

    def test_the_run_plan_and_the_environment_detail_read_it_through_their_views(
        self, user, workspace, monkeypatch, auth_client
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-views", monkeypatch
        )
        base = f"/simulate/api/harness-environments/{environment.id}"
        plan = auth_client.get(f"{base}/run-plan/")
        detail = auth_client.get(f"{base}/")
        assert (plan.status_code, detail.status_code) == (200, 200)
        assert plan.json()["blocked_tools"] and (
            detail.json()["contract"]["production_only"] == sorted(TOOLS)
        )

        self._bundle(environment, version, storage)

        plan = auth_client.get(f"{base}/run-plan/").json()
        contract = auth_client.get(f"{base}/").json()["contract"]
        assert (plan["blocked_tools"], plan["runnable"]) == ([], True)
        assert contract["production_only"] == [] and contract["from_draft"] is False

    def test_fault_targets_and_the_list_row_read_the_contract_the_version_runs_with(
        self, user, workspace, monkeypatch
    ):
        from simulate.services.harness_environment import (
            annotate_for_list,
            edit_fault_side,
            environment_row,
        )

        environment, version, storage = self._version(
            user, workspace, "rc-faults", monkeypatch
        )
        HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
            documents={**version.documents, "run_contract": SEALED}
        )
        self._bundle(environment, version, storage)

        targets, _ = edit_fault_side(environment)
        assert {one["tool"]: one["reachable"] for one in targets} == dict.fromkeys(
            TOOLS, True
        )
        listed = annotate_for_list(
            type(environment).no_workspace_objects.filter(id=environment.id)
        ).get()
        assert listed.version_contract == SEALED
        assert environment_row(listed)["tools_count"] == len(TOOLS)

    def test_the_scenarios_page_and_a_draft_write_judge_faults_on_the_sealed_contract(
        self, user, workspace, monkeypatch, auth_client
    ):
        from simulate.models import (
            HostedHarnessJob,
            HostedHarnessScenario,
            HostedHarnessStageOutput,
        )
        from simulate.services.harness_versioning.drafts import _targets_before

        environment, version, storage = self._version(
            user, workspace, "rc-scenarios", monkeypatch
        )
        self._bundle(environment, version, storage)
        # The authoring snapshot is the first contract, whose in-process tools the door cannot reach.
        unreached = {
            **AUTHORED,
            "tool_entrypoints": [
                {**one, "door_unreached": "a method of a helper object"}
                for one in AUTHORED["tool_entrypoints"]
            ],
        }
        HostedHarnessJob._base_manager.filter(id=environment.id).update(
            stage_outputs=[{"kind": "contract", "data": unreached}]
        )
        HostedHarnessStageOutput._base_manager.filter(
            job=environment, kind="contract"
        ).update(data=unreached)
        scenario = HostedHarnessScenario.no_workspace_objects.filter(
            job=environment
        ).first()
        HostedHarnessScenario._base_manager.filter(id=scenario.id).update(
            faults=[{"action": "get_order", "mode": "timeout", "on_call": 1}]
        )
        environment.refresh_from_db()

        rows = auth_client.get(
            f"/simulate/api/harness-jobs/{environment.id}/scenarios/",
            {"group_by": ""},
        ).json()["results"]
        faulted = [one for one in rows if one.get("faults")]
        assert len(faulted) == 1 and faulted[0]["door_problem"] is None
        assert {
            one["tool"]: one["reachable"] for one in _targets_before(environment)
        } == (dict.fromkeys(TOOLS, True))

    def test_a_draft_that_recorded_no_contract_reads_the_default_version_s(
        self, user, workspace, monkeypatch
    ):
        from simulate.selectors.environment_documents import version_contract

        environment, version, storage = self._version(
            user, workspace, "rc-draft-bare", monkeypatch
        )
        self._bundle(environment, version, storage)
        draft = HarnessEnvironmentVersion._base_manager.create(
            job=environment,
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            status="draft",
            parent=version,
            archive_size=0,
            documents={},
        )

        assert version_contract(environment, draft) == SEALED

    def _saved_edit(self, environment, version, contract):
        """The version a saved builder edit makes, before any build seals it: the edited authored contract."""
        edited = HarnessEnvironmentVersion._base_manager.create(
            job=environment,
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            status="active",
            version_number=version.version_number + 1,
            hash="sha256:" + "e" * 64,
            parent=version,
            archive_size=0,
            documents={"contract": contract},
        )
        type(environment)._base_manager.filter(id=environment.id).update(
            environment_version=edited
        )
        environment.refresh_from_db()
        return edited

    def test_a_builder_edit_to_a_rule_and_a_description_still_reaches_the_tools_as_built(
        self, user, workspace, monkeypatch, auth_client
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-edit-rule", monkeypatch
        )
        self._bundle(environment, version, storage)
        edited = {
            **AUTHORED,
            "hard_constraints": ["Never refund an order that has not shipped."],
            "tools": [
                {
                    "name": tool,
                    "description": "Look up an order." if tool == "get_order" else "",
                }
                for tool in TOOLS
            ],
        }
        drafts.record_draft(
            environment,
            _authoring(contract=edited, sealed=False),
            origin="chat",
            pieces={"environment"},
        )
        base = f"/simulate/api/harness-environments/{environment.id}"

        contract = auth_client.get(f"{base}/").json()["contract"]
        assert contract["from_draft"] is True and contract["production_only"] == []

        saved = self._saved_edit(environment, version, edited)
        plan = auth_client.get(f"{base}/run-plan/").json()
        contract = auth_client.get(f"{base}/").json()["contract"]
        assert (plan["blocked_tools"], plan["runnable"]) == ([], True)
        assert contract["production_only"] == []
        assert _blocked(environment, saved) == []
        assert {one["status"] for one in contract["tool_status"]} != {"unsupported"}

    def _through_the_views(self, auth_client, environment):
        """What the run plan holds back, and each tool's status on the Contract tab, as the views serve them."""
        base = f"/simulate/api/harness-environments/{environment.id}"
        plan = auth_client.get(f"{base}/run-plan/").json()
        contract = auth_client.get(f"{base}/").json()["contract"]
        statuses = {one["tool"]: one["status"] for one in contract["tool_status"]}
        return [one["tool"] for one in plan["blocked_tools"]], statuses

    def _edit(self, user, workspace, monkeypatch, key, entries):
        environment, version, storage = self._version(user, workspace, key, monkeypatch)
        self._bundle(environment, version, storage)
        edited = {
            **AUTHORED,
            "tools": [{"name": one["tool"]} for one in entries],
            "tool_entrypoints": entries,
        }
        self._saved_edit(environment, version, edited)
        return environment

    def test_an_edit_that_adds_a_tool_keeps_the_others_as_built(
        self, user, workspace, monkeypatch, auth_client
    ):
        added = {
            "tool": "track_parcel",
            "mode": "import",
            "module": "agent",
            "callable": "track_parcel",
            "service": "",
            "endpoint": "",
            "method": "",
            "effect": "read",
        }
        environment = self._edit(
            user,
            workspace,
            monkeypatch,
            "rc-add-tool",
            [*AUTHORED["tool_entrypoints"], added],
        )

        blocked, statuses = self._through_the_views(auth_client, environment)

        sealed = {one["tool"]: one["status"] for one in tool_status(SEALED)}
        own = {
            one["tool"]: one["status"]
            for one in tool_status(
                {
                    **AUTHORED,
                    "tools": [{"name": "track_parcel"}],
                    "tool_entrypoints": [added],
                }
            )
        }
        assert blocked == []
        assert statuses == {**sealed, "track_parcel": own["track_parcel"]}

    def test_an_edit_that_only_reorders_the_tools_keeps_every_one_as_built(
        self, user, workspace, monkeypatch, auth_client
    ):
        environment = self._edit(
            user,
            workspace,
            monkeypatch,
            "rc-reorder",
            list(reversed(AUTHORED["tool_entrypoints"])),
        )

        blocked, statuses = self._through_the_views(auth_client, environment)

        assert blocked == []
        assert statuses == {one["tool"]: one["status"] for one in tool_status(SEALED)}

    def test_an_edit_that_changes_how_one_tool_is_reached_changes_only_that_one(
        self, user, workspace, monkeypatch, auth_client
    ):
        from simulate.selectors.environment_documents import version_contract

        changed = {
            **next(
                one
                for one in AUTHORED["tool_entrypoints"]
                if one["tool"] == "get_order"
            ),
            "endpoint": "orders/lookup",
        }
        environment = self._edit(
            user,
            workspace,
            monkeypatch,
            "rc-edit-entry",
            [
                changed if one["tool"] == "get_order" else one
                for one in AUTHORED["tool_entrypoints"]
            ],
        )

        entries = {
            one["tool"]: one
            for one in version_contract(environment)["tool_entrypoints"]
        }
        sealed = {one["tool"]: one for one in SEALED["tool_entrypoints"]}
        assert entries == {**sealed, "get_order": changed}
        blocked, statuses = self._through_the_views(auth_client, environment)
        # get_order now reaches the returns ledger itself, so the ledger is an API this test cannot stand in for: every
        # tool that uses it reads Unsupported, as the production-only rule says of a shared dependency.
        assert blocked == sorted(TOOLS)
        assert set(statuses.values()) == {"unsupported"}

    def test_a_build_that_amends_a_tool_keeps_its_own_entry_for_that_tool(
        self, user, workspace, monkeypatch
    ):
        from simulate.selectors.environment_documents import version_contract

        environment, version, storage = self._version(
            user, workspace, "rc-own-build", monkeypatch
        )
        self._bundle(environment, version, storage)
        edited = {**AUTHORED, "hard_constraints": ["Never refund twice."]}
        saved = self._saved_edit(environment, version, edited)
        # Its own build served get_order from another service; it left the other tools as authored.
        billing = {
            **AUTHORED["tool_entrypoints"][0],
            "mode": "service",
            "service": "billing-api",
        }
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            documents={
                "contract": edited,
                "run_contract": {
                    **edited,
                    "tool_entrypoints": [billing, *AUTHORED["tool_entrypoints"][1:]],
                },
            }
        )
        saved.refresh_from_db()

        entries = {
            one["tool"]: one
            for one in version_contract(environment, saved)["tool_entrypoints"]
        }
        sealed = {one["tool"]: one for one in SEALED["tool_entrypoints"]}
        assert entries == {**sealed, "get_order": billing}

    def test_a_build_whose_entry_differs_from_this_version_s_authored_one_keeps_it(
        self, user, workspace, monkeypatch
    ):
        """An edit moved get_order and its build sealed the old entry: that is the build's own amendment, kept,
        never replaced by how the base was built."""
        from simulate.selectors.environment_documents import version_contract

        environment, version, storage = self._version(
            user, workspace, "rc-build-reverts", monkeypatch
        )
        self._bundle(environment, version, storage)
        old = AUTHORED["tool_entrypoints"][0]
        moved = {
            **AUTHORED,
            "tool_entrypoints": [
                {**old, "endpoint": "orders/lookup"},
                *AUTHORED["tool_entrypoints"][1:],
            ],
        }
        saved = self._saved_edit(environment, version, moved)
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            documents={
                "contract": moved,
                "run_contract": {
                    **moved,
                    "tool_entrypoints": AUTHORED["tool_entrypoints"],
                },
            }
        )
        saved.refresh_from_db()

        entries = {
            one["tool"]: one
            for one in version_contract(environment, saved)["tool_entrypoints"]
        }
        assert entries["get_order"] == old

    def test_a_saved_version_s_ready_bundle_sealed_from_the_author_s_entries_reaches_them_as_built(
        self, user, workspace, monkeypatch, auth_client
    ):
        """After a builder's save, the next run assembles a ready bundle from the saved tree: it seals the
        author's import entries again, and every reader still reaches the tools as the first build did."""
        environment, version, storage = self._version(
            user, workspace, "rc-saved-bundle", monkeypatch
        )
        self._bundle(environment, version, storage)
        edited = {**AUTHORED, "hard_constraints": ["Never refund an unshipped order."]}
        saved = self._saved_edit(environment, version, edited)
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            origin="chat"
        )
        saved.refresh_from_db()
        self._bundle(environment, saved, storage, contract=edited)

        blocked, statuses = self._through_the_views(auth_client, environment)

        assert blocked == []
        assert statuses == {one["tool"]: one["status"] for one in tool_status(SEALED)}

    def _builder_saved(self, environment, version, run_entries, written=None):
        """A builder's save over ``version``: authored entries as the author writes them (``written``, else with
        empty keys and all), and the entries its reseal recorded."""
        full = written or [
            {**one, "factory": "", "first_arg": "", "door_unreached": "", "notes": ""}
            for one in AUTHORED["tool_entrypoints"]
        ]
        authored = {**AUTHORED, "tool_entrypoints": full}
        HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
            documents={**version.documents, "contract": authored}
        )
        edited = {**authored, "hard_constraints": ["Never refund an unshipped order."]}
        saved = self._saved_edit(environment, version, edited)
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            origin="chat",
            documents={
                "contract": edited,
                "run_contract": {**edited, "tool_entrypoints": run_entries},
            },
        )
        return saved

    def test_a_builder_save_whose_seal_drops_empty_keys_still_reaches_the_tools_as_built(
        self, user, workspace, monkeypatch, auth_client
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-dropped-keys", monkeypatch
        )
        self._bundle(environment, version, storage)
        # The reseal keeps the author's import entries but drops the keys left empty.
        self._builder_saved(environment, version, AUTHORED["tool_entrypoints"])

        blocked, statuses = self._through_the_views(auth_client, environment)

        assert blocked == []
        assert statuses == {one["tool"]: one["status"] for one in tool_status(SEALED)}

    def test_a_builder_save_that_really_changes_how_a_tool_is_reached_keeps_it(
        self, user, workspace, monkeypatch, auth_client
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-real-change", monkeypatch
        )
        self._bundle(environment, version, storage)
        moved = {**AUTHORED["tool_entrypoints"][0], "callable": "_orders.get_order"}
        self._builder_saved(
            environment, version, [moved, *AUTHORED["tool_entrypoints"][1:]]
        )

        blocked, _ = self._through_the_views(auth_client, environment)

        # get_order now reaches the shared ledger itself, so every tool on it is held, as on dev.
        assert blocked == sorted(TOOLS)

    def test_a_seal_that_writes_out_a_default_the_author_left_out_still_reaches_the_tools_as_built(
        self, user, workspace, monkeypatch, auth_client
    ):
        environment, version, storage = self._version(
            user, workspace, "rc-defaults", monkeypatch
        )
        self._bundle(environment, version, storage)
        self._builder_saved(
            environment,
            version,
            # The reseal wrote the default method out, in lower case.
            [{**one, "method": "post"} for one in AUTHORED["tool_entrypoints"]],
            # The author left the method out.
            written=[
                {key: value for key, value in one.items() if key != "method"}
                for one in AUTHORED["tool_entrypoints"]
            ],
        )

        blocked, statuses = self._through_the_views(auth_client, environment)

        assert blocked == []
        assert statuses == {one["tool"]: one["status"] for one in tool_status(SEALED)}

    def test_an_edit_to_a_tool_s_effect_keeps_its_effect_and_takes_the_base_s_reach(
        self, user, workspace, monkeypatch, auth_client
    ):
        from simulate.selectors.environment_documents import version_contract

        environment, version, storage = self._version(
            user, workspace, "rc-effect", monkeypatch
        )
        self._bundle(environment, version, storage)
        edited = {
            **AUTHORED,
            "tool_entrypoints": [
                {**one, "effect": "write"} if one["tool"] == "get_order" else one
                for one in AUTHORED["tool_entrypoints"]
            ],
        }
        self._saved_edit(environment, version, edited)

        reached = {
            one["tool"]: one
            for one in version_contract(environment)["tool_entrypoints"]
        }
        sealed = {one["tool"]: one for one in SEALED["tool_entrypoints"]}
        assert reached["get_order"] == {**sealed["get_order"], "effect": "write"}
        blocked, _ = self._through_the_views(auth_client, environment)
        assert blocked == []


def test_a_builder_save_that_seals_the_authored_entries_again_runs_as_the_first_build(
    user, workspace, storage, auth_client
):
    """The builder's save reseals the sandbox's tree: its sealed contract carries the author's import entries, not
    the service ones the first build amended them to."""
    from simulate.tests.test_harness_versioning_drafts import (
        NAMES,
        WORLD_EDIT,
        _publish,
    )
    from simulate.tests.test_harness_versioning_record import (
        _environment as _record_environment,
    )
    from simulate.tests.test_harness_versioning_rollback import _rows

    environment = _record_environment(user, workspace, "rc-builder-save")
    first = record_authoring(environment, _built(), origin="authoring").environment
    environment.refresh_from_db()
    _rows(environment)
    edited = {**AUTHORED, "hard_constraints": ["Never refund an unshipped order."]}
    resealed = {
        "environment-bundle/contract.json": json.dumps(edited),
        "world.sqlite": WORLD_EDIT,
    }
    _publish(environment, storage, NAMES[:2], sequence=10, contract=edited, **resealed)

    _save_drafts(environment)

    saved = environment.environment_version
    assert saved.id != first.id
    reached = {
        one["tool"]: one
        for one in recorded_contract(saved.documents)["tool_entrypoints"]
    }
    assert reached == {one["tool"]: one for one in SEALED["tool_entrypoints"]}
    base = f"/simulate/api/harness-environments/{environment.id}"

    def views():
        plan = auth_client.get(f"{base}/run-plan/").json()
        contract = auth_client.get(f"{base}/").json()["contract"]
        assert (plan["blocked_tools"], plan["runnable"]) == ([], True)
        assert contract["production_only"] == []

    views()


def test_a_full_build_that_keeps_a_tool_imported_is_read_as_that_build_sealed_it(
    user, workspace, storage
):
    """A rebuild that seals the author's import entries (its tools service gone, say) is a build's own decision,
    never replaced by how an earlier build reached the tools."""
    from simulate.tests.test_harness_versioning_record import (
        _environment as _record_environment,
    )

    environment = _record_environment(user, workspace, "rc-rebuild-import")
    record_authoring(environment, _built(), origin="authoring")
    environment.refresh_from_db()
    edited = {**AUTHORED, "hard_constraints": ["Never refund an unshipped order."]}
    rebuilt = record_authoring(
        environment, _built(contract=edited, sealed=edited), origin="authoring"
    ).environment
    environment.refresh_from_db()

    assert (
        recorded_contract(rebuilt.documents)["tool_entrypoints"]
        == (AUTHORED["tool_entrypoints"])
    )
    assert _blocked(environment, rebuilt) == sorted(TOOLS)
