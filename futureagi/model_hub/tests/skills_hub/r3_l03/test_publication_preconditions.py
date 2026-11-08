"""Failed exact-content preconditions never allocate or discard the live draft."""

from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12.authoring import (
    get_compiled_draft,
    save_context_draft,
    start_context_draft,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("failure", ["digest", "evidence", "warnings"])
def test_rejected_publish_retains_draft_and_allocates_nothing(
    principal, context, publish_context, failure
):
    draft = context.drafts.get()
    if failure == "warnings":
        draft = save_context_draft(
            principal=principal,
            context_id=context.pk,
            revision=draft.revision,
            base_version=None,
            files=[
                {
                    "path": "AGENTS.md",
                    "content": "See [missing](references/missing.md).",
                }
            ],
            description="Purpose",
            note="",
        )
    original = get_compiled_draft(
        principal=principal, context_id=context.pk, revision=draft.revision
    ).digest
    overrides, code = (
        ({"expected_digest": "sha256:" + "0" * 64}, "candidate_changed")
        if failure == "digest"
        else (
            ({"evidence_run_id": uuid4()}, "evidence_mismatch")
            if failure == "evidence"
            else ({"acknowledge_warnings": False}, "warnings_unacknowledged")
        )
    )
    with pytest.raises(SkillsHubError) as error:
        publish_context(context, **overrides)
    assert error.value.code == code
    assert context.versions.count() == 0
    assert context.drafts.count() == 1
    current = context.drafts.get()
    assert (
        get_compiled_draft(
            principal=principal, context_id=context.pk, revision=current.revision
        ).digest
        == original
    )


def test_unchanged_publication_validates_supplied_evidence_before_dedupe(
    principal, context, publish_context
):
    first = publish_context(context).version
    start_context_draft(
        principal=principal,
        context_id=context.pk,
        revision=0,
        from_version=first.number,
    )
    with pytest.raises(SkillsHubError) as error:
        publish_context(context, evidence_run_id=uuid4())
    assert error.value.code == "evidence_mismatch"
    assert context.versions.count() == 1 and context.drafts.count() == 1
    unchanged = publish_context(context)
    assert unchanged.unchanged is True and unchanged.version.pk == first.pk
    assert not context.drafts.exists()
