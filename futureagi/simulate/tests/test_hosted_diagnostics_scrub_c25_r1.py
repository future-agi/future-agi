"""Value-free, database-free regression coverage for collected guest diagnostics."""

import ast
import gzip
import inspect
import json
import logging
import subprocess
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from simulate.services import hosted_harness_diagnostics as diagnostics
from tfc.logging import credential_scrub

FAILED = "[hidden: scrub-failed]"
SPELLINGS = (
    "json_unicode",
    "percent",
    "percent_plus",
    "html_decimal",
    "html_hex",
    "python_hex",
    "mixed",
)


def _require(condition, message):
    # Keep failed assertions categorical, even under assertion rewriting.
    if not condition:
        raise AssertionError(message)


def _held_value():
    # synthetic test value
    return "fixture" + " held/" + "Ab7" * 8


def _spelling(value, name):
    encoders = (
        lambda char: "\\u" + format(ord(char), "04x"),
        lambda char: "%" + format(ord(char), "02X"),
        lambda char: "&#" + str(ord(char)) + ";",
        lambda char: "&#x" + format(ord(char), "x") + ";",
        lambda char: "\\x" + format(ord(char), "02x"),
        lambda char: char,
    )
    if name == "raw":
        return value
    if name == "mixed":
        return "".join(
            encoders[index % len(encoders)](c) for index, c in enumerate(value)
        )
    if name == "percent_plus":
        return "".join("+" if c == " " else encoders[1](c) for c in value)
    index = {
        "json_unicode": 0,
        "percent": 1,
        "html_decimal": 2,
        "html_hex": 3,
        "python_hex": 4,
    }[name]
    return "".join(encoders[index](c) for c in value)


class _Attempt(SimpleNamespace):
    def __init__(self):
        super().__init__(
            id="attempt-fixture",
            job_id="job-fixture",
            job=SimpleNamespace(organization_id="org-fixture"),
            attempt_number=1,
            provider_ref="sandbox-fixture",
            snapshot_name="snapshot-fixture",
            diagnostics_object_key="",
            diagnostics_sha256="",
            diagnostics_size=0,
            diagnostics_captured_at=None,
            diagnostics_final=False,
            diagnostics_error="",
            updated_at=None,
            saved=[],
        )

    def save(self, *, update_fields):
        self.saved.append({name: getattr(self, name) for name in update_fields})


