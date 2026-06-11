from rest_framework import serializers

from agent_playground.models.choices import (
    RESERVED_NAME_RE,
    GraphVersionStatus,
    NodeType,
    PortDirection,
)
from agent_playground.models.node import Node
from agent_playground.serializers.port import (
    PortCreateSerializer,
    PortReadSerializer,
    PortWriteSerializer,
)


class NodeReadSerializer(serializers.ModelSerializer):
    """Serializer for reading node data in responses."""

    ports = PortReadSerializer(many=True, read_only=True)
    node_template_id = serializers.UUIDField(
        source="node_template.id", read_only=True, allow_null=True
    )
    node_template_name = serializers.CharField(
        source="node_template.name", read_only=True, allow_null=True, default=None
    )
    ref_graph_version_id = serializers.UUIDField(
        source="ref_graph_version.id", read_only=True, allow_null=True
    )
    ref_graph_name = serializers.CharField(
        source="ref_graph_version.graph.name",
        read_only=True,
        allow_null=True,
        default=None,
    )
    ref_graph_id = serializers.UUIDField(
        source="ref_graph_version.graph.id",
        read_only=True,
        allow_null=True,
        default=None,
    )
    prompt_template = serializers.SerializerMethodField()
    node_connection = serializers.SerializerMethodField()
    input_mappings = serializers.SerializerMethodField()

    class Meta:
        model = Node
        fields = [
            "id",
            "type",
            "name",
            "config",
            "position",
            "node_template_id",
            "node_template_name",
            "ref_graph_version_id",
            "ref_graph_name",
            "ref_graph_id",
            "prompt_template",
            "node_connection",
            "input_mappings",
            "ports",
        ]
        read_only_fields = fields

    def get_prompt_template(self, obj):
        """Read from obj.prompt_template_node → PTV.prompt_config_snapshot."""
        ptn = getattr(obj, "prompt_template_node", None)
        if ptn is None:
            return None
        pv = ptn.prompt_version
        snapshot = pv.prompt_config_snapshot or {}
        # snapshot may be a list (legacy) — normalise to dict
        if isinstance(snapshot, list):
            snapshot = snapshot[0] if snapshot else {}

        cfg = snapshot.get("configuration", {})
        return {
            "prompt_template_id": ptn.prompt_template_id,
            "prompt_version_id": ptn.prompt_version_id,
            "messages": snapshot.get("messages", []),
            "response_format": cfg.get(
                "response_format", snapshot.get("response_format", "text")
            ),
            "response_schema": cfg.get(
                "response_schema", snapshot.get("response_schema")
            ),
            "model": cfg.get("model", snapshot.get("model")),
            "temperature": cfg.get("temperature", snapshot.get("temperature")),
            "max_tokens": cfg.get("max_tokens", snapshot.get("max_tokens")),
            "top_p": cfg.get("top_p", snapshot.get("top_p")),
            "frequency_penalty": cfg.get(
                "frequency_penalty", snapshot.get("frequency_penalty")
            ),
            "presence_penalty": cfg.get(
                "presence_penalty", snapshot.get("presence_penalty")
            ),
            "output_format": cfg.get("output_format", snapshot.get("output_format")),
            "tools": cfg.get("tools", snapshot.get("tools")),
            "tool_choice": cfg.get("tool_choice", snapshot.get("tool_choice")),
            "model_detail": cfg.get("model_detail", snapshot.get("model_detail")),
            "template_format": cfg.get("template_format", "mustache"),
            "variable_names": pv.variable_names,
            "metadata": pv.metadata,
            "is_draft": pv.is_draft,
            "template_version": pv.template_version,
        }

    def get_node_connection(self, obj):
        """Return NodeConnection context set by the view (create response only)."""
        nc = self.context.get("node_connection")
        if nc is None:
            return None
        return {
            "id": nc.id,
            "source_node_id": nc.source_node_id,
            "target_node_id": nc.target_node_id,
        }

    def get_input_mappings(self, obj):
        """Reconstruct input_mappings as list of key-value objects.

        Returns a list like [
            {"key": "context", "value": "DataLoader.output"},
            {"key": "question", "value": None}
        ] for subgraph nodes, or None for atomic nodes.

        Uses prefetched ``ports`` and ``incoming_edges`` when available
        (see ``prefetch_version_detail``) to avoid N+1 queries.
        """
        if obj.type != NodeType.SUBGRAPH:
            return None
        # Use prefetched ports — filter in Python to avoid extra queries
        input_ports = [p for p in obj.ports.all() if p.direction == PortDirection.INPUT]
        if not input_ports:
            return None
        mappings = []
        for port in input_ports:
            # Use prefetched incoming_edges reverse relation
            edges = list(port.incoming_edges.all())
            edge = edges[0] if edges else None
            if edge:
                source_node_name = edge.source_port.node.name
                source_port_name = edge.source_port.display_name
                value = f"{source_node_name}.{source_port_name}"
            else:
                value = None
            mappings.append({"key": port.display_name, "value": value})
        return mappings


