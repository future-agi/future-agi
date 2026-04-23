"""
UPDATE model_hub_message
SET updated_at = NOW()
WHERE updated_at IS NULL;
"""

import json
import uuid

from django.db import models

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from tfc.utils.base_model import BaseModel


class NodeManager(models.Manager):
    """Custom manager for Node model with tree traversal capabilities."""

    def get_all_parent_messages(self, node_id, formatter):
        """Fetch all parent messages from a node up to the root using recursive CTE."""
        query = """
            WITH RECURSIVE node_tree AS (
                SELECT n.id, n.parent_node_id, m.content, m.author , n.created_at
                FROM model_hub_node n
                LEFT JOIN model_hub_message m ON n.message_id = m.id
                WHERE n.id = %s
                UNION ALL
                SELECT n.id, n.parent_node_id, m.content, m.author , n.created_at
                FROM model_hub_node n
                LEFT JOIN model_hub_message m ON n.message_id = m.id
                INNER JOIN node_tree nt ON n.id = nt.parent_node_id
            )
            SELECT id, content,author
            FROM node_tree
            WHERE content IS NOT NULL
            ORDER BY created_at ASC;
        """
        nodes = self.raw(query, [node_id])
        if formatter is not None:
            return [
                {
                    "content": formatter(json.loads(node.content)),
                    "author": json.loads(node.author),
                }
                for node in nodes
            ]
        else:
            return [json.loads(node.content) for node in nodes]


class Conversation(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_provided_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=255)
    root_node = models.OneToOneField(
        "Node",
        on_delete=models.SET_NULL,
        related_name="root_conversation",
        null=True,
        blank=True,
    )
    metadata = models.JSONField(default=dict, blank=True)

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="conversations"
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name="conversations",
        null=True,
        blank=True,
    )

    def __str__(self):
        return self.title


class Node(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_provided_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="nodes"
    )
    parent_node = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="child_nodes",
        null=True,
        blank=True,
    )
    # child_node = models.ForeignKey(
    #     "self", on_delete=models.SET_NULL, related_name="parent", null=True, blank=True
    # )
    message = models.OneToOneField(
        "Message",
        on_delete=models.SET_NULL,
        related_name="node",
        null=True,
        blank=True,
    )

    objects = NodeManager()

    def __str__(self):
        return str(self.id)


class Message(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_provided_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
    )
    author = models.JSONField(default=list, blank=True)
    content = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return str(self.id)
