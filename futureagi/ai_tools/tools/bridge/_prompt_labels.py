"""Bridge registration for PromptLabelViewSet."""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.prompt_labels import PromptLabelViewSet

expose_to_mcp(category="prompts")(PromptLabelViewSet)