class InputMappingSerializer(serializers.Serializer):
    """Serializer for individual input mapping key-value pair."""

    key = serializers.CharField(required=True, help_text="Input port display_name")
    value = serializers.CharField(
        required=False,
        allow_null=True,
        help_text='Source reference in format "NodeName.port_display_name" or null',
    )

    def validate_key(self, value):
        """Ensure key doesn't contain reserved characters."""
        if RESERVED_NAME_RE.search(value):
            raise serializers.ValidationError(
                "Input port name cannot contain reserved characters: . [ ] { }"
            )
        return value

    def validate_value(self, value):
        """Validate value format if not null."""
        if value is None:
            return value

        if "." not in value:
            raise serializers.ValidationError(
                'Value must be in format "NodeName.port_display_name" or null'
            )

        parts = value.split(".", 1)
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise serializers.ValidationError(
                "Value must have non-empty node name and port name"
            )

        return value


class MessageContentItemSerializer(serializers.Serializer):
    """Serializer for content items in message content array."""

    type = serializers.ChoiceField(
        choices=["text", "image_url", "audio_url", "pdf_url"],
        required=True,
        help_text="Type of content item",
    )
    text = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Text content (required when type=text)",
    )
    image_url = serializers.URLField(
        required=False,
        help_text="Image URL (required when type=image_url)",
    )
    audio_url = serializers.URLField(
        required=False,
        help_text="Audio URL (required when type=audio_url)",
    )
    pdf_url = serializers.URLField(
        required=False,
        help_text="PDF URL (required when type=pdf_url)",
    )

    def validate(self, data):
        """Validate that required fields are present based on content type."""
        content_type = data.get("type")

        if content_type == "text" and "text" not in data:
            raise serializers.ValidationError(
                "Message content must be an array of content items. "
                'Format: [{"type": "text", "text": "..."}]'
            )
        if content_type == "image_url" and "image_url" not in data:
            raise serializers.ValidationError(
                "'image_url' field is required when type=image_url"
            )
        if content_type == "audio_url" and "audio_url" not in data:
            raise serializers.ValidationError(
                "'audio_url' field is required when type=audio_url"
            )
        if content_type == "pdf_url" and "pdf_url" not in data:
            raise serializers.ValidationError(
                "'pdf_url' field is required when type=pdf_url"
            )

        return data


class MessageSerializer(serializers.Serializer):
    """Serializer for prompt template messages."""

    id = serializers.CharField(
        required=True,
        help_text="Unique identifier for the message (frontend-provided)",
    )
    role = serializers.CharField(
        required=True,
        help_text="Message role (e.g., 'system', 'user', 'assistant')",
    )
    content = MessageContentItemSerializer(
        many=True,
        required=True,
        allow_empty=False,
        help_text="Array of content items",
    )


