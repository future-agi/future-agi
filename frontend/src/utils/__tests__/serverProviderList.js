// GET /model-hub/develops/provider-status/ as the server lists it: one row
// per LiteLlmModelProvider member (futureagi/model_hub/models/choices.py), in
// enum order. display_name is LiteLlmModelProvider.get_choices(): the member
// name with "_" as a space, title-cased, except typesafe ("TypeSafe", the
// _PROVIDER_DISPLAY_NAMES override). type is "json" for the ids that start
// with one of PROVIDERS_WITH_JSON (model_hub/views/run_prompt.py).
export const SERVER_PROVIDERS = Object.freeze([
  { provider: "openai", display_name: "Openai", type: "text" },
  { provider: "anthropic", display_name: "Anthropic", type: "text" },
  { provider: "azure", display_name: "Azure", type: "json" },
  { provider: "vertex_ai", display_name: "Vertex Ai", type: "json" },
  { provider: "palm", display_name: "Palm", type: "text" },
  { provider: "gemini", display_name: "Gemini", type: "text" },
  { provider: "cohere", display_name: "Cohere", type: "text" },
  { provider: "huggingface", display_name: "Huggingface", type: "text" },
  {
    provider: "text-completion-openai",
    display_name: "Text Completion Openai",
    type: "text",
  },
  { provider: "together_ai", display_name: "Together Ai", type: "text" },
  { provider: "replicate", display_name: "Replicate", type: "text" },
  { provider: "cerebras", display_name: "Cerebras", type: "text" },
  { provider: "cohere_chat", display_name: "Cohere Chat", type: "text" },
  { provider: "friendliai", display_name: "Friendliai", type: "text" },
  { provider: "fireworks_ai", display_name: "Fireworks Ai", type: "text" },
  { provider: "anyscale", display_name: "Anyscale", type: "text" },
  {
    provider: "text-completion-codestral",
    display_name: "Text Completion Codestral",
    type: "text",
  },
  { provider: "nlp_cloud", display_name: "Nlp Cloud", type: "text" },
  { provider: "openrouter", display_name: "Openrouter", type: "text" },
  { provider: "cloudflare", display_name: "Cloudflare", type: "text" },
  { provider: "deepseek", display_name: "Deepseek", type: "text" },
  { provider: "aleph_alpha", display_name: "Aleph Alpha", type: "text" },
  {
    provider: "fireworks_ai-embedding-models",
    display_name: "Fireworks Ai Embedding Models",
    type: "text",
  },
  { provider: "mistral", display_name: "Mistral", type: "text" },
  { provider: "bedrock", display_name: "Bedrock", type: "json" },
  { provider: "ollama", display_name: "Ollama", type: "text" },
  { provider: "deepinfra", display_name: "Deepinfra", type: "text" },
  { provider: "databricks", display_name: "Databricks", type: "text" },
  { provider: "voyage", display_name: "Voyage", type: "text" },
  { provider: "groq", display_name: "Groq", type: "text" },
  { provider: "codestral", display_name: "Codestral", type: "text" },
  { provider: "perplexity", display_name: "Perplexity", type: "text" },
  { provider: "ai21", display_name: "Ai21", type: "text" },
  { provider: "sagemaker", display_name: "Sagemaker", type: "json" },
  { provider: "elevenlabs", display_name: "Elevenlabs", type: "text" },
  { provider: "deepgram", display_name: "Deepgram", type: "text" },
  { provider: "inworld", display_name: "Inworld", type: "text" },
  { provider: "rime", display_name: "Rime", type: "text" },
  { provider: "neuphonic", display_name: "Neuphonic", type: "text" },
  { provider: "hume", display_name: "Hume", type: "text" },
  { provider: "cartesia", display_name: "Cartesia", type: "text" },
  { provider: "lmnt", display_name: "Lmnt", type: "text" },
  { provider: "typesafe", display_name: "TypeSafe", type: "text" },
]);

// The response body the provider-status endpoint sends for SERVER_PROVIDERS,
// with no key saved for any of them. Each row gets its own id, as lists of
// cards key on it.
export const providerStatusResponse = () => ({
  data: {
    result: {
      providers: SERVER_PROVIDERS.map((row) => ({
        ...row,
        has_key: false,
        masked_key: null,
        logo_url: null,
        id: `status-${row.provider}`,
      })),
    },
  },
});
