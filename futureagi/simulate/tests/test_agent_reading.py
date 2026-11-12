import copy
import json
from types import SimpleNamespace

import pytest

from simulate.services.agent_reading import (
    AGENT_FILE,
    AGENTS_FILE,
    CONTRACT_FILE,
    CONTRACT_MAX_BYTES,
    FAILURE_FILE,
    ReadingResultInvalid,
    collect_reading,
    collect_scan,
)
from simulate.services.hosted_sandbox import (
    SandboxFileNotFoundError,
    SandboxProviderError,
)

SHA = "a" * 40
CHUNK = 64 * 1024
SUPPORT = {
    "name": "support_agent",
    "subfolder": "agents/support",
    "entry": "agents/support/main.py",
    "framework": {"name": "langgraph", "version": "0.2.60", "label": None},
    "confidence": "high",
    "evidence": ["langgraph.json -> support"],
    "env_names": ["OPENAI_API_KEY", "DB_URL"],
    "estimates": {"tools": 3, "prompt_files": 1},
}
FOUND = {
    "schema_version": "futureagi.agent-scan.v1",
    "status": "found",
    "commit_sha": SHA,
    "agents": [SUPPORT],
    "truncated": False,
    "error": None,
}
REFUSAL = {
    "domain": "agent",
    "stage": "understanding_agent",
    "code": "agent_type_unsupported",
    "message": "This agent drives a browser.",
    "retryable": False,
    "details": {},
}


def _sandbox(files, read=None):
    read = read if read is not None else []

    def download_file(path, _timeout=None):
        if path not in files:
            raise SandboxFileNotFoundError(path)
        body = files[path]
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, bytes) else json.dumps(body).encode()

    def download_file_stream(path, _timeout=None):
        body = download_file(path)
        for start in range(0, len(body), CHUNK):
            read.append(path)
            yield body[start : start + CHUNK]

    return SimpleNamespace(
        fs=SimpleNamespace(
            download_file=download_file, download_file_stream=download_file_stream
        )
    )


def _scan(**changes):
    scan = copy.deepcopy(FOUND)
    scan.update(changes)
    return scan


def _agent(**changes):
    agent = copy.deepcopy(SUPPORT)
    agent.update(changes)
    return _scan(agents=[agent])


def test_a_found_scan_is_returned_as_written():
    result = collect_scan(_sandbox({AGENTS_FILE: FOUND}))

    assert result.failure is None
    assert result.scan["commit_sha"] == SHA
    assert result.scan["agents"][0]["subfolder"] == "agents/support"
    assert result.scan["agents"][0]["framework"]["name"] == "langgraph"


@pytest.mark.parametrize(
    "scan",
    [
        _scan(status="none", agents=[]),
        _scan(
            status="error",
            agents=[],
            error={"code": "scan_limit_reached", "message": "Too many files."},
        ),
        _scan(commit_sha=None, agents=[{**SUPPORT, "subfolder": ""}]),
        _agent(framework={"name": "other", "version": None, "label": "camel"}),
    ],
    ids=["none", "error", "upload", "other_framework"],
)
def test_every_agreed_shape_is_accepted(scan):
    assert collect_scan(_sandbox({AGENTS_FILE: scan})).scan is not None


@pytest.mark.parametrize(
    "scan",
    [
        _scan(schema_version="futureagi.agent-scan.v2"),
        _scan(status="found", agents=[]),
        _scan(status="none"),
        _scan(status="error", error=None, agents=[]),
        _scan(commit_sha="main"),
        _scan(agents=[SUPPORT] * 51),
        _agent(subfolder="/agents/support"),
        _agent(subfolder="agents/../secrets"),
        _agent(entry="../main.py"),
        _agent(framework={"name": "raw", "version": None, "label": None}),
        _agent(framework={"name": "other", "version": None, "label": None}),
        _agent(framework={"name": "langgraph", "version": None, "label": "camel"}),
        _agent(confidence="certain"),
        _agent(evidence=["x"] * 6),
        _agent(evidence=["x" * 201]),
        _agent(env_names=["OPENAI_API_KEY=sk-123"]),
        _agent(estimates={"tools": -1, "prompt_files": 0}),
    ],
    ids=[
        "unknown_schema",
        "found_without_agents",
        "none_with_agents",
        "error_without_error",
        "branch_not_commit",
        "over_the_cap",
        "absolute_folder",
        "folder_escapes",
        "entry_escapes",
        "raw_not_none",
        "other_without_label",
        "label_without_other",
        "unknown_confidence",
        "too_much_evidence",
        "evidence_too_long",
        "env_value_not_name",
        "negative_estimate",
    ],
)
def test_a_scan_outside_the_format_is_a_retryable_failure(scan):
    with pytest.raises(ReadingResultInvalid) as raised:
        collect_scan(_sandbox({AGENTS_FILE: scan}))

    assert raised.value.code == "reading_file_invalid"


