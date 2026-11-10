"""Whole-context testing contracts; leaf-only enums never truncate context evidence."""

from rest_framework import serializers

from model_hub.serializers import skills_hub_v11 as leaf
from model_hub.serializers import skills_hub_v12 as context


class ContextTestPreview(leaf.TestRunPreview):
    reference = leaf.SnapshotIdentity(allow_null=True)


class CreateContextTest(leaf.CreateTestRunRequest):
    configuration = context.ContextRunConfig()


class ContextAttemptDetail(leaf.AttemptDetail):
    case = context.ContextCaseOut()
    reference = leaf.SnapshotIdentity(allow_null=True)
    variant = serializers.ChoiceField(
        choices=["candidate", "production", "reference", "without_target"]
    )


ContextRunPage = leaf.page_of(context.ContextRunSummary)
ContextResultsPage = leaf.page_of(context.ContextCaseResult)
