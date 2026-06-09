from rest_framework import serializers

from model_hub.models.kb import KnowledgeBase


class KnowledgeBaseSerializer(serializers.ModelSerializer):
    """A knowledge base is a chunked, embedded corpus used to ground
    LLM prompts (RAG) or synthetic data generation.

    Each KB has an embedding model (e.g. 'text-embedding-3-small'), a
    chunk size (in tokens), and a collection of indexed files. Use KBs
    in prompt templates as a retrieval source, in dataset synthesis to
    seed examples, or attach to agents for grounded answers. Names are
    unique per organization.
    """

    id = serializers.UUIDField(
        read_only=True,
        help_text=(
            "Unique knowledge base identifier (UUID v4). **How to get it:** "
            "call `list_knowledge_bases` first."
        ),
    )
    name = serializers.CharField(
        max_length=255,
        help_text=(
            "Human-readable KB name. Must be unique within the organization. "
            "Examples: 'product-docs-v3', 'company-handbook', 'api-reference'."
        ),
    )
    embedding_model = serializers.CharField(
        required=False,
        help_text=(
            "Embedding model used to vectorise chunks. Examples: "
            "'text-embedding-3-small', 'text-embedding-3-large', "
            "'voyage-3'. Determines retrieval quality and dimensionality."
        ),
    )
    chunk_size = serializers.IntegerField(
        required=False,
        help_text=(
            "Chunk size in tokens. Typical values 256-1024. Smaller chunks "
            "give more granular retrieval; larger chunks preserve context."
        ),
    )
    organization = serializers.UUIDField(
        read_only=True,
        help_text="Organization UUID, auto-set from the authenticated user.",
    )

    class Meta:
        model = KnowledgeBase
        fields = [
            "id",
            "name",
            "embedding_model",
            "chunk_size",
            "organization",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]


class KnowledgeBaseCreateSerializer(serializers.ModelSerializer):
    """Create a knowledge base — a chunked, embedded corpus used to ground LLM
    prompts (RAG), seed synthetic data, or back agents. Provide a name (unique
    per organization) and optionally the embedding model and chunk size; the
    organization is set automatically from the authenticated user. Files are
    indexed separately after creation.
    """

    class Meta:
        model = KnowledgeBase
        fields = [
            "id",
            "name",
            "embedding_model",
            "chunk_size",
            "organization",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "name": {
                "help_text": "Human-readable KB name. Must be unique within the organization."
            },
            "embedding_model": {
                "help_text": (
                    "Embedding model used to vectorise chunks, e.g. "
                    "'text-embedding-3-small'. Determines retrieval quality and "
                    "dimensionality."
                )
            },
            "chunk_size": {
                "help_text": (
                    "Chunk size in tokens (typically 256-1024). Smaller chunks give "
                    "more granular retrieval; larger ones preserve context."
                )
            },
            "organization": {
                "help_text": "Organization UUID, auto-set from the authenticated user."
            },
        }

    def create(self, validated_data):
        request = self.context["request"]
        validated_data["organization"] = (
            getattr(request, "organization", None) or request.user.organization
        )
        return super().create(validated_data)