def test_a_failure_file_wins_over_the_result_files():
    files = {FAILURE_FILE: REFUSAL, AGENTS_FILE: FOUND}

    scan = collect_scan(_sandbox(files))
    reading = collect_reading(_sandbox(files))

    assert scan.scan is None and scan.failure["code"] == "agent_type_unsupported"
    assert reading.agent is None and reading.failure["retryable"] is False


def test_a_malformed_failure_file_is_not_read_as_success():
    with pytest.raises(ReadingResultInvalid) as raised:
        collect_reading(
            _sandbox({FAILURE_FILE: {"code": "x"}, CONTRACT_FILE: {}, AGENT_FILE: {}})
        )

    assert raised.value.path == FAILURE_FILE


def test_a_reading_returns_the_raw_contract_and_the_agent_document():
    contract = {"base_environment": {"rows": [1, 2]}, "secret_like": "kept"}
    agent = {"schema_version": "1.0", "extension": {"framework": {"name": "none"}}}

    reading = collect_reading(_sandbox({CONTRACT_FILE: contract, AGENT_FILE: agent}))

    assert reading.failure is None
    assert reading.contract == contract
    assert reading.agent == agent


@pytest.mark.parametrize(
    ("files", "code", "path"),
    [
        ({AGENT_FILE: {}}, "reading_file_missing", CONTRACT_FILE),
        ({CONTRACT_FILE: {}}, "reading_file_missing", AGENT_FILE),
        ({CONTRACT_FILE: b"", AGENT_FILE: {}}, "reading_file_missing", CONTRACT_FILE),
        (
            {CONTRACT_FILE: b"[1, 2]", AGENT_FILE: {}},
            "reading_file_invalid",
            CONTRACT_FILE,
        ),
        (
            {CONTRACT_FILE: b"not json", AGENT_FILE: {}},
            "reading_file_invalid",
            CONTRACT_FILE,
        ),
        (
            {CONTRACT_FILE: b" " * (CONTRACT_MAX_BYTES + 1), AGENT_FILE: {}},
            "reading_file_too_large",
            CONTRACT_FILE,
        ),
    ],
    ids=["no_contract", "no_agent", "empty", "not_an_object", "not_json", "too_large"],
)
def test_missing_or_broken_reading_files_are_retryable_failures(files, code, path):
    with pytest.raises(ReadingResultInvalid) as raised:
        collect_reading(_sandbox(files))

    assert (raised.value.code, raised.value.path) == (code, path)


def test_a_scan_with_no_agent_list_is_a_retryable_failure():
    with pytest.raises(ReadingResultInvalid) as raised:
        collect_scan(_sandbox({}))

    assert (raised.value.code, raised.value.path) == (
        "reading_file_missing",
        AGENTS_FILE,
    )


def test_an_oversized_file_is_refused_before_it_is_read_in_full():
    read = []
    oversized = b" " * (CONTRACT_MAX_BYTES * 4)

    with pytest.raises(ReadingResultInvalid) as raised:
        collect_reading(_sandbox({CONTRACT_FILE: oversized, AGENT_FILE: {}}, read))

    assert raised.value.code == "reading_file_too_large"
    assert len(read) * CHUNK <= CONTRACT_MAX_BYTES + CHUNK


@pytest.mark.parametrize(
    ("files", "path"),
    [
        ({FAILURE_FILE: SandboxProviderError("timed out")}, FAILURE_FILE),
        ({AGENTS_FILE: SandboxProviderError("timed out")}, AGENTS_FILE),
    ],
    ids=["failure_file", "result_file"],
)
def test_a_read_that_times_out_is_unreadable_not_invalid(files, path):
    with pytest.raises(ReadingResultInvalid) as raised:
        collect_scan(_sandbox(files))

    assert (raised.value.code, raised.value.path) == ("reading_file_unreadable", path)
