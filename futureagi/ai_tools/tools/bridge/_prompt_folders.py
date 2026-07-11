"""Bridge registration for PromptFolderViewSet."""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.prompt_folder import PromptFolderViewSet

expose_to_mcp(category="prompts")(PromptFolderViewSet)
