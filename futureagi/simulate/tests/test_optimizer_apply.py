"""DB tests for applying an optimised prompt as a new PromptVersion (TH-5642).

Verifies the product-decided "new version + confirm" apply: a NEW PromptVersion is
created carrying the optimised system prompt, takes the next template_version, goes
live as the default, and the baseline row is left intact.
"""

import pytest

from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from simulate.services.optimizer_apply import (
    apply_optimized_prompt_as_new_version,
    snapshot_with_prompt,
)

BASE_SNAPSHOT = [
    {
        "messages": [
            {"role": "system", "content": "BASE SYSTEM PROMPT"},
            {"role": "user", "content": "{{question}}"},
        ],
        "configuration": {"model": "gpt-4o-mini", "temperature": 0.7},
    }
]


@pytest.mark.unit
def test_snapshot_with_prompt_replaces_only_system_message():
    out = snapshot_with_prompt(BASE_SNAPSHOT, "NEW PROMPT")
    msgs = out[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "NEW PROMPT"}
    assert msgs[1] == {"role": "user", "content": "{{question}}"}  # untouched
    # configuration preserved
    assert out[0]["configuration"]["model"] == "gpt-4o-mini"
    # original snapshot object not mutated
    assert BASE_SNAPSHOT[0]["messages"][0]["content"] == "BASE SYSTEM PROMPT"


@pytest.mark.unit
def test_snapshot_prepends_system_message_when_absent():
    snap = [{"messages": [{"role": "user", "content": "hi"}], "configuration": {}}]
    out = snapshot_with_prompt(snap, "SYS")
    assert out[0]["messages"][0] == {"role": "system", "content": "SYS"}
    assert out[0]["messages"][1]["role"] == "user"


@pytest.mark.unit
@pytest.mark.django_db
def test_apply_creates_new_default_version_non_destructively(organization, workspace):
    template = PromptTemplate.objects.create(
        name="Optimiser Apply Prompt", organization=organization, workspace=workspace,
    )
    base = PromptVersion.objects.create(
        original_template=template,
        template_version="v1",
        is_default=True,
        commit_message="Initial",
        prompt_config_snapshot=BASE_SNAPSHOT,
    )

    new_version = apply_optimized_prompt_as_new_version(base, "OPTIMISED SYSTEM PROMPT")

    # A distinct new row.
    assert new_version.id != base.id
    assert new_version.original_template_id == template.id
    assert new_version.template_version == "v2"
    # Carries the optimised system prompt in the snapshot the adapter reads.
    assert new_version.prompt_config_snapshot[0]["messages"][0] == {
        "role": "system", "content": "OPTIMISED SYSTEM PROMPT",
    }
    # The rest of the config is preserved from the base.
    assert new_version.prompt_config_snapshot[0]["messages"][1]["content"] == "{{question}}"
    # The apply request is the confirmation -> the new version is live.
    assert new_version.is_default is True

    # Non-destructive: the base row still exists, its prompt is unchanged, and it is
    # no longer the default (superseded by the new version).
    base.refresh_from_db()
    assert base.prompt_config_snapshot[0]["messages"][0]["content"] == "BASE SYSTEM PROMPT"
    assert base.is_default is False
    assert PromptVersion.objects.filter(original_template=template).count() == 2


@pytest.mark.unit
@pytest.mark.django_db
def test_apply_can_skip_default(organization, workspace):
    template = PromptTemplate.objects.create(
        name="Optimiser Apply NoDefault", organization=organization, workspace=workspace,
    )
    base = PromptVersion.objects.create(
        original_template=template, template_version="v1", is_default=True,
        prompt_config_snapshot=BASE_SNAPSHOT,
    )
    new_version = apply_optimized_prompt_as_new_version(
        base, "OPT", make_default=False
    )
    assert new_version.is_default is False
    base.refresh_from_db()
    assert base.is_default is True  # base stays default when we don't promote


@pytest.mark.unit
@pytest.mark.django_db
def test_apply_does_not_overwrite_when_no_system_message(organization, workspace):
    template = PromptTemplate.objects.create(
        name="Optimiser Apply NoSys", organization=organization, workspace=workspace,
    )
    base = PromptVersion.objects.create(
        original_template=template, template_version="v1",
        prompt_config_snapshot=[{"messages": [{"role": "user", "content": "hi"}], "configuration": {}}],
    )
    new_version = apply_optimized_prompt_as_new_version(base, "INJECTED SYS")
    msgs = new_version.prompt_config_snapshot[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "INJECTED SYS"}
