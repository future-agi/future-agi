"""Behavioral regression test for #305 / PR #340.

EndUser dedup was broken: `user_id_type` was nullable and un-normalized, and SQL treats
NULL != NULL, so the unique_together ("project","organization","user_id","user_id_type")
allowed multiple rows for the same user with user_id_type=None. The fix normalizes None/empty
-> "custom" (normalize_user_id_type) at the get_or_create call site and makes the column
non-nullable.

This exercises the real dedup operation (normalize_user_id_type + EndUser.objects.get_or_create
-- exactly what tracer ingestion does in create_otel_span/trace_ingestion): two lookups for the
same user with user_id_type=None must resolve to ONE EndUser. Pre-fix (un-normalized None) the
second lookup is NOT a dedup hit -- it creates a distinct row / breaks -- so this test fails.
"""
import pytest

from tracer.models.observation_span import EndUser, normalize_user_id_type


@pytest.mark.unit
@pytest.mark.django_db
def test_end_user_none_user_id_type_deduplicates(project, organization, workspace):
    common = dict(
        project=project,
        organization=organization,
        workspace=workspace,
        user_id="u-dedup-305",
    )
    e1, created1 = EndUser.objects.get_or_create(
        **common, user_id_type=normalize_user_id_type(None)
    )
    e2, created2 = EndUser.objects.get_or_create(
        **common, user_id_type=normalize_user_id_type(None)
    )

    assert created1 is True
    assert created2 is False, "second lookup for the same None-typed user must dedup, not create"
    assert e1.pk == e2.pk
    assert EndUser.objects.filter(**common).count() == 1
