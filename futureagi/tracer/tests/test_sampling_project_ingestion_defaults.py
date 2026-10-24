"""Collector inserts must remain compatible with additive project fields."""

import re
import uuid
from pathlib import Path

import pytest
from django.db import connection

from tracer.models.project import Project

pytestmark = pytest.mark.django_db


def collector_insert(cursor, project, name):
    # Execute the collector's actual insert against the Django-migrated schema,
    # not a second fixture schema that can miss newly required columns.
    source = (
        Path(__file__).resolve().parents[3] / "fi-collector/pkg/auth/pgresolver.go"
    ).read_text()
    match = re.search(r"const insertQ = `(.*?)`", source, re.DOTALL)
    assert match is not None
    sql = re.sub(r"\$[1-5]\b", "%s", match.group(1))
    cursor.execute(
        sql,
        [
            uuid.uuid4(),
            name,
            project.organization_id,
            project.workspace_id,
            "observe",
        ],
    )
    return cursor.fetchone()[0]


def test_collector_project_insert_uses_database_default(project):
    with connection.cursor() as cursor:
        project_id = collector_insert(cursor, project, f"collector-{uuid.uuid4().hex}")
    created = Project.objects.get(id=project_id)
    assert created.sampling_contact_attribute == ""
    assert created.organization_id == project.organization_id
    assert created.workspace_id == project.workspace_id


def test_collector_upsert_preserves_configured_contact_attribute(project):
    name = f"collector-{uuid.uuid4().hex}"
    with connection.cursor() as cursor:
        project_id = collector_insert(cursor, project, name)
    Project.objects.filter(id=project_id).update(
        sampling_contact_attribute="contact_id"
    )
    with connection.cursor() as cursor:
        existing_id = collector_insert(cursor, project, name)
    assert existing_id == project_id
    assert Project.objects.get(id=project_id).sampling_contact_attribute == "contact_id"