class PromptTemplateDataSerializer(serializers.Serializer):
    """Nested object inside Create/Update node for LLM prompt data.

    Accepts the same model-config fields that model_hub prompt template APIs
    accept so that the playground stores a compatible prompt_config_snapshot.

    Response Format Options:
    ----------------------
    The `response_format` field controls how the LLM structures its output:

    1. "text" (default) - Plain text output
       Example: "The capital of France is Paris."

    2. "json" - Free-form JSON output (must include "json" in prompt)
       Example: {"city": "Paris", "country": "France"}

    3. "json_schema" - Structured JSON with custom schema
       - Requires `response_schema` field with JSON Schema (Draft 7)
       - Example response_schema: {
           "type": "object",
           "properties": {"name": {"type": "string"}},
           "required": ["name"],
           "additionalProperties": false
         }
       - Validates output against schema
       - Supported by OpenAI GPT-4o+, Anthropic Claude 3.5+

    4. UUID string - Reference to saved UserResponseSchema (advanced)
       - Pass a UUID string (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890")
       - Reuses pre-saved schemas across nodes
       - Validated at API layer, looked up at runtime
    """

    prompt_template_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    prompt_version_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    messages = MessageSerializer(
        many=True,
        required=True,
        allow_empty=False,
        help_text="Array of message objects with id, role, and content array",
    )
    response_format = serializers.JSONField(
        required=False,
        default="text",
        help_text=(
            "LLM output format: 'text' (plain text), 'json' (free-form JSON), "
            "'json_schema' (structured with schema), UUID string (saved schema reference), "
            "or object with 'id' field (prompt playground format). "
            "See class docstring for details."
        ),
    )
    response_schema = serializers.JSONField(
        required=False,
        allow_null=True,
        default=None,
        help_text=(
            "JSON Schema (Draft 7) for structured outputs. "
            "Required when response_format='json_schema'. "
            "Example: {'type': 'object', 'properties': {...}, 'required': [...]}"
        ),
    )
    model = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )
    temperature = serializers.FloatField(required=False, allow_null=True, default=None)
    max_tokens = serializers.IntegerField(required=False, allow_null=True, default=None)
    top_p = serializers.FloatField(required=False, allow_null=True, default=None)
    frequency_penalty = serializers.FloatField(
        required=False, allow_null=True, default=None
    )
    presence_penalty = serializers.FloatField(
        required=False, allow_null=True, default=None
    )
    output_format = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )
    tools = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_null=True,
        default=None,
    )
    tool_choice = serializers.JSONField(required=False, allow_null=True, default=None)
    model_detail = serializers.DictField(required=False, allow_null=True, default=None)
    variable_names = serializers.DictField(
        required=False, allow_null=True, default=None
    )
    metadata = serializers.DictField(required=False, allow_null=True, default=None)
    commit_message = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )
    template_format = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        default=None,
        help_text="Template format: 'mustache' or 'jinja'",
    )
    save_prompt_version = serializers.BooleanField(required=False, default=False)

    def validate_response_format(self, value):
        """Validate response_format and normalize to execution format.

        Accepts:
        - "text", "json", "json_schema" (predefined formats)
        - {"type": "text"} or {"type": "json_object"} (LiteLLM format)
        - Object with 'schema' field (prompt playground format) - preserves 'id', 'name', and 'schema'

        Returns:
            str or dict: Normalized response_format for execution

        Raises:
            ValidationError: If value is invalid
        """
        # Handle prompt playground object format with schema
        # Expected input: {"id": "uuid", "name": "A1", "schema": {...}}
        # Output format: {"id": "uuid", "name": "A1", "schema": {...}}
        # The payload_builder will convert this to LiteLLM format during execution
        if isinstance(value, dict) and "schema" in value:
            # Preserve id, name, and schema fields
            result = {
                "name": value.get("name", "response_schema"),
                "schema": value["schema"],
            }
            # Include id if provided
            if "id" in value:
                result["id"] = value["id"]
            return result

        # Handle LiteLLM dict format (already correct)
        if isinstance(value, dict) and "type" in value:
            format_type = value.get("type")
            if format_type in ["text", "json_object", "json_schema"]:
                return value
            raise serializers.ValidationError(
                f"response_format type must be 'text', 'json_object', or 'json_schema', got '{format_type}'"
            )

        # Handle string format
        if isinstance(value, str):
            # Validate string value
            if value in ["text", "json", "json_schema", "string", "json_object"]:
                return value
            # Reject UUID strings - not supported for execution
            raise serializers.ValidationError(
                f"response_format string must be 'text', 'json', 'json_schema', 'string', or 'json_object'. Got: {value}"
            )

        raise serializers.ValidationError(
            f"response_format must be a string or dict, got {type(value).__name__}"
        )


