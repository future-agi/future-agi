"""Every reader takes the contract of the version it acts on, as that version's build sealed it.

The contracts are a real returns build's, reduced to the fields that differ: the builder amended the authored
contract while it built, serving the four tools from the agent's tools service instead of importing them. The
authored one reads all four as reaching production; the sealed one, the one the bundle certified and runs, none.
"""

import json
from types import SimpleNamespace

import pytest

from simulate.models import HarnessEnvironmentVersion
from simulate.services.harness_environment import environment_detail
from simulate.services.harness_tool_reach import blocked_tools, production_only
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


def _blocked(environment, version=None) -> list[str]:
    pins = SimpleNamespace(environment_version=version) if version is not None else None
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

    assert recorded.environment.documents["contract"] == SEALED
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

    assert recorded.environment.documents["contract"] == AUTHORED


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
    assert older.documents["contract"] == AUTHORED


def test_a_draft_the_chat_resealed_with_an_amended_contract_shows_it_as_the_draft_s(
    user, workspace, storage
):
    environment = _environment(user, workspace, "rc-draft")
    record_authoring(environment, _built(sealed=AUTHORED), origin="authoring")
    environment.refresh_from_db()
    drafts.record_draft(environment, _resealed(), origin="chat")

    draft = drafts.open_draft(environment, "environment")
    assert draft.documents["contract"] == SEALED
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
        drafts.open_draft(environment, "environment").documents["contract"] == AUTHORED
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
    assert drafts.open_draft(environment, "environment").documents["contract"] == SEALED

    edited = {**AUTHORED, "one_liner": "returns desk"}
    drafts.record_draft(
        environment,
        _authoring(contract=edited, sealed=False, **{"handlers/refund.py": "x = 1\n"}),
        origin="chat",
    )
    assert drafts.open_draft(environment, "environment").documents["contract"] == edited


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
    assert drafts.open_draft(environment, "environment").documents["contract"] == SEALED

    _save_drafts(environment)

    saved = environment.environment_version
    assert saved.id != first.id and saved.documents["contract"] == SEALED
    assert _blocked(environment) == [] and _blocked(environment, saved) == []
    assert first.documents["contract"] != SEALED


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

    def _bundle(self, environment, version, storage, contract=SEALED):
        from simulate.tests.test_harness_versioning_picks import _ready, _suite

        row = _ready(
            environment, environment.agent_version_id, version, _suite(environment, 1)
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
