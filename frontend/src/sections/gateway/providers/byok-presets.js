// The provider presets the Add provider dialog offers.
//
// Two sources:
// - REGISTRY_HOSTS: the OpenAI-compatible inference hosts, each with the
//   base URL, path prefix and wire format its vendor documents. A host is
//   offered only when it claims an endpoint (its vendor documents a route),
//   so an org key is never sent toward a host with none. The dialog sends
//   these values explicitly, and the gateway's own defaults
//   (agentcc-gateway/internal/providers/presets.go) never override an
//   explicit value; byok-presets.test.js checks the two never disagree on a
//   host's wire format.
// - The providers the gateway has native adapters for (OpenAI, Anthropic,
//   Google, Vertex, Bedrock, Cohere) and Custom, which the registry does not
//   list.

export const REGISTRY_HOSTS = Object.freeze([
  {
    id: "anyscale",
    displayName: "Anyscale",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.endpoints.anyscale.com",
    apiPathPrefix: "/v1",
    endpoints: [],
    docsUrl: "https://docs.anyscale.com/llm/serving",
    status: "preset",
  },
  {
    id: "azure",
    displayName: "Azure OpenAI",
    apiFormat: "azure",
    extraFormats: [],
    baseUrl: "",
    apiPathPrefix: "",
    endpoints: ["chat"],
    docsUrl: "https://learn.microsoft.com/en-us/azure/foundry/openai/reference",
    status: "preset",
  },
  {
    id: "cerebras",
    displayName: "Cerebras",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.cerebras.ai",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl: "https://inference-docs.cerebras.ai/resources/openai",
    status: "preset",
  },
  {
    id: "deepinfra",
    displayName: "DeepInfra",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.deepinfra.com",
    apiPathPrefix: "/v1/openai",
    endpoints: ["chat"],
    docsUrl: "https://docs.deepinfra.com/chat/overview",
    status: "preset",
  },
  {
    id: "fireworks",
    displayName: "Fireworks AI",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.fireworks.ai/inference",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl: "https://docs.fireworks.ai/api-reference/post-chatcompletions",
    status: "preset",
  },
  {
    id: "groq",
    displayName: "Groq",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.groq.com/openai",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl: "https://console.groq.com/docs/api-reference",
    status: "preset",
  },
  {
    id: "huggingface",
    displayName: "Hugging Face",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://router.huggingface.co",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl: "https://huggingface.co/docs/inference-providers/index",
    status: "preset",
  },
  {
    id: "mistral",
    displayName: "Mistral",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.mistral.ai",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl:
      "https://docs.mistral.ai/getting-started/quickstarts/developer/first-api-request",
    status: "preset",
  },
  {
    id: "openrouter",
    displayName: "OpenRouter",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://openrouter.ai/api",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl: "https://openrouter.ai/docs/quickstart",
    status: "preset",
  },
  {
    id: "perplexity",
    displayName: "Perplexity",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.perplexity.ai",
    apiPathPrefix: "/v1",
    endpoints: [],
    docsUrl:
      "https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/how-to",
    status: "preset",
  },
  {
    id: "replicate",
    displayName: "Replicate",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.replicate.com",
    apiPathPrefix: "/v1",
    endpoints: [],
    docsUrl: "https://replicate.com/docs/reference/http",
    status: "preset",
  },
  {
    id: "together",
    displayName: "Together AI",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.together.ai",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl: "https://docs.together.ai/docs/inference/openai-compatibility",
    status: "preset",
  },
  {
    id: "vercel-ai-gateway",
    displayName: "Vercel AI Gateway",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://ai-gateway.vercel.sh",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl: "https://vercel.com/docs/ai-gateway",
    status: "verified",
  },
  {
    id: "xai",
    displayName: "xAI",
    apiFormat: "openai",
    extraFormats: [],
    baseUrl: "https://api.x.ai",
    apiPathPrefix: "/v1",
    endpoints: ["chat"],
    docsUrl:
      "https://docs.x.ai/developers/rest-api-reference/inference/chat-completions",
    status: "preset",
  },
]);

