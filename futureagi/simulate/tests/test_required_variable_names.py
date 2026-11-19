"""A stored reading lists the names its agent needs on the version, so a run warns of each
one with no value; the environment-creation check adds to that list and never erases it.
A writer with no answer (no process named, or a check that did not run) writes nothing."""

from io import StringIO

import pytest
from django.core.management import call_command

from simulate.models import AgentDefinition, AgentVersion, HarnessAgentVersionRuntime
from simulate.models.agent_setup import AgentVersionSetup
from simulate.serializers.harness_job import HarnessJobCreateSerializer
from simulate.services import agent_reading_sandbox, harness_provider
from simulate.services import harness_agent_versions as service
from simulate.services.agent_reading_sandbox import _store_reading
from simulate.services.agent_setup import AgentStoreError, store_agent
from simulate.services.harness_agent_versions import (
    REQUIRED_NAMES_FROM_PARENT,
    CreationCheck,
    NoAnswer,
    SubmittedCredentials,
    ensure_environment_agent,
    merge_required_names,
    reading_required_names,
)
from simulate.services.harness_versioning.run_warnings import _credential_warnings
from simulate.services.hosted_harness import create_hosted_job
from simulate.tests.test_agent_setup_service import source_agent
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_agent_creation import _vapi
from simulate.tests.test_harness_connected_versions import _connect, _runtime

pytestmark = pytest.mark.django_db

Status = HarnessAgentVersionRuntime.PreflightStatus
Trigger = HarnessAgentVersionRuntime.Trigger
BOTH = ["OPENAI_API_KEY", "OPENAI_BASE_URL"]


@pytest.fixture(autouse=True)
def versioning_on(settings):
    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False


@pytest.fixture
def agent(organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="support",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
    )


def _read(document=None):
    return {"agent": document or source_agent(), "sdk_version": "0.2.3"}


def _contract(*names, process="agent", **runtime):
    required = {process: list(names)}
    return {
        "agent": {"name": "support"},
        "runtime": {"required_environment": required, **runtime},
    }


def _store(version, agent, contract, **kwargs):
    return _store_reading(_runtime(version), agent, _read(), contract, None, **kwargs)


def _names(version):
    return _runtime(version).required_variable_names


def _payload(version):
    return _runtime(version).agent_payload or {}


def _missing(version):
    return [
        warning["message"]
        for warning in _credential_warnings(_runtime(version))
        if warning["code"] == "variable_missing"
    ]


# --- the store step ---------------------------------------------------------------


@pytest.mark.usefixtures("storage")
def test_a_stored_reading_lists_what_the_agent_needs_and_a_run_warns(agent):
    version = _connect(agent)
    assert _names(version) is None

    outcome = _store(version, agent, _contract("OPENAI_BASE_URL", "OPENAI_API_KEY"))

    assert outcome.status == "stored"
    assert _names(version) == BOTH
    assert _missing(version) == [
        "This agent version has no value for OPENAI_API_KEY, OPENAI_BASE_URL"
    ]


@pytest.mark.usefixtures("storage")
def test_a_version_with_both_values_does_not_warn(agent):
    version = _connect(
        agent,
        secret_values={"OPENAI_API_KEY": "val-key", "OPENAI_BASE_URL": "val-base"},
    )

    _store(version, agent, _contract(*BOTH))

    assert _names(version) == BOTH
    assert _missing(version) == []


@pytest.mark.usefixtures("storage")
def test_a_process_named_with_no_names_is_an_empty_list_not_null(agent):
    version = _connect(agent)

    _store(version, agent, _contract())

    assert _names(version) == []
    assert _missing(version) == []


@pytest.mark.usefixtures("storage")
@pytest.mark.parametrize(
    "runtime",
    [
        {},
        {"required_environment": {}},
        {"required_environment": None},
        {"required_environment": {"agent": None}},
    ],
)
def test_a_reading_that_names_no_process_leaves_null_as_null(agent, caplog, runtime):
    version = _connect(agent)

    with caplog.at_level("INFO"):
        outcome = _store(version, agent, {"agent": {}, "runtime": runtime})

    assert outcome.status == "stored"
    assert _names(version) is None
    assert _missing(version) == []
    assert "required_names_not_given" in caplog.text