@pytest.fixture
def poll(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    def run(entrypoint, process, values=()):
        uploaded = []
        commands = []
        attempt = _Attempt()

        def put_object(**kwargs):
            uploaded.append(kwargs["data"].read())

        def exec_command(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(result=process)

        monkeypatch.setattr(
            diagnostics,
            "get_storage_client",
            lambda: SimpleNamespace(put_object=put_object),
        )
        sandbox = SimpleNamespace(
            fs=SimpleNamespace(download_file=lambda *args: b"command-fixture"),
            process=SimpleNamespace(
                get_session_command=lambda *args, **kwargs: SimpleNamespace(
                    status="completed", exit_code=0
                ),
                get_session_command_logs=lambda *args, **kwargs: SimpleNamespace(
                    output=entrypoint
                ),
                exec=exec_command,
            ),
        )
        capture = None
        exception_messages = []
        try:
            capture = diagnostics.poll_sandbox_diagnostics(
                attempt,
                sandbox,
                session_id="session-fixture",
                final=True,
                secret_values=values,
            )
        except Exception as exc:
            exception_messages.append(str(exc))
        _require(not exception_messages, "poll raised instead of returning normally")
        _require(len(uploaded) == 1, "expected exactly one diagnostics upload")
        body = gzip.decompress(uploaded[0]).decode("utf-8")
        document = json.loads(body)
        sinks = {
            "upload_body": body,
            "uploaded_document": json.dumps(document, ensure_ascii=False),
            "entrypoint_log": document["entrypoint_log"],
            "process_logs": document["process_logs"],
            "returned_capture": repr(asdict(capture)),
            "returned_entrypoint": capture.entrypoint_log,
            "returned_process": capture.process_logs,
            "saved_attempt_fields": repr(attempt.saved),
            "attempt_fields": repr(vars(attempt)),
            "caplog_all_loggers": caplog.text,
            "exception_messages": repr(exception_messages),
        }
        return SimpleNamespace(
            document=document,
            capture=capture,
            attempt=attempt,
            sinks=sinks,
            exception_messages=exception_messages,
            commands=commands,
        )

    return run


def _assert_absent(result, *values):
    for sink_name, text in result.sinks.items():
        for value in values:
            _require(value not in text, "planted material reached " + sink_name)


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_t1_encoded_held_value_is_masked_in_both_tails(poll, spelling):
    value = _held_value()
    encoded = _spelling(value, spelling)
    result = poll("entry " + encoded + "\n", "process " + encoded + "\n", [value])
    _assert_absent(result, value, encoded)
    _require(
        result.capture.entrypoint_log == "entry [REDACTED]\n", "entry mask differs"
    )
    _require(
        result.capture.process_logs == "process [REDACTED]\n", "process mask differs"
    )


@pytest.mark.parametrize("tail", ("entrypoint", "process"))
def test_t2_unheld_provider_pattern_is_masked(poll, tail):
    # synthetic test value
    value = "gh" + "p" + "_" + "Ab7" * 12
    planted = "provider output " + value + "\n"
    result = poll(
        planted if tail == "entrypoint" else "", planted if tail == "process" else ""
    )
    _assert_absent(result, value)
    expected = "provider output [hidden: github-token]\n"
    actual = (
        result.capture.entrypoint_log
        if tail == "entrypoint"
        else result.capture.process_logs
    )
    _require(actual == expected, "provider family marker differs")


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "failure",
    (
        "patterns_raise",
        "known_marker",
        "values_raise",
        "known_raise",
        "patterns_marker",
        "known_none",
        "patterns_none",
        "known_bytes",
        "patterns_bytes",
        "known_subclass",
        "patterns_subclass",
        "legacy_raise",
    ),
)
def test_t3_scrub_failures_close_both_tails_without_raising(poll, monkeypatch, failure):
    raw = "ordinary untrusted guest output\n"

    def broken(*args, **kwargs):
        raise RuntimeError(raw)

    class BrokenValues:
        def __iter__(self):
            raise RuntimeError(raw)

    values = []
    if failure == "values_raise":
        values = BrokenValues()
    elif failure == "legacy_raise":
        monkeypatch.setattr(diagnostics, "_redact", broken)
    else:
        stage, behavior = failure.split("_")
        function = "scrub_text" if stage == "patterns" else "redact_known_values"
        if behavior == "raise":
            replacement = broken
        else:
            result = {
                "marker": FAILED,
                "none": None,
                "bytes": raw.encode(),
                "subclass": _StringSubclass(raw),
            }[behavior]

            def replacement(*args, **kwargs):
                return result

        monkeypatch.setattr(credential_scrub, function, replacement)
    capture = poll(raw, raw, values)
    for name in ("entrypoint_log", "process_logs"):
        _require(capture.document[name] == FAILED, "persisted tail did not fail closed")
        _require(
            getattr(capture.capture, name) == FAILED,
            "returned tail did not fail closed",
        )
    _assert_absent(capture, raw)


def test_t4_clean_ordinary_log_is_byte_identical(poll):
    text = (
        "2026-11-09T10:47:00Z INFO ready /work/authoring/main.py\n"
        "Collecting package-fixture==1.2.3\n"
        "Successfully installed package-fixture-1.2.3\n"
        "revision " + "0123456789abcdef" * 2 + "01234567\n"
        "request 12345678-1234-1234-1234-123456789abc\n"
        "docs https://example.invalid/docs?page=2&sort=asc\n"
    )
    result = poll(text, text)
    for name in ("entrypoint_log", "process_logs"):
        _require(result.document[name] == text, "clean persisted log changed")
        _require(getattr(result.capture, name) == text, "clean returned log changed")


def test_t5_raw_held_value_retains_legacy_marker(poll):
    value = _held_value()
    result = poll("entry " + value, "process " + value, [value])
    _require(
        result.capture.entrypoint_log == "entry [REDACTED]",
        "legacy entry marker changed",
    )
    _require(
        result.capture.process_logs == "process [REDACTED]",
        "legacy process marker changed",
    )
    _require(
        result.document["entrypoint_log"] == result.capture.entrypoint_log,
        "upload differs",
    )
    _require(
        result.document["process_logs"] == result.capture.process_logs, "upload differs"
    )
    _assert_absent(result, value)


@pytest.mark.parametrize("spelling", ("raw", *SPELLINGS))
def test_t6_all_sink_census(poll, spelling):
    value = _held_value()
    planted = _spelling(value, spelling)
    result = poll(planted, planted, [value])
    _assert_absent(result, value, planted)
    _require(not result.exception_messages, "exception messages escaped")
    _assert_raw_text_census()


def _assert_raw_text_census():
    tree = ast.parse(inspect.getsource(diagnostics.poll_sandbox_diagnostics))
    parents = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }
    raw_names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        command_logs = (
            isinstance(call.func, ast.Name) and call.func.id == "_command_logs"
        )
        if command_logs:
            raw_names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    # The process tail is collected via getattr(..., 'result', '') or ''.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for child in ast.walk(node.value):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "getattr"
                and len(child.args) >= 2
                and isinstance(child.args[1], ast.Constant)
                and child.args[1].value == "result"
            ):
                raw_names.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )
    _require(len(raw_names) == 2, "raw tail collection census changed")
    reads = dict.fromkeys(raw_names, 0)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        if node.id not in raw_names:
            continue
        parent = parents[node]
        _require(
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == "_scrub_collected"
            and parent.args[0] is node,
            "raw tail has a read outside the scrub helper",
        )
        reads[node.id] += 1
    _require(
        all(count == 1 for count in reads.values()), "raw tail scrub census changed"
    )


