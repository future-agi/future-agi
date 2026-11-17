// The name each model provider is shown with, by provider id: the provider
// key cards (Get started > Add Keys, Settings > AI Providers, the Configure
// API keys drawer), the model pickers' missing-key hints, and Run Prompt's
// provider-key message for the providers listed here. The server's provider
// list title-cases the ids
// ("Openai", "Palm"), so the brand spelling lives here. Keys are the server's
// provider ids in lower case; an id not listed is named the way the server's
// list names it (titleCaseProviderId).
export const PROVIDER_DISPLAY_NAMES = Object.freeze({
  openai: "OpenAI",
  anthropic: "Anthropic",
  azure: "Azure OpenAI",
  bedrock: "AWS Bedrock",
  cohere: "Cohere",
  deepseek: "DeepSeek",
  fireworks_ai: "Fireworks AI",
  gemini: "Google Gemini",
  groq: "Groq",
  huggingface: "Hugging Face",
  mistral: "Mistral",
  openrouter: "OpenRouter",
  perplexity: "Perplexity",
  together_ai: "Together AI",
  vertex_ai: "Google Vertex AI",
  xai: "xAI",
  // Title case gets these brands wrong.
  ai21: "AI21",
  deepinfra: "DeepInfra",
  elevenlabs: "ElevenLabs",
  "fireworks_ai-embedding-models": "Fireworks AI Embedding Models",
  friendliai: "FriendliAI",
  lmnt: "LMNT",
  nlp_cloud: "NLP Cloud",
  palm: "PaLM",
  sagemaker: "SageMaker",
  "text-completion-openai": "Text Completion OpenAI",
  // The server's own spelling (choices.py _PROVIDER_DISPLAY_NAMES).
  typesafe: "TypeSafe",
});

/**
 * A provider id as the server's provider list names it
 * (LiteLlmModelProvider.get_choices): "_" and "-" read as spaces, then
 * Python's str.title(), which capitalises each letter that does not follow
 * a letter and lower-cases the others ("cohere_chat" -> "Cohere Chat").
 * @param {string} id
 * @returns {string}
 */
export function titleCaseProviderId(id) {
  let name = "";
  let afterLetter = false;
  for (const char of String(id).replace(/[_-]/g, " ")) {
    const upper = char.toUpperCase();
    const lower = char.toLowerCase();
    const isLetter = upper !== lower;
    name += isLetter && !afterLetter ? upper : lower;
    afterLetter = isLetter;
  }
  return name;
}

/**
 * A listed provider's display name, else null.
 * @param {unknown} provider a provider id ("openai"), or a list that starts with one
 * @returns {string|null}
 */
export function knownProviderDisplayName(provider) {
  const id = Array.isArray(provider) ? provider[0] : provider;
  if (typeof id !== "string") return null;
  const key = id.trim().toLowerCase();
  return Object.hasOwn(PROVIDER_DISPLAY_NAMES, key)
    ? PROVIDER_DISPLAY_NAMES[key]
    : null;
}

/**
 * The name to show for a provider: its display name when listed, otherwise
 * the name the server's provider list gives it. A provider card and a model's
 * missing-key hint both use it, so they always read the same.
 * @param {unknown} provider a provider id ("openai"), or a list that starts with one
 * @returns {string}
 */
export function providerDisplayName(provider) {
  const known = knownProviderDisplayName(provider);
  if (known) return known;
  const id = Array.isArray(provider) ? provider[0] : provider;
  return id == null ? "" : titleCaseProviderId(String(id).trim());
}
