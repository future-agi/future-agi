import pytest

from accounts.services.onboarding.activation_events import record_event
from accounts.services.onboarding.prompt_signals import (
    collect_prompt_onboarding_signals,
)
from accounts.services.onboarding.signal_resolver import collect_onboarding_signals
from accounts.tests.onboarding_model_factories import (
    create_prompt_eval_config,
    create_prompt_template,
    create_prompt_version,
)


@pytest.mark.django_db
def test_sample_prompt_does_not_count_as_real_prompt(organization, workspace, user):
    create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
        is_sample=True,
    )

    prompt_signals = collect_prompt_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert prompt_signals.prompt_count == 0
    assert prompt_signals.sample_prompt_count == 1
    assert prompt_signals.first_loop_completed is False
    assert signals.prompt_templates == 0
    assert signals.prompt_sample_templates == 1


@pytest.mark.django_db
def test_prompt_signals_collect_quality_loop_state(organization, workspace, user):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
        name="Support classifier",
    )
    create_prompt_version(
        template=template,
        version="v1",
        is_draft=False,
        is_default=True,
        commit_message="Baseline",
        output=["billing"],
    )
    create_prompt_version(
        template=template,
        version="v2",
        is_draft=False,
        commit_message="Safer answer",
        output=["billing escalation"],
    )
    record_event(
        user=user,
        organization=organization,
        workspace=workspace,
        event_name="prompt_comparison_completed",
        source="test",
        product_path="prompt",
        activation_stage="compare_prompt_versions",
    )
    create_prompt_eval_config(
        organization=organization,
        workspace=workspace,
        template=template,
    )

    prompt_signals = collect_prompt_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert prompt_signals.prompt_count == 1
    assert prompt_signals.latest_prompt_id == str(template.id)
    assert prompt_signals.latest_prompt_name == "Support classifier"
    assert prompt_signals.has_test_run is True
    assert prompt_signals.has_committed_version is True
    assert prompt_signals.has_comparable_versions is True
    assert prompt_signals.comparison_completed is True
    assert prompt_signals.latest_comparison_at is not None
    assert prompt_signals.has_next_loop_action is True
    assert prompt_signals.first_loop_completed is True
    assert signals.prompt_comparable_versions_exist is True
    assert signals.prompt_first_loop_completed is True


@pytest.mark.django_db
def test_single_committed_prompt_version_is_not_comparable(
    organization,
    workspace,
    user,
):
    template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(
        template=template,
        version="v1",
        is_draft=False,
        is_default=True,
        commit_message="Baseline",
        output=["billing"],
    )

    prompt_signals = collect_prompt_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )
    signals = collect_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert prompt_signals.has_committed_version is True
    assert prompt_signals.has_comparable_versions is False
    assert prompt_signals.first_loop_completed is False
    assert signals.prompt_comparable_versions_exist is False


@pytest.mark.django_db
def test_committed_prompt_versions_must_share_a_template_to_be_comparable(
    organization,
    workspace,
    user,
):
    first_template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    second_template = create_prompt_template(
        organization=organization,
        workspace=workspace,
        user=user,
    )
    create_prompt_version(
        template=first_template,
        version="v1",
        is_draft=False,
        is_default=True,
        commit_message="First baseline",
        output=["billing"],
    )
    create_prompt_version(
        template=second_template,
        version="v1",
        is_draft=False,
        is_default=True,
        commit_message="Second baseline",
        output=["refund"],
    )

    prompt_signals = collect_prompt_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert prompt_signals.committed_version_count == 2
    assert prompt_signals.has_comparable_versions is False


@pytest.mark.django_db
def test_prompt_signals_are_workspace_scoped(organization, workspace, user):
    other_workspace = type(workspace).no_workspace_objects.create(
        name="Other Prompt Workspace",
        organization=organization,
        created_by=user,
    )
    template = create_prompt_template(
        organization=organization,
        workspace=other_workspace,
        user=user,
    )
    create_prompt_version(
        template=template,
        is_draft=False,
        is_default=True,
        commit_message="Other baseline",
        output=["other"],
    )

    prompt_signals = collect_prompt_onboarding_signals(
        user=user,
        organization=organization,
        workspace=workspace,
    )

    assert prompt_signals.prompt_count == 0
    assert prompt_signals.has_test_run is False
    assert prompt_signals.has_committed_version is False