class NodeWriteSerializer(serializers.Serializer):
    """Serializer for writing node data in requests."""

    id = serializers.UUIDField(
        required=True, help_text="Frontend-generated UUID for the node"
    )
    type = serializers.ChoiceField(required=True, choices=NodeType.choices)
    name = serializers.CharField(required=True, max_length=255)
    node_template_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    ref_graph_version_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    config = serializers.JSONField(required=False, default=dict)
    position = serializers.JSONField(required=False, default=dict)
    ports = PortWriteSerializer(many=True, required=False, default=list)
    prompt_template = PromptTemplateDataSerializer(
        required=False, allow_null=True, default=None
    )
    input_mappings = InputMappingSerializer(
        many=True,
        required=False,
        default=list,
        help_text="List of input mappings from port display_name to source reference",
    )

    def validate_name(self, value):
        if RESERVED_NAME_RE.search(value):
            raise serializers.ValidationError(
                "Node name cannot contain reserved characters: . [ ] { }"
            )
        return value

    def validate(self, attrs):
        version_status = self.context.get("version_status")
        if version_status == GraphVersionStatus.DRAFT:
            return attrs

        node_type = attrs.get("type")
        node_template_id = attrs.get("node_template_id")
        ref_graph_version_id = attrs.get("ref_graph_version_id")

        if node_type == NodeType.ATOMIC:
            if not node_template_id:
                raise serializers.ValidationError(
                    {"node_template_id": "Atomic nodes must have node_template_id set"}
                )
            if ref_graph_version_id:
                raise serializers.ValidationError(
                    {
                        "ref_graph_version_id": "Atomic nodes cannot have ref_graph_version_id"
                    }
                )
        elif node_type == NodeType.SUBGRAPH:
            if not ref_graph_version_id:
                raise serializers.ValidationError(
                    {
                        "ref_graph_version_id": "Subgraph nodes must have ref_graph_version_id set"
                    }
                )
            if node_template_id:
                raise serializers.ValidationError(
                    {"node_template_id": "Subgraph nodes cannot have node_template_id"}
                )
            if attrs.get("config"):
                raise serializers.ValidationError(
                    {"config": "Subgraph nodes must have empty config"}
                )

        return attrs


class CreateNodeSerializer(serializers.Serializer):
    """Serializer for POST /nodes/ — granular node creation."""

    id = serializers.UUIDField(
        required=True, help_text="FE-generated UUID for the node"
    )
    type = serializers.ChoiceField(required=True, choices=NodeType.choices)
    name = serializers.CharField(required=True, max_length=255)
    node_template_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    ref_graph_version_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    position = serializers.JSONField(required=False, default=dict)
    source_node_id = serializers.UUIDField(
        required=False, allow_null=True, default=None
    )
    config = serializers.JSONField(required=False, default=dict)
    prompt_template = PromptTemplateDataSerializer(
        required=False, allow_null=True, default=None
    )
    ports = PortCreateSerializer(many=True, required=False, default=list)
    input_mappings = InputMappingSerializer(
        many=True,
        required=False,
        default=list,
        help_text="List of input mappings from port display_name to source reference",
    )

    def validate_name(self, value):
        if RESERVED_NAME_RE.search(value):
            raise serializers.ValidationError(
                "Node name cannot contain reserved characters: . [ ] { }"
            )
        return value

    def validate_input_mappings(self, value):
        """Ensure all keys are unique in the list."""
        if not value:
            return value

        keys = [mapping["key"] for mapping in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("Duplicate keys found in input_mappings")

        return value

    def validate(self, attrs):
        node_type = attrs.get("type")
        if node_type == NodeType.ATOMIC and not attrs.get("node_template_id"):
            raise serializers.ValidationError(
                {"node_template_id": "Required for atomic nodes."}
            )

        # Validate input_mappings only for subgraph nodes
        input_mappings = attrs.get("input_mappings")
        if input_mappings and node_type != NodeType.SUBGRAPH:
            raise serializers.ValidationError(
                {"input_mappings": "Only supported for subgraph nodes"}
            )
        if attrs.get("config") and node_type == NodeType.SUBGRAPH:
            raise serializers.ValidationError(
                {"config": "Subgraph nodes derive config from ref_graph_version_id."}
            )

        return attrs


class UpdateNodeSerializer(serializers.Serializer):
    """Serializer for PATCH /nodes/{node_id}/ — all fields optional."""

    name = serializers.CharField(required=False, max_length=255)
    position = serializers.JSONField(required=False)
    config = serializers.JSONField(required=False)
    prompt_template = PromptTemplateDataSerializer(required=False, allow_null=True)
    ref_graph_version_id = serializers.UUIDField(required=False, allow_null=True)
    input_mappings = InputMappingSerializer(
        many=True,
        required=False,
        help_text="List of input mappings from port display_name to source reference",
    )
    ports = PortCreateSerializer(
        many=True,
        required=False,
        help_text="Replace all OUTPUT ports with this new set (input ports preserved)",
    )

    def validate_name(self, value):
        if RESERVED_NAME_RE.search(value):
            raise serializers.ValidationError(
                "Node name cannot contain reserved characters: . [ ] { }"
            )
        return value

    def validate_input_mappings(self, value):
        """Ensure all keys are unique in the list."""
        if not value:
            return value

        keys = [mapping["key"] for mapping in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("Duplicate keys found in input_mappings")

        return value
