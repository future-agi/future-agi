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


def _recorded(contract: dict) -> dict:
    """A run contract as a save records it now: stamped with the revision of the reading that wrote it."""
    from simulate.selectors.environment_documents import RUN_CONTRACT_VERSION

    return {"run_contract": contract, "run_contract_version": RUN_CONTRACT_VERSION}


def _built(contract=AUTHORED, sealed=SEALED, **extra) -> bytes:
    """A build's archive: the authored contract at the root, and the bundle it sealed with ``sealed``."""
    files = {"environment-bundle/contract.json": json.dumps(sealed)} if sealed else {}
    return _authoring(contract=contract, **files, **extra)


def _blocked(environment, version=None, agent_version_id=None, suite=None) -> list[str]:
    """What the run gate holds back for a run of ``version`` by the agent and suite named, else the defaults; no
    pins at all without a version."""
    pins = None
    if version is not None:
        from simulate.models import HarnessAgentVersionRuntime

        pins = SimpleNamespace(
            environment_version=version,
            suite_version=suite or environment.suite_version,
            # The bundle a run replays is the one its agent's side row, versions and suite key.
            runtime=HarnessAgentVersionRuntime.no_workspace_objects.filter(
                agent_version_id=agent_version_id or environment.agent_version_id
            ).first(),
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


def test_a_draft_runs_with_the_build_s_reach_whether_or_not_it_edits_the_contract(
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
    # An edit to the contract that leaves how the tools are reached alone is recorded with the build's reach.
    assert recorded_contract(
        drafts.open_draft(environment, "environment").documents
    ) == {**edited, "tool_entrypoints": SEALED["tool_entrypoints"]}


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
        self,
        environment,
        version,
        storage,
        contract=SEALED,
        agent_version_id=None,
        resealed=False,
        built=False,
        seeded=False,
    ):
        """``seeded``: the row a full build cached when it recorded the version, else one a run filled."""
        from simulate.tests.test_harness_versioning_picks import _ready

        row = _ready(
            environment,
            agent_version_id or environment.agent_version_id,
            version,
            environment.suite_version,
        )
        if contract is not None:
            body = archives.pack_archive(
                {
                    # A chat's reseal stamps the tree it sealed from, a build stamps that it is one; an older
                    # bundle says neither.
                    "environment-bundle/manifest.json": json.dumps(
                        {"metadata": {"sealed_from": "sha256:tree"}}
                        if resealed
                        else {"metadata": {"sealed_from": "build"}}
                        if built
                        else {}
                    ).encode(),
                    "environment-bundle/contract.json": json.dumps(contract).encode(),
                }
            )
            if seeded:
                row.object_key = archives.sealed_key(
                    environment.organization_id, environment.id, body
                )
            # The row records the digest of the bundle it holds, as a build's seed and a run's fill both do.
            row.sha256 = archives.sha256_hex(body)
            row.save(update_fields=["object_key", "sha256"])
            storage.objects[row.object_key] = body
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
            with_version_contract,
        )

        environment, version, storage = self._version(
            user, workspace, "rc-faults", monkeypatch
        )
        HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
            documents={**version.documents, **_recorded(SEALED)}
        )
        self._bundle(environment, version, storage)

        targets, _ = edit_fault_side(environment)
        assert {one["tool"]: one["reachable"] for one in targets} == dict.fromkeys(
            TOOLS, True
        )
        listed = with_version_contract(
            annotate_for_list(
                type(environment).no_workspace_objects.filter(id=environment.id)
            )
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
            origin="chat"
        )
        saved.refresh_from_db()
        self._bundle(
            environment,
            saved,
            storage,
            contract={
                **edited,
                "tool_entrypoints": [billing, *AUTHORED["tool_entrypoints"][1:]],
            },
            resealed=True,
        )

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
                **_recorded(
                    {**moved, "tool_entrypoints": AUTHORED["tool_entrypoints"]}
                ),
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
        self._bundle(environment, saved, storage, contract=edited, resealed=True)

        blocked, statuses = self._through_the_views(auth_client, environment)

        assert blocked == []
        assert statuses == {one["tool"]: one["status"] for one in tool_status(SEALED)}

    def _builder_saved(self, environment, version, storage, run_entries, written=None):
        """A builder's save over ``version``: authored entries as the author writes them (``written``, else with
        empty keys and all), and the entries its resealed bundle carries."""
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
            origin="chat", documents={"contract": edited}
        )
        saved.refresh_from_db()
        self._bundle(
            environment,
            saved,
            storage,
            contract={**edited, "tool_entrypoints": run_entries},
            resealed=True,
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
        self._builder_saved(environment, version, storage, AUTHORED["tool_entrypoints"])

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
            environment, version, storage, [moved, *AUTHORED["tool_entrypoints"][1:]]
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
            storage,
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

    def test_the_gate_reads_the_bundle_a_run_replays_by_its_key_even_under_new_version_ids(
        self, user, workspace, monkeypatch
    ):
        """A credential rotation or a check-only suite edit moves the version ids but keeps the bundle's key, and a
        rerun keeps a historical key: the run replays the bundle under that key."""
        environment, version, storage = self._version(
            user, workspace, "rc-key", monkeypatch
        )
        row = self._bundle(environment, version, storage)
        # New version ids, read as a full build of the author's contract would be: only the key finds the bundle.
        moved = self._saved_edit(environment, version, AUTHORED)
        HarnessEnvironmentVersion._base_manager.filter(id=moved.id).update(
            origin="authoring"
        )
        moved.refresh_from_db()
        keyed = SimpleNamespace(
            environment_version=moved,
            suite_version=environment.suite_version,
            assembly_key=row.assembly_key,
        )
        unkeyed = SimpleNamespace(
            environment_version=moved, suite_version=environment.suite_version
        )

        assert [entry["tool"] for entry in blocked_tools(environment, keyed)] == []
        assert [
            entry["tool"] for entry in blocked_tools(environment, unkeyed)
        ] == sorted(TOOLS)

    @pytest.mark.parametrize(
        "origin, resealed, blocked",
        # A present stamp always wins over the version's origin.
        [
            ("authoring", False, list(TOOLS)),
            ("chat", True, []),
            ("authoring", True, []),
        ],
    )
    def test_a_bundle_reads_as_its_own_stamp_says_it_was_sealed(
        self, user, workspace, monkeypatch, origin, resealed, blocked
    ):
        """Identical authored bytes, two seals: a build's (no stamp, on a version a full build made) is kept as
        sealed, even keeping the author's import entries; a chat's stamped reseal is reached as built."""
        environment, version, storage = self._version(
            user, workspace, f"rc-reused-{origin}", monkeypatch
        )
        self._bundle(environment, version, storage)
        edited = {**AUTHORED, "hard_constraints": ["Never refund twice."]}
        saved = self._saved_edit(environment, version, edited)
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            origin=origin
        )
        saved.refresh_from_db()
        self._bundle(environment, saved, storage, contract=edited, resealed=resealed)

        assert _blocked(environment, saved) == sorted(blocked)

    def test_the_gate_reads_the_scenarios_of_the_pinned_suite(
        self, user, workspace, monkeypatch
    ):
        from simulate.models import HarnessSuiteVersion
        from simulate.services.harness_tool_reach import refuse_production_only
        from simulate.services.hosted_harness import HostedHarnessError

        environment, version, storage = self._version(
            user, workspace, "rc-suite", monkeypatch
        )
        older = HarnessSuiteVersion.no_workspace_objects.get(
            job=environment, version_number=1
        )
        HarnessSuiteVersion._base_manager.filter(id=older.id).update(
            documents={
                **(older.documents or {}),
                "scenarios": [
                    {"name": "refund", "solution": [{"tool": "issue_refund"}]}
                ],
            }
        )
        HarnessSuiteVersion._base_manager.filter(
            id=environment.suite_version_id
        ).update(
            documents={
                **(environment.suite_version.documents or {}),
                "scenarios": [{"name": "lookup", "solution": [{"tool": "lookup"}]}],
            }
        )
        older.refresh_from_db()
        pins = SimpleNamespace(environment_version=version, suite_version=older)

        found = {
            entry["tool"]: entry["scenarios"]
            for entry in blocked_tools(environment, pins)
        }
        assert found["issue_refund"] == [{"key": "refund", "name": "refund"}]
        with pytest.raises(HostedHarnessError) as refused:
            refuse_production_only(environment, pins, ["refund"])
        assert refused.value.code == "production_only"

    def test_a_built_entry_clears_the_reach_fields_it_leaves_out(
        self, user, workspace, monkeypatch
    ):
        from simulate.selectors.environment_documents import version_contract

        environment, version, storage = self._version(
            user, workspace, "rc-clear", monkeypatch
        )
        # The build's entry names only the service route: no module, callable or door note.
        served = {
            "tool": "get_order",
            "mode": "service",
            "service": "tools-api",
            "endpoint": "/find",
            "method": "POST",
        }
        imported = {
            **AUTHORED["tool_entrypoints"][0],
            "door_unreached": "a method of a helper object",
            "notes": "the author's note",
        }
        authored = {**AUTHORED, "tool_entrypoints": [imported]}
        HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
            documents={**version.documents, "contract": authored}
        )
        version.refresh_from_db()
        self._bundle(
            environment,
            version,
            storage,
            contract={**authored, "tool_entrypoints": [served]},
        )
        saved = self._saved_edit(
            environment,
            version,
            {**authored, "hard_constraints": ["Never refund twice."]},
        )

        (entry,) = version_contract(environment, saved)["tool_entrypoints"]
        assert entry == {**served, "effect": "read", "notes": "the author's note"}

    def test_a_long_history_is_read_without_recursing_and_stops_at_a_recorded_contract(
        self, user, workspace, monkeypatch
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from simulate.selectors.environment_documents import version_contract

        environment, version, storage = self._version(
            user, workspace, "rc-history", monkeypatch
        )
        self._bundle(environment, version, storage)
        parent = version
        for number in range(2, 402):
            parent = HarnessEnvironmentVersion._base_manager.create(
                job=environment,
                organization_id=environment.organization_id,
                workspace_id=environment.workspace_id,
                status="active",
                version_number=version.version_number + number,
                hash=f"sha256:{number:064d}",
                parent=parent,
                archive_size=0,
                documents={"contract": AUTHORED},
            )

        # Saved before run contracts were recorded: four hundred versions back to the first build's bundle.
        assert production_only(version_contract(environment, parent)) == []
        # A recorded run contract stops the walk where it is.
        HarnessEnvironmentVersion._base_manager.filter(id=parent.parent_id).update(
            documents={"contract": AUTHORED, **_recorded(SEALED)}
        )
        with CaptureQueriesContext(connection) as queries:
            assert production_only(version_contract(environment, parent)) == []
        assert len(queries) < 10

    def test_a_run_contract_an_earlier_reading_recorded_is_never_trusted_over_the_build_s_seal(
        self, user, workspace, monkeypatch
    ):
        """As on a stack that ran an earlier build of this code: the first build sealed the four tools behind the
        tools service, a chat save kept the author's entries and recorded them imported with no revision stamp. The
        save is read through its ancestry to the build's seal; only a record this reading stamped stops the walk."""
        from simulate.selectors.environment_documents import version_contract

        environment, version, storage = self._version(
            user, workspace, "rc-stale-record", monkeypatch
        )
        self._bundle(environment, version, storage)
        saved = HarnessEnvironmentVersion._base_manager.create(
            job=environment,
            organization_id=environment.organization_id,
            workspace_id=environment.workspace_id,
            status="active",
            origin="chat",
            version_number=version.version_number + 1,
            hash="sha256:" + "5" * 64,
            parent=version,
            archive_size=0,
            documents={"contract": AUTHORED, "run_contract": AUTHORED},
        )

        unstamped = version_contract(environment, saved)
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            documents={"contract": AUTHORED, **_recorded(AUTHORED)}
        )
        saved.refresh_from_db()

        assert production_only(unstamped) == []
        assert (
            recorded_contract(saved.documents)["tool_entrypoints"][0]["mode"]
            == "import"
        )
        assert sorted(production_only(version_contract(environment, saved))) == sorted(
            TOOLS
        )

    def test_the_list_reads_a_recorded_run_contract_only_when_this_reading_stamped_it(
        self, user, workspace, monkeypatch
    ):
        from simulate.models import HostedHarnessJob
        from simulate.services.harness_environment import with_version_contract

        environment, version, _ = self._version(
            user, workspace, "rc-list-stamp", monkeypatch
        )

        def listed(documents):
            HarnessEnvironmentVersion._base_manager.filter(id=version.id).update(
                documents=documents
            )
            row = with_version_contract(
                HostedHarnessJob.no_workspace_objects.filter(id=environment.id)
            ).get()
            return row.version_contract

        assert listed({"contract": AUTHORED, "run_contract": SEALED}) == AUTHORED
        assert listed({"contract": AUTHORED, **_recorded(SEALED)}) == SEALED


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