@pytest.mark.usefixtures("storage")
def test_a_reading_that_names_no_process_keeps_the_parents_copy_and_its_mark(agent):
    _, child = _child_with_parents_names(agent)

    _store(child, agent, _contract(required_environment={}))

    assert _names(child) == ["PARENT_ONLY"]
    assert _payload(child)[REQUIRED_NAMES_FROM_PARENT] is True
    # The mark kept, the child's own reading with names still replaces the copy.
    _store(child, agent, _contract("CHILD_ONLY"), document_only=True)
    assert _names(child) == ["CHILD_ONLY"]


def test_no_reading_leaves_the_names_unknown(agent):
    version = _connect(agent)

    assert _names(version) is None
    assert _missing(version) == []


@pytest.mark.usefixtures("storage")
def test_a_document_only_reading_fills_the_names(agent):
    version = _connect(agent)
    HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version=version
    ).update(preflight_status=Status.PASSED)

    outcome = _store(version, agent, _contract(*BOTH), document_only=True)

    assert outcome.status == "stored"
    assert _names(version) == BOTH


def _child_with_parents_names(agent, parent_names=("PARENT_ONLY",), **extra):
    parent = _connect(agent)
    store_agent(agent_version_id=parent.id, agent=source_agent(), sdk_version="0.2.3")
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=parent).update(
        required_variable_names=list(parent_names)
    )
    child = _connect(
        agent,
        parent=parent,
        trigger=Trigger.CREDENTIALS,
        secret_values={"API_KEY": "val-fresh"},
        **extra,
    )
    assert child.id != parent.id
    return parent, child


@pytest.mark.usefixtures("storage")
@pytest.mark.parametrize(
    "ending",
    ["not_pending", "channel_changed", "store_failed", "invalid", "subfolder_ignored"],
)
def test_an_ending_that_stores_nothing_leaves_the_names_alone(
    agent, monkeypatch, ending
):
    _, child = _child_with_parents_names(agent)
    before = (_names(child), _payload(child).get(REQUIRED_NAMES_FROM_PARENT))
    assert before == (["PARENT_ONLY"], True)
    runtime = _runtime(child)
    agent_json = _read()
    if ending == "not_pending":
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            preflight_status=Status.PASSED
        )
    elif ending == "channel_changed":
        agent_json = _read(source_agent(type="voice"))
    elif ending == "store_failed":

        def refuse(**_kwargs):
            raise AgentStoreError("agent_invalid")

        monkeypatch.setattr(agent_reading_sandbox, "store_agent", refuse)
    elif ending == "invalid":
        agent_json = {"agent": "not-a-document"}
    else:
        HarnessAgentVersionRuntime.no_workspace_objects.filter(id=runtime.id).update(
            source={**(runtime.source or {}), "subfolder": "agents/support"}
        )
        runtime.refresh_from_db()

    outcome = _store_reading(runtime, agent, agent_json, _contract(*BOTH), None)

    assert outcome.status in ("skipped", "failed")
    expected_code = {"store_failed": "agent_invalid", "invalid": None}.get(
        ending, ending
    )
    if expected_code:
        assert outcome.code == expected_code
    assert (_names(child), _payload(child).get(REQUIRED_NAMES_FROM_PARENT)) == before


@pytest.mark.usefixtures("storage")
def test_a_name_public_settings_meet_is_not_listed(agent):
    version = _connect(agent)
    HarnessAgentVersionRuntime.no_workspace_objects.filter(
        agent_version=version
    ).update(
        agent_payload={
            **_payload(version),
            "config": {"livekit_url": "wss://lk.example.test"},
        }
    )

    _store(version, agent, _contract("LIVEKIT_URL", "OPENAI_API_KEY"))

    assert _names(version) == ["OPENAI_API_KEY"]


@pytest.mark.usefixtures("storage")
def test_a_name_the_run_supplies_is_not_listed(agent):
    version = _connect(agent)
    contract = _contract(
        "APP_TOKEN",
        "REGION",
        "EMPTY_IN_FILE",
        "OPENAI_API_KEY",
        interface={
            "kind": "http",
            "local_auth": {
                "server_environment": "APP_TOKEN",
                "template_variable": "token",
            },
        },
        config_files={".env": {"values": {"REGION": "eu", "EMPTY_IN_FILE": ""}}},
    )

    _store(version, agent, contract)

    assert _names(version) == ["EMPTY_IN_FILE", "OPENAI_API_KEY"]


