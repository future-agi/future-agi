"""Tests for PromptTemplateNode model."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from agent_playground.models.choices import NodeType
from agent_playground.models.node import Node
from agent_playground.models.prompt_template_node import PromptTemplateNode


@pytest.mark.unit
class TestPromptTemplateNodeModel:
    """Tests for the PromptTemplateNode model."""

    def test_create_prompt_template_node(
        self, prompt_template_node, node, prompt_template, prompt_version
    ):
        """Creating a PromptTemplateNode links node, template, and version."""
        assert prompt_template_node.node_id == node.id
        assert prompt_template_node.prompt_template_id == prompt_template.id
        assert prompt_template_node.prompt_version_id == prompt_version.id

    def test_str_representation(
        self, prompt_template_node, node, prompt_template, prompt_version
    ):
        """__str__ includes node, template, and version UUIDs."""
        expected = (
            f"PromptTemplateNode(node={node.id}, "
            f"template={prompt_template.id}, "
            f"version={prompt_version.id})"
        )
        assert str(prompt_template_node) == expected

    def test_unique_node_constraint(
        self, prompt_template_node, node, prompt_template, prompt_version
    ):
        """Cannot create two PromptTemplateNode rows for the same Node."""
        with pytest.raises(IntegrityError):
            PromptTemplateNode.no_workspace_objects.create(
                node=node,
                prompt_template=prompt_template,
                prompt_version=prompt_version,
            )

    def test_reverse_accessor_from_node(self, prompt_template_node, node):
        """Node has a prompt_template_node reverse accessor."""
        assert node.prompt_template_node == prompt_template_node

    def test_multiple_nodes_same_template(
        self,
        prompt_template_node,
        second_node,
        prompt_template,
        prompt_version,
    ):
        """Two different nodes can reference the same PromptTemplate/PromptVersion."""
        ptn2 = PromptTemplateNode.no_workspace_objects.create(
            node=second_node,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
        )
        assert ptn2.prompt_template_id == prompt_template.id
        assert ptn2.prompt_version_id == prompt_version.id

    def test_version_must_belong_to_template(
        self, node, prompt_template, other_prompt_version
    ):
        """Creating with a version from a different template raises ValidationError."""
        with pytest.raises(ValidationError):
            PromptTemplateNode.no_workspace_objects.create(
                node=node,
                prompt_template=prompt_template,
                prompt_version=other_prompt_version,
            )

    def test_cascade_delete_node(self, prompt_template_node, node):
        """Hard-deleting the node cascades and deletes the bridge row."""
        ptn_id = prompt_template_node.id
        Node.no_workspace_objects.filter(
            id=node.id
        ).delete()  # hard delete via queryset
        assert not PromptTemplateNode.no_workspace_objects.filter(id=ptn_id).exists()

    def test_cascade_soft_delete_prompt_template(
        self, prompt_template_node, prompt_template, node
    ):
        """Soft-deleting a PromptTemplate cascades to linked nodes."""
        # Soft delete the template
        prompt_template.deleted = True
        prompt_template.save()

        # Reload node - should be soft-deleted
        node.refresh_from_db()
        assert node.deleted is True

        # PTN should also be soft-deleted
        prompt_template_node.refresh_from_db()
        assert prompt_template_node.deleted is True

    def test_cascade_soft_delete_prompt_version(
        self, prompt_template_node, prompt_version, node
    ):
        """Soft-deleting a PromptVersion cascades to linked nodes."""
        # Soft delete the version
        prompt_version.deleted = True
        prompt_version.save()

        # Reload node - should be soft-deleted
        node.refresh_from_db()
        assert node.deleted is True

        # PTN should also be soft-deleted
        prompt_template_node.refresh_from_db()
        assert prompt_template_node.deleted is True

    def test_soft_delete_hides_from_queryset(self, prompt_template_node):
        """Soft-deleted PromptTemplateNode is hidden by no_workspace_objects."""
        ptn_id = prompt_template_node.id
        prompt_template_node.delete()  # soft-delete
        assert not PromptTemplateNode.no_workspace_objects.filter(id=ptn_id).exists()
