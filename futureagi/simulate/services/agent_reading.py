import json
from dataclasses import dataclass
from typing import Any

import structlog

from simulate.serializers.agent_reading import AgentScanSerializer
from simulate.services.agent_setup import AGENT_MAX_BYTES
from simulate.services.hosted_harness_gateway import (
    FAILURE_FILE_UNREADABLE,
    read_failure_file,
    refusal_failure,
)
from simulate.services.hosted_sandbox import (
    SandboxFileNotFoundError,
    SandboxNotFoundError,
    SandboxProviderError,
)

logger = structlog.get_logger(__name__)

READING_DIR = "/work/reading"
AGENTS_FILE = f"{READING_DIR}/agents.json"
CONTRACT_FILE = f"{READING_DIR}/contract.json"
AGENT_FILE = f"{READING_DIR}/agent.json"
FAILURE_FILE = f"{READING_DIR}/failure.json"
AGENTS_MAX_BYTES = 1_048_576
CONTRACT_MAX_BYTES = 5 * 1_048_576
_READ_TIMEOUT_SECONDS = 30


class ReadingResultInvalid(Exception):
    """The sandbox exited without the agreed result files; the reading can be retried."""

    def __init__(self, code: str, path: str):
        super().__init__(f"{code}: {path}")
        self.code = code
        self.path = path


@dataclass(frozen=True)
class AgentScan:
    failure: dict[str, Any] | None
    scan: dict[str, Any] | None


@dataclass(frozen=True)
class AgentReading:
    failure: dict[str, Any] | None
    contract: dict[str, Any] | None
    agent: dict[str, Any] | None


def collect_scan(sandbox) -> AgentScan:
    """What "find the agents" left in the sandbox: its failure, else its agent list."""
    failure = _failure(sandbox)
    if failure is not None:
        return AgentScan(failure=failure, scan=None)
    serializer = AgentScanSerializer(
        data=_json_object(sandbox, AGENTS_FILE, AGENTS_MAX_BYTES)
    )
    if not serializer.is_valid():
        logger.warning(
            "agent_scan_invalid", path=AGENTS_FILE, errors=str(serializer.errors)[:2000]
        )
        raise ReadingResultInvalid("reading_file_invalid", AGENTS_FILE)
    return AgentScan(failure=None, scan=serializer.validated_data)


def collect_reading(sandbox) -> AgentReading:
    """What "read an agent" left in the sandbox: its failure, else the raw contract and
    the agent document. The document is checked when it is stored."""
    failure = _failure(sandbox)
    if failure is not None:
        return AgentReading(failure=failure, contract=None, agent=None)
    return AgentReading(
        failure=None,
        contract=_json_object(sandbox, CONTRACT_FILE, CONTRACT_MAX_BYTES),
        agent=_json_object(sandbox, AGENT_FILE, AGENT_MAX_BYTES),
    )


def _failure(sandbox) -> dict[str, Any] | None:
    found = read_failure_file(sandbox, FAILURE_FILE)
    if found is None:
        return None
    if found == FAILURE_FILE_UNREADABLE:
        raise ReadingResultInvalid("reading_file_unreadable", FAILURE_FILE)
    failure = refusal_failure({"refusal_file": found})
    if failure is None:
        raise ReadingResultInvalid("reading_file_invalid", FAILURE_FILE)
    return failure


def _download(sandbox, path: str, max_bytes: int) -> bytes:
    """The file's bytes, read in chunks so an oversized file is refused before it
    is held in memory."""
    body = bytearray()
    try:
        for chunk in sandbox.fs.download_file_stream(path, _READ_TIMEOUT_SECONDS):
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ReadingResultInvalid("reading_file_too_large", path)
    except (SandboxNotFoundError, SandboxFileNotFoundError, FileNotFoundError) as error:
        raise ReadingResultInvalid("reading_file_missing", path) from error
    except SandboxProviderError as error:
        raise ReadingResultInvalid("reading_file_unreadable", path) from error
    return bytes(body)


def _json_object(sandbox, path: str, max_bytes: int) -> dict[str, Any]:
    body = _download(sandbox, path, max_bytes)
    if not body:
        raise ReadingResultInvalid("reading_file_missing", path)
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ReadingResultInvalid("reading_file_invalid", path) from error
    if not isinstance(value, dict):
        raise ReadingResultInvalid("reading_file_invalid", path)
    return value