BUILD_LOG = "/work/authoring/generic-harness/process-logs/worlds-w0-agent-a.log"
RUN_LOG_DIR = "/work/diagnostics/process-logs/"


def _worker_log_listing(
    root, build_text, run_text=None, run_name="worlds-w0-agent-b.log"
):
    # What the poll's own listing command prints for a guest tree, read as /work.
    written = {BUILD_LOG: build_text}
    if run_text is not None:
        written[RUN_LOG_DIR + run_name] = run_text
    for path, text in written.items():
        target = root / path.removeprefix("/work/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    listing = subprocess.run(
        ["sh", "-c", diagnostics._process_log_command(str(root))],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.replace(str(root), "/work")
    _require(
        "===== build: " + BUILD_LOG + " =====\n" in listing,
        "the build's log was not listed",
    )
    if run_text is not None:
        _require(
            "===== run: " + RUN_LOG_DIR + run_name + " =====\n" in listing,
            "the run's worker log was not listed",
        )
    return listing


@pytest.mark.parametrize("spelling", ("raw", *SPELLINGS))
def test_a_worker_log_holding_a_run_value_reads_masked_in_every_sink(
    poll, tmp_path, spelling
):
    value = _held_value()
    planted = _spelling(value, spelling)
    listing = _worker_log_listing(tmp_path, "build ready\n", "worker " + planted + "\n")
    result = poll("entry ready\n", listing, [value])
    _require(
        result.commands == [diagnostics._process_log_command()],
        "the poll did not list the run's worker logs",
    )
    _assert_absent(result, value, planted)
    expected = listing.replace(planted, "[REDACTED]")
    run_tail = (
        "===== run: " + RUN_LOG_DIR + "worlds-w0-agent-b.log =====\nworker [REDACTED]\n"
    )
    _require(run_tail in expected, "the worker log is not masked under its run label")
    _require(result.document["process_logs"] == expected, "stored worker log differs")
    _require(result.capture.process_logs == expected, "returned worker log differs")
    _require(result.document["entrypoint_log"] == "entry ready\n", "entry changed")


def test_a_worker_log_named_with_a_run_value_reads_masked_in_its_label(poll, tmp_path):
    # synthetic test value, safe in a file name
    value = "fixture-held-" + "Ab7" * 8
    listing = _worker_log_listing(
        tmp_path, "build ready\n", "worker ready\n", "worker-" + value + ".log"
    )
    result = poll("entry ready\n", listing, [value])
    _assert_absent(result, value)
    expected = listing.replace(value, "[REDACTED]")
    _require(
        "===== run: " + RUN_LOG_DIR + "worker-[REDACTED].log =====\n" in expected,
        "the run label is not masked",
    )
    _require(result.document["process_logs"] == expected, "stored label differs")
    _require(result.capture.process_logs == expected, "returned label differs")


@pytest.mark.parametrize("stage", ("legacy", "known", "patterns"))
def test_a_worker_log_the_scrubber_fails_on_reads_the_hidden_marker(
    poll, monkeypatch, tmp_path, stage
):
    worker_line = "worker output the scrubber cannot read"
    listing = _worker_log_listing(tmp_path, "build ready\n", worker_line + "\n")
    module, name = {
        "legacy": (diagnostics, "_redact"),
        "known": (credential_scrub, "redact_known_values"),
        "patterns": (credential_scrub, "scrub_text"),
    }[stage]
    original = getattr(module, name)

    def failing_on_worker_logs(text, *args, **kwargs):
        if "===== run: " in str(text):
            raise RuntimeError(str(text))
        return original(text, *args, **kwargs)

    monkeypatch.setattr(module, name, failing_on_worker_logs)
    result = poll("entry ready\n", listing)
    for tail in (result.document["process_logs"], result.capture.process_logs):
        _require(tail == FAILED, "worker log tail did not fail closed")
    for tail in (result.document["entrypoint_log"], result.capture.entrypoint_log):
        _require(tail == "entry ready\n", "entry tail changed")
    _assert_absent(result, worker_line, RUN_LOG_DIR)


def test_the_builds_tails_read_the_same_beside_the_runs_worker_logs(poll, tmp_path):
    value = _held_value()
    build_text = "build step ready\nbuild " + value + "\n"
    build_only = _worker_log_listing(tmp_path / "alone", build_text)
    with_run = _worker_log_listing(
        tmp_path / "beside", build_text, "worker " + value + "\n"
    )
    alone = poll("entry " + value + "\n", build_only, [value])
    beside = poll("entry " + value + "\n", with_run, [value])
    build_tail = (
        "===== build: " + BUILD_LOG + " =====\nbuild step ready\nbuild [REDACTED]\n"
    )
    run_tail = (
        "===== run: " + RUN_LOG_DIR + "worlds-w0-agent-b.log =====\nworker [REDACTED]\n"
    )
    for result, process_tail in ((alone, build_tail), (beside, build_tail + run_tail)):
        _require(result.document["process_logs"] == process_tail, "stored tail differs")
        _require(result.capture.process_logs == process_tail, "returned tail differs")
        for tail in (result.document["entrypoint_log"], result.capture.entrypoint_log):
            _require(tail == "entry [REDACTED]\n", "entry tail changed")
        _assert_absent(result, value)


def test_worker_logs_share_the_process_tail_cap_and_keep_the_runs_end(
    poll, monkeypatch, tmp_path
):
    value = _held_value()
    limit = 256
    monkeypatch.setattr(diagnostics, "_PROCESS_LOG_LIMIT_BYTES", limit)
    listing = _worker_log_listing(
        tmp_path, "build line ready\n" * 64, "worker " + value + "\nworker done\n"
    )
    result = poll("entry ready\n", listing, [value])
    truncated = "[earlier output truncated]\n"
    run_tail = (
        "===== run: " + RUN_LOG_DIR + "worlds-w0-agent-b.log =====\n"
        "worker [REDACTED]\nworker done\n"
    )
    for tail in (result.document["process_logs"], result.capture.process_logs):
        _require(tail.startswith(truncated), "worker logs were not capped")
        _require(
            len(tail.encode("utf-8")) == len(truncated) + limit,
            "worker logs exceed the process tail cap",
        )
        _require(tail.endswith(run_tail), "the run's worker log end was not kept")
    _assert_absent(result, value)


def availability_input(size):
    # synthetic test value
    values = [
        "fixture" + " held/" + format(index, "02d") + "Ab7" * 8 for index in range(32)
    ]
    line = "2026-11-09 INFO progress 50% &amp; + \\path /work/build\n"
    planted = "\n".join(_spelling(value, "mixed") for value in values) + "\n"
    text = (line * (size // len(line) + 1))[: size - len(planted)] + planted
    return text, values


def test_t7_one_mib_with_32_held_values_stays_available(poll):
    text, values = availability_input(1024 * 1024)
    result = poll(text, text, values)
    for name in ("entrypoint_log", "process_logs"):
        _require(result.document[name] != FAILED, "ordinary large tail failed closed")
        _require(
            len(result.document[name]) > 900_000, "ordinary large tail was discarded"
        )
    _assert_absent(result, *values)


def many_held_values(count):
    # synthetic test values
    return [
        "fixture" + " held/" + format(index, "03d") + "Ab7" * 8
        for index in range(count)
    ]


def test_more_held_values_than_one_scrub_call_takes_are_all_masked(poll):
    # More values than the known-value scrubber takes in one call.
    values = many_held_values(100)
    planted = [
        _spelling(value, "mixed" if index % 2 else "raw")
        for index, value in enumerate(values)
    ]
    line = "INFO ready /work/build\n"
    text = line + "".join("held " + spelled + "\n" for spelled in planted)
    expected = line + "held [REDACTED]\n" * len(values)
    result = poll(text, text, values)
    for name in ("entrypoint_log", "process_logs"):
        _require(result.document[name] != FAILED, "many held values failed closed")
        _require(result.document[name] == expected, "persisted tail not masked")
        _require(getattr(result.capture, name) == expected, "returned tail not masked")
    _assert_absent(result, *values, *planted)


def test_a_held_value_longer_than_one_scrub_call_takes_closes_both_tails(poll):
    # synthetic test value, longer than the known-value scrubber takes in one call
    value = "fixture" + " held/" + "Ab7" * 22_000
    planted = _spelling(value, "mixed")
    values = [value, *many_held_values(3)]
    result = poll("entry " + planted + "\n", "process " + planted + "\n", values)
    for name in ("entrypoint_log", "process_logs"):
        _require(result.document[name] == FAILED, "persisted tail did not fail closed")
        _require(
            getattr(result.capture, name) == FAILED, "returned tail did not fail closed"
        )
    _assert_absent(result, value, planted)


def _spelled_units(text, spelling):
    # Each character's spelling exactly as it appears inside the spelled whole.
    units, done = [], ""
    for end in range(1, len(text) + 1):
        spelled = _spelling(text[:end], spelling)
        units.append(spelled[len(done) :])
        done = spelled
    return units


def straddling_held_values():
    # synthetic test values: the shorter one starts inside the longer one's
    # occurrence and runs past its end
    head = "first part/" + "Qz5" * 9
    shared = "/both Wy4Wy4"
    tail = "/after " + "Jx8" * 5
    return head + shared, shared + tail, head + shared + tail


@pytest.mark.parametrize("spelling", ("raw", *SPELLINGS))
def test_held_values_whose_occurrences_overlap_across_groups_are_masked_as_one(
    poll, spelling
):
    longer, shorter, joined = straddling_held_values()
    # More values than one scrub call takes. The longer value sorts first and the
    # shorter one last, so they are scrubbed in different groups.
    values = [longer, *many_held_values(64), shorter]
    units = _spelled_units(joined, spelling)
    line = "INFO ready /work/build\n"
    text = line + "held " + "".join(units) + " done\n"
    expected = line + "held [REDACTED] done\n"
    result = poll(text, text, values)
    for name in ("entrypoint_log", "process_logs"):
        _require(result.document[name] != FAILED, "overlapping values failed closed")
        _require(result.document[name] == expected, "persisted overlap not masked")
        _require(
            getattr(result.capture, name) == expected, "returned overlap not masked"
        )
    # No run of 8 or more characters of either value, raw or as spelled, survives.
    runs = [joined[index : index + 8] for index in range(len(joined) - 7)]
    spelled = ["".join(units[index : index + 8]) for index in range(len(units) - 7)]
    _assert_absent(result, longer, shorter, *runs, *spelled)


def test_more_held_values_than_the_diagnostics_take_hide_both_tails_whole(poll):
    # One more held value than the diagnostics scrub; the text is hidden whole.
    values = many_held_values(4096 + 1)
    planted = [values[0], _spelling(values[1], "mixed")]
    text = "".join("held " + spelled + "\n" for spelled in planted)
    result = poll(text, text, values)
    for name in ("entrypoint_log", "process_logs"):
        _require(result.document[name] == FAILED, "persisted tail was scrubbed")
        _require(getattr(result.capture, name) == FAILED, "returned tail was scrubbed")
    _assert_absent(result, *values, *planted)
