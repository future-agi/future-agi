"""The validation sandbox around probe.py: process cap placement and exit mapping.

RLIMIT_NPROC counts every process the caller's UID owns, so the cap has to be
set inside bubblewrap, after the namespaces exist. A cap on bwrap itself made a
non-root worker running 16 or more processes fail to create the namespace, and
that setup failure was recorded as a permanent rejection of valid bytes.

Only ``REJECTED_EXIT`` from probe.py is a verdict on the input. Every other
nonzero exit is the sandbox failing (503 ``validation_unavailable``), and it
leaves the upload VALIDATING with its reservation, to be retried.

The last tests run the real sandbox. They skip where bubblewrap cannot run
(no binary, root, user namespaces blocked) unless ``FI_REQUIRE_SANDBOX=1``,
which turns the skip into a failure for the environments that must prove it.
"""

import json
import os
import stat
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tracer.models.attachment import (
    AttachmentBlob,
    AttachmentBudget,
    AttachmentOutbox,
    AttachmentState,
    AttachmentUpload,
    UploadReservation,
)
from tracer.services.attachments import uploads, validation
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.probe import REJECTED_EXIT, probe
from tracer.tests import test_attachment_core as core
from tracer.tests.test_attachment_core import ImmutableStore, initialized

setup = core.setup

pytestmark = [pytest.mark.unit]

FIXTURES = Path(__file__).parent / "fixtures" / "attachments"
OGG_FACTS = (
    b'{"mime_type":"audio/ogg","sample_rate":48000,"channels":1,"frame_count":9600}'
)


def fake_run(returncode, output=OGG_FACTS, calls=None):
    def run(command, *args, stdout, **kwargs):
        if calls is not None:
            calls.append(command)
        stdout.write(output)
        return SimpleNamespace(returncode=returncode)

    return run


def test_process_cap_is_set_inside_the_sandbox(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(validation.subprocess, "run", fake_run(0, calls=calls))
    assert validation.sandbox_probe(tmp_path / "input")["mime_type"] == "audio/ogg"
    (command,) = calls
    bwrap = command.index("/usr/bin/bwrap")
    # Nothing that counts the worker UID's processes may wrap bwrap itself.
    assert not any(part.startswith("--nproc") for part in command[:bwrap])
    inner = command[bwrap:]
    cap = inner.index(f"--nproc={validation.SANDBOX_NPROC}")
    assert inner[cap - 1] == "/usr/bin/prlimit"
    assert inner[cap + 1 : cap + 3] == ["--", "/usr/bin/python3"]
    assert inner[-2:] == ["/probe.py", "/input"]


def test_only_the_probe_rejection_exit_rejects(monkeypatch, tmp_path):
    monkeypatch.setattr(validation.subprocess, "run", fake_run(REJECTED_EXIT, b""))
    with pytest.raises(AttachmentError) as exc:
        validation.sandbox_probe(tmp_path / "input")
    assert (exc.value.code, exc.value.status) == ("validation_rejected", 422)


@pytest.mark.parametrize(
    "returncode",
    [
        pytest.param(1, id="bwrap-or-prlimit-setup"),
        pytest.param(2, id="interpreter-cannot-open-script"),
        pytest.param(128 + 9, id="sigkill-via-bwrap"),
        pytest.param(128 + 24, id="sigxcpu-via-bwrap"),
        pytest.param(-9, id="sigkill-direct"),
        pytest.param(255, id="other"),
    ],
)
def test_sandbox_failure_is_unavailable_not_rejected(monkeypatch, tmp_path, returncode):
    monkeypatch.setattr(validation.subprocess, "run", fake_run(returncode, b""))
    with pytest.raises(AttachmentError) as exc:
        validation.sandbox_probe(tmp_path / "input")
    assert (exc.value.code, exc.value.status) == ("validation_unavailable", 503)


def test_probe_entrypoint_rejects_with_its_own_exit(tmp_path):
    bad = tmp_path / "input"
    bad.write_bytes((FIXTURES / "tone.ogg").read_bytes()[:-8])
    script = Path(validation.__file__).with_name("probe.py")
    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(script), str(bad)],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == REJECTED_EXIT and completed.stdout == b""


