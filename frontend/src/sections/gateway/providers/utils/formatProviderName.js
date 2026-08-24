/**
 * Format a provider name for display in the gateway UI.
 *
 * Priority: display_name > known display name for identifier > title-cased identifier > "Provider N"
 *
 * @param {object} provider - Provider object with optional display_name, name, provider_name, id
 * @param {number} [index] - Fallback index for "Provider N" naming
 * @returns {string} Human-readable provider name
 */
export function formatProviderName(provider, index) {
  // 1. Explicit display_name takes priority
  const displayName = (provider?.display_name || "").trim();
  if (displayName) return displayName;

  // 2. Use provider identifier with proper casing
  const rawName =
    provider?.name || provider?.provider_name || provider?.id || "";
  if (rawName) {
    return formatProviderIdentifier(rawName);
  }

  // 3. Last resort — only when no identifier exists at all
  if (typeof index === "number") {
    return `Provider ${index + 1}`;
  }

  return "Unknown Provider";
}

/**
 * Known provider identifiers mapped to their human-readable display names.
 * Covers all providers in LiteLlmModelProvider enum and gateway KnownProviders.
 */
const KNOWN_DISPLAY_NAMES = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google (Gemini)",
  gemini: "Google Gemini",
  azure: "Azure OpenAI",
  bedrock: "AWS Bedrock",
  groq: "Groq",
  cohere: "Cohere",
  mistral: "Mistral AI",
  together: "Together AI",
  together_ai: "Together AI",
  perplexity: "Perplexity",
  deepseek: "DeepSeek",
  fireworks: "Fireworks AI",
  fireworks_ai: "Fireworks AI",
  cerebras: "Cerebras",
  deepinfra: "DeepInfra",
  huggingface: "Hugging Face",
  anyscale: "Anyscale",
  replicate: "Replicate",
  openrouter: "OpenRouter",
  xai: "xAI",
  vertex: "Google Vertex",
  vertex_ai: "Google Vertex AI",
  ollama: "Ollama",
  databricks: "Databricks",
  sagemaker: "SageMaker",
  custom: "Custom / Self-hosted",
};

/**
 * Format a raw provider identifier (e.g., "openai", "together_ai") into a
 * human-readable display name (e.g., "OpenAI", "Together AI").
 *
 * @param {string} name - Raw provider identifier
 * @returns {string} Human-readable display name
 */
export function formatProviderIdentifier(name) {
  const key = name.toLowerCase().replace(/-/g, "_");
  if (KNOWN_DISPLAY_NAMES[key]) return KNOWN_DISPLAY_NAMES[key];

  // Fallback: title-case with underscores and hyphens replaced by spaces
  return name
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
