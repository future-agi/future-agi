"""Regression tests for TH-5655 — prompt metrics must surface spans even
when prompt_label_id is NULL (i.e., the SDK didn't pass `template_label`).

Two halves of the fix:

1. Ingestion (`tracer.utils.trace_ingestion._fetch_prompt_versions`) must
   resolve `prompt_version_id` from `template_name` alone — `template_label`
   stays optional. When the SDK omits the label, the span row still gets
   tagged with `prompt_version_id`, `prompt_label_id` stays NULL.

2. Retrieval (`SQL_queries.prompt_metrics_cte_base_query`) must surface
   those NULL-label rows by LEFT-JOINing `model_hub_promptlabel` instead
   of INNER-JOINing. The metric row reports `prompt_label_name = NULL`.
"""

import uuid

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplateVersion  # noqa: F401
from model_hub.models.run_prompt import (
    PromptLabel,
    PromptTemplate,
    PromptVersion,
)
from tracer.utils.trace_ingestion import _fetch_prompt_versions


@pytest.fixture
def organization(db):
    return Organization.objects.create(name=f"th5655-org-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def workspace(db, organization):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
    )


@pytest.fixture
def prompt_template(db, organization, workspace):
    return PromptTemplate.objects.create(
        name=f"tpl-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
    )


@pytest.fixture
def prompt_version(db, prompt_template):
    return PromptVersion.objects.create(
        original_template=prompt_template,
        template_version="v1",
        is_default=True,
    )


# ── Storage half: ingestion resolves version_id without a label ────────────


@pytest.mark.django_db
class TestFetchPromptVersionsLabelOptional:
    def test_no_label_still_resolves_version(self, prompt_template, prompt_version):
        """SDK omitted template_label → version_id set, label_id stays None."""
        parsed = [
            {
                "observation_span": {"observation_type": "llm"},
                "prompt_details": {
                    "prompt_template_name": prompt_template.name,
                    "prompt_template_version": "v1",
                    # template_label intentionally absent
                },
            }
        ]

        result = _fetch_prompt_versions(parsed, str(prompt_template.organization_id))

        assert len(result) == 1
        ((key, entry),) = result.items()
        assert entry["prompt_version_id"] == str(prompt_version.id)
        assert entry["prompt_label_id"] is None

    def test_with_label_resolves_both(
        self, prompt_template, prompt_version, organization, workspace
    ):
        """When label is provided and exists, both ids are set."""
        label = PromptLabel.objects.create(
            name="production",
            type="CUSTOM",
            organization=organization,
            workspace=workspace,
        )
        prompt_version.labels.add(label)

        parsed = [
            {
                "observation_span": {"observation_type": "llm"},
                "prompt_details": {
                    "prompt_template_name": prompt_template.name,
                    "prompt_template_version": "v1",
                    "prompt_template_label": "production",
                },
            }
        ]

        result = _fetch_prompt_versions(parsed, str(prompt_template.organization_id))

        assert len(result) == 1
        entry = next(iter(result.values()))
        assert entry["prompt_version_id"] == str(prompt_version.id)
        assert entry["prompt_label_id"] == str(label.id)

    def test_no_template_name_returns_empty(self, prompt_template):
        """Without a name there's nothing to match — early return."""
        parsed = [
            {
                "observation_span": {"observation_type": "llm"},
                "prompt_details": {"prompt_template_version": "v1"},
            }
        ]
        assert _fetch_prompt_versions(parsed, str(prompt_template.organization_id)) == {}

    def test_non_llm_spans_ignored(self, prompt_template):
        """Only llm spans carry prompt_details — others skipped."""
        parsed = [
            {
                "observation_span": {"observation_type": "tool"},
                "prompt_details": {
                    "prompt_template_name": prompt_template.name,
                    "prompt_template_version": "v1",
                },
            }
        ]
        assert _fetch_prompt_versions(parsed, str(prompt_template.organization_id)) == {}


# ── Retrieval half: CTE must LEFT-JOIN prompt_label, not INNER-JOIN ────────


class TestMetricsCteLabelJoin:
    """Static guard against the INNER JOIN regressing back into the CTE."""

    def test_cte_uses_left_join_on_prompt_label(self):
        from model_hub.utils.SQL_queries import prompt_metrics_cte_base_query

        assert "LEFT JOIN model_hub_promptlabel" in prompt_metrics_cte_base_query
        assert "INNER JOIN model_hub_promptlabel" not in prompt_metrics_cte_base_query