def _validating(setup):
    upload, token, _, _ = initialized(setup)
    store = ImmutableStore()
    uploads.transfer(
        token,
        scope_id=upload.scope_id,
        upload_id=upload.id,
        chunks=[core.wav_bytes()],
        storage=store,
    )
    uploads.complete(token, scope_id=upload.scope_id, upload_id=upload.id)
    return upload, store


def _validation_jobs():
    return sorted(AttachmentBudget.objects.values_list("key", "validation_jobs"))


@pytest.mark.django_db
def test_unavailable_sandbox_leaves_the_upload_retryable(setup, monkeypatch):
    upload, store = _validating(setup)
    jobs = _validation_jobs()
    monkeypatch.setattr(validation.subprocess, "run", fake_run(1, b""))
    with pytest.raises(AttachmentError, match="validation_unavailable"):
        validation.validate(upload.id, scope_id=upload.scope_id, storage=store)
    assert (
        AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.VALIDATING
    )
    assert UploadReservation.objects.get(upload_id=upload.id).settled_at is None
    assert _validation_jobs() == jobs
    assert AttachmentOutbox.objects.filter(
        upload_id=upload.id, event="validate", processed_at=None
    ).exists()
    assert not AttachmentOutbox.objects.filter(
        upload_id=upload.id, event="settled"
    ).exists()
    assert store.promotions == [] and not AttachmentBlob.objects.exists()

    # The retry, once the sandbox works, publishes the same bytes.
    monkeypatch.setattr(validation, "sandbox_probe", probe)
    blob = validation.validate(upload.id, scope_id=upload.scope_id, storage=store)
    assert blob.mime_type == "audio/wav"
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.READY
    assert UploadReservation.objects.get(upload_id=upload.id).settled_at is not None


@pytest.mark.django_db
def test_probe_rejection_still_settles_rejected(setup, monkeypatch):
    upload, store = _validating(setup)
    monkeypatch.setattr(validation.subprocess, "run", fake_run(REJECTED_EXIT, b""))
    with pytest.raises(AttachmentError, match="validation_rejected"):
        validation.validate(upload.id, scope_id=upload.scope_id, storage=store)
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.REJECTED
    assert UploadReservation.objects.get(upload_id=upload.id).settled_at is not None


@pytest.mark.parametrize(
    "facts",
    [
        pytest.param({"mime_type": "text/html"}, id="unvalidated-type"),
        pytest.param({"mime_type": "image/svg+xml"}, id="active-image-type"),
        pytest.param({"sample_rate": -1}, id="negative-fact"),
        pytest.param({"channels": "1"}, id="non-integer-fact"),
        pytest.param({"extra": 1}, id="extra-key"),
    ],
)
def test_sandbox_output_outside_the_allowlist_is_unavailable(
    monkeypatch, tmp_path, facts
):
    """probe.py output is trusted only for validated types and integer facts.

    A sandbox that prints anything else has failed (503), it has not accepted
    the bytes, and nothing it printed reaches a blob.
    """
    output = {**json.loads(OGG_FACTS), **facts}
    monkeypatch.setattr(
        validation.subprocess, "run", fake_run(0, json.dumps(output).encode())
    )
    with pytest.raises(AttachmentError) as exc:
        validation.sandbox_probe(tmp_path / "input")
    assert (exc.value.code, exc.value.status) == ("validation_unavailable", 503)


# --- the outbox validate consumer ------------------------------------------


def _validate_event(upload):
    return AttachmentOutbox.objects.get(upload_id=upload.id, event="validate")


def _make_due(upload):
    AttachmentOutbox.objects.filter(upload_id=upload.id, event="validate").update(
        available_at=timezone.now()
    )


def _unavailable(monkeypatch):
    calls = []
    monkeypatch.setattr(validation.subprocess, "run", fake_run(1, b"", calls=calls))
    return calls


