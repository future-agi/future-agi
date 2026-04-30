"""
LLM Prompt Runner for Graph Execution Engine.

This runner executes llm_prompt nodes by fetching all configuration from a
linked PromptVersion (via the PromptTemplateNode bridge table). It renders
message templates with input variables — supporting JSON dot notation for
extracting nested data from parent nodes — and calls the specified model
via RunPrompt.

Variable resolution:
    - Simple variables like {{question}} are resolved directly from inputs.
    - Dot notation like {{Node1.response.data.name}} resolves the full variable
      name from inputs, then extracts .data.name from the raw data.
    - If extraction fails, the variable is treated as a global variable.

Output:
    - response: str | dict — The LLM's response. Format depends on response_format:
        - "string" or "text" → Plain text string
        - "json" → Parsed JSON object (free-form)
        - "json_schema" → Parsed JSON object (validated against schema)
        - UUID string → Parsed JSON object (schema from UserResponseSchema)
"""

import json
import re
from typing import Any

from agent_playground.services.engine.node_runner import BaseNodeRunner, register_runner
from agent_playground.services.engine.utils.json_path import resolve_variable
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt

# Regex pattern to match {{variable}} placeholders (with optional whitespace)
_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(?P<placeholder>.*?)\s*\}\}")


class LLMPromptRunner(BaseNodeRunner):
    """
    Runner for llm_prompt template nodes.

    Fetches all configuration from the linked PromptVersion via
    PromptTemplateNode, renders messages with dot notation variable
    resolution, and calls RunPrompt.
    """

    def run(
        self,
        config: dict[str, Any],
        inputs: dict[str, Any],
        execution_context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute LLM prompt via RunPrompt using config from PromptVersion.

        Args:
            config: Node configuration (ignored — config comes from PromptVersion)
            inputs: Input port values used to fill template variables
            execution_context: Must contain node_id, organization_id, workspace_id

        Returns:
            Dict with "response" key containing LLM response

        Raises:
            ValueError: If PromptTemplateNode not found, modality unsupported,
                        model missing, or variable resolution fails
        """
        from agent_playground.models.prompt_template_node import PromptTemplateNode

        # Fetch config from PromptVersion via bridge table
        node_id = execution_context.get("node_id")
        if not node_id:
            raise ValueError("execution_context missing 'node_id'")

        try:
            ptn = PromptTemplateNode.no_workspace_objects.select_related(
                "prompt_version"
            ).get(node_id=node_id, deleted=False)
        except PromptTemplateNode.DoesNotExist:
            raise ValueError(
                f"No PromptTemplateNode found for node {node_id}. "
                "Link a prompt version to this node before execution."
            )

        prompt_config = ptn.prompt_version.prompt_config_snapshot
        if not prompt_config:
            raise ValueError("PromptVersion has empty prompt_config_snapshot")

        configuration = prompt_config.get("configuration", {})

        # Pre-execution validation: modality check
        modality = configuration.get("model_detail", {}).get("type", "chat")
        if modality != "chat":
            raise ValueError(
                f"Unsupported modality '{modality}' for agent playground. "
                "Only 'chat' modality is supported."
            )

        model = configuration.get("model")
        if not model:
            raise ValueError("PromptVersion configuration missing 'model'")

        messages = prompt_config.get("messages", [])
        template_format = configuration.get("template_format")
        rendered_messages = self._render_messages(messages, inputs, model, template_format)

        # Extract tool configs
        tools = configuration.get("tools", [])
        tools_to_send = []
        for tool in tools:
            if isinstance(tool, dict):
                tool_config = tool.get("config")
                if tool_config:
                    tools_to_send.append(tool_config)

        rp = RunPrompt(
            model=model,
            messages=rendered_messages,
            organization_id=execution_context.get("organization_id", ""),
            output_format=configuration.get("output_format", "string"),
            temperature=configuration.get("temperature"),
            frequency_penalty=configuration.get("frequency_penalty"),
            presence_penalty=configuration.get("presence_penalty"),
            max_tokens=configuration.get("max_tokens"),
            top_p=configuration.get("top_p"),
            response_format=configuration.get("response_format"),
            tool_choice=configuration.get("tool_choice"),
            tools=tools_to_send if tools_to_send else None,
            workspace_id=execution_context.get("workspace_id"),
            run_prompt_config=configuration,
        )

        response, value_info = rp.litellm_response(streaming=False)

        return {"response": self._format_response(response, configuration)}

    def _render_messages(
        self,
        messages: list[dict[str, Any]],
        inputs: dict[str, Any],
        model: str,
        template_format: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Render messages by replacing {{variable}} placeholders and processing media.

        Handles the prompt_config_snapshot format where content is a list of
        typed items: [{type: "text", text: "..."}, {type: "image_url", ...}].

        Args:
            messages: Messages from prompt_config_snapshot
            inputs: Input port values for variable resolution
            model: Model name (needed for media capability checks)
            template_format: "jinja" for Jinja2 rendering, otherwise regex replacement

        Returns:
            List of rendered messages ready for RunPrompt
        """
        from model_hub.views.prompt_template import handle_media

        rendered = []
        for message in messages:
            content = message.get("content", "")
            processed_content = []

            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or "type" not in item:
                        raise ValueError(
                            "Invalid content format: expected list of dicts "
                            "with 'type' key."
                        )

                    if item["type"] == "text":
                        text = item.get("text", "")
                        text = self._replace_placeholders(text, inputs, template_format)
                        processed_content.append({"type": "text", "text": text})
                    else:
                        # Non-text items (image_url, audio_url, pdf_url)
                        media_result = handle_media(item, model)
                        if media_result is not None:
                            processed_content.append(media_result)
                        else:
                            processed_content.append(item)

            elif isinstance(content, str):
                text = self._replace_placeholders(content, inputs, template_format)
                processed_content.append({"type": "text", "text": text})

            rendered.append({"role": message["role"], "content": processed_content})

        return rendered

    def _replace_placeholders(
        self,
        text: str,
        inputs: dict[str, Any],
        template_format: str | None = None,
    ) -> str:
        """Replace {{variable}} placeholders in text using resolve_variable.

        For Jinja2 mode, resolves all variables first then renders through
        the Jinja2 engine so {% if %}, {% for %}, filters, etc. work.

        Args:
            text: Text containing {{variable}} or Jinja2 placeholders
            inputs: Input port values for variable resolution
            template_format: "jinja" for Jinja2 rendering, otherwise regex replacement

        Returns:
            Text with all resolvable placeholders replaced
        """
        if not text:
            return text

        if template_format in ("jinja", "jinja2"):
            # Resolve all input variables, then render through Jinja2
            from model_hub.views.run_prompt import render_template, TEMPLATE_FORMAT_JINJA2

            # Build context by resolving each input variable.
            # Dot-notation keys (e.g. "Node1.response") must be nested into
            # dicts so Jinja2 attribute lookup ({{ Node1.response }}) works.
            context = {}
            for key, value in inputs.items():
                resolved = value
                if isinstance(resolved, str):
                    try:
                        parsed = json.loads(resolved)
                        if isinstance(parsed, (list, dict)):
                            resolved = parsed
                    except (ValueError, TypeError):
                        pass
                # Nest dot-notation keys: "Node1.response" -> context["Node1"]["response"]
                parts = key.split(".")
                target = context
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = resolved

            return render_template(text, context, TEMPLATE_FORMAT_JINJA2)

        # Default: regex-based placeholder replacement
        def replacer(match: re.Match) -> str:
            variable_str = match.group("placeholder").strip()
            try:
                value = resolve_variable(variable_str, inputs)
                return str(value) if not isinstance(value, str) else value
            except ValueError:
                raise ValueError(
                    f"Failed to resolve variable '{{{{{variable_str}}}}}'. "
                    f"Available inputs: {list(inputs.keys())}"
                )

        return _PLACEHOLDER_PATTERN.sub(replacer, text)

    def _format_response(
        self,
        response: Any,
        configuration: dict[str, Any],
    ) -> Any:
        """Format the LLM response based on response_format.

        Args:
            response: Raw response from RunPrompt
            configuration: The prompt configuration

        Returns:
            Formatted response based on response_format:
            - "text" → str (plain text)
            - "json" → dict (parsed JSON object)
            - "json_schema" → dict (parsed and validated JSON)
            - UUID string → dict (parsed JSON with schema validation)

        Raises:
            ValueError: If response_format is JSON but response is not valid JSON
        """
        response_format = configuration.get("response_format")

        if response_format == "text" or response_format is None:
            return str(response) if response else ""

        if self._is_json_response_format(response_format):
            if isinstance(response, str):
                try:
                    return json.loads(response)
                except json.JSONDecodeError:
                    return response
            return response

        return str(response) if response else ""

    def _is_json_response_format(self, response_format) -> bool:
        """Check if response_format indicates JSON output.

        Returns True for:
        - "json" → Free-form JSON output
        - "json_schema" → Structured JSON output (attempts UUID parse, may fail)
        - UUID string → Saved schema reference
        - dict with "schema" key → Inline JSON schema (e.g. UserResponseSchema)

        Returns False for:
        - None, "text" → Plain text output
        - Invalid UUID strings → Falls back to text
        """
        if response_format == "json":
            return True
        if isinstance(response_format, dict) and response_format.get("schema"):
            return True
        if isinstance(response_format, str) and response_format not in ("text", "json"):
            # Possibly a UUID pointing to UserResponseSchema
            import uuid as _uuid

            try:
                _uuid.UUID(response_format)
                return True
            except ValueError:
                return False
        return False


# Self-register on module import
register_runner("llm_prompt", LLMPromptRunner())