def test_a_draft_keeps_an_unchanged_fault_on_a_tool_its_build_moved_behind_the_door(
    user, workspace, storage
):
    """The author's contract imports get_order where the door cannot reach it; the build served it from the tools
    service, so a timeout fault on it holds. An edit elsewhere keeps that fault, judged on the contract the draft
    runs with."""
    from simulate.tests.test_harness_versioning_record import NAMES, _scenario

    unreached = {
        **AUTHORED,
        "tool_entrypoints": [
            {**one, "door_unreached": "a method of a helper object"}
            if one["tool"] == "get_order"
            else one
            for one in AUTHORED["tool_entrypoints"]
        ],
    }
    faulted = [
        _scenario(NAMES[0], faults=[{"action": "get_order", "mode": "timeout"}]),
        _scenario(NAMES[1]),
    ]
    tree = {
        "scenarios.json": json.dumps(faulted),
        **{
            f"scenarios/{one['name']}/scenario.json": json.dumps(one) for one in faulted
        },
    }
    environment = _environment(user, workspace, "rc-draft-fault")
    record_authoring(
        environment, _built(contract=unreached, **tree), origin="authoring"
    )
    environment.refresh_from_db()

    write = drafts.record_draft(
        environment,
        _authoring(
            contract=unreached,
            sealed=False,
            **tree,
            **{"handlers/refund.py": "x = 1\n"},
        ),
        origin="chat",
    )

    assert write is not None and "environment" in write.written


