from typing import Any

from jinja2 import Undefined
from pydantic import BaseModel


class PreserveUndefined(Undefined):
    """Custom Jinja2 Undefined that preserves undefined variables as {{variable}} instead of raising errors."""

    def __str__(self):
        return f"{{{{{self._undefined_name}}}}}"


class CustomModelConfig(BaseModel):
    completion_config: list[dict[str, Any]]
    env_config: list[dict[str, Any]]


"""
For azure, this config looks like this:
{
    "completion_config": [
    {
        "api_base": "<YOUR_AZURE_DEPLOYMENT_API_BASE>"
    },
    {
         "api_version": "<YOUR_AZURE_DEPLOYMENT_API_VERSION>"
    }
    ],
    "env_config": []
}
"""
