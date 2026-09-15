"""
Regression tests for calculate_cost_from_tokens with custom-model pricing.

The custom-model settings UI collects "Input/Output Token Cost Per Million
Tokens" and stores it unchanged (model_hub/views/custom_model.py). The cost
calculation must therefore divide by 1_000_000, not 1_000 — see
https://github.com/future-agi/future-agi/issues/2634.
"""

import pytest
from django.core.cache import cache

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.custom_models import CustomAIModel
from tracer.utils.otel import calculate_cost_from_tokens


@pytest.mark.django_db
class TestCustomModelCostCalculation:
    def setup_method(self):
        cache.clear()
        self.organization = Organization.objects.create(name="Cost Test Org")
        self.user = User.objects.create_user(
            email="cost-test@example.com", password="password123"
        )
        self.workspace = Workspace.objects.create(
            name="Cost Test Workspace",
            organization=self.organization,
            created_by=self.user,
        )
        self.custom_model = CustomAIModel.objects.create(
            user_model_id="my-custom-llm",
            provider="openai",
            input_token_cost=2.50,
            output_token_cost=10.00,
            organization=self.organization,
            workspace=self.workspace,
            user=self.user,
            key_config={"key": "test-api-key"},
        )

    def teardown_method(self):
        cache.clear()

    def test_custom_model_cost_uses_per_million_token_pricing(self):
        """1M prompt tokens at $2.50/1M input must cost $2.50, not $2500."""
        cost = calculate_cost_from_tokens(
            prompt_tokens=1_000_000,
            completion_tokens=0,
            model="my-custom-llm",
            organization_id=str(self.organization.id),
        )
        assert cost == pytest.approx(2.50)

    def test_custom_model_cost_combines_prompt_and_completion(self):
        cost = calculate_cost_from_tokens(
            prompt_tokens=1_000_000,
            completion_tokens=1_000_000,
            model="my-custom-llm",
            organization_id=str(self.organization.id),
        )
        assert cost == pytest.approx(2.50 + 10.00)

    def test_custom_model_cost_scales_linearly_below_a_million_tokens(self):
        """1000 tokens at $2.50/1M input should cost a fraction of a cent, not
        the pre-fix $0.0025 (which was itself 1000x the correct $0.0000025)."""
        cost = calculate_cost_from_tokens(
            prompt_tokens=1_000,
            completion_tokens=0,
            model="my-custom-llm",
            organization_id=str(self.organization.id),
        )
        assert cost == pytest.approx(2.50 * 1_000 / 1_000_000)