@pytest.mark.django_db
def test_consumer_retries_unavailable_then_settles_failed(setup, monkeypatch):
    upload, store = _validating(setup)
    jobs = _validation_jobs()
    calls = _unavailable(monkeypatch)
    for attempt in range(1, validation.MAX_VALIDATION_ATTEMPTS):
        before = timezone.now()
        assert not validation.consume_validation(
            _validate_event(upload).pk, storage=store
        )
        after = timezone.now()
        event = _validate_event(upload)
        assert event.attempts == attempt and event.processed_at is None
        # Backed off exponentially: not due again until later.
        delay = timedelta(
            seconds=min(
                validation.VALIDATION_BACKOFF_SECONDS * 2 ** (attempt - 1),
                validation.VALIDATION_MAX_BACKOFF_SECONDS,
            )
        )
        assert before + delay <= event.available_at <= after + delay
        assert not validation.consume_validation(event.pk, storage=store)
        assert _validate_event(upload).attempts == attempt
        assert (
            AttachmentUpload.objects.get(pk=upload.id).state
            == AttachmentState.VALIDATING
        )
        assert UploadReservation.objects.get(upload_id=upload.id).settled_at is None
        assert not AttachmentOutbox.objects.filter(
            upload_id=upload.id, event="erase"
        ).exists()
        _make_due(upload)
    assert len(calls) == validation.MAX_VALIDATION_ATTEMPTS - 1

    assert validation.consume_validation(_validate_event(upload).pk, storage=store)
    event = _validate_event(upload)
    assert event.attempts == validation.MAX_VALIDATION_ATTEMPTS
    assert event.processed_at is not None
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.FAILED
    assert UploadReservation.objects.get(upload_id=upload.id).settled_at is not None
    assert _validation_jobs() == [(key, jobs_ - 1) for key, jobs_ in jobs]
    assert AttachmentOutbox.objects.filter(upload_id=upload.id, event="erase").exists()
    assert store.promotions == [] and not AttachmentBlob.objects.exists()

    # A finished job is never run again.
    _make_due(upload)
    assert not validation.consume_validation(event.pk, storage=store)
    assert len(calls) == validation.MAX_VALIDATION_ATTEMPTS


@pytest.mark.django_db
def test_attempts_of_dead_workers_count(setup, monkeypatch):
    """Claims that never reported back still use up attempts."""
    upload, store = _validating(setup)
    event = _validate_event(upload)
    for _ in range(validation.MAX_VALIDATION_ATTEMPTS):
        claimed, exhausted = validation._claim(event.pk)
        assert claimed is not None and not exhausted
        _make_due(upload)
    calls = _unavailable(monkeypatch)
    assert validation.consume_validation(event.pk, storage=store)
    assert calls == []
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.FAILED
    assert _validate_event(upload).processed_at is not None


@pytest.mark.django_db
def test_consumer_publishes_and_finishes(setup, monkeypatch):
    upload, store = _validating(setup)
    _unavailable(monkeypatch)
    assert not validation.consume_validation(_validate_event(upload).pk, storage=store)
    _make_due(upload)
    monkeypatch.setattr(validation, "sandbox_probe", probe)
    assert validation.consume_validation(_validate_event(upload).pk, storage=store)
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.READY
    event = _validate_event(upload)
    assert event.processed_at is not None and event.attempts == 2


@pytest.mark.django_db
def test_consumer_finishes_rejected_bytes(setup, monkeypatch):
    upload, store = _validating(setup)
    monkeypatch.setattr(validation.subprocess, "run", fake_run(REJECTED_EXIT, b""))
    assert validation.consume_validation(_validate_event(upload).pk, storage=store)
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.REJECTED
    assert _validate_event(upload).processed_at is not None


