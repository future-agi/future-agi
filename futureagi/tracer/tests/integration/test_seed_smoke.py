"""Smoke test for the dual-write seeder.

Asserts the 24-span corpus lands in both Postgres (ORM) and ClickHouse (SQL).
"""
import pytest
from django.conf import settings

from tracer.models.observation_span import ObservationSpan


@pytest.mark.django_db
def test_seeded_corpus_lands_in_pg_and_ch(seeded_corpus, ch_schema):
    """Sanity: the session-scoped seed wrote the same row set to PG and CH."""
    project = seeded_corpus.project
    expected = seeded_corpus.counts["span_count"]
    # PG side
    assert ObservationSpan.objects.filter(project=project).count() == expected
    # CH side
    db = settings.CLICKHOUSE["CH_DATABASE"]
    ch_count = ch_schema.command(
        f"SELECT count() FROM {db}.tracer_observation_span "
        f"WHERE project_id = '{project.id}' AND _peerdb_is_deleted = 0"
    )
    assert int(ch_count) == expected