@pytest.mark.usefixtures("storage")
@pytest.mark.parametrize(
    "runtime",
    [
        {"required_environment": ["OPENAI_API_KEY"]},
        {"required_environment": {"agent": "OPENAI_API_KEY"}},
        {"required_environment": {"agent": [""]}},
        {"required_environment": {"agent": ["A"]}, "interface": "http"},
        {"required_environment": {"agent": ["A"]}, "config_files": ["x"]},
        "not-a-runtime",
    ],
)
def test_a_section_of_the_wrong_shape_writes_nothing(agent, caplog, runtime):
    version = _connect(agent)

    with caplog.at_level("WARNING"):
        outcome = _store(version, agent, {"agent": {}, "runtime": runtime})

    assert outcome.status == "stored"
    assert _names(version) is None
    assert "required_names_unreadable" in caplog.text


# --- more than one writer ---------------------------------------------------------


@pytest.mark.usefixtures("storage")
def test_version_one_takes_its_readings_names(agent):
    version = _connect(agent)

    _store(version, agent, _contract("OPENAI_API_KEY"))

    assert _names(version) == ["OPENAI_API_KEY"]
    assert REQUIRED_NAMES_FROM_PARENT not in _payload(version)


@pytest.mark.usefixtures("storage")
def test_a_child_replaces_its_parents_copy_at_its_own_reading(agent):
    parent, child = _child_with_parents_names(agent, parent_names=("PARENT_ONLY",))
    # Until its own reading, the child holds its parent's names, marked as a copy.
    assert _names(child) == ["PARENT_ONLY"]
    assert _payload(child)[REQUIRED_NAMES_FROM_PARENT] is True

    _store(child, agent, _contract("CHILD_ONLY"))

    assert _names(child) == ["CHILD_ONLY"]
    assert REQUIRED_NAMES_FROM_PARENT not in _payload(child)
    assert _names(parent) == ["PARENT_ONLY"]


@pytest.mark.usefixtures("storage")
def test_a_child_with_its_parents_prompt_and_names_keeps_neither_flag(agent):
    _, child = _child_with_parents_names(agent, prompt=None)
    assert _payload(child).get("prompt_from_parent") is True
    assert _payload(child)[REQUIRED_NAMES_FROM_PARENT] is True

    _store(child, agent, _contract("CHILD_ONLY"))

    payload = _payload(child)
    assert "prompt_from_parent" not in payload
    assert REQUIRED_NAMES_FROM_PARENT not in payload
    assert (
        AgentVersion.no_workspace_objects.get(id=child.id).description
        == (source_agent()["behaviour"][0]["instructions"])
    )


def _create_environment(user, workspace, check, key):
    payload = HarnessJobCreateSerializer(data=_vapi()).run_validation(_vapi())
    payload.pop("credential_values")
    submitted = SubmittedCredentials(
        secret_set=service.CredentialSet(),
        provider_secrets={"VAPI_API_KEY": "val-vapi-1"},
    )

    def make(job):
        ensure_environment_agent(job, submitted=submitted, check=check)

    job, _created = create_hosted_job(
        user.organization,
        payload,
        idempotency_key=key,
        workspace=workspace,
        on_create=make,
    )
    return job


def _environment_version(job):
    job.refresh_from_db()
    version_id = (job.payload.get("metadata") or {}).get("build_agent_version_id")
    assert isinstance(version_id, str)
    return AgentVersion.no_workspace_objects.get(id=version_id)


@pytest.mark.usefixtures("storage")
def test_an_older_creation_keeps_the_checks_names_and_adds_the_readings(
    user, workspace
):
    job = _create_environment(
        user,
        workspace,
        CreationCheck(report={}, required_variable_names=["VAPI_API_KEY"]),
        "older-creation",
    )
    version = _environment_version(job)
    assert _names(version) == ["VAPI_API_KEY"]
    definition = AgentDefinition.no_workspace_objects.get(
        id=version.agent_definition_id
    )

    outcome = _store_reading(
        _runtime(version),
        definition,
        _read(),
        _contract("OPENAI_API_KEY"),
        None,
        document_only=True,
    )

    assert outcome.status == "stored"
    assert _names(version) == ["OPENAI_API_KEY", "VAPI_API_KEY"]