@pytest.mark.django_db
def test_drain_takes_only_due_jobs(setup, monkeypatch):
    upload, store = _validating(setup)
    AttachmentOutbox.objects.filter(upload_id=upload.id, event="validate").update(
        available_at=timezone.now() + timedelta(minutes=5)
    )
    calls = _unavailable(monkeypatch)
    assert validation.drain_validations(storage=store) == 0
    assert calls == [] and _validate_event(upload).attempts == 0
    _make_due(upload)
    monkeypatch.setattr(validation, "sandbox_probe", probe)
    assert validation.drain_validations(storage=store) == 1
    assert AttachmentUpload.objects.get(pk=upload.id).state == AttachmentState.READY


# --- the real sandbox -------------------------------------------------------


def _sandbox_unusable():
    if not sys.platform.startswith("linux"):
        return "not Linux"
    if os.geteuid() == 0:
        return "root ignores RLIMIT_NPROC; run as a non-root UID"
    for binary in ("/usr/bin/bwrap", "/usr/bin/prlimit", "/usr/bin/python3"):
        if not os.path.exists(binary):
            return f"{binary} missing"
    trial = subprocess.run(
        [
            "/usr/bin/bwrap",
            "--unshare-all",
            "--die-with-parent",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
            *(["--ro-bind", "/lib64", "/lib64"] if os.path.exists("/lib64") else []),
            "/usr/bin/true",
        ],
        capture_output=True,
        check=False,
    )
    if trial.returncode != 0:
        return "bubblewrap cannot create namespaces here"
    return None


@pytest.fixture
def real_sandbox():
    reason = _sandbox_unusable()
    if reason:
        if os.environ.get("FI_REQUIRE_SANDBOX") == "1":
            pytest.fail(f"FI_REQUIRE_SANDBOX=1 but {reason}")
        pytest.skip(reason)


@pytest.fixture
def busy_uid():
    """The worker UID owns well over SANDBOX_NPROC processes, as in production."""
    children = [
        subprocess.Popen(["sleep", "300"]) for _ in range(validation.SANDBOX_NPROC + 8)
    ]
    try:
        yield
    finally:
        for child in children:
            child.kill()
        for child in children:
            child.wait()


@pytest.mark.usefixtures("real_sandbox", "busy_uid")
def test_real_sandbox_validates_for_a_busy_non_root_uid(tmp_path):
    good = FIXTURES / "tone.ogg"
    assert validation.sandbox_probe(good) == probe(good)
    bad = tmp_path / "input"
    bad.write_bytes(good.read_bytes()[:-8])
    with pytest.raises(AttachmentError, match="validation_rejected"):
        validation.sandbox_probe(bad)


@pytest.mark.usefixtures("real_sandbox", "busy_uid")
def test_real_sandbox_still_caps_processes(monkeypatch, tmp_path):
    calls = []
    real_run = subprocess.run

    def recording(command, *args, **kwargs):
        calls.append(command)
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(validation.subprocess, "run", recording)
    validation.sandbox_probe(FIXTURES / "tone.ogg")
    (command,) = calls
    python = command.index("/usr/bin/python3", command.index("/usr/bin/bwrap"))
    forker = (
        "import os,time\n"
        "n=0\n"
        "try:\n"
        "    for _ in range(64):\n"
        "        if os.fork()==0:\n"
        "            time.sleep(3); os._exit(0)\n"
        "        n+=1\n"
        "except OSError:\n"
        "    print(n); raise SystemExit(3)\n"
        "print(n)\n"
    )
    inside = real_run(
        command[:python] + ["/usr/bin/python3", "-I", "-S", "-c", forker],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert inside.returncode == 3, inside
    assert int(inside.stdout) < validation.SANDBOX_NPROC

    # Precondition: with the cap on bwrap (the old placement) this busy UID
    # cannot even create the namespace. Setuid bwrap would be exempt.
    if not os.stat("/usr/bin/bwrap").st_mode & stat.S_ISUID:
        bwrap = command.index("/usr/bin/bwrap")
        old = command[: bwrap - 1] + [f"--nproc={validation.SANDBOX_NPROC}", "--"]
        old += command[bwrap:]
        assert real_run(old, capture_output=True, check=False).returncode != 0
