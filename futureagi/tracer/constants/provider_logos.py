"""Provider logos for spans served from Postgres, as root-relative paths.

The paths are files in frontend/public that the install's own UI serves, the
same artwork model_hub.models.choices.ProviderLogoUrls returns. Before, these
were absolute URLs on the fi-image-assets S3 bucket (Google's on fi-content-dev),
so every browser drawing a span's provider logo fetched it from that bucket.
See tracer/constants/provider_icons.py for the ClickHouse reader's paths.
"""

from tracer.utils.otel import FiLLMProviderValues

PROVIDER_LOGOS = {
    FiLLMProviderValues.OPENAI.value: "/icons/settings/openai.png",
    FiLLMProviderValues.ANTHROPIC.value: "/icons/settings/anthropic.png",
    FiLLMProviderValues.COHERE.value: "/icons/settings/cohere.png",
    FiLLMProviderValues.MISTRALAI.value: "/icons/settings/mistral.png",
    FiLLMProviderValues.GOOGLE.value: "/icons/settings/google.png",
    FiLLMProviderValues.AZURE.value: "/icons/settings/azure.png",
    FiLLMProviderValues.AWS.value: "/assets/provider-logos/bedrock-small.svg",
    FiLLMProviderValues.VERTEXAI.value: "/assets/provider-logos/vertex-ai.png",
}