@pytest.mark.usefixtures("storage")
def test_the_creation_check_does_not_erase_a_readings_names(
    agent, user, workspace, monkeypatch
):
    version = _connect(agent)
    _store(version, agent, _contract(*BOTH))
    assert _names(version) == BOTH
    assert _runtime(version).preflight_status == Status.PENDING
    monkeypatch.setattr(
        service, "create_agent_version", lambda *_args, **_kwargs: version
    )

    _create_environment(
        user,
        workspace,
        CreationCheck(report={}, required_variable_names=[]),
        "after-reading",
    )

    runtime = _runtime(version)
    assert runtime.preflight_status == Status.PASSED
    assert runtime.required_variable_names == BOTH


@pytest.mark.usefixtures("storage")
def test_the_creation_check_replaces_a_parents_copy_and_takes_its_mark_off(
    agent, user, workspace, monkeypatch
):
    _, child = _child_with_parents_names(agent, parent_names=("PARENT_ONLY",))
    assert _runtime(child).preflight_status == Status.PENDING
    monkeypatch.setattr(
        service, "create_agent_version", lambda *_args, **_kwargs: child
    )

    _create_environment(
        user,
        workspace,
        CreationCheck(report={}, required_variable_names=["CHECK_ONLY"]),
        "check-over-copy",
    )

    assert _names(child) == ["CHECK_ONLY"]
    assert REQUIRED_NAMES_FROM_PARENT not in _payload(child)


@pytest.mark.usefixtures("storage")
def test_the_skipped_check_of_a_connected_agent_leaves_null_as_null(
    agent, user, workspace, monkeypatch
):
    version = _connect(agent)
    assert _names(version) is None

    def no_check(*_args):
        raise AssertionError("a connected agent's creation is not checked")

    monkeypatch.setattr(harness_provider, "_builds_connected_agent", lambda *_a: True)
    monkeypatch.setattr(service, "check_creation_credentials", no_check)
    monkeypatch.setattr(
        service, "create_agent_version", lambda *_args, **_kwargs: version
    )
    payload = HarnessJobCreateSerializer(data=_vapi()).run_validation(_vapi())
    payload.pop("credential_values")
    make = harness_provider._environment_agent_maker(
        user.organization,
        payload,
        {"VAPI_API_KEY": "val-vapi-1"},
        None,
        user,
        workspace=workspace,
    )

    create_hosted_job(
        user.organization,
        payload,
        idempotency_key="skipped-check",
        workspace=workspace,
        on_create=make,
    )

    runtime = _runtime(version)
    assert runtime.preflight_status == Status.PASSED
    assert runtime.required_variable_names is None
    assert _missing(version) == []


# --- the functions themselves -----------------------------------------------------


@pytest.mark.parametrize(
    "contract, expected",
    [
        ({}, NoAnswer.NOT_GIVEN),
        ({"runtime": None}, NoAnswer.NOT_GIVEN),
        ({"runtime": {}}, NoAnswer.NOT_GIVEN),
        ({"runtime": {"required_environment": None}}, NoAnswer.NOT_GIVEN),
        ({"runtime": {"required_environment": {}}}, NoAnswer.NOT_GIVEN),
        ({"runtime": {"required_environment": {"agent": None}}}, NoAnswer.NOT_GIVEN),
        ({"runtime": {"required_environment": {"agent": []}}}, []),
        (
            {"runtime": {"required_environment": {"agent": None, "worker": []}}},
            [],
        ),
        # A name the run supplies is subtracted from an answer, which stays an answer.
        (
            {
                "runtime": {
                    "required_environment": {"agent": ["TOKEN"]},
                    "interface": {"local_auth": {"server_environment": "TOKEN"}},
                }
            },
            [],
        ),
        (
            {
                "runtime": {
                    "required_environment": {"agent": ["B", "A"]},
                    "interface": None,
                }
            },
            ["A", "B"],
        ),
        (
            {
                "runtime": {
                    "required_environment": {"agent": ["A"], "worker": ["A", "C"]}
                }
            },
            ["A", "C"],
        ),
        (
            {
                "runtime": {
                    "required_environment": {"agent": ["A"]},
                    "interface": {"local_auth": None},
                    "config_files": {".env": None},
                }
            },
            ["A"],
        ),
    ],
)
def test_the_names_are_read_from_the_contract(contract, expected):
    assert reading_required_names(contract, {}) == expected


