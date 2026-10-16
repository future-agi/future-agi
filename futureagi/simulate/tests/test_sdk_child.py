import asyncio
import subprocess

import pytest
from temporalio.exceptions import ApplicationError

from simulate.temporal import sdk_child
from simulate.temporal.sdk_child import (
    SdkChildBusy,
    SdkChildCrashed,
    SdkChildError,
    SdkChildProtocolError,
    SdkChildTimeout,
    SdkChildUnavailable,
)
from simulate.temporal.testing import FakeSdkChild

OK_READ_CALL = {
    "protocol": 1,
    "op": "read_call",
    "request_id": "from-fixture",
    "ok": True,
    "result": {"provider_call_id": "c1", "terminal": True},
}

BAD_CALLS = [
    (
        "read_call",
        {"provider": "vapi"},
        {"credential_id": "cred-1", "timeout_seconds": 4, "job_ref": "read-1"},
        "timeout_seconds must be an integer from 5 to 300",
    ),
    (
        "read_call",
        {"provider": "vapi"},
        {"credential_id": "cred-1", "timeout_seconds": 301, "job_ref": "read-1"},
        "timeout_seconds must be an integer from 5 to 300",
    ),
    (
        "read_call",
        {"provider": "vapi"},
        {"credential_id": "cred-1", "timeout_seconds": True, "job_ref": "read-1"},
        "timeout_seconds must be an integer from 5 to 300",
    ),
    (
        "read_call",
        {"provider": "vapi"},
        {
            "credential_id": "cred-1",
            "timeout_seconds": 60,
            "job_ref": "task-" + "a" * 60,
        },
        "job_ref is not a valid request id",
    ),
    (
        "read_call",
        {"provider": "vapi"},
        {"credential_id": "cred-1", "timeout_seconds": 60, "job_ref": "read 1"},
        "job_ref is not a valid request id",
    ),
    (
        "read_call",
        {"provider": "vapi", "op": "describe"},
        {"credential_id": "cred-1", "timeout_seconds": 60, "job_ref": "read-1"},
        "fields must not hold envelope names",
    ),
    (
        "describe",
        {},
        {"credential_id": "cred-1", "timeout_seconds": 30, "job_ref": "provider-list"},
        "credential_id is not allowed for describe",
    ),
    (
        "convert_calls",
        {"provider": "vapi", "records": [], "context": {}},
        {"credential_id": "cred-1", "timeout_seconds": 120, "job_ref": "poll-1.1"},
        "credential_id is not allowed for convert_calls",
    ),
    (
        "read_setup",
        {"provider": "vapi", "agent_id": "asst"},
        {"credential_id": None, "timeout_seconds": 90, "job_ref": "setup-1"},
        "credential_id is required for read_setup",
    ),
    (
        "read_call",
        {"provider": "vapi", "call_id": "c1"},
        {"credential_id": None, "timeout_seconds": 60, "job_ref": "read-1"},
        "credential_id is required for read_call",
    ),
]