/** hosts.ClaimsEndpoints: only a host that claims an endpoint is a BYOK route. */
export function claimsEndpoints(host) {
  if (!host) return false;
  return (
    host.endpoints.length > 0 ||
    host.extraFormats.some((format) => format.endpoints.length > 0)
  );
}

// Key hints the vendors print on their own keys. Anything else gets a plain
// "Enter your <vendor> API key".
const KEY_PLACEHOLDERS = {
  groq: "gsk_...",
  openrouter: "sk-or-...",
  xai: "xai-...",
  huggingface: "hf_...",
  azure: "Enter your Azure API key",
};

function registryPreset(host) {
  return {
    label: host.displayName,
    baseUrl: host.baseUrl,
    apiFormat: host.apiFormat,
    apiPathPrefix: host.apiPathPrefix,
    keyPlaceholder:
      KEY_PLACEHOLDERS[host.id] || `Enter your ${host.displayName} API key`,
    supportedFormats: [
      host.apiFormat,
      ...host.extraFormats.map((format) => format.apiFormat),
    ],
    docsUrl: host.docsUrl,
    source: "registry",
  };
}

const NATIVE_PRESETS = {
  openai: {
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    apiFormat: "openai",
    keyPlaceholder: "sk-...",
    supportedFormats: ["openai", "anthropic"],
    docsUrl: "https://platform.openai.com/docs/api-reference",
    source: "native",
  },
  anthropic: {
    label: "Anthropic",
    baseUrl: "https://api.anthropic.com",
    apiFormat: "anthropic",
    keyPlaceholder: "sk-ant-...",
    supportedFormats: ["openai", "anthropic"],
    docsUrl: "https://docs.anthropic.com/en/api",
    source: "native",
  },
  google: {
    label: "Google (Gemini)",
    baseUrl: "https://generativelanguage.googleapis.com",
    apiFormat: "google",
    keyPlaceholder: "AIza...",
    supportedFormats: ["openai", "google"],
    docsUrl: "https://ai.google.dev/gemini-api/docs",
    source: "native",
  },
  vertex: {
    label: "Google Vertex AI",
    baseUrl: "",
    apiFormat: "gemini",
    authType: "gcp",
    supportedFormats: ["gemini"],
    docsUrl: "https://cloud.google.com/vertex-ai/generative-ai/docs",
    source: "native",
  },
  bedrock: {
    label: "AWS Bedrock",
    baseUrl: "",
    apiFormat: "bedrock",
    authType: "aws",
    supportedFormats: ["anthropic"],
    docsUrl: "https://docs.aws.amazon.com/bedrock/",
    source: "native",
  },
  cohere: {
    label: "Cohere",
    baseUrl: "https://api.cohere.ai/compatibility/v1",
    apiFormat: "openai",
    keyPlaceholder: "Enter your Cohere API key",
    supportedFormats: ["openai"],
    docsUrl: "https://docs.cohere.com/docs/compatibility-api",
    source: "native",
  },
};

const CUSTOM_PRESET = {
  label: "Custom / Self-hosted",
  baseUrl: "",
  apiFormat: "openai",
  keyPlaceholder: "Enter API key",
  supportedFormats: ["openai", "anthropic", "google"],
  source: "custom",
};

const BYOK_HOSTS = REGISTRY_HOSTS.filter(claimsEndpoints);

export const PROVIDER_PRESETS = Object.freeze({
  ...NATIVE_PRESETS,
  ...Object.fromEntries(
    BYOK_HOSTS.map((host) => [host.id, registryPreset(host)]),
  ),
  custom: CUSTOM_PRESET,
});

// The picker's groups, in the order they are listed. Every preset is in one.
export const PROVIDER_GROUPS = Object.freeze([
  {
    label: "Model providers",
    ids: ["openai", "anthropic", "google", "vertex", "bedrock", "azure"],
  },
  {
    label: "Inference hosts",
    ids: [
      "cerebras",
      "cohere",
      "deepinfra",
      "fireworks",
      "groq",
      "huggingface",
      "mistral",
      "together",
      "xai",
    ],
  },
  { label: "Routers and gateways", ids: ["openrouter", "vercel-ai-gateway"] },
  { label: "Other", ids: ["custom"] },
]);
