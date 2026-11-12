// Provider identity, never an API-supplied logo URL, selects bundled artwork.
// Unknown providers get a local generic icon, including URL-shaped identities.
// Keep the known identities aligned with model_hub.models.choices.ProviderLogoUrls.
export const GENERIC_PROVIDER_LOGO = "/assets/icons/settings/AIProviders.svg";
export const PROVIDER_LOGOS = Object.freeze({
  openai: "/icons/settings/openai.png",
  text_completion_openai: "/icons/settings/openai.png",
  anthropic: "/icons/settings/anthropic.png",
  azure: "/icons/settings/azure.png",
  azure_ai: "/icons/settings/azure.png",
  google: "/icons/settings/google.png",
  gemini: "/icons/settings/google.png",
  palm: "/assets/provider-logos/palm.png",
  vertex_ai: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_embedding_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_anthropic_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_mistral_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_text_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_image_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_code_text_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_llama_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_language_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_vision_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_ai21_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_chat_models: "/assets/provider-logos/vertex-ai.png",
  vertex_ai_code_chat_models: "/assets/provider-logos/vertex-ai.png",
  cohere: "/icons/settings/cohere.png",
  cohere_chat: "/icons/settings/cohere.png",
  huggingface: "/icons/settings/huggingface.png",
  hugging_face: "/icons/settings/huggingface.png",
  together_ai: "/icons/settings/together_ai.png",
  together: "/icons/settings/together_ai.png",
  mistral: "/icons/settings/mistral.png",
  text_completion_codestral: "/icons/settings/mistral.png",
  codestral: "/icons/settings/mistral.png",
  groq: "/icons/settings/groq.png",
  perplexity: "/icons/settings/perplexity.png",
  replicate: "/assets/provider-logos/replicate-small.svg",
  cerebras: "/assets/provider-logos/Cerebras.jpg",
  friendliai: "/assets/provider-logos/friendliai-small.jpeg",
  fireworks_ai: "/assets/provider-logos/fireworks-small.jpeg",
  fireworks_ai_embedding_models: "/assets/provider-logos/fireworks-small.jpeg",
  anyscale: "/assets/provider-logos/anyscale-small.png",
  nlp_cloud: "/assets/provider-logos/nlp-cloud.png",
  openrouter: "/assets/provider-logos/openrouter.png",
  cloudflare: "/assets/provider-logos/cloudflare-icon.png",
  deepseek: "/assets/provider-logos/deep-seek-small.png",
  aleph_alpha: "/assets/provider-logos/aleph-alpha-small.jpeg",
  bedrock: "/assets/provider-logos/bedrock-small.svg",
  ollama: "/assets/provider-logos/ollama.png",
  deepinfra: "/assets/provider-logos/deepinfram-small.jpeg",
  databricks: "/assets/provider-logos/databricks-small.png",
  voyage: "/assets/provider-logos/voyage-ai.png",
  ai21: "/assets/provider-logos/ai21-small.png",
  sagemaker: "/assets/provider-logos/Sagemaker.png",
  elevenlabs: "/assets/provider-logos/elevenlabs.svg",
  deepgram: "/assets/provider-logos/deepgram.svg",
  inworld: "/assets/provider-logos/inworld-small.png",
  rime: "/assets/provider-logos/rime-small.png",
  neuphonic: "/assets/provider-logos/neuphonic-small.png",
  hume: "/assets/provider-logos/hume-small.png",
  cartesia: "/assets/provider-logos/cartesia-small.png",
  lmnt: "/assets/provider-logos/lmnt-small.png",
});

export function localProviderLogo(value) {
  const identity =
    value && typeof value === "object"
      ? value.providers ??
        value.provider ??
        value.provider_id ??
        value.id ??
        value.name
      : value;
  const name = Array.isArray(identity) ? identity[0] : identity;
  if (typeof name !== "string") return GENERIC_PROVIDER_LOGO;
  const key = name
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
  return Object.hasOwn(PROVIDER_LOGOS, key)
    ? PROVIDER_LOGOS[key]
    : GENERIC_PROVIDER_LOGO;
}