class TestResealsSavedBeforeTheyWereStamped:
    """A chat save resealed its bundle before reseals were stamped, and full builds did not stamp theirs either: no
    `sealed_from` on either, so where the row came from and what the save recorded say which it is."""

    def _chat_saved(self, user, workspace, monkeypatch, key, *, resealed):
        """A version a chat save made over the first build's, recording whether the chat resealed a bundle for it,
        as each save has since drafts were kept."""
        legacy = TestVersionRecordedBeforeItsSealedContract()
        environment, version, storage = legacy._version(
            user, workspace, key, monkeypatch
        )
        legacy._bundle(environment, version, storage, seeded=True)
        edited = {**AUTHORED, "hard_constraints": ["Never refund twice."]}
        saved = legacy._saved_edit(environment, version, edited)
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            origin="chat",
            documents={
                "contract": edited,
                "workspace_sealed": {"key": "harness-sealed/x", "resealed": resealed},
            },
        )
        saved.refresh_from_db()
        return legacy, environment, saved, storage, edited

    def _record_reseal(self, saved, row):
        """The save recorded resealing exactly the bundle ``row`` holds."""
        documents = saved.documents
        documents["workspace_sealed"] = {
            **documents["workspace_sealed"],
            "sha256": row.sha256,
        }
        HarnessEnvironmentVersion._base_manager.filter(id=saved.id).update(
            documents=documents
        )
        saved.refresh_from_db()

    def test_an_unstamped_bundle_a_run_replayed_from_a_chat_reseal_is_read_as_a_reseal(
        self, user, workspace, monkeypatch, auth_client
    ):
        legacy, environment, saved, storage, edited = self._chat_saved(
            user, workspace, monkeypatch, "rc-unstamped", resealed=True
        )
        # The reseal carries the author's import entries and no stamp; a run of the saved pair cached it.
        row = legacy._bundle(environment, saved, storage, contract=edited)
        self._record_reseal(saved, row)

        blocked, statuses = legacy._through_the_views(auth_client, environment)

        assert blocked == []
        assert statuses == {one["tool"]: one["status"] for one in tool_status(SEALED)}

    def test_another_pair_s_unstamped_bundle_on_a_chat_resealed_version_keeps_the_contract_it_sealed(
        self, user, workspace, monkeypatch
    ):
        """The save resealed the default pair's bundle; another agent's run then certified its own, sealing the
        author's production-only import. Only the bundle the save recorded is the chat's reseal: the other pair's
        keeps its own contract, never the parent's service route."""
        from simulate.tests.test_harness_versioning_picks import _second_agent

        legacy, environment, saved, storage, edited = self._chat_saved(
            user, workspace, monkeypatch, "rc-unstamped-other-pair", resealed=True
        )
        chat = {
            **edited,
            "hard_constraints": ["Never refund twice.", "Confirm the order first."],
        }
        resealed = legacy._bundle(environment, saved, storage, contract=chat)
        self._record_reseal(saved, resealed)
        other = _second_agent(environment)
        own = legacy._bundle(
            environment, saved, storage, contract=edited, agent_version_id=other.id
        )
        assert own.sha256 != resealed.sha256

        assert _blocked(environment, saved) == []
        assert _blocked(environment, saved, agent_version_id=other.id) == sorted(TOOLS)

    @pytest.mark.parametrize(
        "seeded, resealed",
        [
            # A later full build reused the chat's row and cached its own bundle for it.
            (True, True),
            # The save resealed nothing, so a run certified the pair itself.
            (False, False),
        ],
    )
    def test_an_unstamped_build_on_a_chat_saved_version_keeps_the_contract_it_sealed(
        self, user, workspace, monkeypatch, auth_client, seeded, resealed
    ):
        """A build that sealed the author's production-only import keeps it, never the parent's service route."""
        legacy, environment, saved, storage, edited = self._chat_saved(
            user,
            workspace,
            monkeypatch,
            f"rc-unstamped-build-{seeded}",
            resealed=resealed,
        )
        legacy._bundle(environment, saved, storage, contract=edited, seeded=seeded)

        blocked, statuses = legacy._through_the_views(auth_client, environment)

        assert sorted(blocked) == sorted(TOOLS)
        assert statuses == {one["tool"]: one["status"] for one in tool_status(AUTHORED)}

    def test_a_chat_saved_row_reused_by_a_stamped_build_reads_as_the_build_sealed_it(
        self, user, workspace, monkeypatch
    ):
        """A build stamps its seal, so a row a chat save made, reused by a later full build that kept the author's
        import entries, reads as that build sealed it."""
        legacy, environment, saved, storage, edited = self._chat_saved(
            user, workspace, monkeypatch, "rc-build-stamp", resealed=True
        )
        legacy._bundle(environment, saved, storage, contract=edited, built=True)

        assert _blocked(environment, saved) == sorted(TOOLS)