def test_the_names_public_settings_meet_are_left_out():
    contract = _contract("LIVEKIT_URL", "OPENAI_API_KEY")

    assert reading_required_names(contract, {"config": {"LIVEKIT_URL": "wss://x"}}) == [
        "OPENAI_API_KEY"
    ]
    # A name the version holds as a credential is still listed: it has a value to check.
    assert reading_required_names(
        contract,
        {"config": {"livekit_url": "wss://x"}, "secret_refs": {"LIVEKIT_URL": {}}},
    ) == ["LIVEKIT_URL", "OPENAI_API_KEY"]


@pytest.mark.parametrize(
    "current, payload, names, expected, flag_removed",
    [
        (None, {}, ["B", "A"], ["A", "B"], False),
        (["P"], {REQUIRED_NAMES_FROM_PARENT: True, "x": 1}, ["A"], ["A"], True),
        (["P"], {"x": 1}, ["A"], ["A", "P"], False),
        (["A"], None, [], ["A"], False),
        (None, {REQUIRED_NAMES_FROM_PARENT: True}, [], [], True),
    ],
)
def test_the_first_own_writer_replaces_and_a_later_one_adds(
    current, payload, names, expected, flag_removed
):
    required, own_payload = merge_required_names(current, payload, names)

    assert required == expected
    if flag_removed:
        assert own_payload == {
            key: value
            for key, value in payload.items()
            if key != REQUIRED_NAMES_FROM_PARENT
        }
    else:
        assert own_payload is None


# --- the fill command -------------------------------------------------------------


def _with_stored_contract(version, contract):
    from simulate.services.agent_contracts import store_version_contract

    store_agent(agent_version_id=version.id, agent=source_agent(), sdk_version="0.2.3")
    assert store_version_contract(
        organization_id=version.organization_id,
        agent_version_id=version.id,
        contract=contract,
    )


def _versions_for_the_command(agent):
    unknown = _connect(agent, commit="unknown")
    _with_stored_contract(unknown, _contract("OPENAI_API_KEY"))
    listed = _connect(agent, commit="listed")
    _with_stored_contract(listed, _contract("OPENAI_API_KEY"))
    HarnessAgentVersionRuntime.no_workspace_objects.filter(agent_version=listed).update(
        required_variable_names=["KEPT"]
    )
    uncontracted = _connect(agent, commit="uncontracted")
    store_agent(
        agent_version_id=uncontracted.id, agent=source_agent(), sdk_version="0.2.3"
    )
    return unknown, listed, uncontracted


@pytest.mark.usefixtures("storage")
def test_the_command_fills_only_unknown_versions_with_a_contract(agent):
    unknown, listed, uncontracted = _versions_for_the_command(agent)
    assert (
        AgentVersionSetup.no_workspace_objects.get(
            agent_version_id=uncontracted.id
        ).contract_object_key
        == ""
    )

    unanswered = _connect(agent, commit="unanswered")
    _with_stored_contract(unanswered, _contract(required_environment={}))
    out = StringIO()

    call_command("fill_required_variable_names", stdout=out)

    assert _names(unknown) == ["OPENAI_API_KEY"]
    assert _names(listed) == ["KEPT"]
    assert _names(uncontracted) is None
    assert _names(unanswered) is None
    assert "Filled 1 version(s); 1 with no answer;" in out.getvalue()


@pytest.mark.usefixtures("storage")
def test_the_command_dry_run_writes_nothing(agent):
    unknown, _, _ = _versions_for_the_command(agent)
    out = StringIO()

    call_command("fill_required_variable_names", "--dry-run", stdout=out)

    assert _names(unknown) is None
    assert f"{unknown.id}: OPENAI_API_KEY" in out.getvalue()
    assert "Would fill 1 version(s)" in out.getvalue()


@pytest.mark.usefixtures("storage")
def test_the_command_fills_only_the_versions_it_is_given(agent):
    unknown, _, _ = _versions_for_the_command(agent)
    other = _connect(agent, commit="other")
    _with_stored_contract(other, _contract("OPENAI_API_KEY"))

    call_command(
        "fill_required_variable_names",
        "--agent-version",
        str(other.id),
        stdout=StringIO(),
    )

    assert _names(other) == ["OPENAI_API_KEY"]
    assert _names(unknown) is None
