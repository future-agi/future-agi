"""A candidate's changed files, counted once when it is stored, so reading an epoch never
reads its patch. Candidates stored before this are counted here from their patch. One
whose patch cannot be read or counted keeps none and reads with no change line. When
storage does not answer, the rest are left as they are, rather than waiting on it."""

import logging
import os
import subprocess
import tempfile

from django.db import migrations, models
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

logger = logging.getLogger(__name__)

# Seconds to wait on storage, and how often to try again, before giving up.
CONNECT_S, READ_S, RETRIES = 5, 30, 1


def _numstat(patch: bytes) -> list[list]:
    """A frozen copy of simulate.services.agent_fix_engine.patch_files."""
    if not patch.strip():
        return []
    with tempfile.TemporaryDirectory(prefix="agent-fix-numstat-") as cwd:
        counted = subprocess.run(
            ["git", "-c", "core.quotePath=false", "apply", "--numstat", "-z"],
            cwd=cwd,
            input=patch,
            env=dict(os.environ),
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    fields = counted.split(b"\0")
    files, at = [], 0
    while at < len(fields):
        entry = fields[at]
        at += 1
        if not entry:
            continue
        added, removed, path = entry.split(b"\t", 2)
        if not path:
            path, at = fields[at + 1], at + 2
        files.append(
            [
                path.decode("utf-8", "replace"),
                int(added) if added.isdigit() else 0,
                int(removed) if removed.isdigit() else 0,
            ]
        )
    return files


def _read(client, bucket: str, key: str) -> bytes:
    response = client.get_object(bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def count_changed_files(apps, schema_editor):
    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage_client import bounded_storage_client

    Candidate = apps.get_model("simulate", "AgentFixCandidate")
    pending = Candidate.objects.filter(changed_files__isnull=True).exclude(
        patch_object_key=""
    )
    if not pending.exists():
        return
    client = bounded_storage_client(connect=CONNECT_S, read=READ_S, retries=RETRIES)
    counted = missed = 0
    for candidate in pending.only("id", "patch_object_key").iterator(chunk_size=200):
        try:
            patch = _read(client, UPLOAD_BUCKET_NAME, candidate.patch_object_key)
        except S3Error as exc:
            if exc.code != "NoSuchKey":
                logger.warning("agent_fix_backfill_storage_refused: %s", exc.code)
                break
            missed += 1
            continue
        except (MinioException, HTTPError, OSError) as exc:
            # Storage is down or unreachable: every other read would wait the same.
            logger.warning("agent_fix_backfill_storage_down: %s", type(exc).__name__)
            break
        try:
            files = _numstat(patch)
        except (subprocess.SubprocessError, ValueError) as exc:
            logger.warning(
                "agent_fix_backfill_not_counted: %s %s",
                candidate.id,
                type(exc).__name__,
            )
            missed += 1
            continue
        Candidate.objects.filter(id=candidate.id).update(changed_files=files)
        counted += 1
    logger.info(
        "agent_fix_backfill_changed_files: %s counted, %s missed", counted, missed
    )


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0116_agent_fix_issue_job"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentfixcandidate",
            name="changed_files",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(count_changed_files, migrations.RunPython.noop),
    ]
