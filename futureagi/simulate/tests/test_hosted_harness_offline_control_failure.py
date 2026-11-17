"""A sandbox control request that fails answers the guest with a fixed message.

The guest asks the platform through its control mailbox; the answer is a file in
the guest's own sandbox. When serving a request fails on an error, the answer
keeps the code "offline_control_failed" and says "sandbox control request
failed"; the error itself goes to the server log. The harness's own coded
refusals keep their own code and message.
"""

import json
from types import SimpleNamespace

from simulate.services.hosted_harness_gateway import HostedHarnessGateway

REQUEST_PATH = "/work/outbound-spool/control/one.request.json"
RESPONSE_PATH = "/work/outbound-spool/control/one.response.json"
# Made-up error text, worded as a database or client error might be.
ERROR_TEXT = "connect to https://store.example.test:5432 failed for key sk-example-0000"
ERROR_PARTS = ("store.example.test", "https://", "sk-example")


def _sandbox_with_request(operation, uploads):
    request = {
        "job_id": "job-1",
        "attempt_id": "attempt-1",
        "attempt_number": 1,
        "payload": {"operation": operation},
    }

    def download(path, timeout=None):
        if path == REQUEST_PATH:
            return json.dumps(request).encode()
        raise FileNotFoundError(path)

    return SimpleNamespace(
        process=SimpleNamespace(
            exec=lambda *args, **kwargs: SimpleNamespace(
                exit_code=0, result=REQUEST_PATH
            )
        ),
        fs=SimpleNamespace(
            download_file=download,
            upload_file=lambda body, path: uploads.__setitem__(path, body),
        ),
    )


ATTEMPT = SimpleNamespace(id="attempt-1", job_id="job-1", attempt_number=1)


def test_a_failed_control_request_answers_the_fixed_message(monkeypatch):
    def fail(actual_attempt, payload):
        raise RuntimeError(ERROR_TEXT)

    monkeypatch.setattr("simulate.services.hosted_harness.provision_scenarios", fail)
    uploads = {}

    HostedHarnessGateway._sync_offline_control(
        ATTEMPT, _sandbox_with_request("provision", uploads)
    )

    answer = uploads[RESPONSE_PATH].decode("utf-8")
    assert json.loads(answer) == {
        "error": {
            "code": "offline_control_failed",
            "message": "sandbox control request failed",
        }
    }
    for part in ERROR_PARTS:
        assert part not in answer


def test_the_harness_refusal_keeps_its_own_code_and_message():
    uploads = {}

    HostedHarnessGateway._sync_offline_control(
        ATTEMPT, _sandbox_with_request("rewind", uploads)
    )

    assert json.loads(uploads[RESPONSE_PATH]) == {
        "error": {
            "code": "offline_control_operation_unknown",
            "message": "sandbox control operation is not supported",
        }
    }
