from __future__ import annotations

from dataclasses import dataclass

from accounts.services.onboarding.activation_events import (
    has_event,
    latest_event,
)


@dataclass(frozen=True)
class PromptOnboardingSignals:
    prompt_count: int = 0
    sample_prompt_count: int = 0
    first_prompt_id: str | None = None
    latest_prompt_id: str | None = None
    latest_prompt_name: str | None = None
    run_count: int = 0
    committed_version_count: int = 0
    comparable_version_count: int = 0
    draft_version_count: int = 0
    comparison_completed: bool = False
    next_loop_action_count: int = 0
    default_version_id: str | None = None
    latest_run_at: object | None = None
    latest_version_at: object | None = None
    latest_comparison_at: object | None = None
    diagnostics: tuple[str, ...] = ()

    @property
    def has_real_prompt(self):
        return self.prompt_count > 0

    @property
    def has_test_run(self):
        return self.run_count > 0

    @property
    def has_committed_version(self):
        return self.committed_version_count > 0

    @property
    def has_comparable_versions(self):
        return self.comparable_version_count > 1

    @property
    def has_next_loop_action(self):
        return self.next_loop_action_count > 0

    @property
    def first_loop_completed(self):
        return (
            self.has_real_prompt
            and self.has_test_run
            and self.has_comparable_versions
            and self.comparison_completed
            and self.has_next_loop_action
        )

    def to_activation_prompt_state(self, stage):
        return {
            "prompt_id": self.latest_prompt_id or self.first_prompt_id,
            "prompt_name": self.latest_prompt_name,
            "stage": stage,
            "has_real_prompt": self.has_real_prompt,
            "has_test_run": self.has_test_run,
            "has_committed_version": self.has_committed_version,
            "has_comparable_versions": self.has_comparable_versions,
            "has_comparison": self.comparison_completed,
            "has_next_loop_action": self.has_next_loop_action,
            "is_sample": False,
            "sample_prompt_count": self.sample_prompt_count,
            "diagnostics": list(self.diagnostics),
        }


def _safe_json_has_value(value):
    return value not in (None, "", [], {})


def _prompt_templates(*, organization, workspace, is_sample):
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.no_workspace_objects.filter(
        organization=organization,
        workspace=workspace,
        is_sample=is_sample,
    ).order_by("-updated_at", "-created_at")


def _prompt_versions(template_ids):
    from model_hub.models.run_prompt import PromptVersion

    if not template_ids:
        return []
    return list(
        PromptVersion.no_workspace_objects.filter(
            original_template_id__in=template_ids,
            original_template__is_sample=False,
        )
        .select_related("original_template")
        .order_by("-updated_at", "-created_at")[:200]
    )


def _prompt_eval_count(template_ids):
    if not template_ids:
        return 0
    from model_hub.models.run_prompt import PromptEvalConfig

    return PromptEvalConfig.no_workspace_objects.filter(
        prompt_template_id__in=template_ids,
    ).count()


def collect_prompt_onboarding_signals(*, user, organization, workspace):
    if not organization or not workspace:
        return PromptOnboardingSignals()

    real_templates = list(
        _prompt_templates(
            organization=organization,
            workspace=workspace,
            is_sample=False,
        )[:50]
    )
    sample_count = _prompt_templates(
        organization=organization,
        workspace=workspace,
        is_sample=True,
    ).count()
    if not real_templates:
        return PromptOnboardingSignals(sample_prompt_count=sample_count)

    template_ids = [template.id for template in real_templates]
    versions = _prompt_versions(template_ids)
    run_versions = [
        version for version in versions if _safe_json_has_value(version.output)
    ]
    committed_versions = [
        version
        for version in versions
        if not version.is_draft and (version.commit_message or version.is_default)
    ]
    committed_run_versions_by_template = {}
    for version in committed_versions:
        if not _safe_json_has_value(version.output):
            continue
        template_id = version.original_template_id
        committed_run_versions_by_template[template_id] = (
            committed_run_versions_by_template.get(template_id, 0) + 1
        )
    comparable_version_count = max(
        committed_run_versions_by_template.values(),
        default=0,
    )
    draft_versions = [version for version in versions if version.is_draft]
    default_version = next(
        (version for version in versions if version.is_default), None
    )
    comparison_completed = has_event(
        organization=organization,
        workspace=workspace,
        event_name="prompt_comparison_completed",
        is_sample=False,
    )
    next_loop_count = _prompt_eval_count(template_ids)
    if has_event(
        organization=organization,
        workspace=workspace,
        event_name="dataset_example_added",
        is_sample=False,
    ):
        next_loop_count += 1
    if has_event(
        organization=organization,
        workspace=workspace,
        event_name="eval_scorer_created",
        is_sample=False,
    ):
        next_loop_count += 1

    latest_run = latest_event(
        organization=organization,
        workspace=workspace,
        event_names=["prompt_test_run_completed"],
        is_sample=False,
    )
    latest_version = latest_event(
        organization=organization,
        workspace=workspace,
        event_names=["prompt_version_created"],
        is_sample=False,
    )
    latest_comparison = latest_event(
        organization=organization,
        workspace=workspace,
        event_names=["prompt_comparison_completed"],
        is_sample=False,
    )

    latest_template = real_templates[0]
    return PromptOnboardingSignals(
        prompt_count=len(real_templates),
        sample_prompt_count=sample_count,
        first_prompt_id=str(real_templates[-1].id),
        latest_prompt_id=str(latest_template.id),
        latest_prompt_name=latest_template.name,
        run_count=len(run_versions) + (1 if latest_run else 0),
        committed_version_count=len(committed_versions),
        comparable_version_count=comparable_version_count,
        draft_version_count=len(draft_versions),
        comparison_completed=comparison_completed,
        next_loop_action_count=next_loop_count,
        default_version_id=str(default_version.id) if default_version else None,
        latest_run_at=latest_run.occurred_at if latest_run else None,
        latest_version_at=latest_version.occurred_at if latest_version else None,
        latest_comparison_at=(
            latest_comparison.occurred_at if latest_comparison else None
        ),
        diagnostics=(),
    )