@pytest.fixture
def no_process(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a process was started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.mark.parametrize(
    "error_class, code, retryable",
    [
        (SdkChildUnavailable, "child_unavailable", False),
        (SdkChildBusy, "child_busy", True),
        (SdkChildTimeout, "child_timeout", True),
        (SdkChildProtocolError, "child_protocol_error", False),
    ],
)
def test_child_errors_carry_code_retryable_and_exit_code(error_class, code, retryable):
    error = error_class(exit_code=2)

    assert isinstance(error, SdkChildError)
    assert str(error) == error.code == code
    assert error.retryable is retryable
    assert error.exit_code == 2


@pytest.mark.parametrize(
    "exit_code, retryable", [(-9, True), (1, False), (None, False)]
)
def test_crashed_is_retryable_only_when_killed_by_a_signal(exit_code, retryable):
    error = SdkChildCrashed(exit_code=exit_code)

    assert str(error) == "child_crashed"
    assert error.retryable is retryable


@pytest.mark.parametrize("operation, fields, options, message", BAD_CALLS)
async def test_helper_and_fake_refuse_the_same_bad_calls(
    operation, fields, options, message, no_process
):
    fake = FakeSdkChild()
    fake.respond(operation, OK_READ_CALL)

    with pytest.raises(ValueError, match=f"^{message}$"):
        await sdk_child.run_sdk_child(operation, fields, **options)
    with pytest.raises(ValueError, match=f"^{message}$"):
        await fake.run_sdk_child(operation, fields, **options)
    assert fake.calls == []


async def test_fake_checks_arguments_like_the_helper_queues_answers_and_starts_nothing(
    no_process,
):
    fake = FakeSdkChild()
    first = {**OK_READ_CALL, "result": {"provider_call_id": "first"}}
    second = {
        **OK_READ_CALL,
        "result": {"provider_call_id": "second"},
        "sdk_version": "0.3.0",
    }
    fake.respond("read_call", first)
    fake.respond("read_call", second)

    answers = [
        await fake.run_sdk_child(
            "read_call",
            {"provider": "vapi", "call_id": f"c{n}"},
            credential_id="cred-1",
            timeout_seconds=60,
            job_ref=f"read-{n}",
        )
        for n in range(3)
    ]

    assert [a["result"]["provider_call_id"] for a in answers] == [
        "first",
        "second",
        "second",
    ]
    assert [a["request_id"] for a in answers] == ["read-0", "read-1", "read-2"]
    assert [a["sdk_version"] for a in answers] == ["fake", "0.3.0", "0.3.0"]
    assert fake.calls[0] == {
        "operation": "read_call",
        "fields": {"provider": "vapi", "call_id": "c0"},
        "credential_id": "cred-1",
        "timeout_seconds": 60,
        "job_ref": "read-0",
    }
    assert first["request_id"] == "from-fixture"
    with pytest.raises(AssertionError, match="^no answer for describe$"):
        await fake.run_sdk_child(
            "describe", {}, timeout_seconds=30, job_ref="provider-list"
        )


@pytest.mark.parametrize(
    "answer",
    [
        ApplicationError(
            "missing", type="provider_credentials_missing", non_retryable=True
        ),
        SdkChildBusy(),
    ],
)
async def test_fake_raises_a_queued_application_error(answer):
    fake = FakeSdkChild()
    fake.respond("read_setup", answer)

    with pytest.raises(type(answer)) as raised:
        await fake.run_sdk_child(
            "read_setup",
            {"provider": "vapi", "agent_id": "asst"},
            credential_id="cred-1",
            timeout_seconds=90,
            job_ref="setup-1",
        )
    assert raised.value is answer


def test_fake_blocking_form_answers_off_the_loop_and_refuses_on_it(no_process):
    fake = FakeSdkChild()
    fake.respond("convert_calls", {**OK_READ_CALL, "op": "convert_calls"})
    fields = {"provider": "vapi", "records": [{"id": "c1"}], "context": {}}

    answer = fake.run_sdk_child_blocking(
        "convert_calls", fields, timeout_seconds=120, job_ref="poll-1.1"
    )

    async def on_the_loop():
        fake.run_sdk_child_blocking(
            "convert_calls", fields, timeout_seconds=120, job_ref="poll-1.1"
        )

    assert answer["request_id"] == "poll-1.1"
    with pytest.raises(
        RuntimeError, match="^run_sdk_child_blocking called on the event loop$"
    ):
        asyncio.run(on_the_loop())


def test_helper_blocking_form_needs_a_bound_loop(monkeypatch):
    monkeypatch.setattr(sdk_child, "_worker_loop", None)

    with pytest.raises(RuntimeError, match="^no worker loop bound$"):
        sdk_child.run_sdk_child_blocking(
            "describe", {}, timeout_seconds=30, job_ref="provider-list"
        )


async def test_install_replaces_both_entry_points_on_the_helper_and_given_modules(
    monkeypatch,
):
    import types

    caller = types.ModuleType("caller")
    caller.run_sdk_child = sdk_child.run_sdk_child
    fake = FakeSdkChild()
    fake.respond("describe", {**OK_READ_CALL, "op": "describe"})

    fake.install(monkeypatch, caller)

    assert sdk_child.run_sdk_child_blocking == fake.run_sdk_child_blocking
    assert not hasattr(caller, "run_sdk_child_blocking")
    answer = await caller.run_sdk_child(
        "describe", {}, timeout_seconds=30, job_ref="provider-list"
    )
    assert answer["request_id"] == "provider-list"
    assert len(fake.calls) == 1