class TestARecordedRunContractStopsTheWalkAtAReseal:
    """A version whose READY bundle a chat resealed, and which recorded the contract it runs with, is read from that
    record: its ancestry is not walked again."""

    def _resaves(self, user, workspace, monkeypatch, key, count):
        """``count`` chat saves over a first version no build sealed, each with a READY stamped reseal of the
        author's import entries and the service route it recorded running with; the newest last."""
        from simulate.models import HarnessAssembledBundle

        legacy = TestVersionRecordedBeforeItsSealedContract()
        environment, root, storage = legacy._version(user, workspace, key, monkeypatch)
        HarnessEnvironmentVersion._base_manager.filter(id=root.id).update(origin="chat")
        body = archives.pack_archive(
            {
                "environment-bundle/manifest.json": json.dumps(
                    {"metadata": {"sealed_from": "sha256:tree"}}
                ).encode(),
                "environment-bundle/contract.json": json.dumps(AUTHORED).encode(),
            }
        )
        versions = []
        parent = root
        for number in range(1, count + 1):
            parent = HarnessEnvironmentVersion._base_manager.create(
                job=environment,
                organization_id=environment.organization_id,
                workspace_id=environment.workspace_id,
                status="active",
                origin="chat",
                version_number=root.version_number + number,
                hash=f"sha256:{number:064d}",
                parent=parent,
                archive_size=0,
                documents={"contract": AUTHORED, **_recorded(SEALED)},
            )
            versions.append(parent)
        HarnessAssembledBundle._base_manager.bulk_create(
            HarnessAssembledBundle(
                job_id=environment.id,
                organization_id=environment.organization_id,
                agent_version_id=environment.agent_version_id,
                environment_version_id=version.id,
                suite_version_id=environment.suite_version_id,
                assembly_key=f"sha256:{number:064x}",
                state=HarnessAssembledBundle.State.READY,
                object_key=f"harness-bundles/{number}.tar.gz",
            )
            for number, version in enumerate(versions, start=1)
        )
        for number in range(1, count + 1):
            storage.objects[f"harness-bundles/{number}.tar.gz"] = body
        return environment, versions

    @pytest.mark.parametrize("count", [400, 1001])
    def test_a_long_run_of_resealed_saves_reads_the_newest_one_s_record_with_one_seal_lookup(
        self, user, workspace, monkeypatch, count
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from simulate.selectors.environment_documents import version_contract

        environment, versions = self._resaves(
            user, workspace, monkeypatch, f"rc-resaves-{count}", count
        )

        with CaptureQueriesContext(connection) as queries:
            contract = version_contract(environment, versions[-1])

        assert production_only(contract) == []
        seal_lookups = [
            one
            for one in queries.captured_queries
            if "simulate_harness_assembled_bundle" in one["sql"]
        ]
        assert len(seal_lookups) == 1
        assert len(queries) < 10

    def test_an_ancestor_with_a_reseal_and_a_recorded_run_contract_ends_the_walk(
        self, user, workspace, monkeypatch
    ):
        """The newest save recorded nothing; its parent resealed and recorded the service route. The walk stops
        there instead of reaching the first version, which reaches the tools by the author's import."""
        from simulate.selectors.environment_documents import version_contract

        environment, versions = self._resaves(
            user, workspace, monkeypatch, "rc-resaves-middle", 3
        )
        newest = versions[-1]
        HarnessEnvironmentVersion._base_manager.filter(id=newest.id).update(
            documents={"contract": AUTHORED}
        )
        newest.refresh_from_db()

        assert production_only(version_contract(environment, newest)) == []
