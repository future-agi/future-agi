"""Quarantine uses the same sorted graph locks as release/publication."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from model_hub.services.skills_hub.v12.safety import set_quarantine
from model_hub.tests.skills_hub.r3_l03 import conftest as fixtures

pytestmark = pytest.mark.django_db(transaction=True)
principal = fixtures.principal
context_factory = fixtures.context_factory
context = fixtures.context
accept_links = fixtures.accept_links
publish_context = fixtures.publish_context


def test_quarantine_locks_parent_and_child_in_uuid_order(
    principal, context, accept_links, publish_context, hub_skill, publish_skill
):
    child = publish_skill(hub_skill).version
    accept_links(
        context,
        [{"skill_id": str(hub_skill.pk), "selector": {"version": child.number}}],
    )
    parent = publish_context(context).version
    with CaptureQueriesContext(connection) as captured:
        result = set_quarantine(
            principal=principal,
            version_id=parent.pk,
            revision=0,
            active=True,
            reason="Synthetic safety review",
            replacement_version_id=None,
        )
    locks = [
        row["sql"]
        for row in captured.captured_queries
        if "FOR UPDATE" in row["sql"] and '"model_hub_hubskill"' in row["sql"]
    ]
    assert len(locks) == 1
    assert context.pk.hex in locks[0].replace("-", "") and hub_skill.pk.hex in locks[
        0
    ].replace("-", "")
    assert 'ORDER BY "model_hub_hubskill"."id" ASC' in locks[0]
    assert result["quarantined"] is True
