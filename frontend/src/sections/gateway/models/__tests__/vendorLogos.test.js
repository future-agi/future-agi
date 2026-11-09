import { describe, expect, it } from "vitest";

import { logoMap, vendorLogo } from "../vendorLogos";

// Future AGI's provider logo set: every provider id model_hub's
// ProviderLogoUrls gives a logo, as /model-hub/develops/provider-status/
// lists them (logo_url). Hosts trimmed: only the file names are compared.
const FA_LOGO_SET = [
  ["openai", "openai-icon.png"],
  ["anthropic", "claude-ai-icon.png"],
  ["azure", "azure-icon.png"],
  ["vertex_ai", "vertex+ai.png"],
  ["gemini", "google-gemini-icon.png"],
  ["cohere", "cohere-small.png"],
  ["huggingface", "hugginface.png"],
  ["together_ai", "together-small.png"],
  ["replicate", "replicate-small.svg"],
  ["cerebras", "Cerebras.jpg"],
  ["friendliai", "friendliai-small.jpeg"],
  ["fireworks_ai", "fireworks-small.jpeg"],
  ["openrouter", "openrouter.png"],
  ["cloudflare", "cloudflare-icon.png"],
  ["deepseek", "deep-seek-small.png"],
  ["aleph_alpha", "aleph-alpha-small.jpeg"],
  ["mistral", "mistral-ai-icon.png"],
  ["bedrock", "bedrock-small.svg"],
  ["databricks", "databricks-small.png"],
  ["voyage", "Voyage+ai.png"],
  ["groq", "groq-small.png"],
  ["perplexity", "perplexity-ai-icon.png"],
  ["ai21", "ai21-small.png"],
].map(([provider, file]) => ({
  provider,
  logo_url: `https://logos.example/${file}`,
}));

const LOGOS = logoMap(FA_LOGO_SET);
const file = (vendor) => vendorLogo(vendor, LOGOS).split("/").pop();

// Only OpenAI, Anthropic, Gemini and Mistral
// drew a logo; every other vendor got a letter tile. Every catalog vendor
// the FA logo set covers takes its logo; the rest keep their initials.
describe("catalog vendor logos", () => {
  it.each([
    ["openai", "openai-icon.png"],
    ["anthropic", "claude-ai-icon.png"],
    ["google", "google-gemini-icon.png"],
    ["mistralai", "mistral-ai-icon.png"],
    ["cohere", "cohere-small.png"],
    ["deepseek", "deep-seek-small.png"],
    ["perplexity", "perplexity-ai-icon.png"],
    ["~perplexity", "perplexity-ai-icon.png"],
    ["ai21", "ai21-small.png"],
    ["amazon", "bedrock-small.svg"],
    ["microsoft", "azure-icon.png"],
    ["together", "together-small.png"],
    ["fireworks", "fireworks-small.jpeg"],
    ["aleph-alpha", "aleph-alpha-small.jpeg"],
    ["cerebras", "Cerebras.jpg"],
    ["databricks", "databricks-small.png"],
    ["huggingface", "hugginface.png"],
  ])("%s takes Future AGI's logo", (vendor, expected) => {
    expect(file(vendor)).toBe(expected);
  });

  it("vendors outside the set keep their initials, and the reseller draws none", () => {
    for (const vendor of [
      "meta-llama",
      "x-ai",
      "qwen",
      "nvidia",
      "openrouter",
    ]) {
      expect(vendorLogo(vendor, LOGOS)).toBe("");
    }
  });
});
