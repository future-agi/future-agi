"""Shared suite edits retain reviewed Dataset lineage without trusting input."""

import pytest

from model_hub.serializers.skills_hub_v11 import CaseInput, SuiteOut
from model_hub.services.skills_hub.r3.case_import import accept_dataset_case
from model_hub.services.skills_hub.v11.inventory import fingerprint
from model_hub.services.skills_hub.v11.suites import get_suite, save_suite
from model_hub.tests.skills_hub.r3_l04 import test_case_import as fixtures

pytestmark = pytest.mark.django_db(transaction=True)
principal = fixtures.principal
source = fixtures.source
preview = fixtures.preview


def test_dataset_derivation_survives_ordinary_edits(principal, hub_skill, preview):
    accepted = fixtures.acceptance(preview)
    accept_dataset_case(
        principal=principal, resource_id=hub_skill.pk, kind="skill", **accepted
    )
    before = get_suite(scope=principal.scope, skill_id=hub_skill.pk)
    case = accepted["case"]
    edited = {**case, "prompt": "Reviewed changed synthetic request", "reviewed": False}
    saved = save_suite(
        principal=principal,
        skill_id=hub_skill.pk,
        revision=before["revision"],
        cases=[edited],
    )
    lineage = saved.cases[0]["source"]
    assert lineage["source_fingerprint"] == preview["source_fingerprint"]
    assert lineage["revision"] == preview["source"]["revision"]
    assert lineage["sanitized_digest"] == fingerprint(edited)
    assert saved.cases[0]["reviewed_by"] is None
    output = SuiteOut(get_suite(scope=principal.scope, skill_id=hub_skill.pk)).data
    assert (
        output["cases"][0]["source"]["sanitized_digest"] == lineage["sanitized_digest"]
    )
    assert output["cases"][0]["source"]["dataset_id"] == preview["source"]["dataset_id"]
    forged = CaseInput(data={**edited, "source": lineage})
    assert not forged.is_valid()
